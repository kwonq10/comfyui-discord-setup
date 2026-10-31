# トラブルシューティング

> Claude Code への指示：
> 1. まず `diagnose.py --symptom <症状>` で確認順を出し、**読み取りの確認だけ** を行う。
> 2. ログは関連する部分だけを見る（`diagnose.py --log <ファイル>` は該当行だけをマスクして表示）。ログ全文をチャットに貼ってもらわない。
> 3. 原因が特定できないときは「追加調査が必要」と伝える。**推測で設定を変更しない。**
> 4. 修正に設定変更が要る場合は、変更内容・影響・戻し方を説明して承認を得る。変更前にバックアップする。

```
python "${CLAUDE_SKILL_DIR}/scripts/diagnose.py" --list
python "${CLAUDE_SKILL_DIR}/scripts/diagnose.py" --symptom bot_offline
python "${CLAUDE_SKILL_DIR}/scripts/diagnose.py" --log "<保存したログ>"
```

Bot のログをファイルに残す方法（ユーザーに案内。**この保存方法自体は実機未検証**）：Bot 起動コマンドの2行目を
`$env:PYTHONUNBUFFERED='1'; ..\.venv\Scripts\python.exe main.py 2>&1 | Tee-Object -FilePath ..\bot_log.txt` にして起動する。
画面にもログが出つつ、`bot_log.txt`（UTF-16）に保存される。diagnose.py は UTF-16 / UTF-8 / cp932 を読める。
ImageSmith のログにトークンは出ない作りだが、念のため diagnose.py はマスクして表示する。
ログが折り返されて1行が分割されることがあるため、該当なしの場合は画面の表示も確認する。

## 症状別

| 症状（`--symptom`） | よくある原因 | 確認（読み取り） | 対処 |
|---|---|---|---|
| ComfyUIに接続できない `comfyui_unreachable` | ComfyUI未起動／ポート違い | `detect_comfyui.py`、`check_connection.py api` | ComfyUI起動、設定のURLを検出結果に合わせる（要承認） |
| Botがオンラインにならない `bot_offline` | トークン誤り・古い／DISCORD_TOKEN未反映／特権Intent OFF（切断コード `4014`「Disallowed intent(s)」、discord.py では `PrivilegedIntentsRequired`）／設定ファイルの文字コード | `check_environment.py`（有無のみ）、`diagnose.py --log`、Developer Portal の Intents を見る | 新しいPowerShellで起動。Intent を ON にする・トークンを再発行するのは **設定変更なので、理由と影響を説明し承認を得てから別工程で**（既存Botでは Reset Token を自動で勧めない） |
| スラッシュコマンドが出ない `commands_missing` | Bot が起動していない／コマンド登録の反映待ち／アプリ未再起動／（ごく古い招待方法で `bot` を付けずに招待した場合） | ログの `Registered N Discord commands`、R1（メンバー一覧に Bot がいるか） | Discord アプリを再起動。公式では `applications.commands` は `bot` スコープに既定で含まれる（確認日 2026-10-29）ため、`bot` で招待済みなら招待し直しは通常不要 |
| ワークフローを検出できない `workflow_not_found` | パス違い／UI形式JSON／`selectable: false` | `check_connection.py config` | API形式で再書き出し、パス修正（要承認） |
| モデルが見つからない `model_missing` | ファイル名・フォルダ違い／未導入 | `check_connection.py workflow --url` | ComfyUI画面でモデルを選び直して再書き出し。モデル導入は自動化しない |
| 画像生成が始まらない `generation_not_starting` | 権限設定（ユーザー名・チャンネル名不一致）／ComfyUIのエラー／待ち行列 | Discordの表示、`check_connection.py api`（待ち行列） | 表示名ではなくユーザー名を設定 |
| VRAM不足 `vram` | 解像度・モデルが大きい | ComfyUIログの `CUDA out of memory` | 解像度・フレーム数を下げる、他のGPUアプリを閉じる |
| RAM不足 `ram` | 常駐アプリ／大型モデル | `check_environment.py` の RAM・RAM+ページファイル空き | 常駐アプリを閉じる。ページファイル変更は要承認・再起動あり |
| /reforge・/upscale が失敗する（試験的な機能。症状名なし） | 設定の `image_input_node_id` が LoadImage ノードでない／ワークフローが ComfyUI で動かない／ComfyUI が古く `/api/upload/image` が無い | ログの `Failed to process input image`・`Image upload failed`、ComfyUI の input フォルダに `image*` が届いているか、`check_connection.py workflow` | ノードIDを直す（要承認）。`input_dir` は原因ではない（画像は API で渡されるため変更しない。workflow_setup.md 6章） |
| 動画後にDiscordへ返信できない `video_reply_failed` | Interaction Token（公式：15分有効、確認日 2026-10-29）切れ／添付サイズ上限 | ComfyUI出力フォルダに動画があるか、`diagnose.py --log` | 短く・軽くする。原本は出力フォルダから。**未検証の修正は自動適用しない** |

## 既存検証環境で実際に起きたこと（参考）

- 特権 Intent：ImageSmith は Server Members と Message Content の2つが必要（Presence は不要）。OFF のまま起動すると Discord が `4014`（Disallowed intent(s)）で切断する（公式 Gateway、確認日 2026-10-29）。
- settings 単位の権限判定バグ：上流 `security.py` の1行（[workflow_setup.md](workflow_setup.md) 4章）。
- `settings` の連結はセミコロンの後に空白を入れない（例：`a(5);b`）。
- 同じ Bot を2か所（別フォルダ・別PC）で同時に起動すると、Discord に二重で応答する恐れがある。
- 外付けドライブに導入した場合、ドライブ未接続・ドライブ文字変更で起動できない。
- RAM 16GB で動画生成：成功はしたが、Windows のページファイル自動拡張に依存していた（余裕ほぼゼロ）。
- 長時間ジョブ後に `401` / `50027` / `Invalid Webhook Token` のログを観測。15分超過が唯一の原因とは未確定。

## 元に戻す方法

| 変更 | 戻し方 |
|---|---|
| configuration.yml 作成・上書き | `configuration.yml.<日時>.bak` の名前を `configuration.yml` に戻す |
| workflows へのコピー | コピー先のファイルを削除（元ファイルは変更していない）。上書き時は `.bak` を戻す |
| security.py の1行修正 | `security.py.<日時>.bak` を戻す、または `git checkout -- src/core/security.py` |
| ImageSmith 導入 | 導入先フォルダごと削除（ユーザーが自分で。既存環境と別フォルダなので他に影響しない） |
| ImageSmith 導入の途中失敗 | 自動削除はしない。表示の「次にすること」に従い、原因を解消して `install --resume --confirm`（処理記録と実際の状態が一致する場合だけ続きから）。途中のフォルダの削除を求められたら、ユーザーがエクスプローラーで削除。やり直したい場合は導入先フォルダごと削除して新規に導入 |
| DISCORD_TOKEN 登録 | 「環境変数を編集」から `DISCORD_TOKEN` を削除。トークン自体は Developer Portal で Reset Token すると古いものが無効になる |
| 進捗ファイル | `manage_progress.py reset --confirm`（バックアップあり） |
