# Phase 4：ワークフローの選定と ImageSmith 設定

> Claude Code への指示：**存在しないモデル名・ワークフロー名を仮定しない。** ユーザーが ComfyUI 上で実際に生成できているワークフローを、API形式で書き出してもらって使う。
> ImageSmith 付属の例（`sd_xl_base_1.0`・Mochi 前提）は、そのモデルが ComfyUI に無ければ使わない。

## 1. 使うワークフローを決める（1ステップずつ）

### W1. ComfyUI で一度生成できるワークフローを用意
1. **開くもの**：ComfyUI の画面
2. **操作**：いつも使っている画像生成ワークフローを開き、「実行（Run / Queue）」で1枚生成できるか確認
3. **正常な状態**：画像が表示される（赤いノード・エラー表示がない）
4. **完了後**：「生成できた」と伝える。生成できない場合はまず ComfyUI 側で直す（このSkillはモデルやノードを導入しない）

> GPU を使うのはこの確認だけ。画像生成はユーザーが自分で実行する。

### W2. API 形式で書き出す
1. **開くもの**：同じワークフローを開いた ComfyUI の画面
2. **クリック**：上部メニュー「ワークフロー（Workflow）」→「エクスポート（API）／Export (API)」
   （古い版：設定で「開発者モードを有効にする（Enable Dev mode Options）」を ON にすると「Save (API Format)」が出る）
3. **入力**：ファイル名は英数字（例：`my_image_api.json`）。保存先を覚えておく
4. **正常な状態**：JSON ファイルが保存される
5. **完了後**：保存した場所（フォルダとファイル名）を伝える

> 用語：**API形式**＝プログラムから送るための書き出し形式。普通の「保存」（UI形式）とは中身が違い、ImageSmith は API 形式しか使えない。

## 2. 書き出したJSONを検査（読み取りのみ）

```
python "${CLAUDE_SKILL_DIR}/scripts/check_connection.py" workflow --file "<保存したJSON>" --url http://127.0.0.1:8188
```

表示されるもの：
- **プロンプト候補**：文章を入れるノードの ID と入力名（例：ノード `6` の `text`）。ポジティブ用とネガティブ用の両方が出ることがあるので、どちらがポジティブかはノードのタイトルで確認し、迷ったらユーザーに ComfyUI 画面で確認してもらう（推測で決めない）
- **seed候補**：毎回違う絵にするための乱数を入れる場所
- **出力ノード**：SaveImage など。無いと Discord に結果が返らない
- **参照モデル**：ComfyUI に実在するか（[あり]／[見つからない]）
- **ComfyUIに無いノード**：カスタムノード未導入

問題があれば Phase 4 を止めて、ユーザーに状況を説明する。モデルやカスタムノードの導入はこのSkillでは自動化しない。

## 3. 設定ファイル（configuration.yml）を作る

### 3-1. 設定内容を JSON にまとめる（スクラッチ領域に作る）
`templates/config_spec.example.json` を元に、確認できた値だけを入れる：

| 項目 | 入れる値 | 出どころ |
|---|---|---|
| `comfy_url` | 例 `http://127.0.0.1:8188` | `detect_comfyui.py` |
| `workflows[].name` | 英数字（例 `my_image`） | ユーザーと決める |
| `workflows[].file` | 書き出したJSONのパス | W2 |
| `prompt_node_id` / `prompt_input_key` | プロンプト候補 | 2. の検査 |
| `seed_node_id` / `seed_input_key` | seed候補（任意） | 2. の検査 |
| `image_input_node_id` | img2img・upscale だけ：画像を入れる LoadImage ノードの ID（試験的。6章） | 2. の検査・ComfyUI 画面 |
| `allowed_users` / `allowed_channels` | D2・D1 の値 | discord_setup |
| `default` | 最初の画像ワークフローだけ `true` | — |

`input_dir` は spec に書かない（生成される設定は常に `input_dir: "input"`）。ComfyUI の input フォルダに合わせる必要はない（6章）。

**トークンは書かない。** 日本語も書かない（日本語Windowsで ImageSmith が設定を読めない恐れ：ImageSmith は文字コードを指定せずにファイルを開くため）。

### 3-2. 内容を見せて承認を得る
```
python "${CLAUDE_SKILL_DIR}/scripts/setup_imagesmith.py" make-config --dir "<導入先>" --spec "<spec.json>"
```
`--confirm` なしでは **生成予定の内容を表示するだけ**。内容をユーザーに見せ、「この内容で作成してよいか」を聞く。

### 3-3. 承認後に作成
```
python "${CLAUDE_SKILL_DIR}/scripts/setup_imagesmith.py" make-config --dir "<導入先>" --spec "<spec.json>" --confirm
```
- ワークフローJSONは ImageSmith の `workflows\` に **コピー**する（元ファイルは変更しない）
- 既存の `configuration.yml` がある場合は `--overwrite` が必要。上書き前に `configuration.yml.<日時>.bak` を作る
- 元に戻す方法：`.bak` を `configuration.yml` に名前を戻す

### 3-4. 作成後の検査
```
"<導入先>\.venv\Scripts\python.exe" "${CLAUDE_SKILL_DIR}/scripts/check_connection.py" config --file "<導入先>\ImageSmith\configuration.yml" --url http://127.0.0.1:8188 --consent-read-config
```
（YAML を読むため ImageSmith の仮想環境の python で実行する。設定ファイルを開くので、実行前にユーザーの同意を得る）

### 3-5. 既存の設定に登録されたワークフロー名だけを知りたいとき
既存の configuration.yml はトークンを含む可能性があるため、通常は開かない。ユーザーが同意したときだけ：
```
python "${CLAUDE_SKILL_DIR}/scripts/check_connection.py" workflow-names --file "<ImageSmith>\configuration.yml" --consent-read-config
```
ワークフロー名だけを表示する。書き方が想定外・秘密らしい名前がある場合は中止し、中身は表示しない。

## 4. 上流の既知バグ（任意で修正）

ImageSmith（コミット `dcc0807`、2026-10-29 時点の最新）の `src/core/security.py` 110行付近で、**settings（`--settings` で指定する追加設定）ごとの権限判定が効かない** バグがある（`if not result:` が結果オブジェクトを判定している）。既存の検証環境では `if not result.state:` に1行修正し、再発防止テストで確認済み。

- ワークフロー単位の `security`（許可ユーザー・チャンネル）は **このバグの影響を受けない**。
- settings に個別の権限を付けない運用なら修正は不要。
- 修正する場合：`setup_imagesmith.py patch-security --dir <導入先>` で内容を確認 → 承認後 `--confirm`（`.bak` を作成）。上流へは報告・送信しない。

## 5. 動画ワークフロー（Phase 7 で使う）

- 動画も `type: txt2img` で登録する。ImageSmith は ComfyUI の出力（images / gifs / audio など）を見て自動で Discord に送る。
- 動画ワークフローも W1〜W2 と同じく、**ComfyUI で生成できることをユーザーが確認したものだけ** 使う。
- 既存検証環境の `duration(5);turbo` のような settings は、その環境のワークフロー専用に書いたもの。一般の動画ワークフローには存在しない。

## 6. 画像を入力に使うワークフロー（/reforge・/upscale）— 試験的

> 根拠：上流 ImageSmith の固定コミット `dcc08071e6e6c8b35aeb971b829e8a3f122790e7` のコード調査（2026-10-31）。上流が更新されたら再確認が必要。

- **画像は ComfyUI の API で受け渡される。** Discord の添付画像は ImageSmith がメモリに読み込み、ComfyUI の `POST /api/upload/image` へ直接アップロードする（`src/bot/imagesmith.py` `_upload_and_prepare_workflow`、`src/comfy/client.py` `upload_image`）。ComfyUI は受け取った画像を **ComfyUI 自身の input フォルダ** に保存し、その名前を応答で返す。
- 応答の名前は、設定の `image_input_node_id` で指定したノードの `inputs.image` に入る（`src/comfy/workflow_manager.py` `update_workflow_nodes`）。ノードが無い・`image` 入力が無いときはエラーになる。LoadImage ノードの ID を指定する。
- `/reforge`（img2img）と `/upscale` は、どちらもこのアップロード処理を使う。
- **`input_dir` は画像の受け渡しに使われない。** 上流は起動時にそのフォルダを作ってログに出すだけ（`workflow_manager.py` の `__init__`）。ImageSmith と ComfyUI の input フォルダが別でも動作に影響しない。生成される設定の `input_dir: "input"`（ImageSmith フォルダ内）は変更しない。上流の README・設定例にある「ComfyUI の input フォルダを指定する」は、このコミットでは意味を持たない。ComfyUI のパスを書くと、パスの誤りで別の場所にフォルダが作られたり、日本語を含むパスで設定を生成できなかったりするため、書かない。
- ComfyUI 側の input フォルダには、アップロードした画像が `image`、`image (1)`、… の名前で **たまっていく**（内容が同じ画像は再利用される）。削除はユーザーの判断で行う（Skill は削除しない）。
- `/api` 付きの接続先を使うため、`/api` に対応していないごく古い ComfyUI では失敗する（0.43.2 で対応をコード上確認）。
- **`/reforge`・`/upscale` の実機での動作は未検証。** 正式な対応は txt2img（画像・動画）だけ。使う場合は「試験的」と伝え、ComfyUI で実際に生成できる img2img／upscale ワークフローを W1〜W2 の手順で用意する。
