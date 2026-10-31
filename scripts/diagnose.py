"""エラー診断（読み取りのみ・設定は一切変更しない）。

使い方:
  python diagnose.py --list                         # 対応している症状の一覧
  python diagnose.py --symptom comfyui_unreachable  # 症状ごとの確認手順
  python diagnose.py --log bot_log.txt              # ログから既知のエラーを探す（該当行だけをマスクして表示）
  python diagnose.py --log bot_log.txt --symptom video_reply_failed

ログは外部に送信しない。表示する行はトークン等をマスクし、最大数行に絞る。
原因が特定できない場合は「追加調査が必要」と表示し、推測で設定を変えない。
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True  # Skill フォルダに __pycache__ を作らない

import argparse
import os
import re

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import emit, redact, setup_stdout  # noqa: E402

# 既知のログパターン → 原因候補と次の行動。confidence: high=ほぼ確定 / medium=有力 / low=可能性
PATTERNS = [
    {"id": "login_failure", "symptoms": ["bot_offline"], "confidence": "high",
     "regex": r"LoginFailure|Improper token has been passed|401 Unauthorized.*(?:gateway|users/@me)",
     "cause": "Botトークンが正しくない、または再発行されて古くなっています。",
     "action": "Developer Portal の Bot ページで Reset Token し、新しいトークンを自分のPCで環境変数 DISCORD_TOKEN に登録し直す（チャットに貼らない）。"},
    {"id": "token_missing", "symptoms": ["bot_offline"], "confidence": "high",
     "regex": r"KeyError: 'discord'|KeyError: 'token'|DISCORD_TOKEN が設定されていません",
     "cause": "環境変数 DISCORD_TOKEN が、Botを起動したウィンドウから見えていません。",
     "action": "登録後に新しく開いた PowerShell から起動する（登録前に開いた画面には反映されない）。"},
    {"id": "privileged_intents", "symptoms": ["bot_offline"], "confidence": "high",
     # discord.py は PrivilegedIntentsRequired として報告する。Discord 公式の Gateway 切断コードは
     # 4014「Disallowed intent(s)」（確認日 2026-10-29）
     "regex": r"PrivilegedIntentsRequired|privileged intents|Disallowed intents?|\b4014\b",
     "cause": "Developer Portal で Server Members Intent / Message Content Intent が OFF の可能性があります"
              "（ImageSmithは両方を要求。OFF だと Discord が接続をコード 4014「Disallowed intent(s)」で切断します）。",
     "action": "Developer Portal の Bot ページで Privileged Gateway Intents を確認する。ON にするのは設定変更なので、"
               "ユーザーが内容を理解し承認したうえで、本人が Save Changes を押す。その後 Bot を再起動。"},
    {"id": "comfy_connect", "symptoms": ["comfyui_unreachable", "generation_not_starting"], "confidence": "high",
     "regex": r"Cannot connect to host 127\.0\.0\.1|Cannot connect to host localhost|ClientConnectorError|Connection refused|接続できません",
     "cause": "ComfyUI が起動していないか、ポート番号が設定と違います。",
     "action": "ComfyUI を起動 → check_connection.py api で接続先を確認 → configuration.yml の url と一致させる。"},
    {"id": "missing_access", "symptoms": ["commands_missing", "generation_not_starting"], "confidence": "medium",
     "regex": r"Missing Access|50001|Missing Permissions|50013|Forbidden",
     "cause": "Botにそのチャンネルの閲覧・送信・ファイル添付の権限がありません。",
     "action": "サーバー設定 → チャンネルの権限で、Botに「チャンネルを見る」「メッセージを送信」「埋め込みリンク」「ファイルを添付」を許可。"},
    {"id": "unknown_interaction", "symptoms": ["generation_not_starting"], "confidence": "medium",
     "regex": r"Unknown interaction|10062",
     "cause": "Botが3秒以内に応答できず、Discordがコマンドを無効にしました（PCが重い・起動直後など）。",
     "action": "もう一度コマンドを実行。頻発する場合はPCの負荷（生成中の重い処理）を確認。"},
    {"id": "interaction_expired", "symptoms": ["video_reply_failed"], "confidence": "medium",
     "regex": r"Invalid Webhook Token|50027",
     "cause": "Discordの応答用トークン（Interaction Token）の期限（15分）が切れた可能性があります。生成自体は成功していることがあります。",
     "action": "ComfyUI の出力フォルダに生成物があるか確認。短い動画・軽い設定にして15分以内に終わるようにする。自動修正は行わない（未検証）。"},
    {"id": "unauthorized_after_long_job", "symptoms": ["video_reply_failed"], "confidence": "low",
     "regex": r"401 Unauthorized",
     "cause": "応答の編集が拒否されました。長時間生成後なら Interaction Token の期限切れの可能性がありますが、401だけでは確定できません。",
     "action": "/forge 実行時刻と失敗時刻の差が15分を超えているか確認する。原因の確定には追加調査が必要。"},
    {"id": "file_too_large", "symptoms": ["video_reply_failed"], "confidence": "high",
     "regex": r"Request entity too large|40005|413 Payload Too Large",
     "cause": "生成物のファイルサイズがDiscordの添付上限を超えています。",
     "action": "解像度・長さを下げる。原本はComfyUIの出力フォルダから取り出す。"},
    {"id": "cuda_oom", "symptoms": ["vram"], "confidence": "high",
     "regex": r"CUDA out of memory|OutOfMemoryError|torch\.cuda\.OutOfMemory|Allocation on device",
     "cause": "VRAM（GPUメモリ）が足りません。",
     "action": "解像度・フレーム数を下げる、軽量（量子化）モデルを使う、他のGPUを使うアプリを閉じる。"},
    {"id": "ram_oom", "symptoms": ["ram"], "confidence": "medium",
     "regex": r"MemoryError|paging file is too small|ページング ファイル|WinError 1455|Unable to allocate",
     "cause": "RAM（とページファイル）が足りません。",
     "action": "Chrome等の常駐アプリを閉じる。ページファイル設定の変更はWindows設定の変更になるため、必要なら別途承認を得て案内する。"},
    {"id": "model_missing", "symptoms": ["model_missing", "generation_not_starting"], "confidence": "high",
     "regex": r"Value not in list|not in \[|FileNotFoundError.*\.(?:safetensors|ckpt|gguf)",
     "cause": "ワークフローが参照するモデルファイルが ComfyUI にありません（ファイル名・フォルダ違い）。",
     "action": "check_connection.py workflow --url で見つからないモデルを確認。モデルの入手は別途ユーザー承認が必要。"},
    {"id": "missing_node", "symptoms": ["workflow_not_found", "generation_not_starting"], "confidence": "high",
     "regex": r"missing_node_type|Cannot execute because a node is missing|node .* does not exist|class_type.*not found",
     "cause": "ワークフローに、ComfyUI に入っていないカスタムノードが含まれています。",
     "action": "check_connection.py workflow --url で不足ノードを確認。カスタムノード導入は別途ユーザー承認が必要。"},
    {"id": "prompt_invalid", "symptoms": ["generation_not_starting", "workflow_not_found"], "confidence": "medium",
     "regex": r"prompt_outputs_failed_validation|invalid_prompt|Prompt outputs failed validation|400 Bad Request",
     "cause": "ComfyUI がワークフローを受け付けませんでした（UI形式のJSON・ノードID違い・入力値の不正など）。",
     "action": "API形式で書き出したJSONか、check_connection.py workflow で確認。"},
    {"id": "workflow_file", "symptoms": ["workflow_not_found"], "confidence": "high",
     "regex": r"FileNotFoundError.*workflows|No such file or directory.*\.json|workflow_not_found",
     "cause": "configuration.yml に書いたワークフローJSONのパスにファイルがありません。",
     "action": "check_connection.py config で workflow のパスを確認。"},
    {"id": "config_encoding", "symptoms": ["bot_offline"], "confidence": "high",
     "regex": r"UnicodeDecodeError|'cp932' codec|yaml\.reader\.ReaderError",
     "cause": "configuration.yml の日本語などが、日本語Windowsの既定文字コードで読めません。",
     "action": "設定ファイルを英数字だけにする（このSkillの make-config は英数字のみで生成）。"},
    {"id": "yaml_syntax", "symptoms": ["bot_offline"], "confidence": "high",
     "regex": r"yaml\.(?:scanner|parser)\.\w+Error|mapping values are not allowed|could not find expected ':'",
     "cause": "configuration.yml の書式（インデント・コロン）が崩れています。",
     "action": "check_connection.py config で検査。バックアップ（*.bak）から戻すこともできる。"},
    {"id": "security_denied", "symptoms": ["generation_not_starting"], "confidence": "medium",
     "regex": r"not allowed|permission denied for workflow|You don't have permission|権限がありません",
     "cause": "ワークフローの security 設定で、あなたのユーザー名またはチャンネル名が許可されていません。",
     "action": "Discordのユーザー名（表示名ではない）とチャンネル名が allowed_users / allowed_channels と一致しているか確認。"},
]

SUCCESS_MARKERS = {
    "registered": re.compile(r"Registered (\d+) Discord commands"),
    "ready": re.compile(r"Bot is ready!"),
    "comfy_connected": re.compile(r"Connected to ComfyUI|Connected to \d+ ComfyUI"),
    "logged_in": re.compile(r"Logged in as"),
}

SYMPTOMS = {
    "comfyui_unreachable": {
        "title": "ComfyUIに接続できない",
        "checks": ["ComfyUI（Desktop等）が起動しているか", "python detect_comfyui.py で起動中のポートを確認",
                   "python check_connection.py api --url <検出したURL>", "configuration.yml の comfyui.instances.url が同じか"],
    },
    "bot_offline": {
        "title": "Discord Botがオンラインにならない",
        "checks": ["Bot起動画面のログに Logged in as / Bot is ready! が出ているか",
                   "環境変数 DISCORD_TOKEN が登録済みか（check_environment.py で有無のみ確認）",
                   "Developer Portal で Server Members Intent と Message Content Intent が ON か",
                   "Botをサーバーに招待済みか", "ログを --log で診断"],
    },
    "commands_missing": {
        "title": "スラッシュコマンドが表示されない",
        "checks": ["ログに Registered N Discord commands が出ているか（N=4以上）",
                   "招待URLのスコープに applications.commands が入っていたか",
                   "Discordアプリを再起動（スマホは完全終了→再起動）。反映に少し時間がかかることがある",
                   "サーバー設定 → 連携サービス でBotのコマンドが制限されていないか"],
    },
    "workflow_not_found": {
        "title": "ワークフローを検出できない",
        "checks": ["/workflows に表示されるか", "check_connection.py config で workflow パスとAPI形式を確認",
                   "selectable: false になっていないか"],
    },
    "model_missing": {
        "title": "モデルが見つからない",
        "checks": ["check_connection.py workflow --file <json> --url <ComfyUI> で参照モデルを確認",
                   "ComfyUI の画面で同じワークフローを開き、モデル選択欄に赤い表示がないか"],
    },
    "generation_not_starting": {
        "title": "画像生成が始まらない",
        "checks": ["Discordにエラー表示が出ているか（権限なし・ワークフローなし）",
                   "check_connection.py api で待ち行列（実行中・待機）を確認", "ComfyUI側のコンソールに赤いエラーがないか",
                   "Botのログを --log で診断"],
    },
    "vram": {"title": "VRAM不足", "checks": ["ComfyUIログに CUDA out of memory があるか", "check_environment.py でVRAM使用量",
                                            "解像度・フレーム数・モデルの大きさを下げる"]},
    "ram": {"title": "RAM不足", "checks": ["check_environment.py で RAM空き・RAM+ページファイル空き",
                                          "常駐アプリを閉じる", "動画は特にメモリを使う（実機ではRAM16GBで余裕ほぼゼロ）"]},
    "video_reply_failed": {
        "title": "動画生成後にDiscordへ返信できない",
        "checks": ["ComfyUI の出力フォルダに動画ができているか（できていれば生成は成功）",
                   "/forge 実行から失敗までが15分を超えていないか（Interaction Tokenの期限）",
                   "ファイルサイズがDiscordの添付上限を超えていないか", "Botログを --log で診断（Invalid Webhook Token / 50027 / 401 / 40005）"],
        "known_limitation": "長時間の生成ではDiscordの応答用トークン（15分）が切れて、生成に成功しても返信できないことがあります。"
                            "ImageSmith標準ではこの対策はありません。現段階では未検証の修正を自動適用しません。",
    },
}


def scan_log(text: str, symptom: str | None = None, max_lines: int = 3) -> dict:
    lines = text.splitlines()
    findings = []
    for p in PATTERNS:
        if symptom and symptom not in p["symptoms"]:
            continue
        rx = re.compile(p["regex"], re.IGNORECASE)
        hits = [(i + 1, redact(l.strip())[:300]) for i, l in enumerate(lines) if rx.search(l)]
        if hits:
            findings.append({"id": p["id"], "confidence": p["confidence"], "cause": p["cause"],
                             "action": p["action"], "count": len(hits), "examples": hits[:max_lines]})
    # 401 は期限切れの専用パターンと重複するので、より強い根拠があれば弱い方を外す
    ids = {f["id"] for f in findings}
    if "login_failure" in ids or "interaction_expired" in ids:
        findings = [f for f in findings if f["id"] != "unauthorized_after_long_job"]
    markers = {}
    for k, rx in SUCCESS_MARKERS.items():
        m = None
        for l in lines:
            m = rx.search(l) or m
        markers[k] = (m.group(1) if m and m.groups() else bool(m))
    order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: order[f["confidence"]])
    return {"findings": findings, "markers": markers,
            "conclusion": "known" if findings else "needs_investigation"}


def human(result: dict | None, symptom: str | None) -> list[str]:
    out = []
    if symptom:
        s = SYMPTOMS[symptom]
        out.append(f"=== 症状: {s['title']} ===")
        out.append("確認する順番:")
        out += [f"  {i}. {c}" for i, c in enumerate(s["checks"], 1)]
        if s.get("known_limitation"):
            out.append(f"[既知の制限] {s['known_limitation']}")
        out.append("")
    if result is not None:
        m = result["markers"]
        out.append("=== ログ診断（該当行のみ・秘密情報はマスク） ===")
        out.append(f"起動の目印: ComfyUI接続 {'あり' if m['comfy_connected'] else 'なし'} / "
                   f"ログイン {'あり' if m['logged_in'] else 'なし'} / "
                   f"コマンド登録 {m['registered'] or 'なし'} / 準備完了 {'あり' if m['ready'] else 'なし'}")
        label = {"high": "原因（ほぼ確定）", "medium": "原因（有力）", "low": "原因の可能性"}
        for f in result["findings"]:
            out.append(f"- {label[f['confidence']]}: {f['cause']}（{f['count']}件）")
            out.append(f"  対処: {f['action']}")
            for ln, txt in f["examples"]:
                out.append(f"  {ln}行目: {txt}")
        if not result["findings"]:
            out.append("既知のエラーは見つかりませんでした。原因を推測で決めず、追加調査が必要です"
                       "（ComfyUI側のログ、エラー発生時刻前後の行、Discordの画面表示を確認）。")
    return out


def main(argv=None) -> int:
    setup_stdout()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symptom", choices=sorted(SYMPTOMS))
    ap.add_argument("--log", help="診断するログファイル（UTF-8 / cp932 どちらも可）")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--read-only", action="store_true", help="読み取り専用（このスクリプトは常に読み取り専用）")
    a = ap.parse_args(argv)
    if a.list or (not a.symptom and not a.log):
        for k, v in SYMPTOMS.items():
            print(f"{k:24} {v['title']}")
        return 0
    result = None
    if a.log:
        with open(a.log, "rb") as f:
            raw = f.read()
        # PowerShell 5.1 の Tee-Object / > は UTF-16 LE（BOM付き）で保存する
        encs = ("utf-16",) if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8-sig", "cp932")
        for enc in encs:
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            text = raw.decode("utf-8", "replace")
        result = scan_log(text, a.symptom)
    data = {"symptom": a.symptom, "guide": SYMPTOMS.get(a.symptom), "log": result}
    emit(data, a.json, human(result, a.symptom))
    return 0


if __name__ == "__main__":
    sys.exit(main())
