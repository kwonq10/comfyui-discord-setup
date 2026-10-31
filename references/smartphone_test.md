# Phase 5〜7：動作確認（PC → スマホ → 動画）

> Claude Code への指示：生成は GPU・電気・時間を使う。**毎回、実行前にユーザーの承認を得る**（「この内容で1回生成してよいですか？」）。
> Claude Code 自身は ComfyUI に `/prompt` を送らない。生成はユーザーが Discord から行う。
> 結果は「ユーザーが画面で確認した」のか「ログで照合した」のかを区別して記録する。

## Phase 5：PC の Discord から画像生成

### P1. Bot を起動する（工程ID: `bot_started`）
1. **開くもの**：新しい PowerShell（DISCORD_TOKEN 登録後に開いたもの）
2. **入力**：`setup_imagesmith.py start-command --dir <導入先>` が表示した2行をコピーして実行
3. **正常な状態**：ログに次が出る
   - `Connected to ComfyUI`（ComfyUI に接続）
   - `Registered 4 Discord commands`（/forge・/reforge・/upscale・/workflows）
   - `Logged in as <Bot名>`、`Bot is ready!`
   - Discord のメンバー一覧で Bot がオンライン（緑）
4. **完了後**：「Bot is ready! が出た」と伝える。出ない場合は、エラー部分の **数行だけ** を伝えてもらう（トークンが写っていないか確認）か、ログを保存して `diagnose.py --log` で調べる
5. **注意**：このウィンドウは閉じない（閉じると Bot が止まる）。停止は Ctrl+C

### P2. コマンドが出るか（工程ID: `slash_commands_visible`）
1. **開くもの**：Discord の `# comfyui-generation`
2. **入力**：`/` を1文字入力
3. **正常な状態**：Bot の `/forge`、`/workflows` などが候補に出る。`/workflows` を実行すると登録したワークフロー名が出る
4. **完了後**：「表示された」と伝える。出なければ Discord アプリを再起動して再確認（反映に時間がかかることがある）

### P3. 画像を1枚生成（工程ID: `image_generation_tested`）— **要承認**
- まずメモリを確認（読み取りのみ）：`python check_environment.py --pre-generation`
  - 「いま空いているRAM」と「RAM＋ページファイルの空き」の両方を見る。警告が出たら、理由を説明し、ユーザー自身に Chrome などを閉じてもらってから再確認する。Skill はアプリを閉じない・メモリを解放しない。
- 承認を得る文例：「これから `/forge` で画像を1枚生成します。GPU を使って数十秒〜数分かかります。実行してよいですか？」
1. **入力**：`/forge` → `prompt` に英語の短文（例：`a cat sitting on a windowsill, soft morning light`）→ `workflow` に設定した名前（例：`my_image`）→ Enter
2. **正常な状態**：進捗が表示され、画像と「生成完了」が投稿される
3. **完了後**：「画像が届いた」と伝える。Claude Code は ComfyUI の `/history` を **読み取り** で確認してよい（任意）

## Phase 6：スマートフォンから遠隔操作

**2段階を区別して記録する。** モバイル回線テストは「家のWi-Fiを使わない経路」の確認、外出テストは実運用の確認。

### M1. モバイル回線テスト（工程ID: `mobile_network_tested`）— **要承認（生成あり）**
1. 自宅PCで ComfyUI と Bot が起動していることを確認（P1 の画面が開いたまま）
2. スマホの **Wi-Fi をオフ** にする（モバイルデータ通信をオン）
3. スマホの Discord アプリで `# comfyui-generation` を開く
4. `/forge` を実行（プロンプト・ワークフローは P3 と同じでよい）
5. 生成結果がスマホに届くか確認
- 正常な状態：スマホに画像が表示される
- 記録：「モバイル回線で1回成功（日時）」。PC の Wi-Fi・家のネットワークは使っていない経路であることが確認できる
- テスト後、スマホの Wi-Fi を元に戻すよう伝える

### M2. 外出先テスト（工程ID: `outside_tested`、任意）
- 実際に外出したときに M1 と同じ操作をしてもらう。**M1 の成功を M2 の成功として記録しない。**
- 注意：外出中に PC がスリープ・再起動・Windows Update すると使えない。電源設定の変更は Windows 設定の変更になるため、必要なら別途承認を得て案内する（自動では変更しない）。

## Phase 7：動画生成と制限事項

### V0. 先に制限を説明する（工程ID: `video_limitations_explained`）
次をユーザーに伝え、理解したことを確認してから進む：
1. **Discord の応答期限（15分）**：スラッシュコマンドの応答を後から書き換えるためのトークン（Interaction Token）は15分で失効する。ImageSmith は結果をこの応答の編集で返すため、**生成に15分以上かかると、ComfyUI では成功しても Discord に返信できない** ことがある（ログに `Invalid Webhook Token` / `50027` / `401` が出る例がある。既存検証環境ではエラーログは観測したが、15分超過だけが原因とは確定していない）。
2. **ImageSmith 標準には対策がない**。未検証の修正は自動で入れない。対策は「短い動画・低い解像度にして15分以内に終わらせる」「失敗したら ComfyUI の出力フォルダから取り出す」。
3. **メモリ**：動画は RAM・VRAM を大量に使う。既存検証（RTX 3060 12GB / RAM 16GB）では5秒動画で約6分、RAM+ページファイルの残りが最小 0.26GB まで減った。常駐アプリを閉じることを推奨。
4. **添付サイズ上限**：Discord の添付ファイルには上限がある（サーバーのブースト状況で異なる）。大きい動画は届かない。
5. **ファイルの違い**：Discord からダウンロードした動画は、映像・音声は同一でも、埋め込みメタデータが元ファイルと異なる例を確認している。完全な原本が必要なら ComfyUI の出力フォルダからコピーする。

### V1. 動画ワークフローを登録（Phase 4 の手順を再利用）
- 対応する動画ワークフローが **ComfyUI で生成できる状態のときだけ**。無ければ Phase 7 は「対象なし」で終了（モデル導入は自動化しない）。

### V2. 動画を1本生成（工程ID: `video_generation_tested`）— **要承認**
- まずメモリを確認（動画用の厳しめの目安）：`python check_environment.py --pre-generation --video`
- 承認文例：「これから動画を1本生成します。数分〜十数分かかり、その間PCが重くなります。15分を超えるとDiscordに返信できないことがあります。実行してよいですか？」
- 生成中は Claude Code から追加の生成を送らない。結果は「Discordに届いた」「ComfyUIでは成功・Discordは失敗」「ComfyUIで失敗」の3つに分けて記録する。
