"""Phase 2/4: ImageSmith の導入と設定ファイル作成の支援。

すべての変更系サブコマンドは --confirm が無いと「何をするか」を表示するだけで何もしない。
Claude Code はユーザーの明示的な承認を得てから --confirm を付けて実行すること。

サブコマンド:
  plan            導入先の確認と実行内容の表示（変更なし）
  install         git clone → 指定コミットに固定 → 仮想環境作成 → 依存導入 → 記録（--confirm 必須）
                  途中で失敗したら、どこまで進んだか・何が残っているか・次にすることを表示する。
                  自動削除はしない。--resume で、Skill が作った記録と実際の状態が一致する場合だけ続きから再開
  make-config     configuration.yml を作る（既存があればバックアップ後。--confirm 必須）
  patch-security  上流の既知バグ（settings単位の権限判定）を1行修正（任意。--confirm 必須）
  start-command   ユーザーが自分で実行する起動コマンドを表示（変更なし）

--read-only を付けると、--confirm があっても変更系サブコマンドは実行しない（終了コード 4）。
plan は既存の ImageSmith を探し、使える環境があれば新規導入を勧めない。

導入先の構成（既存の実機検証環境と同じ形）:
  <dir>\\ImageSmith\\   … 上流リポジトリ（再配布せず、公式から取得）
  <dir>\\.venv\\        … 専用の Python 仮想環境
  <dir>\\.comfy-discord-setup-install.json … 処理記録（工程ごとの成否。秘密情報なし）
  <dir>\\install_record.json … 完了時の記録（取得元・コミット・Python・パッケージ版。秘密情報なし）
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True  # Skill フォルダに __pycache__ を作らない

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import (INSTALL_STATE_FILE, INSTALL_TOOL_ID, UnsafeUrlError,  # noqa: E402
                     anonymize_path, expand_path, redact, setup_stdout, validate_comfy_url)

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_URL = "https://github.com/jtyszkiew/ImageSmith.git"
# 2026-10-29 時点の上流 master 先頭。実機検証もこのコミット
PINNED_COMMIT = "dcc08071e6e6c8b35aeb971b829e8a3f122790e7"
CONSTRAINTS = os.path.join(SKILL_DIR, "templates", "constraints-verified.txt")
DEFAULT_DIR = r"%USERPROFILE%\comfy-discord-imagesmith"
TOKEN_PLACEHOLDER = "SET_VIA_ENV_DISCORD_TOKEN"
STATE_SCHEMA = 1

SECURITY_BUG_OLD = "                if not result:\n                    return SecurityResult(result.state, result.message)"
SECURITY_BUG_NEW = "                if not result.state:\n                    return SecurityResult(result.state, result.message)"

# 導入の工程（順番どおり）。ID は処理記録に保存される
INSTALL_STEPS = [
    ("prepare_dir", "導入先フォルダの作成"),
    ("clone", "ImageSmith の取得（git clone）"),
    ("checkout", "確認済みコミットへの固定"),
    ("verify_commit", "取得元とコミットの照合"),
    ("venv", "Python 3.12 の仮想環境の作成"),
    ("pip_install", "必要なライブラリのインストール"),
    ("pip_check", "ライブラリの整合性の確認（pip check）"),
    ("record", "導入記録（install_record.json）の作成"),
]
STEP_LABELS = dict(INSTALL_STEPS)
STEP_IDS = [s for s, _ in INSTALL_STEPS]

# 工程ごとの初心者向け説明。原因は断定しない（「考えられる例」に留める）
STEP_FAILURE_TEXT = {
    "prepare_dir": "導入先フォルダを作成できませんでした。",
    "clone": "ImageSmith の取得（ダウンロード）に失敗しました。",
    "checkout": "確認済みのコミットに固定できませんでした。",
    "verify_commit": "取得したものが、確認済みの取得元・コミットと一致しませんでした。安全のため先へ進みません。",
    "venv": "Python の仮想環境を作成できませんでした。",
    "pip_install": "必要なライブラリのインストールに失敗しました。",
    "pip_check": "ライブラリの組み合わせに問題が見つかりました（pip check）。",
    "record": "導入記録のファイルを書き込めませんでした。",
}
STEP_HINTS = {
    "clone": "考えられる例：インターネットに接続できない、GitHub に一時的につながらない、Git が正しく入っていない。",
    "checkout": "考えられる例：取得が不完全だった、上流でコミットが消えた。",
    "venv": "考えられる例：Python 3.12 のインストールが壊れている、空き容量が足りない。",
    "pip_install": "考えられる例：インターネットに接続できない、空き容量が足りない、ウイルス対策ソフトがブロックした。",
    "pip_check": "考えられる例：インストールが途中で止まった。",
}
# 再開しても安全に上書きできない（途中のものが残っていたら、ユーザーに消してもらう）工程と、その成果物
STEP_ARTIFACT = {"clone": "ImageSmith", "venv": ".venv"}
ALLOWED_TOP_LEVEL = {INSTALL_STATE_FILE, "ImageSmith", ".venv"}

_HEX40 = re.compile(r"^[0-9a-f]{40}$")


class SetupError(RuntimeError):
    pass


class InstallFailed(SetupError):
    """導入の途中失敗。report に初心者向けの説明材料を持つ。"""

    def __init__(self, report: dict):
        super().__init__(report.get("message", "導入に失敗しました。"))
        self.report = report


def _clean_output(text: str, limit: int = 15) -> str:
    """外部コマンドの出力を、秘密情報を伏せ・ユーザー名を含むパスを匿名化して、末尾だけにする。"""
    home = os.path.expanduser("~")
    lines = (text or "").strip().splitlines()[-limit:]
    out = []
    for line in lines:
        if home and len(home) > 3:
            line = line.replace(home, "%USERPROFILE%").replace(home.replace("\\", "/"), "%USERPROFILE%")
        out.append(redact(line))
    return "\n".join(out)


def run(args: list[str], cwd: str | None = None, timeout: int = 900) -> str:
    """外部コマンドを実行する。シェルを使わず引数のリストで渡す（空白・日本語のパスも安全）。"""
    try:
        p = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
    except FileNotFoundError:
        raise SetupError(f"コマンド「{os.path.basename(args[0])}」が見つかりません。インストールされているか確認してください。")
    except subprocess.TimeoutExpired:
        raise SetupError(f"コマンド「{os.path.basename(args[0])}」が {timeout // 60} 分以内に終わらなかったため中止しました。")
    if p.returncode != 0:
        tail = _clean_output(p.stderr or p.stdout or "")
        raise SetupError(f"コマンドが失敗しました（終了コード {p.returncode}）: {os.path.basename(args[0])} "
                         f"{' '.join(str(a) for a in args[1:3])} ...\n{tail}")
    return p.stdout


def timestamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def now_iso() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def backup(path: str) -> str:
    """変更前のファイルを <名前>.<日時>.bak にコピーする。"""
    dst = f"{path}.{timestamp()}.bak"
    shutil.copy2(path, dst)
    return dst


def validate_commit(commit: str) -> str:
    """コミットは40桁の16進数だけを受け付ける（短縮形・ブランチ名・オプションに見える文字列は拒否）。"""
    c = (commit or "").strip().lower()
    if not _HEX40.match(c):
        raise SetupError("コミットは40桁の16進数（完全なハッシュ）で指定してください。")
    return c


# --- plan / install -----------------------------------------------------------

def _inside(child: str, parent: str) -> bool:
    c, p = os.path.normcase(os.path.abspath(child)), os.path.normcase(os.path.abspath(parent))
    return c == p or c.startswith(p.rstrip("\\/") + os.sep)


def inspect_target(target: str) -> dict:
    target = os.path.abspath(expand_path(target))
    info = {"dir": target, "dir_display": anonymize_path(target), "exists": os.path.exists(target),
            "conflicts": [], "nonempty": False, "ok": True}
    if _inside(target, SKILL_DIR) or _inside(SKILL_DIR, target):
        info["conflicts"].append("Skill のフォルダと重なる場所には導入できません。")
    if info["exists"]:
        if not os.path.isdir(target):
            info["conflicts"].append("同名のファイルがあります。")
        else:
            entries = os.listdir(target)
            if entries:
                info["nonempty"] = True
                info["conflicts"].append(f"フォルダが空ではありません（{len(entries)}項目）。"
                                         "既存環境を守るため、別のフォルダ名を指定してください。")
    drive = os.path.splitdrive(target)[0]
    try:
        free = shutil.disk_usage(drive + "\\" if drive else target).free / 1024 ** 3
        info["free_gb"] = round(free, 1)
        if free < 2:
            info["conflicts"].append("空き容量が2GB未満です。")
    except OSError:
        info["free_gb"] = None
    if any(ord(c) > 127 for c in target) or " " in target:
        info["warnings"] = ["パスに日本語や空白が含まれます。動作はする見込みですが、英数字だけのパスを推奨します。"]
    info["ok"] = not info["conflicts"]
    return info


def find_python312(runner=None) -> list[str]:
    """Python 3.12 の起動方法を返す（py ランチャー優先）。"""
    for cand in (["py", "-3.12"], ["python3.12"], ["python"]):
        try:
            out = (runner or run)(cand + ["--version"], timeout=15)
        except (OSError, subprocess.SubprocessError, SetupError):
            continue
        if (out or "").strip().startswith("Python 3.12"):
            return cand
    raise SetupError("Python 3.12 が見つかりません。python.org から 3.12 をインストールしてください。"
                     "（まだ何も作成していません）")


def existing_installs_summary(finder=None) -> dict:
    """既存の ImageSmith を探して、再利用の判断を返す（読み取りのみ）。"""
    if finder is None:
        import detect_imagesmith as di
        installs = di.find_installs(check_processes=False)
        return {"installs": installs, "recommendation": di.recommend(installs)}
    return finder()


def install_steps(target: str, commit: str) -> list[str]:
    return [
        f"1. フォルダ {anonymize_path(target)} を作成（処理記録 {INSTALL_STATE_FILE} も作成）",
        f"2. git clone {REPO_URL} {anonymize_path(os.path.join(target, 'ImageSmith'))}",
        f"3. コミット {commit[:12]} に固定（git checkout）",
        "4. 取得元のURLとコミットを照合（一致しなければ中止）",
        f"5. Python 3.12 の仮想環境を {anonymize_path(os.path.join(target, '.venv'))} に作成",
        "6. requirements.txt の依存を、実機確認済みの版（constraints-verified.txt）に合わせて導入",
        "7. pip check で依存関係の整合性を確認",
        "8. 取得元・コミット・パッケージ版を install_record.json に記録（秘密情報なし）",
        "途中で失敗した場合：作ったものは自動削除せず、状況と次にすることを表示します。",
    ]


# --- 処理記録（導入先の .comfy-discord-setup-install.json） --------------------

def state_path(target: str) -> str:
    return os.path.join(target, INSTALL_STATE_FILE)


def new_state(target: str, commit: str) -> dict:
    return {
        "tool": INSTALL_TOOL_ID, "schema_version": STATE_SCHEMA, "repository": REPO_URL, "commit": commit,
        "target": os.path.normcase(os.path.abspath(target)), "status": "in_progress",
        "steps": {}, "failed_step": None, "error": None, "started_at": now_iso(), "updated_at": now_iso(),
    }


def save_state(target: str, state: dict) -> None:
    state["updated_at"] = now_iso()
    tmp = state_path(target) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, state_path(target))


def load_state(target: str) -> dict | None:
    """処理記録を読む。無ければ None。壊れている・Skill のものでない場合は SetupError。"""
    p = state_path(target)
    if not os.path.isfile(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            st = json.load(f)
    except (OSError, ValueError):
        raise SetupError("処理記録ファイルを読めません（壊れている可能性）。このフォルダは変更しません。")
    if not isinstance(st, dict) or st.get("tool") != INSTALL_TOOL_ID or st.get("schema_version") != STATE_SCHEMA:
        raise SetupError("このSkillが作った処理記録ではありません。所有元を確認できないため、このフォルダは変更しません。")
    return st


def _vpy(target: str) -> str:
    venv = os.path.join(target, ".venv")
    return os.path.join(venv, "Scripts", "python.exe") if os.name == "nt" else os.path.join(venv, "bin", "python")


def verify_for_resume(target: str, commit: str, runner) -> dict:
    """再開してよいかを、処理記録と実際の状態を照合して判断する。だめなら SetupError（何も変更しない）。"""
    st = load_state(target)
    if st is None:
        raise SetupError("このフォルダには、このSkillの処理記録がありません。所有元を確認できないため、"
                         "再開も変更もしません。別の空のフォルダを指定してください。")
    if st.get("status") == "complete":
        raise SetupError("このフォルダの導入は既に完了しています。再開は不要です。")
    if st.get("repository") != REPO_URL or st.get("commit") != commit:
        raise SetupError("処理記録の取得元・コミットが今回の指定と異なります。安全のため再開しません。")
    if st.get("target") != os.path.normcase(os.path.abspath(target)):
        raise SetupError("処理記録の場所と実際の場所が異なります（フォルダが移動・コピーされた可能性）。再開しません。")
    unknown = sorted(set(os.listdir(target)) - ALLOWED_TOP_LEVEL)
    if unknown:
        raise SetupError("このSkillが作っていないファイル・フォルダがあります: " + "、".join(unknown[:5])
                         + "。安全のため再開しません。")
    steps = st.get("steps") or {}
    # 未完了の工程の途中成果物が残っていたら、上書きせずユーザーに消してもらう
    for step, artifact in STEP_ARTIFACT.items():
        if steps.get(step) != "done" and os.path.exists(os.path.join(target, artifact)):
            raise SetupError(
                f"「{STEP_LABELS[step]}」が完了していないのに、途中まで作られた {artifact} フォルダが残っています。"
                f"自動では削除しません。エクスプローラーで {anonymize_path(os.path.join(target, artifact))} "
                "を削除してから、もう一度 --resume で実行してください。")
    repo = os.path.join(target, "ImageSmith")
    if steps.get("clone") == "done":
        if not os.path.isdir(os.path.join(repo, ".git")):
            raise SetupError("記録では取得済みですが、ImageSmith フォルダ（.git）が見つかりません。再開しません。")
        if run_git_remote(repo, runner) != REPO_URL:
            raise SetupError("ImageSmith の取得元が公式と一致しません。再開しません（追加調査が必要）。")
    if steps.get("checkout") == "done":
        head = runner(["git", "-C", repo, "rev-parse", "HEAD"]).strip()
        if head != commit:
            raise SetupError("ImageSmith のコミットが記録と異なります（誰かが変更した可能性）。再開しません。")
        if runner(["git", "-C", repo, "status", "--porcelain"]).strip():
            raise SetupError("ImageSmith のファイルが取得後に変更されています。再開しません。")
    if steps.get("venv") == "done":
        vpy = _vpy(target)
        if not os.path.isfile(vpy):
            raise SetupError("記録では仮想環境を作成済みですが、python.exe が見つかりません。再開しません。")
        if not runner([vpy, "--version"]).strip().startswith("Python 3.12"):
            raise SetupError("仮想環境の Python が 3.12 ではありません。再開しません。")
    return st


def run_git_remote(repo: str, runner) -> str:
    return runner(["git", "-C", repo, "remote", "get-url", "origin"]).strip()


def leftovers(target: str) -> list[str]:
    if not os.path.isdir(target):
        return []
    return sorted(os.listdir(target))


def state_summary(target: str) -> list[str]:
    """導入先に処理記録があれば、その状態を説明する（読み取りのみ）。"""
    if not os.path.isdir(target):
        return []
    try:
        st = load_state(target)
    except SetupError as e:
        return [f"[注意] {e}"]
    if st is None:
        return []
    steps = st.get("steps") or {}
    done = [STEP_LABELS[s] for s in STEP_IDS if steps.get(s) == "done"]
    if st.get("status") == "complete":
        return ["処理記録: このSkillで導入が完了しているフォルダです（新しい導入・再開は不要）。"]
    failed = st.get("failed_step")
    lines = ["処理記録: このSkillが前回作ったフォルダで、導入は完了していません。",
             "  成功した工程: " + ("、".join(done) if done else "なし")]
    if failed in STEP_LABELS:
        lines.append(f"  止まった工程: {STEP_LABELS[failed]}")
    lines.append("  続きから行う場合は install --resume（承認後に --confirm）。作ったものは自動削除しません。")
    return lines


def failure_report(target: str, state: dict, step: str, error: str, state_saved: bool = True) -> dict:
    """失敗時に表示する内容（秘密情報は伏せる）。"""
    done = [s for s in STEP_IDS if (state.get("steps") or {}).get(s) == "done"]
    left = leftovers(target)
    artifact = STEP_ARTIFACT.get(step)
    must_delete = artifact if artifact and artifact in left else None
    if step == "verify_commit":
        resumable, action = False, ("取得元またはコミットが一致しない原因は、この表示だけでは特定できません（追加調査が必要）。"
                                    "このフォルダは使わず、Claude Code に相談してください。")
    elif step == "prepare_dir" and not state_saved:
        resumable, action = False, "フォルダを作れる場所か（権限・空き容量）を確認し、もう一度 install を実行してください。"
    elif must_delete:
        resumable, action = True, (f"エクスプローラーで {anonymize_path(os.path.join(target, must_delete))} を削除してから、"
                                   "--resume を付けて再実行してください（ほかのものは消さないでください）。")
    else:
        resumable, action = True, "原因を確認・解消してから、--resume を付けて再実行すると続きから再開できます。"
    return {
        "message": STEP_FAILURE_TEXT.get(step, "導入に失敗しました。"),
        "hint": STEP_HINTS.get(step),
        "failed_step": step, "done_steps": done, "leftovers": left, "dir_display": anonymize_path(target),
        "resumable": resumable, "next_action": action, "detail": _clean_output(error, limit=15),
    }


def human_failure(r: dict) -> list[str]:
    lines = ["[失敗] ImageSmith の導入は完了していません（このままでは使えません）。",
             "  " + r["message"]]
    if r.get("hint"):
        lines.append("  " + r["hint"] + "（原因はこの表示だけでは断定できません）")
    lines.append("成功した工程: " + ("、".join(f"{STEP_IDS.index(s) + 1}. {STEP_LABELS[s]}" for s in r["done_steps"])
                                     if r["done_steps"] else "なし"))
    lines.append(f"失敗した工程: {STEP_IDS.index(r['failed_step']) + 1}. {STEP_LABELS[r['failed_step']]}")
    if r["leftovers"]:
        lines.append(f"残っているもの（{r['dir_display']} の中。安全のため自動削除はしていません）: "
                     + "、".join(r["leftovers"]))
    else:
        lines.append("残っているもの: なし")
    lines.append("再実行: " + ("できます" if r["resumable"] else "このままではできません"))
    lines.append("次にすること: " + r["next_action"])
    if r.get("resumable"):
        lines.append(f'  再開コマンド（承認後）: setup_imagesmith.py install --dir "{r["dir_display"]}" --resume --confirm')
    if r.get("detail"):
        lines.append("技術的な詳細（秘密情報は伏せています）:")
        lines += ["  " + l for l in r["detail"].splitlines()]
    return lines


def _do_step(step: str, target: str, commit: str, py: list[str], runner) -> None:
    repo = os.path.join(target, "ImageSmith")
    if step == "clone":
        runner(["git", "clone", "--", REPO_URL, repo])
    elif step == "checkout":
        runner(["git", "-C", repo, "-c", "advice.detachedHead=false", "checkout", "--detach", commit])
    elif step == "verify_commit":
        head = runner(["git", "-C", repo, "rev-parse", "HEAD"]).strip()
        if head != commit:
            raise SetupError(f"コミットが一致しません（取得: {head[:12]} / 指定: {commit[:12]}）")
        remote = run_git_remote(repo, runner)
        if remote != REPO_URL:
            raise SetupError("取得元のURLが公式と一致しません。")
    elif step == "venv":
        runner(py + ["-m", "venv", os.path.join(target, ".venv")])
        vpy = _vpy(target)
        if not os.path.isfile(vpy):
            raise SetupError("仮想環境の python.exe が作成されていません。")
    elif step == "pip_install":
        pip_args = [_vpy(target), "-m", "pip", "install", "-r", os.path.join(repo, "requirements.txt")]
        if os.path.isfile(CONSTRAINTS):
            pip_args += ["-c", CONSTRAINTS]
        runner(pip_args, timeout=1800)
    elif step == "pip_check":
        runner([_vpy(target), "-m", "pip", "check"])
    elif step == "record":
        vpy = _vpy(target)
        freeze = [l for l in runner([vpy, "-m", "pip", "freeze"]).splitlines() if l and not l.startswith("#")]
        record = {
            "repository": REPO_URL,
            "commit": runner(["git", "-C", repo, "rev-parse", "HEAD"]).strip(),
            "installed_at": now_iso(),
            "python": runner([vpy, "--version"]).strip(),
            "packages": freeze,
            "constraints": os.path.basename(CONSTRAINTS) if os.path.isfile(CONSTRAINTS) else None,
            "license_note": "上流READMEはMITと記載。ただしこのコミットにLICENSEファイルは存在しない（2026-10-29確認）。",
        }
        with open(os.path.join(target, "install_record.json"), "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)


def do_install(target: str, commit: str, resume: bool = False, runner=None, python_finder=None) -> dict:
    """ImageSmith を導入する。失敗したら InstallFailed（作ったものは削除しない）。"""
    runner = runner or run
    commit = validate_commit(commit)
    info = inspect_target(target)
    target = info["dir"]
    if resume:
        other = [c for c in info["conflicts"] if not c.startswith("フォルダが空ではありません")]
        if other:
            raise SetupError(" / ".join(other))
        if not info["exists"]:
            raise SetupError("再開するフォルダが見つかりません。--resume を外して新規に導入してください。")
        state = verify_for_resume(target, commit, runner)
    else:
        if not info["ok"]:
            if info["nonempty"]:
                try:
                    st = load_state(target)
                except SetupError:
                    st = None
                if st and st.get("status") != "complete":
                    raise SetupError("このフォルダには、前回途中で止まった導入があります（処理記録あり）。"
                                     "続きから行う場合は --resume を付けてください（作ったものは自動削除しません）。")
            raise SetupError(" / ".join(info["conflicts"]))
        state = None
    py = (python_finder or (lambda: find_python312(runner)))()

    current = "prepare_dir"
    if state is None:
        state = new_state(target, commit)
        try:
            os.makedirs(target, exist_ok=True)
            save_state(target, state)
        except OSError as e:
            raise InstallFailed(failure_report(target, state, "prepare_dir", str(e), state_saved=False))
        state["steps"]["prepare_dir"] = "done"
        save_state(target, state)
    state["status"], state["failed_step"], state["error"] = "in_progress", None, None
    try:
        for step in STEP_IDS:
            if state["steps"].get(step) == "done":
                continue
            current = step
            state["steps"][step] = "started"
            save_state(target, state)
            _do_step(step, target, commit, py, runner)
            state["steps"][step] = "done"
            save_state(target, state)
    except Exception as e:  # noqa: BLE001 失敗を記録してから利用者向けに説明する
        msg = str(e) if isinstance(e, (SetupError, OSError)) else f"想定外のエラー（{type(e).__name__}）"
        state["steps"][current] = "failed"
        state["status"], state["failed_step"] = "failed", current
        state["error"] = _clean_output(msg, limit=3)[:500]
        try:
            save_state(target, state)
        except OSError:
            pass
        raise InstallFailed(failure_report(target, state, current, msg))
    state["status"] = "complete"
    save_state(target, state)
    with open(os.path.join(target, "install_record.json"), encoding="utf-8") as f:
        rec = json.load(f)
    return {"dir": anonymize_path(target), "commit": rec["commit"], "python": rec["python"],
            "packages": len(rec["packages"])}


# --- make-config -------------------------------------------------------------

def _q(s) -> str:
    """YAML の二重引用符スカラーとして安全に書く（JSON文字列はYAMLとしても有効）。"""
    return json.dumps(str(s), ensure_ascii=True)


def _require_ascii(label: str, value: str) -> None:
    if any(ord(c) > 127 for c in str(value)):
        raise SetupError(f"{label} に日本語などの非ASCII文字は使えません（日本語Windowsで読み込み失敗の恐れ）: {value!r}")


def _list(items) -> str:
    return "[" + ", ".join(_q(i) for i in items) + "]"


def render_config(spec: dict) -> str:
    """spec(JSON) から configuration.yml を生成する。トークンは書かない。"""
    url = validate_comfy_url(spec.get("comfy_url", "http://127.0.0.1:8188"))
    wfs = spec.get("workflows") or []
    if not wfs:
        raise SetupError("workflows が空です。")
    lines = [
        "# Generated by comfyui-discord-setup skill. ASCII only (cp932-safe).",
        "# The Discord token is NOT stored here. It is read from the DISCORD_TOKEN environment variable.",
        f"language: {_q(spec.get('language', 'ja'))}",
        "",
        "discord:",
        f"  token: {_q(TOKEN_PLACEHOLDER)}",
        "",
        "comfyui:",
        "  instances:",
        f"    - url: {_q(url)}",
        "      weight: 1",
        f"  input_dir: {_q(spec.get('input_dir', 'input'))}",
        f"  show_node_updates: {'true' if spec.get('show_node_updates', False) else 'false'}",
        "",
        "workflows:",
    ]
    seen_default = set()
    for w in wfs:
        for k in ("name", "type", "file", "prompt_node_id"):
            if not w.get(k):
                raise SetupError(f"ワークフロー設定に {k} がありません。")
        if w["type"] not in ("txt2img", "img2img", "upscale"):
            raise SetupError("type は txt2img / img2img / upscale のいずれかです（動画も txt2img）。")
        users, channels = w.get("allowed_users") or [], w.get("allowed_channels") or []
        if not users or not channels:
            raise SetupError(f"{w['name']}: allowed_users と allowed_channels は必須です（最小権限）。")
        for label, val in [("name", w["name"]), ("description", w.get("description", "")),
                           ("file", w["file"])] + [("allowed_users", u) for u in users] + \
                          [("allowed_channels", c) for c in channels]:
            _require_ascii(label, val)
        name = w["name"]
        if not name.replace("_", "").replace("-", "").isalnum():
            raise SetupError("ワークフロー名は英数字・_・- のみにしてください。")
        lines += [
            f"  {name}:",
            f"    type: {w['type']}",
            f"    description: {_q(w.get('description', name))}",
            f"    workflow: {_q('./workflows/' + os.path.basename(w['file']))}",
            f"    text_prompt_node_id: {_q(w['prompt_node_id'])}",
            f"    text_prompt_input_key: {_q(w.get('prompt_input_key', 'text'))}",
        ]
        if w.get("image_input_node_id"):
            lines.append(f"    image_input_node_id: {_q(w['image_input_node_id'])}")
        if w.get("default") and w["type"] not in seen_default:
            lines.append("    default: true")
            seen_default.add(w["type"])
        if w.get("seed_node_id"):
            key = w.get("seed_input_key", "seed")
            _require_ascii("seed_input_key", key)
            lines += [
                "    settings:",
                "      - name: __before",
                "        description: \"Randomize seed\"",
                "        code: |",
                "          def __before(workflowjson):",
                "              import random",
                f"              workflowjson[{_q(w['seed_node_id'])}][\"inputs\"][{_q(key)}] = random.randint(0, 2**32 - 1)",
            ]
        lines += [
            "    security:",
            "      enabled: true",
            f"      allowed_users: {_list(users)}",
            "      allowed_roles: []",
            f"      allowed_channels: {_list(channels)}",
        ]
    text = "\n".join(lines) + "\n"
    if any(ord(c) > 127 for c in text):
        raise SetupError("生成した設定に非ASCII文字が含まれています。")
    return text


def do_make_config(target: str, spec: dict, overwrite: bool) -> dict:
    repo = os.path.join(os.path.abspath(expand_path(target)), "ImageSmith")
    if not os.path.isfile(os.path.join(repo, "main.py")):
        raise SetupError("ImageSmith が見つかりません。先に install を実行してください。")
    text = render_config(spec)
    cfg_path = os.path.join(repo, "configuration.yml")
    result = {"config": anonymize_path(cfg_path), "backups": [], "copied_workflows": []}
    if os.path.exists(cfg_path):
        if not overwrite:
            raise SetupError("configuration.yml が既にあります。上書きするには --overwrite が必要です（自動でバックアップします）。")
        result["backups"].append(anonymize_path(backup(cfg_path)))
    wf_dir = os.path.join(repo, "workflows")
    os.makedirs(wf_dir, exist_ok=True)
    for w in spec["workflows"]:
        src = os.path.abspath(expand_path(w["file"]))
        dst = os.path.join(wf_dir, os.path.basename(w["file"]))
        if os.path.normcase(src) == os.path.normcase(dst):
            continue
        if not os.path.isfile(src):
            raise SetupError(f"ワークフローファイルが見つかりません: {w['file']}")
        if os.path.exists(dst):
            if not overwrite:
                raise SetupError(f"workflows に同名ファイルがあります: {os.path.basename(dst)}（--overwrite で上書き、バックアップあり）")
            result["backups"].append(anonymize_path(backup(dst)))
        shutil.copy2(src, dst)  # 元ファイルは変更しない（コピーのみ）
        result["copied_workflows"].append(os.path.basename(dst))
    with open(cfg_path, "w", encoding="ascii", newline="\n") as f:
        f.write(text)
    return result


# --- patch-security ----------------------------------------------------------

def security_patch_status(target: str) -> dict:
    path = os.path.join(os.path.abspath(expand_path(target)), "ImageSmith", "src", "core", "security.py")
    if not os.path.isfile(path):
        return {"state": "not_found", "path": anonymize_path(path)}
    with open(path, encoding="utf-8") as f:
        src = f.read()
    if SECURITY_BUG_NEW in src:
        return {"state": "already_patched", "path": anonymize_path(path)}
    if src.count(SECURITY_BUG_OLD) == 1:
        return {"state": "needs_patch", "path": anonymize_path(path), "_full": path}
    return {"state": "unknown_version", "path": anonymize_path(path)}


def do_patch_security(target: str) -> dict:
    st = security_patch_status(target)
    if st["state"] != "needs_patch":
        return st
    path = st.pop("_full")
    with open(path, encoding="utf-8") as f:
        src = f.read()
    bak = backup(path)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(src.replace(SECURITY_BUG_OLD, SECURITY_BUG_NEW, 1))
    return {"state": "patched", "path": st["path"], "backup": anonymize_path(bak)}


# --- start-command -------------------------------------------------------------

def start_command(target: str) -> list[str]:
    base = os.path.abspath(expand_path(target))
    return [
        "# 新しい PowerShell ウィンドウで実行（トークンは環境変数 DISCORD_TOKEN から読まれます）",
        f'cd "{os.path.join(base, "ImageSmith")}"',
        r'..\.venv\Scripts\python.exe main.py',
        "# 停止: このウィンドウで Ctrl+C",
    ]


def main(argv=None) -> int:
    setup_stdout()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("plan", "install", "make-config", "patch-security", "start-command"):
        p = sub.add_parser(name)
        p.add_argument("--dir", default=DEFAULT_DIR, help=f"導入先（既定: {DEFAULT_DIR}）")
        p.add_argument("--read-only", action="store_true", help="読み取り専用（変更系は実行しない）")
        if name in ("install", "make-config", "patch-security"):
            p.add_argument("--confirm", action="store_true", help="ユーザー承認済みのときだけ付ける")
        if name in ("plan", "install"):
            p.add_argument("--commit", default=PINNED_COMMIT)
        if name == "install":
            p.add_argument("--resume", action="store_true",
                           help="途中で止まった導入を続きから行う（Skill の処理記録と実際の状態が一致する場合だけ）")
        if name == "make-config":
            p.add_argument("--spec", required=True, help="設定内容のJSON（秘密情報は書かない）")
            p.add_argument("--overwrite", action="store_true", help="既存ファイルをバックアップしてから上書き")
    a = ap.parse_args(argv)
    try:
        if a.read_only and a.cmd in ("install", "make-config", "patch-security"):
            print("[未実行] 読み取り専用モード（--read-only）のため、変更を伴う処理は行いません。")
            return 4

        if a.cmd == "plan":
            ex = existing_installs_summary()
            rec = ex["recommendation"]
            if ex["installs"]:
                print("既存の ImageSmith:")
                for i in ex["installs"]:
                    print(f"  - {i['base_display']}（{'使える状態' if i['status'] == 'ready' else '要確認'}）")
            print("判断: " + rec["message"])
            if rec["action"] in ("reuse", "choose"):
                print("→ 新しい導入は勧めません。既存環境を使う場合は Phase 2 を「再利用」で完了にします。")
                print("  それでも別の場所に新しく入れたい場合だけ、下の内容を確認してください。")
            print("")
            info = inspect_target(a.dir)
            print(f"導入先: {info['dir_display']}（空き {info.get('free_gb')}GB）")
            for c in info["conflicts"]:
                print(f"[中止理由] {c}")
            for w in info.get("warnings", []):
                print(f"[注意] {w}")
            print("実行内容:")
            for s in install_steps(info["dir"], a.commit):
                print("  " + s)
            for line in state_summary(info["dir"]):
                print(line)
            print("判定: " + ("導入可能（承認後に install --confirm）" if info["ok"] else "このフォルダには導入できません"))
            return 0 if info["ok"] else 1

        if a.cmd == "install":
            validate_commit(a.commit)
            if not a.confirm:
                info = inspect_target(a.dir)
                if not a.resume:
                    for c in info["conflicts"]:
                        print(f"[中止理由] {c}")
                for line in state_summary(info["dir"]):
                    print(line)
                print("[未実行] --confirm がないため何もしていません。実行内容:")
                for s in install_steps(info["dir"], a.commit):
                    print("  " + s)
                if a.resume:
                    print("  （--resume：完了済みの工程は飛ばし、処理記録と実際の状態を照合してから続きを行います）")
                return 3
            try:
                r = do_install(a.dir, a.commit, resume=a.resume)
            except InstallFailed as e:
                for line in human_failure(e.report):
                    print(line)
                return 1
            print(f"[完了] ImageSmith {r['commit'][:12]} を {r['dir']} に導入（{r['python']}、{r['packages']}パッケージ）")
            return 0

        if a.cmd == "make-config":
            with open(a.spec, encoding="utf-8") as f:
                spec = json.load(f)
            text = render_config(spec)
            if not a.confirm:
                print("[未実行] --confirm がないため書き込んでいません。生成予定の内容:")
                print(text)
                return 3
            r = do_make_config(a.dir, spec, a.overwrite)
            print(f"[完了] {r['config']} を作成")
            for b in r["backups"]:
                print(f"  バックアップ: {b}")
            for c in r["copied_workflows"]:
                print(f"  ワークフローをコピー: {c}（元ファイルは変更していません）")
            return 0

        if a.cmd == "patch-security":
            st = security_patch_status(a.dir)
            st.pop("_full", None)
            msg = {"not_found": "security.py が見つかりません。", "already_patched": "修正済みです。",
                   "unknown_version": "想定と異なる版のため修正しません（追加調査が必要）。",
                   "needs_patch": "上流の既知バグがあります（settings個別の権限が効かない）。"}[st["state"]]
            print(f"{st['path']}: {msg}")
            if st["state"] != "needs_patch":
                return 0 if st["state"] == "already_patched" else 1
            print("変更内容: `if not result:` → `if not result.state:`（1行）。変更前に .bak を作成します。")
            if not a.confirm:
                print("[未実行] --confirm がないため変更していません。")
                return 3
            r = do_patch_security(a.dir)
            print(f"[完了] 修正しました。バックアップ: {r['backup']}")
            return 0

        for line in start_command(a.dir):
            print(line)
        return 0
    except (SetupError, UnsafeUrlError, OSError, ValueError) as e:
        print(redact(f"[エラー] {e}"))
        return 1


if __name__ == "__main__":
    sys.exit(main())
