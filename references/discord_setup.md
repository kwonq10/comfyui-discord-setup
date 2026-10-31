# Phase 3：Discord Bot 作成ガイド（1ステップずつ案内する）

> **このファイルは「新しい Bot を作る」とユーザーが選んだときだけ使う。** 既存の Bot を再利用する場合は [discord_reuse.md](discord_reuse.md)（確認のみ・Reset Token を案内しない）を使い、このファイルへは進まない。
>
> 公式仕様の確認日：**2026-10-29**（docs.discord.com：Gateway / OAuth2 / Receiving and Responding to Interactions）

> Claude Code への指示：このファイルの **D1〜D9 を1回に1つだけ** ユーザーに示す。
> 各ステップは「開くページ／クリックする場所／入力する内容／正常な状態／完了後にすること」の5点を必ず含める。
> ユーザーが「できた」と伝えるまで次へ進まない。完了したら `manage_progress.py complete <工程ID>` を実行する。
>
> **絶対に守ること**
> - Botトークンをチャットに貼らせない。貼られた場合は「そのトークンは無効化が必要」と伝え、D5 の Reset Token をやり直してもらう（貼られた値は復唱・記録しない）。
> - トークンを Claude Code の Bash/PowerShell から読まない・表示しない。登録はユーザー自身が別の PowerShell 画面で行う。
> - スクリーンショットを頼むときは「トークン欄が写っていないこと」を確認してもらう。
>
> 画面の名前は 2026-10 時点の Discord 公式ドキュメント（docs.discord.com）に合わせている。画面が違う場合は、ユーザーに見えているボタン名を聞いて合わせる（推測でクリック先を断定しない）。

---

## D1. 自分専用のサーバーとチャンネルを用意する（工程ID: なし／準備）

1. **開くもの**：Discord アプリ（PC版）
2. **クリック**：左端の「＋」（サーバーを追加）→「オリジナルの作成」→「自分と友達のため」
3. **入力**：サーバー名は自由（例：`my-comfyui`）。作成後、左の「テキストチャンネル」の「＋」で `comfyui-generation` というチャンネルを作る
4. **正常な状態**：左側に新しいサーバー、その中に `# comfyui-generation` が見える
5. **完了後**：「できた」と伝える。既に自分が管理者のサーバーがあればこのステップは飛ばしてよい

> 用語：**サーバー**＝Discord内のグループ。**チャンネル**＝サーバー内の話題ごとの部屋。

## D2. Discord のユーザー名を確認する（工程ID: なし／準備）

1. **開くもの**：Discord アプリ
2. **クリック**：左下の自分のアイコン横の歯車（ユーザー設定）→「マイアカウント」
3. **入力**：なし
4. **正常な状態**：「ユーザー名」欄に英数字の名前が表示される（表示名ではなく **ユーザー名**）
5. **完了後**：ユーザー名をチャットで教えてもらう（これは秘密情報ではない。ただし進捗ファイルには保存しない。設定ファイル作成時にだけ使う）

> 理由：ImageSmith はユーザー名（`member.name`）とチャンネル名で利用を許可する。表示名では一致しない。

## D3. Developer Portal でアプリを作る（工程ID: `discord_app_created`）

1. **開くページ**：https://discord.com/developers/applications （Discordアカウントでログイン）
2. **クリック**：右上の「**Create App**」（古い画面では「New Application」）
3. **入力**：名前（例：`My ComfyUI Bot`）。利用規約に同意するチェックを入れて「Create」
4. **正常な状態**：左側に「General Information」「Installation」「OAuth2」「Bot」などのメニューが出る
5. **完了後**：「できた」と伝える

> 用語：**Bot**＝Discord上で自動で動くアカウント。今回は「自宅PCで動く ImageSmith」が Bot として振る舞う。

## D4. 特権 Intent を設定する（工程ID: `discord_intents_configured`）

1. **開くページ**：同じアプリの左メニュー「**Bot**」
2. **クリック**：下の方の「**Privileged Gateway Intents**」
3. **設定**：
   - **Presence Intent**：OFF のまま
   - **Server Members Intent**：ON
   - **Message Content Intent**：ON
   - 画面下に出る「**Save Changes**」を押す
4. **正常な状態**：2つが ON（色付き）、保存済みの表示
5. **完了後**：「できた」と伝える

> 理由：ImageSmith のソース（`src/bot/imagesmith.py`）が `members` と `message_content` を要求するため。
> 公式（Gateway、確認日 2026-10-29）：特権インテントは `GUILD_PRESENCES`・`GUILD_MEMBERS`・`MESSAGE_CONTENT` の3つ。アプリの設定で ON にしていない特権インテントを要求すると、Gateway 接続はコード **`4014`（Disallowed intent(s)）** で切断される。discord.py ではこれが `PrivilegedIntentsRequired` というエラーで表示される。利用者が1万人未満のアプリは Developer Portal で ON にするだけで使える（それ以上は審査が必要）。
> Presence は使わないので OFF（最小権限）。

## D5. トークンを発行して、自分のPCに登録する（工程ID: `discord_token_registered`）

**このステップは2つに分けて案内する（D5-a → D5-b）。**

### D5-a. トークンを発行してコピー

1. **開くページ**：左メニュー「**Bot**」
2. **クリック**：「Token」欄の「**Reset Token**」→ 確認で「Yes, do it!」（二段階認証のコード入力を求められたら入力）→「**Copy**」
3. **入力**：なし。**コピーした文字列はどこにも貼らない（このチャットにも貼らない）**
4. **正常な状態**：「Copied」等の表示。トークンはこの画面を離れると二度と表示されない
5. **完了後**：すぐ D5-b へ（「コピーした」と伝える）

> 用語：**トークン**＝Bot としてログインするための合言葉。漏れると他人があなたの Bot を操作できる。

### D5-b. 自分の PowerShell でトークンを登録（Claude Code には見えない方法）

1. **開くもの**：スタートメニューで「PowerShell」と入力 →「Windows PowerShell」を開く（**Claude Code とは別のウィンドウ**。管理者として実行は不要）
2. **入力**：次の3行をまとめてコピーして貼り付け → Enter。`トークンを貼り付けてEnter` と出たら **右クリック（または Ctrl+V）でトークンを貼り付けて Enter**（画面には `*` しか出ない）

```powershell
$s = Read-Host 'トークンを貼り付けてEnter' -AsSecureString
[Environment]::SetEnvironmentVariable('DISCORD_TOKEN', [Net.NetworkCredential]::new('', $s).Password, 'User'); Remove-Variable s
Set-Clipboard -Value ' '; Write-Host '登録しました（値は表示しません）。このウィンドウは閉じてかまいません。'
```

3. **正常な状態**：`登録しました（値は表示しません）` と表示される
4. **完了後**：その PowerShell ウィンドウを閉じて「登録した」と伝える
5. **Claude Code 側の確認**：`python check_environment.py` を実行し「DISCORD_TOKEN: 登録あり（値は表示しません）」となることだけを確認する

> 補足：
> - **ユーザー環境変数**＝Windows のユーザーごとに保存される設定値。ファイルやGitには入らない。
> - 登録前に開いていた PowerShell・Claude Code には反映されない。Bot は **新しく開いた** PowerShell から起動する。
> - 代替手段（GUI）：スタートメニューで「環境変数」→「環境変数を編集」→ ユーザー環境変数の「新規」で 変数名 `DISCORD_TOKEN`、値にトークン。
> - 注意：ユーザー環境変数は、同じWindowsユーザーで動くプログラムからは読めます。共用PCでは使わないでください。

## D6. 「誰でも招待できる」設定を切る（工程ID: なし／D7の前準備）

1. **開くページ**：左メニュー「**Installation**」
2. **クリック／設定**：
   - 「**Installation Contexts**」：**Guild Install** だけ ON（User Install は OFF）
   - 「**Install Link**」：**None** を選ぶ → 「Save Changes」
   - 次に左メニュー「**Bot**」→「**Public Bot**」を OFF → 「Save Changes」
3. **正常な状態**：保存エラーが出ない（Install Link が None でないと Public Bot を OFF にできないことがある）
4. **完了後**：「できた」と伝える

> 理由：Public Bot が ON だと、招待リンクを知った他人も自分のサーバーにこの Bot を入れられる。自宅PCで画像生成を受け付けるBotなので、本人以外が招待できないようにする。

## D7. 招待URLを作ってサーバーに招待する（工程ID: `discord_bot_invited`）

1. **開くページ**：左メニュー「**OAuth2**」→「**URL Generator**」（OAuth2 ページ内の項目）
2. **クリック**：
   - **Scopes**：`bot` にチェック（`applications.commands` にもチェックしてよい）
     - 公式（OAuth2、確認日 2026-10-29）：`applications.commands`（サーバーにスラッシュコマンドを追加する権限）は **`bot` スコープに既定で含まれる**。以前の手順書では両方の選択が必要とされていたため、両方にチェックしても問題はない
   - **Bot Permissions**（`bot` を選ぶと出る）：このSkillが推奨する次の5つにチェック（**個々の必要性は実機で検証していない**。最小限の目安）
     - View Channels（チャンネルを見る）
     - Send Messages（メッセージを送信）
     - Embed Links（埋め込みリンク）
     - Attach Files（ファイルを添付）
     - Read Message History（メッセージ履歴を読む）
   - Integration Type が出る場合は **Guild Install**
   - 一番下の「Generated URL」の「Copy」
3. **入力**：コピーした URL をブラウザのアドレス欄に貼り付けて開く →「サーバーに追加」で D1 のサーバーを選ぶ →「認証」
4. **正常な状態**：サーバーのメンバー一覧に Bot が（まだオフラインで）表示される
5. **完了後**：「招待できた」と伝える

> 注意：
> - **Administrator（管理者）には絶対にチェックしない**（最小権限）。
> - ImageSmith 自身が起動時にログへ出す招待リンクには、上記に加えて「リアクションの追加」「外部の絵文字を使用」も含まれる。**5権限だけでの動作は実機未検証**。Phase 5 で返信・添付に失敗したら、`diagnose.py` で `Missing Permissions` を確認してから追加を検討する。
> - 招待URL自体は秘密ではないが、進捗ファイルには保存しない。

## D8. チャンネルの権限を確認する（任意・推奨）

1. **開くもの**：Discord アプリ → `# comfyui-generation` の歯車（チャンネルの編集）→「権限」
2. **確認**：Bot（またはBotのロール）が、このチャンネルで「チャンネルを見る」「メッセージを送信」「ファイルを添付」できること
3. **正常な状態**：赤い × になっていない
4. **完了後**：「確認した」と伝える

## D9. 許可するユーザーとチャンネルを決める（工程ID: `discord_bot_configuration`）

- 許可ユーザー：D2 で確認したユーザー名（基本は本人だけ）
- 許可チャンネル：`comfyui-generation`（DM・スレッドでは使わない）
- この2つは Phase 4 の設定ファイル作成でワークフローごとに必ず設定する（ImageSmith の `security` はワークフロー単位。付け忘れたワークフローはサーバー全員が使える）

完了したら `manage_progress.py complete discord_bot_configuration`。**ユーザー名・チャンネル名は進捗ファイルに保存しない。**

---

### 参照した公式情報
- Discord Developer Docs「Getting Started」（docs.discord.com/developers/quick-start/getting-started）：Create App、Bot ページの Reset Token・Privileged Gateway Intents、Installation ページの Installation Contexts・Install Link・Default Install Settings
- Interaction（スラッシュコマンド）：最初の応答は **3秒以内**、応答の編集・追加メッセージに使うトークン（Interaction Token）は **15分** 有効（Discord Developer Docs「Receiving and Responding to Interactions」、確認日 2026-10-29）→ [troubleshooting.md](troubleshooting.md) の動画の項
- Gateway：特権インテントと切断コード `4014`（Disallowed intent(s)）（確認日 2026-10-29）
- OAuth2：`applications.commands` は `bot` スコープに既定で含まれる。Public Bot を OFF にすると Bot を追加できるのは所有者だけ（確認日 2026-10-29）
- 権限：ImageSmith 自身の招待リンクは7権限（送信・閲覧・ファイル添付・埋め込みリンク・外部絵文字・リアクション追加・履歴の閲覧）。このSkillの5権限は推奨であり、各権限の必要性は未検証
