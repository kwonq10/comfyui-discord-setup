"""セットアップ進捗の保存・再開（工程の進み具合だけを記録する）。

保存先（既定）: %LOCALAPPDATA%\\comfy-discord-skill\\setup_progress.json
  ※ Skillフォルダの外に置くので、Skillを他人に渡しても進捗は含まれない。

記録しないもの: トークン・APIキー・パスワード・認証URL・個人名・Discordのユーザー名/チャンネル名。
許可されたキーと値の形式以外は保存を拒否する。

使い方:
  python manage_progress.py show                       # 現在地と次にやること
  python manage_progress.py init
  python manage_progress.py complete environment_check
  python manage_progress.py pending discord_bot_configuration --status waiting_for_user
  python manage_progress.py fact comfyui_url http://127.0.0.1:8188
  python manage_progress.py reset --confirm            # 進捗を消す（バックアップあり）
  python manage_progress.py --read-only show           # 読み取り専用（作成・変更は一切しない）
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
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import UnsafeUrlError, anonymize_path, looks_secret, setup_stdout, validate_comfy_url  # noqa: E402

SCHEMA_VERSION = 1

# 工程ID → (Phase番号, 日本語名)。順番どおりに並べる
STEPS = {
    "environment_check": (1, "環境診断"),
    "comfyui_detected": (1, "ComfyUIの検出"),
    "prerequisites_ready": (1, "Python 3.12・Gitの準備"),
    "imagesmith_location_decided": (2, "ImageSmithの導入先を決定"),
    "imagesmith_installed": (2, "ImageSmithの導入"),
    "discord_app_created": (3, "Discordアプリの作成"),
    "discord_intents_configured": (3, "特権Intentの設定"),
    "discord_token_registered": (3, "トークンをPCに登録"),
    "discord_bot_invited": (3, "Botをサーバーに招待"),
    "discord_bot_configuration": (3, "許可ユーザー・チャンネルの決定"),
    "comfyui_api_checked": (4, "ComfyUI API接続確認"),
    "workflow_selected": (4, "ワークフローの選定・検査"),
    "imagesmith_configured": (4, "ImageSmith設定ファイル作成"),
    "bot_started": (5, "Botの起動確認"),
    "slash_commands_visible": (5, "スラッシュコマンドの表示確認"),
    "image_generation_tested": (5, "Discordから画像生成"),
    "mobile_network_tested": (6, "スマホ（モバイル回線）で確認"),
    "outside_tested": (6, "外出先で確認"),
    "video_limitations_explained": (7, "動画の制限事項の説明"),
    "video_generation_tested": (7, "Discordから動画生成"),
}
STATUSES = ("not_started", "in_progress", "waiting_for_user", "waiting_for_approval", "blocked", "completed")
# 記録してよい事実（値の形式も限定する）
FACT_RULES = {
    "comfyui_url": "url",
    "comfyui_version": r"^[0-9][0-9A-Za-z.\-+]{0,30}$",
    "comfyui_kind": r"^(comfy_desktop|legacy_desktop|portable|folder|unknown)$",
    "imagesmith_dir": "path",
    "imagesmith_commit": r"^[0-9a-f]{7,40}$",
    "python_version": r"^3\.\d{1,2}(\.\d{1,3})?$",
    "workflow_names": r"^[A-Za-z0-9_\-]{1,40}(,[A-Za-z0-9_\-]{1,40}){0,19}$",
    "gpu_vram_gb": r"^\d{1,3}(\.\d)?$",
    "ram_gb": r"^\d{1,4}(\.\d)?$",
    "security_patch": r"^(applied|declined|not_needed)$",
    "imagesmith_source": r"^(reused|new_install)$",   # 既存環境を再利用したか、新規導入か
    "discord_bot_source": r"^(reused|new)$",           # Phase 3: 既存Botを再利用したか、新しく作ったか
    # Phase 3 既存Botの確認結果（4分類のみ。名前・トークンは記録しない）
    **{f"bot_check_r{i}": r"^(screen_confirmed|record_only|unconfirmed|mismatch)$" for i in range(1, 8)},
}


class ProgressError(ValueError):
    pass


def default_path() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "comfy-discord-skill", "setup_progress.json")


def new_progress() -> dict:
    return {"schema_version": SCHEMA_VERSION, "current_phase": 1, "completed_steps": [],
            "pending_step": "environment_check", "status": "not_started", "facts": {},
            "updated_at": now()}


def now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def validate_fact(key: str, value: str) -> str:
    if key not in FACT_RULES:
        raise ProgressError(f"記録できない項目です: {key}（許可: {', '.join(FACT_RULES)}）")
    value = str(value).strip()
    if looks_secret(value):
        raise ProgressError("秘密情報らしき値は記録できません。")
    rule = FACT_RULES[key]
    if rule == "url":
        try:
            return validate_comfy_url(value, allow_lan=True)
        except UnsafeUrlError as e:
            raise ProgressError(str(e))
    if rule == "path":
        shown = anonymize_path(value)
        if "%USERPROFILE%" not in shown and re.search(r"(?i)\\users\\[^\\]+", shown):
            raise ProgressError("他ユーザーのフォルダを含むパスは記録できません。")
        if re.search(r"[?&=]|https?://", shown):
            raise ProgressError("パスにURLやクエリは含められません。")
        return shown
    if not re.match(rule, value):
        raise ProgressError(f"{key} の値の形式が正しくありません。")
    return value


def validate(p: dict) -> dict:
    """読み込んだ／保存する進捗を検査し、余計なキーを捨てる。"""
    if not isinstance(p, dict):
        raise ProgressError("進捗ファイルの形式が正しくありません。")
    out = new_progress()
    phase = p.get("current_phase", 1)
    if not isinstance(phase, int) or not 1 <= phase <= 7:
        raise ProgressError("current_phase は 1〜7 です。")
    out["current_phase"] = phase
    steps = p.get("completed_steps", [])
    if not isinstance(steps, list) or any(s not in STEPS for s in steps):
        raise ProgressError("completed_steps に未知の工程があります。")
    out["completed_steps"] = [s for s in STEPS if s in steps]  # 重複除去・順序統一
    pending = p.get("pending_step")
    if pending is not None and pending not in STEPS:
        raise ProgressError("pending_step が未知の工程です。")
    out["pending_step"] = pending
    status = p.get("status", "not_started")
    if status not in STATUSES:
        raise ProgressError(f"status は {', '.join(STATUSES)} のいずれかです。")
    out["status"] = status
    facts = p.get("facts", {}) or {}
    if not isinstance(facts, dict):
        raise ProgressError("facts の形式が正しくありません。")
    out["facts"] = {k: validate_fact(k, v) for k, v in facts.items()}
    out["updated_at"] = p.get("updated_at") or now()
    return out


def load(path: str) -> dict | None:
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return validate(json.load(f))


def save(path: str, p: dict) -> dict:
    p = validate(p)
    p["updated_at"] = now()
    text = json.dumps(p, ensure_ascii=False, indent=2)
    if looks_secret(text):  # 二重チェック
        raise ProgressError("保存内容に秘密情報らしき文字列があるため保存しません。")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)  # 途中で落ちても壊れたファイルを残さない
    return p


def next_step(p: dict) -> str | None:
    if p.get("pending_step") and p["pending_step"] not in p["completed_steps"]:
        return p["pending_step"]
    for s in STEPS:
        if s not in p["completed_steps"] and s not in ("outside_tested", "video_generation_tested"):
            return s
    return None


def complete(p: dict, step: str) -> dict:
    if step not in STEPS:
        raise ProgressError(f"未知の工程です: {step}")
    if step not in p["completed_steps"]:
        p["completed_steps"].append(step)
    nxt = None
    for s in STEPS:
        if s not in p["completed_steps"] and s not in ("outside_tested", "video_generation_tested"):
            nxt = s
            break
    p["pending_step"] = nxt
    p["current_phase"] = STEPS[nxt][0] if nxt else 7
    p["status"] = "in_progress" if nxt else "completed"
    return p


def summary(p: dict | None) -> list[str]:
    if p is None:
        return ["進捗の記録はまだありません（最初から始めます）。"]
    lines = [f"現在: Phase {p['current_phase']} / 状態 {p['status']}（最終更新 {p['updated_at']}）"]
    for s, (ph, label) in STEPS.items():
        mark = "済" if s in p["completed_steps"] else ("→" if s == p.get("pending_step") else "  ")
        lines.append(f"  [{mark}] Phase{ph} {label}")
    nxt = next_step(p)
    lines.append(f"次にやること: {STEPS[nxt][1] if nxt else 'なし（必須工程は完了）'}")
    if p["facts"]:
        lines.append("記録済みの情報: " + ", ".join(f"{k}={v}" for k, v in p["facts"].items()))
    return lines


def select_imagesmith(path: str, target_dir: str, read_only: bool, inspector=None) -> int:
    """既存の ImageSmith を「使う環境」として選ぶ。

    - 指定先だけを検査し、使える状態のときだけ選択を受け付ける（ほかの環境に切り替えない）。
    - read_only=True: 何も書かない。選択はこの会話の中だけで保持する（Claude Code が以降の
      コマンドに --target-dir として渡す）。次の会話には引き継がれない。
    - read_only=False: 進捗ファイルに imagesmith_dir / imagesmith_source=reused を記録し、
      Phase 2 の工程を完了にする（パスは %USERPROFILE% 形式。秘密情報・個人名は保存しない）。
    """
    if inspector is None:
        import detect_imagesmith as di
        inspector = lambda d: di.inspect_target(d, check_processes=False)  # noqa: E731
    res = inspector(target_dir)
    rec = res["recommendation"]
    if rec["action"] != "reuse":
        print("[未選択] " + rec["message"])
        return 1
    shown = anonymize_path(rec["target"])
    if read_only:
        print(f"選択: {shown} の ImageSmith を使います。")
        print("※ 読み取り専用モードのため、この選択はファイルに記録していません。"
              "この会話の中だけで有効です（次の会話には引き継がれません）。")
        print(f"   以降のコマンドには --target-dir \"{rec['target']}\" を付けます。")
        return 0
    try:
        p = load(path) or new_progress()
        p["facts"]["imagesmith_dir"] = validate_fact("imagesmith_dir", rec["target"])
        p["facts"]["imagesmith_source"] = "reused"
        for step in ("imagesmith_location_decided", "imagesmith_installed"):
            p = complete(p, step)
        commit = (res["installations"][0].get("git") or {}).get("commit")
        if commit:
            p["facts"]["imagesmith_commit"] = validate_fact("imagesmith_commit", commit)
        save(path, p)
    except (ProgressError, ValueError, OSError) as e:
        print(f"[エラー] {e}")
        return 1
    print(f"選択: {shown} の ImageSmith を使います（進捗ファイルに記録しました。再インストールはしません）。")
    return 0


def main(argv=None) -> int:
    setup_stdout()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", default=default_path())
    ap.add_argument("--read-only", action="store_true", help="読み取り専用（show / steps 以外は実行しない）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show").add_argument("--json", action="store_true")
    sub.add_parser("init")
    sub.add_parser("steps")
    c = sub.add_parser("complete"); c.add_argument("step")
    pe = sub.add_parser("pending"); pe.add_argument("step"); pe.add_argument("--status", default="waiting_for_user", choices=STATUSES)
    st = sub.add_parser("status"); st.add_argument("status", choices=STATUSES)
    f = sub.add_parser("fact"); f.add_argument("key"); f.add_argument("value")
    r = sub.add_parser("reset"); r.add_argument("--confirm", action="store_true")
    si = sub.add_parser("select-imagesmith", help="使う ImageSmith 環境を決める（読み取り専用では会話内のみ）")
    si.add_argument("dir")
    a = ap.parse_args(argv)
    if a.cmd == "select-imagesmith":
        return select_imagesmith(a.file, a.dir, a.read_only)
    if a.read_only and a.cmd not in ("show", "steps"):
        print("[未実行] 読み取り専用モード（--read-only）のため、進捗ファイルを作成・変更しません。")
        return 4
    try:
        if a.cmd == "steps":
            for s, (ph, label) in STEPS.items():
                print(f"Phase{ph} {s:30} {label}")
            return 0
        p = load(a.file)
        if a.cmd == "show":
            if a.json:
                print(json.dumps(p, ensure_ascii=False, indent=2))
            else:
                print("\n".join(summary(p)))
            return 0
        if a.cmd == "init":
            if p is not None:
                print("進捗は既にあります（上書きしません）。show で確認、やり直す場合は reset --confirm。")
                return 1
            save(a.file, new_progress())
            print(f"進捗ファイルを作成しました: {anonymize_path(a.file)}")
            return 0
        if a.cmd == "reset":
            if p is None:
                print("進捗はありません。")
                return 0
            if not a.confirm:
                print("[未実行] --confirm がないため消していません。")
                return 3
            bak = a.file + "." + dt.datetime.now().strftime("%Y%m%d-%H%M%S") + ".bak"
            shutil.copy2(a.file, bak)
            os.remove(a.file)
            print(f"進捗を消しました（バックアップ: {anonymize_path(bak)}）")
            return 0
        p = p or new_progress()
        if a.cmd == "complete":
            p = complete(p, a.step)
        elif a.cmd == "pending":
            if a.step not in STEPS:
                raise ProgressError(f"未知の工程です: {a.step}")
            p["pending_step"], p["status"], p["current_phase"] = a.step, a.status, STEPS[a.step][0]
        elif a.cmd == "status":
            p["status"] = a.status
        elif a.cmd == "fact":
            p["facts"][a.key] = validate_fact(a.key, a.value)
        p = save(a.file, p)
        print("\n".join(summary(p)))
        return 0
    except (ProgressError, ValueError, OSError) as e:
        print(f"[エラー] {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
