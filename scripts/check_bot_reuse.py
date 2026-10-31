"""Phase 3（既存 Bot の再利用）の確認を支援する。Discord の設定は一切変更しない。

サブコマンド:
  checklist       最初の注意と R1〜R7 の一覧を表示（変更なし）
  summary         確認結果を「画面で確認済み／記録のみ／未確認／不一致」で一覧にする
                  例: summary --r1 screen_confirmed --r2 record_only
  token-presence  R6: DISCORD_TOKEN が登録されているかだけを表示（値は表示しない）
                  通常モードで、ユーザーが承認したときだけ --consent-token-presence を付けて実行
                  --read-only では実行しない（終了コード 4）。同意フラグなしでも実行しない（終了コード 5）
  access-limits   R7: ImageSmith のワークフローごとの利用者・チャンネル制限だけを表示
                  設定ファイルを開くことにユーザーが同意したときだけ --consent-read-config を付ける
                  （pyyaml が必要なため ImageSmith の venv の python で実行）

このスクリプトは Discord に接続しない。Reset Token・設定変更は案内も実行もしない。
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True  # Skill フォルダに __pycache__ を作らない

import argparse  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import looks_secret, setup_stdout  # noqa: E402

BANNER = "今回は既存Botの確認だけを行います。設定を変更する必要はありません。Reset TokenやSave Changesは押さないでください。"

ITEMS = {
    "r1": "Botがサーバーのメンバー一覧にいる",
    "r2": "Developer Portal の Bot ページの Intents（Server Members・Message Content が ON）",
    "r3": "Public Bot の設定（OFF が推奨）",
    "r4": "Installation ページの Install Link（None が推奨）",
    "r5": "Bot のチャンネル権限",
    "r6": "トークンの登録の有無（値は見ない）",
    "r7": "ImageSmith の利用者・チャンネル制限",
}
STATUS_LABELS = {
    "screen_confirmed": "画面で確認済み",
    "record_only": "記録のみ",
    "unconfirmed": "未確認",
    "mismatch": "不一致",
}
CONSENT_TOKEN = "--consent-token-presence"
CONSENT_CONFIG = "--consent-read-config"
SAFE_KEY = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_CONFIG_BYTES = 1024 * 1024


# --- checklist / summary ------------------------------------------------------

def checklist_lines() -> list[str]:
    lines = [f"【注意】{BANNER}", "", "確認する項目（1つずつ案内します）:"]
    for k, v in ITEMS.items():
        lines.append(f"  {k.upper()}: {v}")
    lines.append("")
    lines.append("R1〜R5 は画面を見るだけです。R6・R7 はあなたの同意があるときだけ確認します。")
    return lines


def summarize(results: dict) -> dict:
    out = {}
    for k in ITEMS:
        v = results.get(k) or "unconfirmed"
        if v not in STATUS_LABELS:
            raise ValueError(f"{k}: 分類は {', '.join(STATUS_LABELS)} のいずれかです")
        out[k] = v
    mismatches = [k for k, v in out.items() if v == "mismatch"]
    screen_ok = all(out[k] == "screen_confirmed" for k in ("r1", "r2", "r3", "r4", "r5"))
    if mismatches:
        verdict = ("不一致があります（" + "、".join(k.upper() for k in mismatches) + "）。ここでは直しません。"
                   "内容と影響を説明し、次へ進むか・別工程で直すかを決めます。")
    elif screen_ok:
        verdict = "既存のDiscord Botが利用できるため、新規作成は不要です。必要な設定を確認したので次の工程へ進めます。"
    else:
        pending = [k.upper() for k in ("r1", "r2", "r3", "r4", "r5") if out[k] != "screen_confirmed"]
        verdict = ("既存のDiscord Botは再利用できる見込みですが、まだ画面で確認できていない項目があります（"
                   + "、".join(pending) + "）。記録だけで「確認済み」にはしません。")
    return {"results": out, "mismatches": mismatches, "ready": screen_ok and not mismatches, "verdict": verdict}


def summary_lines(s: dict) -> list[str]:
    lines = ["=== 既存Botの確認結果 ==="]
    for k, v in s["results"].items():
        lines.append(f"  {k.upper()} {ITEMS[k]}: {STATUS_LABELS[v]}")
    lines.append("")
    lines.append("判断: " + s["verdict"])
    return lines


# --- R6 token presence ----------------------------------------------------------

def token_presence() -> dict:
    """登録の有無だけ。check_environment の同じ関数を使い、値は呼び出し元に返さない。"""
    import check_environment as ce
    r = ce.discord_token_registered()
    return {"in_user_environment": r.get("in_user_environment"), "in_this_process": r.get("in_this_process")}


# --- R7 access limits -------------------------------------------------------------

class UnsafeConfig(Exception):
    pass


def _safe_channel(name) -> str | None:
    s = str(name)
    if not s or len(s) > 100 or any(c.isspace() for c in s) or looks_secret(s):
        return None
    return s


def access_limits(cfg, expect_user: str | None = None) -> dict:
    if not isinstance(cfg, dict) or not isinstance(cfg.get("workflows"), dict):
        raise UnsafeConfig("workflows が見つかりません")
    rows = []
    for name, w in cfg["workflows"].items():
        if not SAFE_KEY.match(str(name)) or looks_secret(str(name)):
            raise UnsafeConfig("表示できない名前のワークフローがあります")
        sec = (w or {}).get("security") if isinstance(w, dict) else None
        sec = sec if isinstance(sec, dict) else {}
        users = sec.get("allowed_users") or []
        roles = sec.get("allowed_roles") or []
        channels = sec.get("allowed_channels") or []
        row = {"workflow": str(name), "enabled": bool(sec.get("enabled")),
               "allowed_users_count": len(users) if isinstance(users, list) else None,
               "allowed_roles_count": len(roles) if isinstance(roles, list) else None,
               "allowed_channels": [(_safe_channel(c) or "（表示できない名前）") for c in channels]
               if isinstance(channels, list) else None}
        if expect_user is not None and isinstance(users, list):
            row["expected_user_allowed"] = expect_user in [str(u) for u in users]
        rows.append(row)
    return {"workflows": rows}


def access_limit_lines(r: dict, expect_user: bool) -> list[str]:
    lines = ["=== ImageSmith の利用者・チャンネル制限（名前・トークンは表示しません） ==="]
    for w in r["workflows"]:
        if not w["enabled"]:
            lines.append(f"- {w['workflow']}: 制限なし（サーバーの全員が使えます）")
            continue
        ch = "、".join(w["allowed_channels"]) if w["allowed_channels"] else "指定なし（どのチャンネルでも）"
        users = w["allowed_users_count"]
        lines.append(f"- {w['workflow']}: 制限あり / 許可ユーザー {users}人 / 許可チャンネル {ch}")
        if expect_user and "expected_user_allowed" in w:
            lines.append("    入力したユーザー名: " + ("含まれています" if w["expected_user_allowed"] else "含まれていません"))
    return lines


# --- main -----------------------------------------------------------------------

def main(argv=None) -> int:
    setup_stdout()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("checklist", "summary", "token-presence", "access-limits"):
        p = sub.add_parser(name)
        p.add_argument("--read-only", action="store_true")
        p.add_argument("--json", action="store_true")
        if name == "summary":
            for k in ITEMS:
                p.add_argument(f"--{k}", choices=list(STATUS_LABELS))
        if name == "token-presence":
            p.add_argument(CONSENT_TOKEN, dest="consent", action="store_true")
        if name == "access-limits":
            p.add_argument("--file", required=True)
            p.add_argument(CONSENT_CONFIG, dest="consent", action="store_true")
            p.add_argument("--expect-user")
    a = ap.parse_args(argv)

    if a.cmd == "checklist":
        print("\n".join(checklist_lines()))
        return 0

    if a.cmd == "summary":
        s = summarize({k: getattr(a, k) for k in ITEMS})
        print(json.dumps(s, ensure_ascii=False) if a.json else "\n".join(summary_lines(s)))
        return 0

    if a.cmd == "token-presence":
        if a.read_only:
            print("[未実行] 読み取り専用モードではトークンの登録有無も確認しません（R6 は「未確認」）。")
            return 4
        if not a.consent:
            print("[未実行] トークンの登録有無を確認するには、ユーザーの承認が必要です。承認後に "
                  + CONSENT_TOKEN + " を付けて実行してください（値は表示しません）。")
            return 5
        r = token_presence()
        reg = r["in_user_environment"]
        print("DISCORD_TOKEN の登録: " + ("登録あり" if reg else "未登録" if reg is False else "確認できず")
              + "（値・長さ・先頭の文字は表示しません）")
        if reg is False:
            print("※ 未登録でも、ここでは Reset Token を案内しません。以前のPCや別のユーザーで登録していないか、まず確認します。")
        return 0

    # access-limits
    if not a.consent:
        print("[未実行] 設定ファイル（configuration.yml）にはトークンなどが含まれる可能性があるため、"
              "ユーザーの明示的な同意が必要です。同意後に " + CONSENT_CONFIG + " を付けて実行してください。")
        return 5
    try:
        import yaml
    except ImportError:
        print("[未実行] pyyaml がありません。ImageSmith の仮想環境の python で実行してください。")
        return 2
    try:
        with open(a.file, "rb") as f:   # 読み取りのみ
            raw = f.read(MAX_CONFIG_BYTES + 1)
        if len(raw) > MAX_CONFIG_BYTES:
            raise UnsafeConfig("ファイルが大きすぎます")
        cfg = yaml.safe_load(raw.decode("utf-8-sig"))
        r = access_limits(cfg, a.expect_user)
    except UnsafeConfig as e:
        print(f"[中止] 安全に確認できませんでした（{e}）。設定ファイルの中身は表示していません。")
        return 1
    except Exception:  # noqa: BLE001 — 例外メッセージに設定の中身が入り得るので出さない
        print("[中止] 設定ファイルを読み込めませんでした。設定ファイルの中身は表示していません。")
        return 1
    print(json.dumps(r, ensure_ascii=False) if a.json else "\n".join(access_limit_lines(r, a.expect_user is not None)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
