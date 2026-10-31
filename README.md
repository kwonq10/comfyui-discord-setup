# comfyui-discord-setup

**自宅の Windows PC で動く ComfyUI を、Discord（スマートフォン含む）から操作できるようにする Claude Code 用 Skill**

> **ステータス：v0.1.0-beta（ベータ版）** — 作者の環境で実機検証済みの範囲と、未検証の範囲があります（[検証状況](#検証状況)）。自己責任でお試しください。

Discord Bot には OSS の [ImageSmith](https://github.com/jtyszkiew/ImageSmith) を使います。この Skill は Claude Code が **1ステップずつ案内しながら** 次を行うための手順書と補助スクリプトのセットです。

- PC の状態（Windows・メモリ・GPU・Python・Git・ComfyUI・既存の ImageSmith）を読み取りだけで診断
- ImageSmith を公式リポジトリから **固定コミットで** 取得し、専用の仮想環境に導入（承認制）
- Discord Bot の作成・設定・招待を1画面ずつ案内（既存 Bot の再利用も可。トークンは Claude Code に見せない）
- ComfyUI 接続・ワークフロー・設定ファイルの検査（GET のみ）と、設定ファイルの生成（承認制・自動バックアップ）
- 画像・動画生成前のメモリ確認、エラー診断、中断と再開

設定が終わると、外出先のスマホの Discord で `/forge` を送るだけで、自宅 PC が生成して Discord に返します。

**方針：自動化よりも、安全性・やり直せること・初心者の分かりやすさを優先します。** インストール・設定の書き換え・生成など影響のある操作の前には、必ず Claude Code があなたに確認します。

---

## 動作条件

| 項目 | 条件 |
|---|---|
| OS | **Windows 11**（Windows 10・macOS・Linux は未検証／対象外） |
| Claude Code | Skill 機能が使えるもの |
| Python | **3.12**（ImageSmith 用。補助スクリプト自体は標準ライブラリのみ） |
| Git | Git for Windows |
| ComfyUI | 導入済みで、**ComfyUI 単体で生成できること**（ComfyUI 本体・モデルの導入は対象外）。作者は Comfy Desktop 0.43.2 で確認 |
| GPU / メモリ | NVIDIA GPU 推奨。作者は RTX 3060 12GB・RAM 16GB で確認（動画は 16GB でぎりぎり） |
| Discord | 自分が管理できるサーバー |
| ネット接続 | ImageSmith と依存パッケージの取得時 |

この Skill には **ImageSmith も ComfyUI も同梱していません**。ImageSmith はインストール時に公式リポジトリから取得します（コミット `dcc08071e6e6c8b35aeb971b829e8a3f122790e7` に固定・取得後に照合）。

## インストール（Claude Code への Skill 追加）

個人用 Skill として、次のフォルダに置きます（フォルダ名は `comfyui-discord-setup`）。

```
%USERPROFILE%\.claude\skills\comfyui-discord-setup\SKILL.md
```

**方法1：git clone**（PowerShell）

```powershell
git clone https://github.com/kwonq10/comfyui-discord-setup.git "$env:USERPROFILE\.claude\skills\comfyui-discord-setup"
```

**方法2：ZIP**：GitHub の「Code → Download ZIP」で取得し、展開したフォルダを上記の場所に `comfyui-discord-setup` という名前で置きます（中に `SKILL.md` があればOK）。

置いたら Claude Code を起動し直してください。特定のプロジェクトだけで使う場合は、そのプロジェクトの `.claude\skills\comfyui-discord-setup\` に置くこともできます。

## 使い方

Claude Code に話しかけるだけです。

> 自宅PCのComfyUIをDiscordから操作できるようにして

または `/comfyui-discord-setup`。続きからは `/comfyui-discord-setup resume`、診断だけなら `/comfyui-discord-setup diagnose <症状>`。
「読み取りだけで診断して」と伝えると、ファイルを一切作らない読み取り専用モードで動きます。

### 導入の流れ（7段階）

| 段階 | 内容 | 操作する人 |
|---|---|---|
| Phase 1 | PC の診断 | Claude Code（読み取りのみ） |
| Phase 2 | ImageSmith の用意（既存の再利用 または 新規導入） | Claude Code（**承認後**） |
| Phase 3 | Discord Bot の用意（新規作成 または 既存の確認） | **あなた**（1ステップずつ案内） |
| Phase 4 | ComfyUI との接続・ワークフロー・設定ファイル | 検査は Claude Code、書き出しは**あなた**、作成は**承認後** |
| Phase 5 | PC の Discord から画像を1枚生成 | **あなた**（承認後） |
| Phase 6 | スマホ（モバイル回線・外出先）から生成 | **あなた** |
| Phase 7 | 動画生成（対応ワークフローがある場合） | **あなた**（制限の説明後・承認後） |

あなたの手で行う操作：Python・Git の導入（無い場合）、ComfyUI の起動とワークフローの API 形式での書き出し、Discord Developer Portal での操作、**Bot トークンの登録（自分の PowerShell で。Claude Code には見せない）**、Bot の起動、Discord からの生成。

## 検証状況

v0.1.0-beta 時点（作者の Windows 11 PC 1台）。**「実機」は作者の環境での確認であり、あなたの環境での動作を保証するものではありません。**

| 機能 | 状態 |
|---|---|
| 環境診断（Phase 1） | 実機で確認 |
| 既存 ImageSmith の再利用判定（Phase 2） | 実機で確認 |
| **ImageSmith の新規導入**（clone → コミット固定・照合 → venv → pip → pip check） | 実機で確認（空のテスト用フォルダへ。依存27パッケージが確認済み版と一致） |
| 導入の途中失敗時の説明・`--resume` による再開 | 自動テストのみ（実機での失敗・再開は未検証） |
| 設定ファイルの生成（`make-config`） | 実機で確認（上流 ImageSmith の読み込み処理でエラーなし） |
| 既存 Discord Bot の確認（Phase 3 再利用） | 実機で確認 |
| **Discord Bot の新規作成**（Phase 3） | **未検証**（公式ドキュメントに基づく手順書のみ） |
| ComfyUI API 接続・ワークフロー検査（Phase 4） | 実機で確認（Comfy Desktop） |
| Discord からの画像生成 `/forge`（Phase 5） | 実機で確認（既存環境） |
| スマホからの操作（Phase 6） | この Skill 経由では未検証 |
| 動画生成（Phase 7） | 実機で確認（約9分の生成で Discord に返信・再生できた） |
| `/reforge`・`/upscale`（画像入力） | **未検証（試験的）** |
| Comfy Desktop 以外（portable・手動導入）の ComfyUI | 未検証（検出ロジックは自動テストのみ） |
| 別の PC・クリーンな Windows | 未検証 |

自動テスト：209件（`python -m unittest discover -s tests -t tests`）。

## セキュリティ

- **Bot トークンは、チャット（Claude Code を含む）やスクリーンショットに絶対に出さないでください。** 登録はあなた自身が PowerShell で Windows のユーザー環境変数 `DISCORD_TOKEN` に行います（画面に表示されない方式）。設定ファイルにはトークンを書きません。万一貼ってしまったら、Developer Portal の「Reset Token」で作り直してください。
- ユーザー環境変数は、同じ Windows ユーザーで動くプログラムから読めます。**共用PCでは使わないでください。**
- ComfyUI を **インターネットに公開しません**（接続先は `127.0.0.1` のみ）。外出先から使えるのは、自宅 PC の Bot が Discord に接続しにいく仕組みだからです。ルーターのポート開放は不要で、しないでください。
- Bot には最小限の権限だけを付けます（Administrator は付けない）。生成できる人とチャンネルは、設定でワークフローごとに必ず制限します（ユーザー名・チャンネル名で照合する上流の仕様）。
- 進捗記録（`%LOCALAPPDATA%\comfy-discord-skill\setup_progress.json`）や導入記録には、秘密情報・ユーザー名・URL を保存しません。
- 外部コマンドの出力は、トークン・パスワード・APIキーらしい文字列を伏せて表示します。

## 既知の制限

- **動画の長時間生成**：Discord はコマンドへの返信を **15分まで** しか受け付けません。生成が15分を超えると、ComfyUI では完成しても Discord に届かないことがあります（ImageSmith 標準に対策なし。この Skill も未検証の修正は入れません）。
- **メモリ**：RAM 16GB では、動画生成の前後にメモリの余裕がほぼ無くなります。生成前にメモリを確認し、アプリを閉じるのはあなた自身です（Skill はアプリ終了・メモリ解放をしません）。
- **添付サイズ**：Discord の添付ファイル上限を超える動画は届きません。
- **画像入力（/reforge・/upscale）**：試験的です。画像は ComfyUI の API に直接アップロードされ、ComfyUI の input フォルダに `image`、`image (1)` … とたまっていきます。
- **PC の電源**：外出中に PC がスリープ・再起動すると使えません。
- **同時起動**：同じ Bot を2か所で同時に起動しないでください（二重に返信することがあります）。
- **上流の既知の不具合**：settings 単位の権限判定が効かない不具合が ImageSmith にあります（ワークフロー単位の権限は正常）。必要な場合だけ、承認のうえで1行修正できます。
- 個人利用（自分の PC・自分のサーバー）を想定しています。

## よくあるエラー

| 困りごと | よくある原因 | まず試すこと |
|---|---|---|
| ComfyUI に接続できない | ComfyUI が起動していない／ポート違い | ComfyUI を起動してから、もう一度 |
| Bot がオンラインにならない | トークンの登録ミス、登録前に開いた画面で起動、Intent が OFF | 新しい PowerShell で起動し直す。Developer Portal の Bot ページを確認 |
| `/forge` が出てこない | Bot が起動していない／コマンドの反映待ち／Discord アプリの再起動が必要 | Bot のログに `Registered N Discord commands` が出ているか確認し、Discord アプリを再起動 |
| 「権限がない」と出る | 設定したユーザー名・チャンネル名の違い（表示名ではなくユーザー名） | Discord の「マイアカウント」でユーザー名を確認 |
| モデルが見つからない | ワークフローのモデルが ComfyUI に無い | ComfyUI 画面でモデルを選び直して書き出し直す |
| 動画が Discord に届かない | 15分の制限／ファイルが大きい | ComfyUI の出力フォルダを確認。短い・軽い動画にする |
| ImageSmith の導入が途中で失敗した | ネット接続・空き容量など（表示の「次にすること」を参照） | 原因を解消して、承認のうえ `--resume` で続きから（途中のものは自動削除しません） |

## フォルダ構成

```
comfyui-discord-setup/
├── SKILL.md              Claude Code が読む手順書（本体）
├── README.md             このファイル
├── CHANGELOG.md          変更履歴
├── LICENSE               MIT License
├── references/           段階ごとの詳しい案内（Discord・ワークフロー・動作確認・トラブル対応）
├── scripts/              補助スクリプト（Python 標準ライブラリのみ）
├── templates/            設定 spec の例・確認済みパッケージ版・進捗のひな形
└── tests/                自動テスト（tests/live は実機・読み取り専用、既定でスキップ）
```

## 開発者向け：テストの実行

```powershell
python -m unittest discover -s tests -t tests
```

- 標準の python では PyYAML 依存のテストなどがスキップされます。ImageSmith の仮想環境の python で実行すると全件実行されます。
- 上流 ImageSmith のコードとの照合テストは、環境変数 `IMAGESMITH_SRC` に ImageSmith のフォルダを指定したときだけ動きます。
- テストは実際の git・pip・Discord・ComfyUI に接続しません（実機テスト `tests/live` は `COMFY_SKILL_LIVE=1` のときだけ、読み取りのみ）。

## サードパーティ

- **ImageSmith**（https://github.com/jtyszkiew/ImageSmith）：上流 README には MIT と記載されていますが、リポジトリに LICENSE ファイルがありません（2026-10-29 確認）。この Skill は ImageSmith のコードを再配布せず、利用者の PC で公式リポジトリから取得します。
- ComfyUI・Discord・Claude Code は、それぞれの提供元の製品です。この Skill はそれらの提供元と関係のない非公式のツールです。

## ライセンス

この Skill（このリポジトリに含まれるファイル）は [MIT License](LICENSE) で公開しています。Copyright (c) 2026 Kenichi Nakatani

**ImageSmith・ComfyUI 本体には、このリポジトリの MIT ライセンスは適用されません。** それぞれ提供元のライセンスに従います（この Skill はそれらのコードを含まず、利用者の PC で各提供元から取得・利用します）。
