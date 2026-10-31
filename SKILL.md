---
name: comfyui-discord-setup
description: Windows 11 の自宅PCで動く ComfyUI を、Discord Bot（ImageSmith）経由でスマートフォンなど外出先から操作できるようにする、非エンジニア向けの対話型セットアップ。環境診断、ImageSmith の導入、Discord Bot 作成の1ステップずつの案内、ComfyUI 接続の読み取り検査、画像生成・スマホ・動画の動作確認、エラー診断、中断と再開を扱う。
when_to_use: 「自宅PCのComfyUIをDiscordから操作できるようにして」「ComfyUIをスマホから使いたい」「ImageSmithを導入したい」「Discord Botで画像生成したい」「ComfyUI Discord連携の続きから」「Botがオンラインにならない」「/forge が出ない」などの依頼。ComfyUI × Discord の導入・再開・トラブル対応のときに使う。
argument-hint: "[resume | diagnose <症状>]"
compatibility: Windows 11、Claude Code、Python 3.12（ImageSmith用）、Git、起動済みまたは導入済みの ComfyUI。ImageSmith 上流コミット dcc0807 で確認。
license: MIT
metadata:
  version: 0.1.0-beta
---

# ComfyUI × Discord セットアップ（comfyui-discord-setup）

自宅の Windows PC で ComfyUI と Discord Bot（OSS の **ImageSmith**）を動かし、スマホの Discord から画像・動画生成を指示できる状態にする。
**自動化よりも安全性・再現性・初心者の分かりやすさを優先する。**

スクリプトはすべて `${CLAUDE_SKILL_DIR}/scripts/` にあり、標準ライブラリだけで動く（`python` で実行）。

## 0. いつも守るルール（最優先）

### 対話
- **1回の案内で1つの操作だけ** を求める。ユーザーが「できた」と言うまで次へ進まない。**勝手に次の Phase へ進まない**（Phase の切り替え時は「次は Phase N の○○です。進めてよいですか？」と確認）。
- ユーザー操作の案内には必ず「①開くページ／もの ②クリックする場所 ③入力する内容 ④正常な状態 ⑤完了後にすること」を入れる。
- 専門用語は初出時に一言で説明する。コマンドはコピーしてそのまま実行できる形で示す。
- 既に確認できた情報（進捗ファイル・スクリプト出力）を再質問しない。
- スクリーンショットは必要なときだけ、撮る場所を指定して1枚。トークン欄が写っていないか確認してもらう。
- ユーザー操作の要らない **読み取りの検査はまとめて実行してよい**。
- エラー時は関連するログの該当部分だけを調べる。原因が特定できなければ「追加調査が必要」と伝え、**推測で設定を変更しない**。
- 実機で確認していないことを「動作確認済み」と言わない。「ユーザーが画面で確認」「ログで照合」「未検証」を区別する。

### セキュリティ
- **Discord Bot トークンをチャットに貼らせない。** Claude Code はトークンを読まない・表示しない・ファイルに書かない。登録はユーザーが別の PowerShell 画面で行う（[references/discord_setup.md](references/discord_setup.md) D5）。確認は `check_environment.py` の「登録あり／未登録」だけ。
- もしトークンが貼られたら：値を復唱・記録せず、「漏れた扱いになるので Reset Token で再発行してください」と案内する。
- 環境変数 `DISCORD_TOKEN` の値を `echo` / `Get-ChildItem Env:` / `[Environment]::GetEnvironmentVariable` 等で表示するコマンドを実行しない。`configuration.yml` を `cat` で全文表示しない（検査は `check_connection.py config`）。
- API キー・トークン・認証URL・個人名を、進捗ファイル・ログ・Skill のファイルに保存しない。
- ComfyUI をインターネットへ公開しない（ポート開放・`--listen 0.0.0.0`・トンネルを提案しない）。接続先は `127.0.0.1` のみ（LAN は明示時のみ）。外出先から使えるのは Bot が Discord に外向きに接続するため。
- 診断ログを外部サービスへ送信しない。管理者権限は要求しない。

### 承認が必要な操作（必ず事前に「何を・影響・戻し方」を説明して明示的な承認を得る）
| 操作 | 方法 |
|---|---|
| ImageSmith の導入（git clone・venv・pip） | `setup_imagesmith.py install --confirm` |
| 設定ファイルの作成・上書き（自動バックアップ） | `setup_imagesmith.py make-config --confirm [--overwrite]` |
| 上流バグの1行修正（任意） | `setup_imagesmith.py patch-security --confirm` |
| 画像生成・動画生成（GPU使用） | ユーザーが Discord から実行。Claude Code は `/prompt` を送らない |
| ComfyUI・Windows・Discord の設定変更 | このSkillでは自動で行わない。必要ならユーザー操作として案内 |
| 削除・進捗リセット | ユーザーの明示指示のときだけ（`manage_progress.py reset --confirm`） |

`--confirm` を付けずに実行すると「実行予定の内容」だけが表示される（終了コード 3）。まずそれを見せてから承認を求める。

**しないこと**：モデル・カスタムノードのダウンロード、ComfyUI のアップデート、既存の ImageSmith 環境の変更、独自プラグイン（`/memory`・`/free`・自動メモリ解放）の導入、GitHub への公開、git commit/push。

## 1. 開始と再開

最初に必ず進捗を確認する：
```
python "${CLAUDE_SKILL_DIR}/scripts/manage_progress.py" show
```
- 記録なし → `manage_progress.py init` して Phase 1 から。
- 記録あり → 完了済みの工程は繰り返さず、「前回は Phase N の○○まで完了。次は△△です。続けますか？」と聞く。ただし環境は変わり得るので、Phase 1 の読み取り検査だけは再実行してよい。
- `python` が見つからない場合：Python 3.12 の導入から案内する（下の Phase 1 の「不足時」）。

進捗の更新：工程が終わるたびに `manage_progress.py complete <工程ID>`。ユーザー待ちは `pending <工程ID> --status waiting_for_user`、承認待ちは `--status waiting_for_approval`。工程ID一覧は `manage_progress.py steps`。
記録してよい事実は `fact` で（`comfyui_url`・`imagesmith_dir`・`imagesmith_commit`・`imagesmith_source`・`workflow_names` など。秘密や個人名は拒否される）。

### 読み取り専用モード（ユーザーが「読み取りだけ」「変更しないで」と言ったとき）
- すべてのスクリプトに `--read-only` を付ける。進捗ファイルは **作らない**（`init`・`complete` などを実行しない。`--read-only` 付きだと拒否され終了コード 4）。
- `check_environment.py --read-only` は `DISCORD_TOKEN` に一切触れない（環境変数・レジストリとも読まない）。トークンの登録有無は「今回は確認していない」と伝える。
- スクリプトは Python のキャッシュ（`__pycache__`）を作らない設定になっている。
- 完了扱いにした工程は、口頭で伝えるだけにする（記録しない）。
- **選んだ ImageSmith 環境はこの会話の中だけで保持する**：`manage_progress.py --read-only select-imagesmith <場所>` で検査して表示し、以降のコマンドには必ず `--target-dir <場所>` を付ける。ファイルには書かないため、**次の会話には引き継がれない**（その旨をユーザーに伝える）。

## Phase 1：環境診断（読み取りのみ・まとめて実行可）

```
python "${CLAUDE_SKILL_DIR}/scripts/check_environment.py"
python "${CLAUDE_SKILL_DIR}/scripts/detect_comfyui.py"
python "${CLAUDE_SKILL_DIR}/scripts/detect_imagesmith.py"
```
- 結果を短くまとめて伝える（Windows・CPU・RAM・GPU/VRAM・Python・Git・ComfyUI の場所と起動状態・空き容量・既存の ImageSmith）。通常表示は初心者向け。詳しく調べるときだけ `--details` を付ける。
- RAM の注意は2種類あり、混同しない：「いま空いている RAM（実メモリ）」と「RAM＋ページファイル（ディスク上の予備）を合わせた空き」。基準は `check_environment.py` の `THRESHOLDS`（`--ram-free-warn-gb` などで変更可）。
- ComfyUI が停止中のときのバージョンは「記録上の版」。実際の版は起動後に API で確認できる、と伝える。
- `detect_imagesmith.py` は設定ファイル（configuration.yml）を開かない。存在の有無だけを見る。
- 既存の ComfyUI があれば **再インストールしない**。起動していなければ起動を1ステップで案内（[references/comfyui_setup.md](references/comfyui_setup.md)）。
- **不足時**：必要な理由と導入方法を説明し、ユーザーに導入してもらう（Claude Code は勝手にインストーラーを実行しない）。
  - Python 3.12：python.org の Windows installer。インストール画面で「Add python.exe to PATH」にチェック。
  - Git：git-scm.com の Git for Windows（既定の選択のままでよい）。
  - ComfyUI：comfy.org の Desktop 版。導入とモデル準備はユーザーが行い、ComfyUI 単体で1枚生成できてから戻る。
  - NVIDIA GPU なし・VRAM/RAM が少ない：動くモデルが限られる・遅いことを正直に伝える。
- 完了：`environment_check`、`comfyui_detected`、`prerequisites_ready`。

## Phase 2：ImageSmith の用意（既存環境の再利用 または 新規導入）

ImageSmith（https://github.com/jtyszkiew/ImageSmith）は **公式から取得し、このSkillには同梱しない**。上流 README は MIT と記載しているが、固定コミットにも最新にも LICENSE ファイルが無い（2026-10-29 確認）。再配布はしない。

**ユーザーが使う場所を指定した場合**は、その場所だけを調べる（ほかの環境は探さない・比較しない）：
```
python "${CLAUDE_SKILL_DIR}/scripts/detect_imagesmith.py" --target-dir "<指定された場所>"
```
- 判断 `reuse` →「既存の ImageSmith が利用できるため、新しいインストールは不要です。この環境を使用して次の工程へ進めます」と伝える。
- 判断 `target_invalid` / `target_not_usable` → 理由を伝える。**勝手に別の環境へ切り替えない。**
- 選択の記録：通常は `manage_progress.py select-imagesmith "<場所>"`（進捗に `imagesmith_dir`・`imagesmith_source=reused` を記録し Phase 2 を完了）。読み取り専用では `--read-only` を付け、会話内だけで保持する。

**場所の指定がない場合**は、まず `detect_imagesmith.py`（指定なし）の「判断」に従って分岐する。既存環境が使えるなら新規導入を勧めない。
デスクトップのショートカットは、表示名ではなくリンク先から「どの環境を使うか」を判定して表示する（`check_shortcuts.py`。ショートカットは実行しない。判定できなければ「不明」）。

| 判断 | すること |
|---|---|
| `reuse`（使える環境が1つ） | 「既存の ImageSmith をそのまま使えます。再インストールは不要です」と伝え、使ってよいか確認。了承されたら `imagesmith_installed` を完了にし、`fact imagesmith_source reused` と `fact imagesmith_dir <場所>` を記録 |
| `choose`（使える環境が複数） | **Skill は選ばない。** 各環境の違い（場所・独自プラグインの有無・起動用スクリプトの有無）を並べ、ユーザーに選んでもらう。同じ Bot を2か所で同時に起動しないよう伝える |
| `needs_attention`（見つかったが不完全） | 何が足りないか（例：仮想環境なし）を説明する。再インストールや修復は勝手に提案せず、「その環境をどうしたいか」をユーザーに聞く |
| （ソースだけのフォルダ） | 仮想環境も設定も無いフォルダは「ImageSmith のソースファイルが見つかりましたが、実行に必要な環境は確認できませんでした。今回は使用しません」と表示され、判断には使われない。フォルダ名で「研究用」などと断定しない |
| `new_install`（見つからない） | 下の新規導入手順へ |

既存環境が独自に変更されている（独自プラグイン・security.py の修正など）場合は、その事実を伝えるだけにし、変更しない。

### 新規導入（`new_install` のとき、またはユーザーが別の場所への新規導入を希望したとき）

1. 導入先を決める（既定 `%USERPROFILE%\comfy-discord-imagesmith`。既存フォルダとは衝突させない）：
   ```
   python "${CLAUDE_SKILL_DIR}/scripts/setup_imagesmith.py" plan --dir "<導入先>"
   ```
   「中止理由」が出たら別のフォルダ名を提案する（既存フォルダに入れない・上書きしない）。
2. 実行内容（clone → コミット `dcc0807` に固定 → Python 3.12 の venv → 確認済み版で pip → pip check → 記録）を見せて承認を得る。所要数分・ネット接続・約200MB。
3. 承認後：
   ```
   python "${CLAUDE_SKILL_DIR}/scripts/setup_imagesmith.py" install --dir "<導入先>" --confirm
   ```
   取得元・コミット・Python・パッケージ版は `<導入先>\install_record.json` に記録される（第三者の再現用、秘密情報なし）。
   **途中で失敗したら**（終了コード 1、`[失敗]`）：表示された「成功した工程／失敗した工程／残っているもの／再実行できるか／次にすること」をそのまま伝える。**作ったものは自動削除しない・Claude Code も削除しない。** 原因は表示の「考えられる例」以上に断定しない。
   - 再開は、原因の解消後に、承認を得てから `install --dir "<導入先>" --resume --confirm`。Skill の処理記録（`.comfy-discord-setup-install.json`）と実際の状態（取得元・コミット・変更の有無・不明なファイル）が一致するときだけ、完了済みの工程を飛ばして続きを行う。一致しなければ何も変えずに止まる。
   - 「〇〇フォルダを削除してから」と表示された場合、削除はユーザー自身がエクスプローラーで行う。
   - 取得元・コミットの照合に失敗した場合は再開しない（追加調査が必要）。
4. 完了：`imagesmith_location_decided`、`imagesmith_installed`。`fact imagesmith_dir` と `fact imagesmith_commit` を記録。

## Phase 3：Discord Bot の用意（既存Botの再利用 または 新規作成）

**最初にユーザーに選んでもらう**（Skill は勝手に決めない）：
1. **既存の Discord Bot を再利用する**
2. **新しい Discord Bot を作成する**

### 3-A. 既存Botを再利用する場合（新規作成の手順へは進まない）
[references/discord_reuse.md](references/discord_reuse.md) の R1〜R7 を **1つずつ** 案内する。
- 冒頭で必ず表示：「今回は既存Botの確認だけを行います。設定を変更する必要はありません。Reset TokenやSave Changesは押さないでください。」（`check_bot_reuse.py checklist`）
- R1〜R5 は画面を見るだけ。スイッチ・保存ボタンを押させない。**Reset Token は案内しない。**
- R6（トークン登録の有無）は **通常モードで、ユーザーが承認したときだけ** `check_bot_reuse.py token-presence --consent-token-presence`。値は出さない。読み取り専用では行わない（未確認）。
- R7（利用者・チャンネル制限）は **設定ファイルを開くことに明示的な同意があるときだけ** `check_bot_reuse.py access-limits --consent-read-config`（ImageSmith の venv の python で）。
- 結果は「画面で確認済み／記録のみ／未確認／不一致」に分類する（`check_bot_reuse.py summary`）。**過去の記録だけで「確認済み」にしない。** 不一致はここで直さず、説明して別工程で決める。
- R1〜R5 がすべて「画面で確認済み」で不一致が無ければ：「既存のDiscord Botが利用できるため、新規作成は不要です。必要な設定を確認して次の工程へ進みます」。通常モードでは `fact discord_bot_source reused` と各 `fact bot_check_rN <分類>` を記録。
- **トークンの再発行は別工程**：漏洩が疑われても自動で勧めない。必要なら理由・既存接続への影響（押した瞬間に古いトークンは無効になり、起動中の Bot は切断）・再登録の必要性を説明し、明示的な承認を得てから discord_setup.md の D5 を案内する。

### 3-B. 新しく作る場合
[references/discord_setup.md](references/discord_setup.md) の D1〜D9 を **1つずつ** 案内する。要点：
- 自分専用サーバーと `#comfyui-generation` チャンネル、Discord の **ユーザー名**（表示名ではない）を確認。
- Create App → Bot ページで Server Members Intent と Message Content Intent を ON（Presence は OFF）。
- Reset Token → **ユーザー自身の PowerShell で環境変数 `DISCORD_TOKEN` に登録**（`Read-Host -AsSecureString`）。
- Install Link を None、Public Bot を OFF（本人以外が招待できないように）。
- OAuth2 URL Generator：scope `bot`（公式：`applications.commands` は `bot` に既定で含まれる。両方にチェックしても問題ない）。権限はこのSkillの推奨5つ（View Channels / Send Messages / Embed Links / Attach Files / Read Message History。個々の必要性は未検証）。Administrator は付けない。
- 完了：`discord_app_created`、`discord_intents_configured`、`discord_token_registered`、`discord_bot_invited`、`discord_bot_configuration`。

## Phase 4：ComfyUI との接続（まず読み取り検査）

```
python "${CLAUDE_SKILL_DIR}/scripts/check_connection.py" api --url http://127.0.0.1:8188
```
- 接続先・バージョン・VRAM・待ち行列・モデル数・外部公開の兆候を確認（GETのみ）。完了：`comfyui_api_checked`。
- ワークフローは [references/workflow_setup.md](references/workflow_setup.md) に従い、**ユーザーが ComfyUI で実際に生成できるもの** を API 形式で書き出してもらい、`check_connection.py workflow --file ... --url ...` で検査する。存在しないモデル・ワークフロー名を仮定しない。完了：`workflow_selected`。
- 正式な対応は txt2img（画像・動画）。`/reforge`・`/upscale`（画像入力）は実機未検証のため試験的扱い。画像は ComfyUI の API で受け渡され、`input_dir` は使われない（`input_dir: "input"` のまま変えない。workflow_setup.md 6章）。
- 設定ファイル：spec JSON（スクラッチ領域に作る、トークンなし・英数字のみ）→ `make-config`（内容表示）→ 承認 → `--confirm` → `check_connection.py config --consent-read-config` で検査（ImageSmith の venv の python で）。完了：`imagesmith_configured`。
- **既存の configuration.yml は、通常は開かない**（トークンが書かれている可能性）。登録済みのワークフロー名を知る必要があるときだけ、「設定ファイルを開いて、ワークフロー名だけを取り出してよいですか？（トークンなど他の値は表示しません）」と聞き、明示的な同意があれば：
  ```
  python "${CLAUDE_SKILL_DIR}/scripts/check_connection.py" workflow-names --file "<ImageSmith>\configuration.yml" --consent-read-config
  ```
  名前以外の値は出さない。想定外の書き方や秘密らしい名前があれば中止される（中身は表示されない）。同意フラグなしでは開かずに終了する（終了コード 5）。
- 設定変更が必要なときは、変更内容・影響・戻し方（`.bak`）を説明して承認を得る。

## Phase 5：画像生成の動作確認（生成は要承認）

[references/smartphone_test.md](references/smartphone_test.md) の P1〜P3。
- Bot 起動コマンドは `setup_imagesmith.py start-command --dir <導入先>` で表示し、**ユーザーが新しい PowerShell で実行**（Claude Code のシェルで常駐起動しない：トークンを Claude Code のプロセスに渡さないため）。
- 確認：`Connected to ComfyUI`、`Registered 4 Discord commands`、`Bot is ready!`、Bot がオンライン、`/` でコマンド表示、`/workflows`。
- **生成の前に必ずメモリを確認する**：`check_environment.py --pre-generation`（動画は `--pre-generation --video`）。空き RAM と RAM＋ページファイルの空きの両方を見る。警告が出たら、生成前にユーザーへ説明し、アプリを閉じるのはユーザー自身にしてもらう（Skill はアプリ終了・メモリ解放をしない）。
- `/forge` で1枚生成する前に承認を得る。完了：`bot_started`、`slash_commands_visible`、`image_generation_tested`。

## Phase 6：スマートフォンからの遠隔操作確認

[references/smartphone_test.md](references/smartphone_test.md) の M1・M2。Wi-Fi オフ（モバイル回線）でのテストと、実際の外出先テストを **区別して記録** する。成功した場合だけ `mobile_network_tested`（外出時は `outside_tested`）。

## Phase 7：動画生成と制限事項

[references/smartphone_test.md](references/smartphone_test.md) の V0〜V2。
- **先に制限を説明する**：長時間生成では Discord の Interaction Token（15分）が切れ、ComfyUI で成功しても Discord に返信できないことがある。ImageSmith 標準に対策はなく、未検証の修正は自動適用しない。メモリ不足・添付サイズ上限も説明。完了：`video_limitations_explained`。
- 対応する動画ワークフローが ComfyUI で動く場合のみ登録（Phase 4 の手順）。動画生成は承認後、ユーザーが実行。完了：`video_generation_tested`。

## エラー診断

[references/troubleshooting.md](references/troubleshooting.md) を参照。
```
python "${CLAUDE_SKILL_DIR}/scripts/diagnose.py" --symptom <症状>
python "${CLAUDE_SKILL_DIR}/scripts/diagnose.py" --log "<ログファイル>"
```
症状：`comfyui_unreachable` `bot_offline` `commands_missing` `workflow_not_found` `model_missing` `generation_not_starting` `vram` `ram` `video_reply_failed`。

## 引数

- `/comfyui-discord-setup resume`：進捗を表示して続きから。
- `/comfyui-discord-setup diagnose <症状>`：診断のみ（変更なし）。
- 引数なし：進捗を確認して開始／再開。

受け取った引数：$ARGUMENTS
