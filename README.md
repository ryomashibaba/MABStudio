# MAB Studio v0.3 — ブラウザー不要・アプリ内ネイティブ3D表示

**Windowsの推奨起動：`START_MAB_STUDIO.bat` を1回ダブルクリックするだけ。** Python 3.13 (64-bit) があるWindows 11を前提に、初回だけ既存の承認済みVTK 9.6.2とPillowをStudio専用`.venv-native`に自動導入します。インターネット接続が必要なのは初回の導入時だけです。続けて合成建築64セルによる**ブラウザー不要のネイティブ3D・視点切替・断面自動QA**を行い、成功した場合はウィンドウだけで起動します。次回は同じ描画コードと依存環境ならQAを再実行しません。`RUN_STUDIO_NATIVE.bat`も同じワンクリック起動に対応しています。

- ネイティブ3Dが準備できない場合は、**高精細表示できたと偽らず**従来の軽量Tk 3Dで自動起動します。ログと判定は `.studio-startup/last-startup.json` 等にローカル自動保存され、送信されません。
- 初回の依存関係導入は無料ですが、インストール容量とネットワーク利用が発生します。管理者権限・全体Pythonの更新・有料APIは不要。ローカルAIの推論やMinecraftへの接続・書込みは起動前診断で一切行いません。
- Windows実機上でこの新しい起動処理は**まだ未検証**です。Linux上のTk/VTK自動QAが通ったことはWindowsでの動作証明ではありません。

- Studioの3Dエリアに**ネイティブOpenGL/VTK描画を直接表示**し、外部ブラウザー、WebView、CDN、WebGLやHTMLスナップショットを使いません。実装はVTKのオフスクリーン3Dレンダリング結果をTkinterに表示する方式で、VTKのOpenGLコンテキストとTkの画像表示が必要です。物理GPUを使えているかはWindows実機で未確認です。
- 6方向すべての境界面をマテリアル別に正確に生成。ブロックの継ぎ目、構造の輪郭、方向別の陰影、二方向照明、地面の**視覚的なグリッド**、3D深度バッファー、画面比率対応自動フィットで従来より構造を読みやすくしました。背景グリッドは実際のMinecraftブロック/建築SOURCEには**追加されません**。
- ドラッグで回転／Shift＋ドラッグ・右ドラッグで移動／ホイールでカーソル位置を中心に拡大／ダブルクリックまたは`F`で全体表示／`1`～`6`で視点切替。断面表示と設計の修正案・履歴の従来機能を維持します。画質は実ブロックテクスチャやPBRを再現したものではありません。
- プレビューの「軽量3Dに切替」で従来Tk表示へ即座に戻れます。VTKがない環境でも`RUN_STUDIO_SOURCE.bat`は従来どおり起動可能。GUIボタンからブラウザーを起動する経路は廃止しました。
- 生成済みSOURCEの座標・色・断面以外の情報をレンダラーへ渡しません。既存SOURCE/未承認修正案/保存プロジェクト/Minecraftワールドは描画によって変更されません。真正AI生成を繰り返す必要はありません。
- 2026-11-17のLinux + VTK 9.6.2 + Xvfb実描画：合成3,539セルのテスト建築で3D生成と6方向表示を確認。初回表示約0.78秒、視点切替約0.10～0.15秒（Linux試験環境のみ。Windows GPU性能ではありません）。**Windows実機での最新ネイティブ表示・配布EXEは未検証**です。

従来のHTML/WebGL版ファイルは履歴・既存テストとの互換性のため残していますが、今のStudio画面からは呼び出しません。既存の旧HTMLファイルやブラウザーを削除する必要はありません。

---

## 2026-11-17 — 真正Gemma成功後のStudio引き継ぎ（v0.3継続）

2026-11-17 のユーザー実機検証で LM Studio `gemma-4-26b-a4b-it-qat` が**真正B143を通過**し、1,168 SOURCEセルをTkinter 3Dに描画しました（45.34秒、11要素）。ただし**その実際の生成JSONは診断ログに保存されていなかったため、1,168セルの建築本体は復元できません**。品質・指示への忠実性・Minecraft施工は未検証です。

今回 `RUN_SOURCE_REAL_MODEL_CHECK.bat` は、真正AIでSOURCEと3Dの両方に合格した場合、`--save-to-studio` により**新しいローカル建築プロジェクト**を保存します。モデルへの再リクエストは行いません。元の履歴は削除せず、ユーザーの編集中に履歴が変われば安全停止します。MAB Studioを閉じてから実行し、成功後Studioを開くと、保存設計を固定B143で再検証して3D表示・AI修正できます。既存の診断だけを実施したい場合は `--save-to-studio` を付けずに `python -m app.acceptance_cli --acceptance ...` を実行してください。模擬AIとCI用エンジンでは保存を禁止します。**保存する設計には日本語の建築内容が含まれます**が、外部送信は行いません。SOURCEの保存・復元はMinecraftの施工・Rollbackではありません。

## 2026-11-17 — B143形状別JSON生成契約の改善（v0.3継続）

真正Gemma 4のSOURCE受入が `B143_TITLE_OR_BRIEF` に到達した後、タイトル・説明の限定補正だけでなく、次の失敗要因を予防するためにAI出力SchemaとB143の形状ごとの契約を照合した。旧Schemaは形状固有の座標や `add` の `material` を要求せず、`cut` に不正な `material` を許容していた。

- AI出力Schemaを `box` / `ellipsoid` / `cylinder` × `add` / `cut` の**6つの排他的な選択肢**へ変更。各形状の必要座標、`add`の必須材質、`cut`の材質・shell禁止、余計なキーの禁止を指定。初回は18要素、既存SOURCE修正では元の規模に応じた最大160要素を維持。
- LM Studioの構造化出力Schemaは文法制約として渡されるため、その全文をチャットの入力トークンとして二重計上しないよう出力予算の概算を調整。出力枠・モデルのロード設定・VRAMを無断変更せず、途中終了は引き続き採用しない。
- SOURCEが失敗した場合に、**要素番号・必須キー不足・余計なキーの件数のみ**を `candidateFieldDiagnostics` に記録。指示文、生成JSON、未知キーの内容、座標、材質は保存しない。構文上のヒントであり、独立したSOURCE合否判定ではない。
- 固定SHAの真正B143で6種類の形状契約の実コンパイルを確認。Linux仮想Tk画面と回帰テスト **194 PASS / 0 SKIP / 0 FAIL**。**真正Gemma 4での新Schemaによる生成成功は未確認**。受入成功や建築品質の改善を主張しない。

WindowsではこのZIPを別フォルダへ展開し、既存のロード済みモデルを使って `RUN_SOURCE_REAL_MODEL_CHECK.bat` を1回実行できる。PyInstallerの再実行やワールド操作は不要。これはWindows EXEそのものを更新するものではなく、受入成功後にのみ既存EXEへの反映を検討する。

## 2026-11-17 — LM Studio 6209-token genuine-model stop: source-only retest

The Windows REAL_MODEL_CHECK reached the already loaded Gemma but failed at
`LOCAL_MODEL_REQUEST` after the output limit (6209 tokens, context 8192, ~322 s).
That is not an MAB SOURCE rejection or an Education issue. Model response text
was not captured, so the exact cause of the long output remains unknown.

This patch changes the **first** model-authored B143 candidate to a compact
18-element maximum (10–14 recommended, grouped window bands and structural
features) with short title/brief/element IDs. It does not alter the real B143
engine's 160-element limit, and it preserves full allowed title, brief and
existing-element counts for revision candidates. The model output must still
pass the original pinned MAB compiler; aesthetic quality is not certified.

For the next attempt use `RUN_SOURCE_REAL_MODEL_CHECK.bat` after extracting this
ZIP and loading **one** local model. It uses the already installed Python and
Node.js, runs a single real-model request and writes a SOURCE-only report under
`%LOCALAPPDATA%\MABStudio\diagnostics\source-model-*`. No PyInstaller
rebuild, model load/unload, Minecraft writes, or world connection occurs. The
console displays character counts, not your prompt or model response. The
source-only success is **not** a new Windows EXE success. Once it passes, build
one updated EXE using the existing Windows QA script. If the model still ends
at the output limit, record JSON text versus reasoning character counts from
the error rather than increasing context by default.

# MAB Studio v0.3 — Windows実用化パッチ（2026-11-17）

**元のv0.3を継承した改良版であり、新しいMAB本体・バージョン体系ではありません。**

2026-11-17時点の **Windows向け開発プレビュー**。既存MAB V11研究とは独立。追加課金0円を前提。**Minecraft Educationへの施工は未実装・無効**。


## 2026-11-17 横断デバッグ＋GUI再設計＋モデルアンロード（v0.3継続）

- **通常画面は「ローカルAIモデルを選ぶ → 日本語で建築を指示する → SOURCEを3Dで確認・部分修正」に集中**。固定B143の導入、GitHub追跡・更新、接続診断、Education階段の読み取り、`.mcworld`の取り込み、履歴詳細は右上の「詳細設定・接続診断」から開くオーバーレイに移動。保存済み建築は右上から、SOURCE JSON保存は3D側から直接利用可能。
- **修正案の承認・却下は修正案があるときだけ表示**。承認前のSOURCE履歴/原本/ワールドは変更しない。3Dプレビューを広くするため左入力列を固定幅に調整。通常画面には施工未許可を明記。
- **「選択モデルをアンロード」ボタンを追加**。Ollamaは実際にロード中の選択モデルを `GET /api/ps` で確認し `POST /api/generate {model,keep_alive:0,stream:false}` で解放。LM Studioはネイティブv1の `GET /api/v1/models` から選択モデルの単一ロードインスタンスを特定し、`POST /api/v1/models/unload {instance_id}`。どちらも操作後にロード状態を再確認。モデル名未選択・別モデル・不明な複数インスタンス・未ロードは操作しない。`--all`やサーバー停止を使わない。モデル選択は保持し、自動再ロードや推論は行わない。
- **生成や検証が進行中はアンロード禁止**。修正案が完成し承認待ちになっていれば、保持したDRAFTを消さずにアンロードできる。アンロードはアプリ内の設計やMinecraftのワールドには触れない。LM Studioの管理用v1 REST APIが存在しない旧版・認証が必要なサーバーでは、安全に停止しエラーを表示する（自動アップグレードしない）。
- 横断確認対象：SOURCE真正B143、生成/通信、承認DRAFT、履歴復元、3D断面・描画、Education読取の安全性、配布経路。**新GUIのWindows実機ビルドおよび真正AIでの生成・モデルのアンロードはまだ未検証**。従来Windows QAではEXEの起動/917セル描画/模擬AI通信が成功、2026-11-17の本人実機テストでは真正LM Studio生成はトークン上限とタイムアウトで未成功。改善版での再確認が必要。
- Linux仮想GUI・固定SHAの真正B143・モデル管理APIの模擬応答で、ユーザー操作と危険な誤アンロードの拒否を確認。**モデル管理の模擬テストは、実Ollama/LM Studioのメモリ解放実証ではない**。

## 2026-11-17 実機LM Studioの途中終了修正（v0.3継続）

- 実機で `finish_reason=length` が報告された。従来の固定 `max_tokens=4500` を修正し、LM Studioの読み取り専用 `/api/v1/models` で**選択したロード済みモデルのコンテキスト長**が分かる場合、プロンプト等の余裕を確保して出力予算を調整する（最大8,000、8k文脈なら目安6,000強）。情報取得に失敗した旧サーバーでは既定6,400。Ollamaも6,400。Studioはモデルのロード/設定変更を行わない。
- 応答がトークン上限で途中終了した場合、**MAB SOURCEは未実行**であることを明示し、同条件の2回目の高負荷推論を中止。SOURCE妥当性を保証するB143の本物の検証と、受け取り途中のJSONを分けて扱う。JSONが不完全な場合も部分建築を承認しない。
- Linux/Xvfb＋真正B143とローカル模擬モデルの回帰テストを実施。**本物のGemma 4の推論品質・8k以上での安定生成はまだ未検証**。WindowsネイティブEXEの前回受入PASSは、旧版の実績。新修正を利用するには最新ソースをビルドし直す必要がある。

## Windows native 1-action test (isolated, no model load)

`RUN_WINDOWS_NATIVE_QA.bat` performs a **non-interactive, isolated** Windows build of the actual one-directory EXE, downloads only the pinned B143 file and verifies its SHA, launches the EXE to compile/render 917 real source voxels, then exercises the packaged EXE through a deliberately marked **test-double** HTTP server on an ephemeral loopback port. The test double does not occupy Ollama's 11434, and its PASS is never recorded as genuine model inference. Output and a ZIP of the EXE live under `%LOCALAPPDATA%\MABStudio\diagnostics\windows-native-YYYYMMDD-HHMMSS`. The original Studio source and prior `dist` are untouched. The script creates an isolated project virtual environment to build; it may download PyInstaller, SocketBE and a fixed GitHub B143 module. **It does not start a real model, connect to Minecraft, or write to any world.**

This only tests native EXE packaging and deterministic SOURCE/GUI functionality; it does not prove unknown-language understanding, visual quality, Education connectivity, licensing, or construction permission. The CI fixture is explicit `--test-double --fixture-port PORT --model CI_TEST_DOUBLE` and must not be reported as a true provider invocation. To test a real loaded local model, use the existing `RUN_WINDOWS_ACCEPTANCE.bat` separately; the model inference can be heavy.

## 2026-11-17 配布ランタイム整合性の追加検証（v0.3継続）

- Windowsビルドは、同梱 `node.exe`（22以降）、Node権限モード、Education読取ブリッジの5コマンド自己試験、SocketBE 2.8.0のモジュール読込を**配布フォルダ内の実体で**確認し、失敗時は配布を停止する。
- ビルドに成功した場合だけ `dist\MABStudio\RUNTIME_MANIFEST.json` を生成し、EXE、同梱Node、ライセンス通知、MAB接続コード、Educationランタイム各ファイルのSHA-256を保存する。EXE起動時にファイル欠損・差し替え・依存内容の追加を検出したら、安全停止する。この仕組みは**偶発的な破損検出**であり、デジタル署名、ソフトウェア供給元の真正性、第三者ライセンス審査を代替しない。
- Linux環境でNode.js 22を用いた配布監査の実行経路を確認（SocketBEはテストダブル）。さらに固定MAB B143の原本をGitHubから確認して実SHA-256 `537aa9e4...` に一致させ、Linux/Xvfbで全回帰 **122 PASS / 0 SKIP / 0 FAIL**。この固定B143ファイルは配布ZIPに含めず、ユーザー初回起動時に検証済みSHAで取得する。
- **Windows EXEの本当の起動・Windows Actions実行・真正SocketBEの配布状態・真正Ollama/LM Studioによる未知指示・Minecraft Education実機は依然未確認。** 現環境から完了を主張せず、Windows側で一括受入検証する。Minecraftワールド書込機能は追加していない。

## 2026-11-17 Windows配布の起動安定化（v0.3継続）

- `start_windows.bat` は **ビルド済み `dist\MABStudio\MABStudio.exe` を優先起動**する。配布版利用時にはPythonやPATH上のNode.jsを使わない。EXEがないソース開発環境だけ従来の`py -3 -m app.main`等へフォールバックする。単体の`MABStudio.exe`だけでは動かないため、**`dist\MABStudio`フォルダ全体**を配置する。
- 凍結EXEでは同梱 `node.exe` がなければ停止し、偶然PCにある別のNodeで動かない。起動前にNodeライセンス通知、MAB実ブリッジ、Education読み取り専用SocketBE部品の存在も確認する。これは静的な配布整合性検査であり、実Education通信やWindows動作の証明ではない。
- 同梱部品が欠けている場合、コンソールなしEXEでも理由をダイアログで示す。`--smoke`と`--acceptance`にも同じ配布整合性ゲートを適用するため、Windows CIのテストが**壊れた配布フォルダを外部Node依存で誤ってPASS**する経路を塞ぐ。
- Linux仮想画面のローカル試験は模擬パッケージを使用。**Windows向けEXE自体はこのLinux環境で作成・起動していない**。実Windows CIは独立したStudioリポジトリにコードを配置してから行う必要がある。MAB本体リポジトリへの変更・Minecraftワールド書き込みはゼロ。

## 最新のWindows初回導入・真正AI受入検証（2026-11-17 追加）

- 最初に使うB143は、**固定コミット `b0295c2f...` の単一 `b143-building-dsl.mjs`** だけを取得し、実バイトのSHA-256 (`537aa9e4...`) と192セルのSOURCE出力を検証して隔離保存する。従来版の保存済み確認済みエンジンは再ダウンロードしない。**最新ブランチの未審査SHAは画面へ通知するだけで導入できない**。原本MABやワールドへの書き込みはない。
- `RUN_WINDOWS_ACCEPTANCE.bat` をダブルクリックすると、EXEがない場合だけ**明示確認後**に隔離ビルドを実行。ユーザーが起動・ロード済みのOllama／LM StudioとモデルIDを選び、日本語指示を指定して**もう一度明示確認してから**ローカル推論を**1回**実行する。勝手にモデルをロードしない。実行中はPC負荷が高くなる可能性がある。
- 受入検証の正確な範囲：**実ローカルHTTP応答 → 確認済みMAB B143コンパイル → 同じTkinterプレビューモジュールで3D描画**。成否・モデル・コンパイルセル数・SOURCE SHA・処理段階を `%LOCALAPPDATA%\MABStudio\diagnostics\acceptance\windows-acceptance-*.json` に記録する。**指示文そのもの・モデルが返した建築JSON全文は保存しない**。この検証では設計履歴もMinecraftワールドも書き換えない。
- 受入PASSは、指定されたローカルサーバーから返った設計がコンパイルされ描画できたことだけを示す。モデルの同一性、未知日本語の意味理解、美しさ、実Educationの歩行・施工は**別途未検証**。GitHub Actionsで行う同経路試験は**固定JSONを返すテストダブル**であり、本物のLLM実証ではない。**このZIPのWindows CIはまだGitHub上で実行していない**。
- Python/Nodeを既にインストール済みなら、従来どおり `start_windows.bat` でStudio GUIを直接開いて利用できる。`RUN_WINDOWS_ACCEPTANCE.bat` は開発版EXEの一括確認用であり、建築の通常操作に毎回必要なものではない。

## 何ができたか

- 日本語入力 → ローカル Ollama / LM Studio → B143の本物の `validateDesign / compileDesign / verifyCompiled` → 3D表示（既存v0.2から継承）。
- **「現在の設計をAIで部分修正」**ボタン：元の設計JSONをLLMへ渡し、修正案を真正MABで再コンパイルする。修正案は承認するまで**一時DRAFT**として保持し、承認・却下できる。AIの修正内容そのものの品質を保証するものではない。
- 以前のソースモデルと新しいモデルの**追加・削除・材料置換・変更なし**を正確な SOURCE セルで比較して色別表示。これは **Minecraftの配置/実行計画ではない**。
- プロジェクト履歴を `%LOCALAPPDATA%\MABStudio\projects` に**追記型**で保存。保存ファイルのSHA-256照合、更新競合の検出、真正MAB再検証を条件とした「前の設計に戻す」を追加。復元は**アプリのプレビューだけ**で、Minecraftワールドは変わらない。
- Minecraft Educationから手動でエクスポートした **`.mcworld`** をアプリ内のファイル選択で取り込む入口を追加。ZIP中身の安全性を検査し、元ファイルを変更せず、実際のブロック抽出用の一時コピーで処理。外部へ送信しない。
- `amulet`（対応版・別途インストール）が使える環境向けに、選択座標周辺の小領域 (24×24×24) を読み取り専用でサンプリングするアダプターを実装。**対応ライブラリなし・Education固有フォーマット未対応の場合は、ブロック未読取として中止**する。地形を推測しない。
- 読み取れた地形と、建物の**仮の配置位置**を重ね、重複セルを警告。これはプレビュー上の概念的な重なりで、現場の建築承認ではない。

## Windowsで試す

1. `start_windows.bat` をダブルクリック。**`dist\MABStudio\MABStudio.exe`が存在する配布版ならそれを優先起動**し、Pythonは不要。**このZIPはソースなのでEXEは含まず**、ソース実行にはPython 3.10+、Node.js 22+ とTkinterが必要。
2. 初回起動時は、コミットと**実コードSHA-256が固定された確認済みB143**だけを、GitHubから自動導入・SOURCEスモーク検証する。ネットワーク失敗時は「MABを初期導入」を押して再試行できる。初回はGitHubへの接続が必要。導入済みの確認済みエンジンはオフラインでも動く。モデルは独立にOllama／LM Studioから取得する。
3. LLMで自由な設計を作る場合は、Ollama / LM Studioでモデルを読み込み、モデルを選択して文章を入力し「AIで設計して3Dプレビュー」。
4. 設計を表示したまま、入力欄に「窓を1ブロック高く」などと書いて「現在の設計をAIで部分修正」。変更セルが差分表示される。「前の設計に戻す」はSOURCEモデルの履歴復元。**今回の継続改良版では自動確定しない**。差分確認後「AI修正案を承認（設計のみ）」を押した場合だけ、元モデルと新モデルをSOURCE再検証してから履歴に追加する。「修正案を却下」なら元設計と保存済みファイルは維持する。B198Dの専用セッションエンジンそのものは未統合で、Tkinter上の一時DRAFTによる最低限のレビュー導線である。
5. **地形の取り込み（方針A）**：Minecraft Educationの「プレイ → マイワールド → 対象ワールド管理 → ワールドをエクスポート」で `.mcworld` ファイルを1回作成。アプリで「予定位置 XYZ」に予定場所のブロック座標を入力し、「.mcworldを読み取る」からファイルを選択。地形デコーダーを利用できる場合は「地形＋建築」表示へ。**`.mcworld` 自体をMinecraftに再インポートする必要はない**。
6. 地形ブロックが読み取れない場合、読み取れていない状態を画面に表示。**未知領域を空気として補完しない。** 3Dプレビューの表示は「標準／精細／高精細／全体メッシュ（境界面）」から選べる。従来の上限付き表示は最大15,000可視セル。「全体メッシュ」は**SOURCEの実際の露出面だけ**を材質別の矩形にまとめる。15,000矩形を超えると部分表示になり、その旨を画面に表示する。

## 11月17日の継続開発：レビュー優先・表示・接続診断

- 修正案は一時DRAFTとしてのみ保持。承認前は設計履歴、`latest-design.json` を変更しない。UIで「建築のみ／修正案（未承認）／変更差分」を切り替えて確認し、承認時には確定元とDRAFTを**同じ固定MABエンジンで再コンパイル**する。SOURCE履歴の承認は実ワールド施工の承認ではない。
- 大規模なソリッド建築の外形を劣化させない**境界面greedy mesh表示**を追加。実セルにない面は描かず、同一材質の連続した面だけを結合。40×40×40＝64,000セルの立方体を3つの露出面として実GUI描画。表示矩形が15,000を超える場合は間引きと明記。
- 「接続診断」でNode.js、SHA固定のB143エンジン、選択中のOllama／LM Studio APIからモデル一覧を読み取る。**モデル取得が成功しても真正推論・建築の品質は未確認**。
- 「B134読取結果」から、既存MAB `feature/b134-websocket-readonly-live-stair-probe` の読み取り専用診断JSONを形式検査して開ける。受け取った外部JSONは独立認証された現物証拠ではない。Studio自身は `/wsserver` 接続・Minecraftのブロック読取・施工・復元は**引き続き未実装**。B135〜B137にある限定実機結果を一般施工許可に転用しない。
- Linux仮想ディスプレイでGUIの承認/却下と大規模メッシュ描画を自動試験。**56 PASS・9 SKIP**（9件は今回の環境で真正B143の現物ファイルを提供できずSKIP）。WindowsネイティブEXEの生成・起動、実モデル推論・実Educationワールド接続は**未実証**。

## 11月17日のWindows実用化パッチ

- 起動時の固定B143自動初期導入、導入済みエンジンでのローカルモデル一覧取得を追加。追跡ブランチやMAB本体mainを勝手に更新しない。
- Tkinterプレビューのユーザー選択による描画精細度を 2,300／8,000／15,000 可視セルに拡張。**高精細ほど描画が遅い可能性がある**。真のWebGLや全セル描画ではない。
- Ollama/LM StudioがHTTPリダイレクトを返すと通信拒否。OSプロキシを回避し、ローカル日本語プロンプトの外部転送を防ぐ。
- Windows CIに **SHA-256検証済み真正B143** の実コンパイル、実Tkウィンドウ・917セル描画・Node同梱EXE起動スモークを追加。**CI用の定義を実装した段階であり、このZIPをGitHubへ配置してActions実行した証拠はまだない**。
- 同梱Node.jsのライセンスが見つからなければWindows配布ビルドを停止。正式な第三者配布の権利確認は引き続きHOLD。
- ローカル確認：Python単体試験 **39 PASS・9 SKIP**、Linux Xvfb上でTk GUIの起動・描画成功。**Windows EXEビルド・真正ローカルLLM・Education連携・実ワールド読取は未実施**。

Windows CIのソースを独立したMAB Studioリポジトリのルートに置き、Actionsから `Windows GUI + pinned SOURCE release smoke` を実行すると、真正エンジンをGitHubの固定コミットから受信してSHAを確認した後、Windows runner上でPyInstaller EXEをビルド・起動して検証する。**原本のMAB本体repoにはコミットしない。**

手動Windows開発ビルドは `build_windows.bat`（PyInstallerはプロジェクト内の隔離 `.venv-build` のみに導入し、システムのPythonパッケージを更新しない）。出力 `dist\MABStudio\MABStudio.exe` を起動し、Pythonがなくても利用できることは**Windows実機での検証後にのみ**確定する。

## 追加依存ライブラリと未検証範囲

`.mcworld` の `db/` はMinecraft Bedrock/Education系の **改変LevelDB**であり、ZIPを展開するだけでは正確なブロック配置は読めない。そこで実装を二段階に分けた。

- 標準機能: ZIP安全検査・世界名・ファイルのSHA-256・試験的地形ビューのデータ経路。追加依存なし。
- **オプション**: Amulet Coreの旧APIがインストール済みの環境では `amulet.load_level()` / `has_chunk()` / `get_block()` を用いる。追加依存は **配布用EXEに未同梱**。Python 3.13 / Minecraft Education 26.33 実ワールドでの互換性は **未検証**。そのため「完成した地形読取機能」とは表示しない。Amuletの新APIやMinecraft Education独自ブロックが非対応の可能性がある。依存ライブラリの導入・既存環境の変更はユーザー操作を最小化できる方法を今後検証する。
- 世界の座標系を利用するのは**読み取り済み地形との仮表示**に限る。B143の `BuildPlan.operations=[]`、`constructionApproved=false`、9ゲート BLOCKED を解除しない。
- 既存の MAB WebSocket 読み取り/小範囲設置実績は参考にするが、MAB StudioにMinecraft実機コマンド送信・差分施工・実ワールドの復元は **未接続**。
- プレイヤーがMinecraft内で手動変更したブロックとMAB設計の同期は未実装。地形の再取り込みも現状はファイルを選び直す。
- 更新機構はv0.2の確認済みB143ハッシュ照合・安全な隔離更新・ロールバックを継続。未承認の新エンジンSHAは導入不可。MAB本体main・研究ブランチ・ワールドには変更を加えない。
- MABリポジトリ自体は独自コードのライセンスが未指定。正式な第三者配布前に権利条件の確認が必要。アプリZIPにMABソースは同梱しない。

## 正確に確認したこと

```bash
MAB_B143_VERIFIED_ENGINE=/path/to/verified/MAB/B143 python -m pytest -q
```

- 真正B143コミット `b0295c2f5012877f57ae218ae791634794de7b52` の実コードを照合してテストに使用（ソースSHA256 `537aa9e4a2b4e027f16179ef7a306b604ca4d850713d6de23940667ed2348e16`）。
- サンプル図書館917セルから正面ガラスの高さを変更 → **14セルが石材からガラスに置換、903セルは不変**。これはAI推論ではなく、意図的な試験データから計算。
- `.mcworld` は**人工ZIPテスト**と**模擬ワールドリーダー**で検証。実際にEducationからエクスポートした `.mcworld` は未提供のため、実ファイルの地形読取を保証しない。
- Windows EXEのネイティブ実行は未検証。UIのLinux仮想画面確認はWindows互換性の証明ではない。

## 次の工程

Windows CIの実行、および真正Ollama/LM Studioモデルを用いた未知の日本語指示の通し試験が最優先。今回の開発環境はLinuxのため、Windows EXEの配布互換性を証明できない。配布版Windowsでのユーザー実機確認は、複数の重要検証をまとめて一度に行う。

Education WebSocket接続は、既存B134の検証済み実装を流用する方向。既存のB134診断JSONはStudioで形式検査できるようになったが、**直接接続の実装ではない**。既存資産の安全な依存管理・読み取り専用範囲の証明・実機証跡保存の設計が完了するまでライブ接続や施工ボタンを有効にしない。

最短で実用に近づけるには、(1) **Education 26.33の実`.mcworld`を使った読み取り検証** → (2) 対応版パーサをWindowsに安定同梱 → (3) ゲーム内ブロック状態のreadback+施工前ジャーナル+差分反映をテストワールドで数ブロックだけ実証 → (4) 実行済み建築を安全に再編集する。ファイルや操作が必要な段階まで、ユーザーに余計なセットアップを求めない。

公式ワールドエクスポート方法: https://edusupport.minecraft.net/hc/en-us/articles/360047555391-Import-Export-and-Manage-Worlds

Amulet旧API参考: https://amulet-core.readthedocs.io/en/stable/getting_started/blocks/basic_block_example.html

## Educationの直接読み取り（追加・テストワールド限定）

Studioの「Educationから直接読取（5照合）」は**設計・施工とは独立した実験用読み取り**です。既存のMAB B134のSocketBE 2.8.0/5件の`testforblock`契約を利用し、指定したオーク階段1個について試します。MAB本体の建築計画・SOURCE施工変換は使わず、MAB本体は改変しません。実機Minecraft Educationでのこの新経路の動作は**未検証**です。

Windows EXE版にはビルド工程でSocketBEをアプリ内の隔離フォルダに含めます。Pythonソース版の初回利用時だけ、利用者が明示的に`prepare_education_readonly.bat`を実行してください（`app/education_runtime/`以外にnpmパッケージを導入しません）。SocketBE 2.8.0はMITライセンスですが、正式配布前に同梱依存関係の通知を確認してください。追加課金は不要です。

1. **専用テストワールド**で既存のオーク階段1個のXYZを確認し、Studio右上の「予定位置 XYZ」に入力します。
2. 「Educationから直接読取（5照合）」を押し、確認画面を読みます。**一度にStudioまたはB134のどちらかだけ**8000番ポートを利用できます。
3. ゲーム内で `/wsserver 127.0.0.1:8000` を実行後、チャットで `!mab-read` を送ります。
4. Studioは`testforblock`5照合の応答をB134互換JSONとしてアプリデータの`diagnostics/education`に保存します。`UNKNOWN`や未確定を推測で上書きしません。待機は約2分半で終了し、画面から停止できます。

これは*指定された1地点での読み取りコマンド応答*に過ぎず、ワールドID・ブロックの独立認証、周辺地形一括読取、施工可能性、Minecraft衝突認証を意味しません。`setblock`/`fill`などの施工経路は含まず、ワールドのRollbackも提供しません。通常の建築作業は引き続き設計のみ（`NOT_PREPARED`、9 BLOCKED、`BuildPlan.operations=[]`）です。EXEのWindows実機検証と真正Minecraft接続はまだ未実施です。


## 地形のセル単位照合・3D警告表示（2026-11-17 継続工程）

既存の `.mcworld` エクスポート方式Aを維持し、**真のワールド操作やSOURCE施工を追加せず**、以下を実装した。

- 既知の実ブロック、読み取れた空気、読み取れなかったブロック位置を**それぞれセル単位の座標一覧**として保持する（最大 32×32×32 セル、標準 24×24×24）。読み取り領域外のセルも別途集計する。総数・重複・範囲・カテゴリーの相互排他を検査し、データが矛盾すれば表示を中断する。
- Studioの地形オーバーレイで、設計セルを **赤＝地形ブロックとの重複候補、紫＝位置を読めなかった、黄＝読み取り範囲外、通常材質色＝空気と読めた** の4区分で表示する。過去の位置情報がない地形スナップショットでは **灰＝セルの空き状態が未立証** とする。これはモデルの表示色の変更だけであり、ワールドにブロックを置いたり空気を創作したりしない。
- 建築モデルがある場合、読み取り範囲を 24〜32 セルで自動調整し、予定基礎位置より下 4 セル前後と上部を優先する。`plannedAnchor`（建物の仮配置）と `center`（読取範囲の中心）を分離し、照合時にアンカーの一致を厳格検査する。
- 予定位置XYZを入力し直すと**以前の地形の重ね合わせを即時解除**する。同じ座標に戻す場合のみ新たに照合でき、別座標はエクスポート済みファイルの再読込が必要。
- 衝突0でも、未読取・範囲外が残れば「建築可能」や「施工可能」とは表示しない。地形の配色・境界面表示は、ブロック状態、衝突、他のプレイヤーの更新、実Minecraftの配置認証を意味しない。

**検証状況：** Linux Xvfb上のGUIと模擬ワールドリーダー・安全なZIPコピー、読み取り専用Nodeコマンドの試験を実施。真正Minecraft Education 26.33の実エクスポート `.mcworld` は未提供、Amuletの実API接続・Windows EXE・ゲーム内同期・実モデルは引き続き未実証。Windows配布版EXEにAmuletは未同梱であり、依存ライブラリがない環境ではブロック読取を実行せず明示的に停止する。**真正B143の9試験は専用ソースを与えられない環境ではSKIPであってPASSではない。**

## 2026-11-17 継続工程 — ローカルAIの構造化応答と旧建築の再オープン

**MAB本体・既存プロジェクト・Minecraftワールドの変更なし。** 最新の追加機能は、Studio側に限定した次の2点。

1. **Ollama／LM StudioのJSON Schema構造化出力**：B143の許可フィールド・型・形状・材料・上限などをローカル推論APIに伝達する。Ollamaは `/api/chat` の `format`、LM Studioは `/v1/chat/completions` の `response_format` を利用する。サーバーが構造化形式を明示的に拒否した場合に限り従来のJSONモードへ1回だけフォールバックする。400が返っても、モデル未ロードなど**形式未対応以外のエラーなら再送しない**。通信失敗時にGUIが2回の重い推論を自動実行しないよう変更。途中終了（`done_reason: length` / `finish_reason: length`）を成功扱いしない。公式仕様: [Ollama](https://github.com/ollama/ollama/blob/main/docs/capabilities/structured-outputs.mdx)、[LM Studio](https://lmstudio.ai/docs/developer/openai-compat/structured-output)。
2. **「過去の建築一覧」ボタン**：`projects/revisions/*.json` に元から残っていた別プロジェクトのSOURCEスナップショットを最大100件一覧表示。ファイルSHA・保存設計の安全条件を検査し、不正なものを開く候補に含めない。選択時に確認済みB143で`sourceSha256`と`geometrySha256`を再検証してからアクティブ履歴ポインタのみ切り替える。過去のスナップショットの削除・上書きなし。これは**Studio内部の設計選択**であり、Minecraftワールドの復元ではない。

ローカルテスト：**85 PASS / 9 SKIP**（Linux/Xvfb、Python 3.13、Node 22。後者9件は真正固定B143実体がこの環境にないため未実行）。HTTPモックサーバーの送信制約・互換性フォールバック・トークン打切り・エラー再試行拒否、SOURCE履歴の改変拒否、実Tkinter画面の旧建築選択をテスト済み。**真正ローカルモデルの未知の日本語建築指示、WindowsネイティブEXE、Minecraft Education実機、実 `.mcworld` は未実証**。JSON形式の安定化は建築の美しさや日本語意味理解の保証ではない。

MAB Studioの使い方は従来どおり。モデルの一覧を表示できるだけでは推論の実行を証明しない。初回にモデルサーバーを起動後、必要なモデルを指定して「AIで設計して3Dプレビュー」を実行する。前の建物を再開したい場合は右上の「過去の建築一覧」で対象の履歴を選択する。配布前のWindows実機・本人確認はまとめて実施する。

## 2026-11-17 実用性監査からの修正（新バージョンではない）

開発を管理・試験件数偏重から通常操作へ戻すため、次の実装をv0.3に加えた。

- **前回の承認済み建築を自動再開**：起動時、履歴SHAと固定MAB B143のSOURCE再コンパイルが一致したものだけ表示。AI推論・施工・履歴追加なし。不一致または壊れた履歴は原本を変更せず中断し、理由を画面に表示する。
- **「検証済み設計をJSON保存」を本物のエクスポートに変更**：以前は既存ファイルの保存先を案内するだけで、古いプロジェクトへ切り替えると表示と`latest-design.json`が不整合になり得た。今は表示中の**承認済み**SOURCEを固定B143で再検証し、選択した新規`.json`へ実際に保存する。既存ファイルを上書きしない。未承認DRAFT・MAB不一致を拒否する。保存ファイルは設計JSONであり、Minecraft施工ファイルではない。
- 保存済みプロジェクトの切替・SOURCE Undoでも、プレビュー用`latest-design.json` / `latest-scene.json` / `latest-receipt.json`を現在の承認済み画面へ合わせる。旧リビジョン原本は保持する。
- **自動15分GitHub更新チェックを終了**：Studioで許可されるB143コードはSHA固定で、別SHAは現状導入不可。バックグラウンド更新確認でGUI操作を妨げないよう、更新照会は「GitHub更新を確認」の明示操作時のみとした。安全なSHA照合・隔離切替・ロールバック方針は変更しない。

この工程は**新たなMinecraftワールド操作を作らず、MAB本体へのコミットも行わない**。UIを含む既存の92件と新規6件の合計**98 PASS / 9 SKIP**（Linux仮想ディスプレイ上、Python 3.13／Node 22）を確認。後者9件は真正B143コードが当環境に提供されていないため未実行。WindowsネイティブEXE／真正LLM／Education実機／実 `.mcworld` は依然として未実証。コード品質や模擬テストだけでこれらの成功を主張しない。

毎日の通常利用は`start_windows.bat`（Windows EXEの実機認証完了後は配布EXE）からGUI操作する。`RUN_WINDOWS_ACCEPTANCE.bat` は本物のモデルを使う**任意の一括受入試験**であり、建築のたびに実行する必要はない。

### 2026-11-17：内部断面表示・ローカルAIモデル選択の再利用

- **3D断面**：プレビュー上の「建築の断面を表示」にチェックを入れ、Yスライダーで上部を一時的に非表示にします。屋根の下や複数階の内部を、現在の表示データから確認できます。メッシュの切断面は**画面上の仮想面**であり、Minecraftに新しい床・屋根が存在する証明ではありません。解除すると全体表示に戻ります。SOURCE JSON、検証ハッシュ、設計履歴、実ワールドは一切変更しません。
- 大きな建築でスライダーを動かしたときの再描画を短時間まとめ、GUI操作の応答性を改善しました。ただし実Windowsでの体感速度は未計測です。極端に複雑な形状では従来の15,000面描画上限が適用されます。
- **AIモデル選択**：最後に選んだ `Ollama` / `LM Studio` とモデルIDを、アプリ専用 `preferences.json` にだけ保存します。パスワード、APIキー、日本語の指示文、モデル回答、Minecraft座標は保存しません。次回起動時の再選択を減らすための設定であり、モデルを自動ロードしたり推論を開始したりはしません。
- モデル一覧が変わって以前のモデルIDが見つからない場合でも、別モデルへ無断切替せず、一覧から選び直せる状態を維持します。
- オフラインGUI試験（Linux + Xvfb）は **104 PASS / 9 SKIP**。モックモデルを含むテスト結果であり、Windows EXE・真正LLMの自由文からの建築品質・Education実機接続・施工の認証ではありません。

> この更新も既存の **MAB Studio v0.3** の継続です。MAB本体の建築SOURCE生成・変換・施工ゲートは変更していません。

### Windows CMD/PowerShell encoding compatibility (2026-11-17 hotfix)
Windows `cmd.exe` on Japanese locales may interpret LF-only `.bat` files incorrectly.
All distributed `.bat` files are now ASCII + CRLF, with no UTF-8 byte-order mark.
`.ps1` files use CRLF; any PowerShell script containing Japanese has a UTF-8 BOM
for Windows PowerShell 5.1 compatibility. A build-time preflight rejects incorrect
`.bat` bytes **before downloading or installing any packages**. The failure
`'ll' is not recognized` / `'global' is not recognized` in the earlier ZIP was
observed after PyInstaller installed successfully; replace the whole ZIP with
this hotfix before rerunning `RUN_WINDOWS_NATIVE_QA.bat`.

To retry: extract into a **new** directory, not over the old extracted copy,
then launch `RUN_WINDOWS_NATIVE_QA.bat` once. It still uses an isolated work area
and emits `WINDOWS_NATIVE_QA.json` with a stage-specific failure reason. Passing
Linux regression tests cannot certify the native Windows build: that still
requires a Windows run. No model inference or Minecraft world writes occur.


### Windows native QA: Node.js license packaging hotfix (2026-11-17)

The normal Node.js Windows installation may have `node.exe` without a sibling `LICENSE`.
The build now copies its locally installed Node.js binary and either its colocated
LICENSE notice or the exact release tag's official `LICENSE` from
`https://raw.githubusercontent.com/nodejs/node/vX.Y.Z/LICENSE`. It fails closed
if no credible license notice can be retrieved. The independent runtime manifest
continues to hash the copied binary and notice. No license check is bypassed.
The `RUN_WINDOWS_NATIVE_QA.bat` acceptance entry point is unchanged.

### LM Studioで長い生成が途中終了／Qwenがタイムアウトした場合（2026-11-17）

- この開発版は**新規設計**の出力JSONを最大32要素に抑え、短い属性・箱/円柱/切り抜きで建築の主要部分を表します。B143本体の160要素上限は変更しません。既存建築の部分修正では原本要素数に応じて上限を引き上げ、省略を強制しません。
- LM Studioには1回の生成をストリーミングで依頼し、画面下部に生成中のJSON本文文字数を表示します。生成ストリームの無通信が300秒に達した場合、または総時間が15分に達した場合は中止します。待機・出力が長すぎる状況に対処するためのもので、実モデルの生成成功を保証しません。
- Qwen3.8のモデルIDに限り、公式モデルカードにあるリクエスト単位の `chat_template_kwargs.enable_thinking=false` を送ります。LM Studioの実行環境による対応差は実機確認が必要です。モデルの自動ロード/再設定/VRAM割当変更はしません。
- JSONの未完了・切断・検証不一致は失敗として処理します。失敗した生成からMinecraft書込コマンドを作ることはありません。AI修正案はDRAFTとして表示し、明示承認まで履歴を変更しません。
- Linux上のローカル試験ではLM StudioのSSEを模擬し、MABの固定B143原本でSOURCE検証しています。**真正Gemma/Qwenの生成、Windows上の新EXE実行、建築の美観・用途忠実度、Education実機施工は未実証**です。


## Windows: one real model check WITHOUT rebuilding (2026-11-17)

`RUN_WINDOWS_NATIVE_QA.bat` only proves a Windows EXE, pinned B143 SOURCE,
Tk GUI and a **TEST DOUBLE**. It does not test a genuine language model.
When the model is already loaded and you have selected it in the Studio GUI,
double-click `RUN_REAL_MODEL_CHECK.bat` from the source release folder.

**Loaded-model ID resolution (Windows diagnostic):** The GUI's `/v1/models` picker
may return an installed/JIT model ID, whereas `/api/v1/models` distinguishes
currently loaded model **instance** IDs. The real-model check only uses loaded
LLM instances: it matches the saved key/ID to exactly one loaded instance; if
there is exactly one loaded LLM but the saved choice is stale, it uses that
sole instance and records both identifiers in `REAL_MODEL_CHECK.json`.
With none loaded or multiple ambiguous candidates it stops with a clear error
and never triggers an automatic model load. This diagnostic fix does **not**
require rebuilding an already-passed Windows EXE; replace only the PS1 script.


The script reads `%LOCALAPPDATA%\MABStudio\preferences.json`, verifies
the actual currently loaded model through read-only provider APIs,
then finds the latest successful `WINDOWS_NATIVE_QA.json` and verifies its ZIP
SHA-256. It extracts that already-built EXE temporarily, sends exactly one
real-model building request using `--acceptance`, and saves a JSON report under
`%LOCALAPPDATA%\MABStudio\diagnostics\real-model-...\REAL_MODEL_CHECK.json`.
No PyInstaller, pip or npm installations, no automatic model loading,
no Minecraft commands, no world writes, no user designs committed, and no
model-generated JSON or prompt text stored in the diagnostic report.

A genuine model response might still fail B143, time out, or hit a token
limit; the receipt preserves the error category, exact stage and request
seconds. Do not confuse this with proof of Japanese intent understanding or
architectural visual quality. This check uses the existing fixed Japanese
diagnostic prompt from `app.acceptance.DEFAULT_PROMPT`; normal custom
Japanese creation remains in the GUI.

### Real-model check: bounded wait and accurate failure evidence

The existing `RUN_REAL_MODEL_CHECK.bat` does not rebuild the EXE. The checker
limits the one acceptance process to 20 minutes and terminates only the
acceptance process it started if it exceeds that bound. Model selection records
loaded instance IDs; if the saved choice differs from the sole loaded LM Studio
model, the mismatch is printed before inference. A failure *before* contacting
the provider is reported as NOT_ATTEMPTED, not as a genuine inference failure.
A successful receipt must match provider/model, approved-source and Tk proof,
and the unchanged world-write/BuildPlan safety gates. No automatic retries.

These changes are source-tested on Linux; Windows PowerShell 5.1 behavior is
not claimed until the updated script is executed there.

### B143 `TITLE_OR_BRIEF` metadata-only hotfix (2026-11-17)

Real LM Studio Gemma 4 inference reached `REAL_MAB_SOURCE_VERIFICATION` but
returned `B143_TITLE_OR_BRIEF`. That receipt does **not** include the original
model JSON, so the specific invalid field remains unknown. Studio now checks
model-authored `title`/`brief` (JavaScript UTF-16 limits and B143 forbidden
characters) before the unchanged pinned B143 compiler. For invalid labels it
normalizes controls/angle brackets, bounds their lengths, or derives **only**
missing labels from the real user instruction (or existing approved revision).
Spatial geometry, element IDs, mode and materials are never touched. A
content-free `metadataAdjustments` summary goes into the source acceptance
JSON. Missing or invalid geometry continues to fail with B143's own error.
No automatic second inference is performed by the source acceptance script.

On Windows, extract this source ZIP to a new folder and run
`RUN_SOURCE_REAL_MODEL_CHECK.bat` with one LM Studio model already loaded.
Do not rerun `RUN_WINDOWS_NATIVE_QA.bat`; the prebuilt EXE is not patched by
this source-only hotfix. A passing SOURCE/GUI acceptance is still not proof of
architecture aesthetics, user-intent fidelity or Minecraft construction.

### Display clarity / source-only launch

Start **`RUN_STUDIO_SOURCE.bat`** to run the current Python/Tk GUI code without rebuilding the Windows executable or accidentally launching an earlier packaged `dist/MABStudio/MABStudio.exe`. It does not contact any Minecraft world on startup. Windows Python and the pinned local MAB engine are still required.

The new default `なめらか（全体）` preview draws the visible boundaries of the same SOURCE cells with merged coplanar faces, without per-block grid lines. It auto-fits projected bounds, supports mouse drag for yaw/elevation and wheel zoom, and offers `全体表示` and an independent `3Dを大きく表示` window. The old voxel-detail views remain selectable. A Windows DPI-awareness request is made before Tk startup to reduce OS bitmap scaling; native Windows visual quality has not yet been verified.

These settings affect **display only**. They cannot improve the architecture of the model's generated building or authorize Minecraft construction. Architectural quality belongs to the upstream MAB research workflow.

### v0.3: portable ZIP EXE and embedded native B143 preview gate (2026-11-18)

The Windows native QA now treats the *delivered ZIP* as the release candidate:
re-extract it outside the build directory, compare the included EXE checksum,
then launch the extracted EXE for the 3,128-cell synthetic VTK camera/section
check and the 917-voxel **real pinned B143 SOURCE** GUI smoke. For the latter,
`--smoke` checks the **embedded VTK framebuffer** (not the Tk Canvas API),
counts visible native cells, and fails closed if a frozen build silently falls
back to the lightweight renderer. Windows Actions validates the same portable
archive before uploading it. These steps make no real model request and never
contact or write to Minecraft. Local Linux/Xvfb tests do NOT certify that the
portable ZIP or physical GPU succeeds on Windows; inspect the new portable
receipts in `WINDOWS_NATIVE_QA.json` after an actual Windows run.
