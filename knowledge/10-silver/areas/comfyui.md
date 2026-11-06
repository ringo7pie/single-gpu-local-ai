# comfyui

ComfyUI（ポート 8188、systemd user ユニット `comfyui`、enable しない）。導入・点検・ワークフローの写し方は
`tools/comfyui/README.md` を Read する。
この機体での実測（時間・memory.peak・VRAM・面数・閉じているか）は `knowledge/10-silver/resources/comfyui-measurements.md` を Read する。

## 画像 1 枚から 3D の入口（2026-10-25）

- 入口は `vendor/ComfyUI/user/default/workflows/Image_to_3D.app.json`（画面で保存し直すと `.json` から `.app.json` に変わる）。
  左の Workflows から開き、Load Image で画像を選んで Run を押す。結果は Save 3D のビューアに出て、
  GLB は `vendor/ComfyUI/output/3d/ComfyUI_*.glb` に残る。
- アプリの入力は 3 つ。順に LoadImage の image、ポリゴン数、テクスチャサイズ。
  - ポリゴン数: 足した PrimitiveInt「ポリゴン数（目標の面数）」を `DecimateMesh.target_face_count` へつなぐ。既定 700000。
    0〜50,000,000（0 は間引かない）。形を作る段階の面数より増えない（斧で 3,353,172 面）。
  - テクスチャサイズ: 上流の PrimitiveInt「Texture Resolution」が `UnwrapMesh.resolution` と `BakeTextureFromVoxel.texture_size` を決める。
    既定 4096。256〜8192 の 256 の倍数。法線マップ 2048px と AO の焼き込み 1024px はアプリの入力では変わらない。
- 土台は ComfyUI 同梱のテンプレート `3d_pixal3d_trellis2_image_to_model`（comfyui-workflow-templates 0.11.85）。
  `tools/comfyui/scripts/adapt_workflows.py`（`setup-server.sh` の [11/12]）が、アプリモード・ポリゴン数用の PrimitiveInt（1 つと結線 1 本）・
  日本語の手順ノート・別の workflow id を足して書く。ほかのノードの値は変えない。
- 画面で保存し直したファイルに今の版の変更だけを当てるときは `adapt_workflows.py --upgrade-easy`（入力画像などの手直しは残す。何度流しても同じ）。
  画面の `linearData` は旧書式 `[ノード id, widget 名]` を、読み込み時に `"<workflow id>:<ノード id>:<widget 名>"` へ変換する（frontend 1.55.17）。
- 必要な重みは導入済みの 9 点に含まれる。追加のダウンロードは要らない。
- 時間の大半は入力画像に依存する前段の KSampler で、ポリゴン数・テクスチャサイズの設定には依らない。
- 確かめていないこと: アプリモードの表示（入力欄の名前・結果の 3D）。利用者が画面で実行した記録はあるが、表示自体は見ていない。
  コードでは 3D 出力の分岐（`Preview3d`）があり、Save 3D の出力の種類（`3d`）と合う。表示がおかしければグラフ表示に切り替えて使う。

## PixelArtistry の 4 本（2026-11-03 に実機で再確認）

- 写し（`workflows/PixelArtistry/` の 4 本）は、Load Image の真上（`pos=(-5090,-240)`）に緑の日本語ノート「使い方（日本語・この機体）」を持ち、
  開いたときの初期表示（`extra.ds`）は縮尺 0.5・`offset=(5250,300)`。本文は `cfui/notes_ja.py`、足すのは `adapt_workflows.py`。表示は目で確かめていない。
- 01b（2K）の WTiVo `proxy_points` は配布元の 25M を **20M** に下げて写す（`cfui/config.py` の `WTIVO_2K_PROXY_POINTS`、`--proxy-points-2k`）。
  斧のサンプルでは 12M・16M・20M が成功し、25M は LODTailor の Blender が RAM の歯止めに当たる。跳ぶ値が画像で変わるかは確かめていない。
- doctor（自己試験込み）は全項目 OK で、01・02・03 の最終 GLB はどれも境界辺 0・非多様体辺 0。
- 形を作るモデルはグループ「Switch Model Here」のノード「Boolean (Switch to Trellis2)」（id 316）で選ぶ（false = Pixal3D、true = TRELLIS.2）。既定は 03 だけ TRELLIS.2。
  Image_to_3D のアプリモードの入力にスイッチは無い。利用者向けの説明は `tools/portal/guides/pixelartistry.html`（入口ページの ComfyUI のカードからリンク）。
- 形を TRELLIS.2 で作ると、斧のサンプルでは **02 の最終 GLB が閉じない**（2026-11-03）。01・01b は閉じる。閉じた低ポリが要るときは Pixal3D を使う。

## エラーの読み方

- `CLIPLoader: Value not in list: clip_name: 'qwen_3_4b.safetensors' not in []` は、Z-Image・FLUX.2 klein などテキスト → 画像の
  テンプレートを開いたときのエラー。3D 生成には要らない。この機体には、その重み（文字エンコーダ・拡散モデル・VAE）を入れていない。
  開くワークフローを `Image_to_3D` に替える。

## 画面を使わずに通す

- `tools/comfyui/scripts/run_workflow.py` は DynamicCombo（型 `COMFY_DYNAMICCOMBO_V3`。RemeshMesh の `sign_mode` など）に対応している。
  API の入力名は「親.子」（`sign_mode` と `sign_mode.qef`）。Autogrow はリンクだけの入力（`ComfyMathExpression` の `values.a`）なら通る。
- subgraph は中のノードへ展開する（id は `外:中`）。`--seed` は名前が seed の入力だけを変える（2026-11-05 までは PrimitiveInt も変えていた）。
- 変換には生きたサーバーの `/object_info` が要る。`tools/comfyui/logs/object_info.json`（2026-10-03 の保存）はテンプレートより古い。
  GPU を使わずに取るなら、`main.py --cpu --listen 127.0.0.1 --port 8189` の確認用サーバーを使う。

## 動画生成（2026-11-05）

- 画面の Workflows → Video に 8 本（HunyuanVideo 1.5 / Kandinsky 6.0 Lite・Lite distill / LTX 2.5 Fast の T2V・I2V）。
  重みは `run-comfyui fetch-models --video`（約 111 GiB）。上流の事情と選んだ重みは `knowledge/10-silver/resources/comfyui-video.md` を Read する。
- 所要時間は LTX・Kandinsky distill が 1.5〜2.5 分、Kandinsky Lite が約 19 分、HunyuanVideo が約 50 分（5 秒の動画、2026-11-06）。
  実測と RAM の振る舞いは `knowledge/10-silver/resources/comfyui-measurements.md` と `resources/comfyui-video.md` を Read する。
