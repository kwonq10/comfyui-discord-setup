"""Phase 1/2: 既存の ImageSmith 環境を読み取りで検出する（何も変更しない）。

確認すること:
  - インストール先（ImageSmith フォルダと仮想環境 .venv）
  - 取得元とコミット（.git のファイルを直接読む。git コマンドはロックを作らない設定で実行）
  - 上流からの変更の有無（変更ファイル名のみ。中身は表示しない）
  - 仮想環境の Python 版（pyvenv.cfg を読むだけ。venv の python は実行しない）
  - 起動中かどうか（venv の python で main.py が動いているか。コマンドライン文字列は出力しない）
  - 設定ファイル configuration.yml は「あるかどうか」だけ。トークンを含み得るため開かない

使い方:
    python detect_imagesmith.py                 # 初心者向けの要約
    python detect_imagesmith.py --details       # 詳細（コミット・変更ファイル名・プロセス番号など）
    python detect_imagesmith.py --dir D:\\path   # 探す場所を追加（複数可）
    python detect_imagesmith.py --deep          # 各ドライブ直下も探す
    python detect_imagesmith.py --json
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True  # Skill フォルダに __pycache__ を作らない

import argparse  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import string  # noqa: E402
import subprocess  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import (INSTALL_STATE_FILE, INSTALL_TOOL_ID, anonymize_path, emit, is_windows,  # noqa: E402
                     redact, setup_stdout)

PINNED_COMMIT = "dcc08071e6e6c8b35aeb971b829e8a3f122790e7"
UPSTREAM_URL_HINT = "github.com/jtyszkiew/imagesmith"
DEFAULT_FOLDER_NAME = "comfy-discord-imagesmith"
RECOMMENDED_PYTHON = "3.12"
# 上流のファイル（plugins/__init__.py）以外は、このPCで独自に追加したプラグイン
UPSTREAM_PLUGIN_FILES = {"__init__.py"}
# 上流の既知バグを直した1行（setup_imagesmith.py patch-security と同じ内容）
SECURITY_FIX_LINE = "if not result.state:"


# --- 検出 -------------------------------------------------------------------

def is_imagesmith_repo(path: str) -> bool:
    return (os.path.isfile(os.path.join(path, "main.py"))
            and os.path.isfile(os.path.join(path, "src", "bot", "imagesmith.py")))


def norm(path: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def candidate_dirs(extra: list[str] | None, deep: bool) -> list[str]:
    home = os.path.expanduser("~")
    cands = [os.path.join(home, DEFAULT_FOLDER_NAME)]
    if is_windows():
        for letter in string.ascii_uppercase:
            root = f"{letter}:\\"
            if os.path.exists(root):
                cands.append(os.path.join(root, DEFAULT_FOLDER_NAME))
    cands += extra or []
    roots = [home, os.path.join(home, "Desktop"), os.path.join(home, "Documents")]
    if deep and is_windows():
        roots += [f"{l}:\\" for l in string.ascii_uppercase if os.path.exists(f"{l}:\\")]
    for root in roots:  # 2階層まで、名前に imagesmith を含むフォルダだけ
        try:
            level1 = [e for e in os.scandir(root) if e.is_dir(follow_symlinks=False)]
        except OSError:
            continue
        for e in level1:
            if "imagesmith" in e.name.lower():
                cands.append(e.path)
    return cands


def resolve_install(path: str) -> dict | None:
    """path が「ImageSmith 本体」か「本体を含む親フォルダ」なら、その構成を返す。"""
    if not os.path.isdir(path):
        return None
    if is_imagesmith_repo(path):
        repo, base = path, os.path.dirname(path)
    else:
        repo = None
        for name in ("ImageSmith", "imagesmith"):
            p = os.path.join(path, name)
            if is_imagesmith_repo(p):
                repo = p
                break
        if repo is None:
            return None
        base = path
    venv = None
    for p in (os.path.join(base, ".venv"), os.path.join(repo, ".venv"), os.path.join(repo, "venv")):
        if os.path.isfile(os.path.join(p, "pyvenv.cfg")):
            venv = p
            break
    return {"base": base, "repo": repo, "venv": venv}


def read_text(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def git_info(repo: str, runner=None) -> dict:
    gitdir = os.path.join(repo, ".git")
    info = {"has_git": os.path.isdir(gitdir), "commit": None, "branch": None, "remote": None,
            "modified": None, "untracked": None}
    if not info["has_git"]:
        return info
    head = (read_text(os.path.join(gitdir, "HEAD")) or "").strip()
    if head.startswith("ref:"):
        ref = head[4:].strip()
        info["branch"] = ref.rsplit("/", 1)[-1]
        sha = (read_text(os.path.join(gitdir, *ref.split("/"))) or "").strip()
        if not sha:
            for line in (read_text(os.path.join(gitdir, "packed-refs")) or "").splitlines():
                if line.endswith(" " + ref):
                    sha = line.split()[0]
        info["commit"] = sha or None
    elif len(head) >= 7:
        info["commit"], info["branch"] = head, "(detached)"
    cfg = read_text(os.path.join(gitdir, "config")) or ""
    in_origin = False
    for line in cfg.splitlines():
        s = line.strip()
        if s.startswith("["):
            in_origin = s.replace(" ", "") in ('[remote"origin"]',)
        elif in_origin and s.startswith("url"):
            info["remote"] = redact(s.split("=", 1)[1].strip())
    runner = runner or _git_status
    out = runner(repo)
    if out is not None:
        mod, untracked = [], 0
        for line in out.splitlines():
            if line.startswith("?? "):
                untracked += 1
            elif len(line) > 3:
                mod.append(line[3:].strip().strip('"'))
        info["modified"], info["untracked"] = mod, untracked
    return info


def _git_status(repo: str) -> str | None:
    """index を書き換えない（optional lock を取らない）設定で git status を読む。
    環境変数をコピーして渡すと DISCORD_TOKEN も読むことになるため、env は指定しない。"""
    try:
        p = subprocess.run(["git", "--no-optional-locks", "-C", repo, "status", "--porcelain=v1"],
                           capture_output=True, text=True, timeout=30, errors="replace")
        return p.stdout if p.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def venv_info(venv: str | None) -> dict:
    if not venv:
        return {"exists": False}
    exe = os.path.join(venv, "Scripts", "python.exe") if is_windows() else os.path.join(venv, "bin", "python")
    version = None
    for line in (read_text(os.path.join(venv, "pyvenv.cfg")) or "").splitlines():
        k, _, v = line.partition("=")
        if k.strip() in ("version", "version_info"):
            version = v.strip()
            break
    return {"exists": True, "python_exe_exists": os.path.isfile(exe), "python_version": version, "_exe": exe}


def list_processes(runner=None) -> list[dict]:
    """python.exe の PID・親PID・実行ファイル・main.py 実行か を返す（コマンドライン文字列は返さない）。"""
    if runner:
        return runner()
    if not is_windows():
        return []
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | ForEach-Object { "
          "[pscustomobject]@{pid=$_.ProcessId; ppid=$_.ParentProcessId; exe=$_.ExecutablePath; "
          "main=($_.CommandLine -match '(^|[\\s\"])main\\.py(\"|\\s|$)')} } | ConvertTo-Json -Compress")
    try:
        p = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                           timeout=30, errors="replace")
        data = json.loads(p.stdout) if p.stdout.strip() else []
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
    return data if isinstance(data, list) else [data]


def running_pids(venv_exe: str | None, procs: list[dict]) -> list[int]:
    if not venv_exe:
        return []
    target = norm(venv_exe)
    launchers = [p for p in procs if p.get("exe") and norm(p["exe"]) == target and p.get("main")]
    pids = [p["pid"] for p in launchers]
    pids += [c["pid"] for c in procs if c.get("main") and c.get("ppid") in pids and c["pid"] not in pids]
    return pids


def install_state(base: str) -> str | None:
    """Skill の処理記録の状態（complete / in_progress / failed / unreadable）。記録が無ければ None。"""
    path = os.path.join(base, INSTALL_STATE_FILE)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            st = json.load(f)
    except (OSError, ValueError):
        return "unreadable"
    if not isinstance(st, dict) or st.get("tool") != INSTALL_TOOL_ID:
        return "unreadable"
    return st.get("status") if st.get("status") in ("complete", "in_progress", "failed") else "unreadable"


def inspect(install: dict, git_runner=None, procs: list[dict] | None = None) -> dict:
    repo = install["repo"]
    g = git_info(repo, git_runner)
    v = venv_info(install["venv"])
    plugins_dir = os.path.join(repo, "plugins")
    plugins = sorted(n for n in os.listdir(plugins_dir)
                     if n.endswith(".py") and n not in UPSTREAM_PLUGIN_FILES) if os.path.isdir(plugins_dir) else []
    workflows_dir = os.path.join(repo, "workflows")
    workflows = sorted(n for n in os.listdir(workflows_dir) if n.endswith(".json")) if os.path.isdir(workflows_dir) else []
    # configuration.yml はトークンを含み得るので存在確認だけ（開かない）
    has_config = os.path.isfile(os.path.join(repo, "configuration.yml"))
    sec = read_text(os.path.join(repo, "src", "core", "security.py")) or ""
    pids = running_pids(v.get("_exe"), procs or [])
    r = {
        "base": install["base"], "repo": repo, "venv": install["venv"],
        "base_display": anonymize_path(install["base"]),
        "git": g, "venv_info": {k: val for k, val in v.items() if not k.startswith("_")},
        "has_config": has_config, "custom_plugins": plugins, "workflow_files": workflows,
        "security_fix_applied": SECURITY_FIX_LINE in sec, "running_pids": pids, "running": bool(pids),
        "has_launcher": os.path.isfile(os.path.join(install["base"], "launcher", "start.ps1")),
    }
    problems, notes = [], []
    if not v["exists"]:
        problems.append("仮想環境（.venv）が見つかりません。")
    elif not v["python_exe_exists"]:
        problems.append("仮想環境の python.exe がありません（壊れている可能性）。")
    inst = install_state(install["base"])
    r["install_state"] = inst
    if inst not in (None, "complete"):
        problems.append("このSkillによる導入が途中で止まっています（処理記録あり）。"
                        "setup_imagesmith.py plan で状況を確認してください。")
    if v.get("python_version") and not v["python_version"].startswith(RECOMMENDED_PYTHON):
        notes.append(f"仮想環境の Python が {v['python_version']} です（確認済みは {RECOMMENDED_PYTHON}）。")
    if not g["has_git"]:
        notes.append("取得元（.git）が無いため、どの版か確認できません。")
    elif g["commit"] and g["commit"] != PINNED_COMMIT:
        notes.append(f"コミット {g['commit'][:7]} は、このSkillで確認した {PINNED_COMMIT[:7]} と異なります（未検証）。")
    if g.get("remote") and UPSTREAM_URL_HINT not in g["remote"].lower():
        notes.append("取得元が公式リポジトリではありません。")
    mod = g.get("modified") or []
    other = [m for m in mod if m.replace("\\", "/") != "src/core/security.py"]
    if "src/core/security.py" in [m.replace("\\", "/") for m in mod]:
        notes.append("security.py に変更あり" + ("（既知バグの1行修正）" if r["security_fix_applied"] else "（内容は未確認）") + "。")
    if other:
        notes.append(f"上流から変更されたファイルが他に {len(other)} 個あります。")
    if plugins:
        notes.append(f"独自プラグインあり（上流の標準機能ではない）: {', '.join(plugins)}")
    if not has_config:
        notes.append("設定ファイル configuration.yml がまだありません（Phase 4 で作成）。")
    r["problems"], r["notes"] = problems, notes
    r["status"] = "ready" if not problems else "needs_attention"
    # ソースだけがある（仮想環境も設定も無い）＝実行環境として成立していない。フォルダ名では判断しない
    r["source_only"] = not v["exists"] and not has_config
    r["shortcuts"] = None  # attach_shortcuts で設定（未確認は None）
    return r


def attach_shortcuts(installs: list[dict], shortcut_results: list[dict]) -> list[dict]:
    """ショートカットの判定結果を、それが使う環境に結びつける。どれにも結びつかないものを返す。"""
    by_base = {norm(i["base"]): i for i in installs}
    for i in installs:
        i["shortcuts"] = []
    unmatched = []
    for s in shortcut_results:
        name = (s.get("name") or "").removesuffix(".lnk")
        target = by_base.get(norm(s["env"])) if s.get("env") else None
        if target is not None:
            target["shortcuts"].append(name)
        else:
            unmatched.append(s)
    return unmatched


def find_installs(extra=None, deep=False, git_runner=None, proc_runner=None, check_processes=True) -> list[dict]:
    seen, installs = set(), []
    procs = list_processes(proc_runner) if check_processes else []
    for c in candidate_dirs(extra, deep):
        inst = resolve_install(c)
        if not inst:
            continue
        key = norm(inst["repo"])
        if key in seen:
            continue
        seen.add(key)
        installs.append(inspect(inst, git_runner, procs))
    return installs


def inspect_target(target_dir: str, git_runner=None, proc_runner=None, check_processes=True) -> dict:
    """ユーザーが指定した場所「だけ」を調べる。ほかの場所は探さない・比較しない。"""
    path = os.path.abspath(os.path.expandvars(os.path.expanduser(target_dir)))
    shown = anonymize_path(path)
    if not os.path.isdir(path):
        return {"installations": [], "recommendation": {
            "action": "target_invalid", "target": path,
            "message": f"指定された場所（{shown}）にフォルダがありません。ほかの環境には切り替えません。場所の綴りを確認してください。"}}
    inst = resolve_install(path)
    if not inst:
        return {"installations": [], "recommendation": {
            "action": "target_invalid", "target": path,
            "message": f"指定された場所（{shown}）に ImageSmith 本体（ImageSmith\\main.py など）が見つかりません。"
                       "ほかの環境には切り替えません。ImageSmith フォルダか、その1つ上のフォルダを指定してください。"}}
    procs = list_processes(proc_runner) if check_processes else []
    r = inspect(inst, git_runner, procs)
    if r["status"] == "ready":
        rec = {"action": "reuse", "target": r["base"],
               "message": f"指定された ImageSmith（{r['base_display']}）は使える状態です。既存環境を再利用できます。"
                          "新しいインストールは不要です。"}
    elif r["source_only"]:
        rec = {"action": "target_not_usable", "target": r["base"],
               "message": f"指定された場所（{r['base_display']}）には ImageSmith のソースファイルはありますが、"
                          "実行に必要な環境（仮想環境）は確認できませんでした。ほかの環境には切り替えません。"
                          "別の場所を指定するか、新しく導入するかを一緒に決めます。"}
    else:
        reasons = " / ".join(p.rstrip("。") for p in r["problems"])
        rec = {"action": "target_not_usable", "target": r["base"],
               "message": f"指定された ImageSmith（{r['base_display']}）は、そのままでは使えません（理由: {reasons}）。"
                          "ほかの環境には切り替えません。この環境をどうするか（修復・別の場所の指定・新規導入）を一緒に決めます。"}
    return {"installations": [r], "recommendation": rec}


def recommend(installs: list[dict]) -> dict:
    # ソースだけのフォルダ（実行環境として成立していない）は判断に使わない
    usable = [i for i in installs if not i.get("source_only")]
    ready = [i for i in usable if i["status"] == "ready"]
    if not usable:
        msg = "既存の ImageSmith は見つかりませんでした。Phase 2 で新しく導入します（承認が必要）。"
        if installs:
            msg = ("ImageSmith のソースファイルだけのフォルダはありましたが、実行できる環境は見つかりませんでした。"
                   "Phase 2 で新しく導入します（承認が必要）。")
        return {"action": "new_install", "message": msg}
    if len(ready) == 1:
        return {"action": "reuse", "target": ready[0]["base"],
                "message": f"既存の ImageSmith（{ready[0]['base_display']}）がそのまま使えます。再インストールは不要です。"}
    if len(ready) > 1:
        return {"action": "choose", "candidates": [i["base"] for i in ready],
                "message": f"使える ImageSmith が {len(ready)} か所あります。どれを使うかをあなたが選んでください"
                           "（Skillは勝手に選びません）。同じBotを2か所で同時に起動しないでください。"}
    return {"action": "needs_attention",
            "message": "ImageSmith は見つかりましたが、そのままでは使えない状態です。再インストールの前に、原因を一緒に確認します。"}


# --- 表示 -----------------------------------------------------------------

def human(installs: list[dict], rec: dict, details: bool) -> list[str]:
    lines = ["=== 既存の ImageSmith（Discord Bot のプログラム）の確認（読み取りのみ） ==="]
    for n, i in enumerate(installs, 1):
        if i.get("source_only") and not details:
            lines.append(f"[{n}] {i['base_display']}  … 今回は使用しません")
            lines.append("    ImageSmith のソースファイルが見つかりましたが、実行に必要な環境（仮想環境）は確認できませんでした。"
                         "今回は使用しません。")
            continue
        state = "使える状態" if i["status"] == "ready" else "要確認"
        run = "起動中" if i["running"] else "停止中"
        lines.append(f"[{n}] {i['base_display']}  … {state} / {run}")
        vi = i["venv_info"]
        lines.append(f"    仮想環境（ImageSmith専用のPython置き場）: "
                     + (f"あり（Python {vi.get('python_version') or '版不明'}）" if vi.get("exists") else "なし"))
        lines.append("    設定ファイル: " + ("あり（中身は開いていません）" if i["has_config"] else "なし"))
        if i["has_launcher"]:
            lines.append("    起動用スクリプト（launcher）: あり")
        if i.get("shortcuts") is not None:
            lines.append("    デスクトップのショートカット: "
                         + ("、".join(f"「{s}」" for s in i["shortcuts"]) + " がこの環境を使います（リンク先で確認）"
                            if i["shortcuts"] else "この環境を使うものはありません（リンク先で確認）"))
        if i["git"]["commit"]:
            same = "Skillで確認済みの版" if i["git"]["commit"] == PINNED_COMMIT else "未確認の版"
            lines.append(f"    版: {same}")
        for p in i["problems"]:
            lines.append(f"    [要確認] {p}")
        for x in i["notes"]:
            lines.append(f"    [参考] {x}")
        if details:
            g = i["git"]
            lines.append(f"    --- 詳細 ---")
            lines.append(f"    本体: {anonymize_path(i['repo'])}")
            lines.append(f"    コミット: {g['commit']}  ブランチ: {g['branch']}  取得元: {g['remote']}")
            lines.append(f"    変更ファイル: {', '.join(g['modified']) if g['modified'] else 'なし'} / 未追跡 {g['untracked']}")
            lines.append(f"    ワークフロー: {', '.join(i['workflow_files']) or 'なし'}")
            lines.append(f"    起動用ショートカットのスクリプト: {'あり' if i['has_launcher'] else 'なし'}"
                         + (f" / 動作中のプロセス番号: {i['running_pids']}" if i["running_pids"] else ""))
    if not installs:
        lines.append("見つかりませんでした。")
    lines.append("")
    lines.append("判断: " + rec["message"])
    if any(i["running"] for i in installs):
        lines.append("※ 起動中の Bot があります。このSkillは起動・停止をしません。")
    if not details and installs:
        lines.append("（詳しい情報は --details で表示できます）")
    return lines


def main(argv=None) -> int:
    setup_stdout()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", action="append", help="探す場所を追加（複数可）")
    ap.add_argument("--deep", action="store_true", help="各ドライブ直下も探す")
    ap.add_argument("--details", action="store_true", help="詳細を表示")
    ap.add_argument("--target-dir", help="この場所だけを調べる（ほかの環境は探さない・比較しない）")
    ap.add_argument("--no-shortcuts", action="store_true", help="デスクトップのショートカットを確認しない")
    ap.add_argument("--read-only", action="store_true", help="読み取り専用（このスクリプトは常に読み取り専用）")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.target_dir:
        if a.dir or a.deep:
            print("[エラー] --target-dir は --dir / --deep と同時に使えません（指定先だけを調べるため）。")
            return 2
        res = inspect_target(a.target_dir)
        installs, rec = res["installations"], res["recommendation"]
    else:
        installs = find_installs(a.dir, a.deep)
        rec = recommend(installs)
    unmatched = []
    if installs and not a.no_shortcuts:
        import check_shortcuts
        unmatched = attach_shortcuts(installs, check_shortcuts.check())
    lines = human(installs, rec, a.details)
    if a.target_dir and rec["action"] == "reuse":
        lines.append(f"この会話で使う ImageSmith: {anonymize_path(rec['target'])}（以降のコマンドにも --target-dir で同じ場所を指定します）")
    if unmatched and a.details:
        lines.append("ほかのショートカット: " + "、".join(f"「{(s['name'] or '').removesuffix('.lnk')}」（{s['state']}）"
                                                        for s in unmatched))
    emit({"installations": installs, "recommendation": rec, "unmatched_shortcuts": unmatched}, a.json, lines)
    return 0 if rec["action"] not in ("target_invalid", "target_not_usable") else 1


if __name__ == "__main__":
    sys.exit(main())
