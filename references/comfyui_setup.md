# Phase 1・4：ComfyUI の確認と接続

> Claude Code への指示：このファイルの検査は **読み取りのみ**。ComfyUI の設定・起動引数・モデル・カスタムノードは変更しない。
> 変更が必要と判断したら「変更内容・影響・元に戻す方法」を説明し、ユーザーの承認を得てから、ユーザー自身に操作してもらう。

## 1. 既存の ComfyUI を探す（Phase 1）

```
python "${CLAUDE_SKILL_DIR}/scripts/detect_comfyui.py"
python "${CLAUDE_SKILL_DIR}/scripts/detect_comfyui.py" --deep      # 見つからないときだけ
```

| 種類 | 検出元 | 実機確認 |
|---|---|---|
| Comfy Desktop（新デスクトップ版） | `%APPDATA%\Comfy Desktop\installations.json`、`comfy-procs\*.json`（ポート） | 確認済み（Desktop 1.1.8 / ComfyUI 0.42.5） |
| 旧 ComfyUI Desktop | `%APPDATA%\ComfyUI\config.json` の `basePath` | **未検証** |
| ポータブル版・git版 | よくある場所の `ComfyUI\main.py` + `comfy\` + `nodes.py` | フォルダ判定のみ |

- **既存の ComfyUI があれば再インストールしない。**
- Desktop版の場所と、その中の本体フォルダ（`…\ComfyUI\ComfyUI`）は同じものとして1件にまとめて表示する。
- 停止中に表示される版は「記録上の版」（設定ファイルの値）。実際に動く版は ComfyUI を起動すると API で確認できる。
- 見つからない場合：ComfyUI の導入はこのSkillの範囲外。公式（https://www.comfy.org/download）の Desktop 版を案内し、ユーザーが自分で導入・初回起動まで済ませてから戻ってもらう。モデルのダウンロードも同様（容量・ライセンス確認が必要なため自動化しない）。

用語：**ComfyUI**＝画像・動画生成AIをノード（箱）をつないで動かすアプリ。**API**＝プログラムから ComfyUI に指示を送る窓口。

## 2. 起動状態と接続先（Phase 1/4）

ComfyUI の API は既定で `http://127.0.0.1:8188`（Desktop 版は設定によって `8000` などの場合がある）。
`detect_comfyui.py` が `127.0.0.1` の候補ポートに `GET /system_stats` を送り、応答したものを「起動中」と判定する。

ComfyUI が起動していない場合の案内（1ステップ）：
1. **開くもの**：ComfyUI Desktop（スタートメニューまたはデスクトップのアイコン）
2. **操作**：起動して、ノード画面が表示されるまで待つ（初回は数分かかることがある）
3. **正常な状態**：ブラウザ風の画面にノードが並ぶ
4. **完了後**：「起動した」と伝える → Claude Code が再検出する

## 3. API の詳しい確認（Phase 4、読み取りのみ）

```
python "${CLAUDE_SKILL_DIR}/scripts/check_connection.py" api --url http://127.0.0.1:8188
```

確認内容：
- `/system_stats`：バージョン、GPU と VRAM 空き
- `/queue`：実行中・待機中のジョブ数
- `/models`、`/models/<フォルダ>`：使えるモデルの数（ファイル名は workflow 検査で使う）
- 起動引数 `--listen` と、`netstat` による待ち受けアドレス（外部公開の兆候）

### 外部公開について（重要）
- このSkillは ComfyUI を **インターネットへ直接公開する設定を行わない**。ルーターのポート開放、`--listen 0.0.0.0`、トンネル（ngrok 等）を提案しない。
- 外出先から使えるのは、**自宅PCの Bot が Discord に「外向き」に接続している**から。ComfyUI 自体はPC内（127.0.0.1）に閉じたままでよい。
- 接続先URLは `127.0.0.1` / `localhost` のみ許可（スクリプトが検証）。LAN 上の別PCは `--allow-lan` を明示した場合のみ。ドメイン名・グローバルIPは常に拒否。
- `0.0.0.0` 待ち受けの警告が出ても、Skillは設定を変えない。意図しない設定なら、変更方法・影響・戻し方を説明して承認を得る。

## 4. ImageSmith が使えるか（Phase 4）

ImageSmith は ComfyUI に次を使う（ソース `src/comfy/` で確認）：
- `GET /history`（接続確認）、WebSocket `/ws`（進捗受信）
- `POST /prompt`（生成依頼）、`GET /view`（生成物の取得）、`POST /api/upload/image`（img2img 用）

Skill の検査は GET のみ。`POST /prompt` は **Phase 5 でユーザーが Discord から実行するまで送らない**。

## 5. メモリ解放について（区別）

- ComfyUI 本体には `POST /free`（モデルのアンロード）APIがある。
- 既存の検証環境で使った Discord の `/memory`・`/free` コマンド、生成前の自動メモリ解放は **独自追加のプラグイン（`plugins/memory_tools.py`）で、ImageSmith 上流の標準機能ではない**。このSkillでは導入しない（配布物にも含めない）。
- このSkillは `/free` を送らない。メモリ不足時の対処は [troubleshooting.md](troubleshooting.md) を参照。
