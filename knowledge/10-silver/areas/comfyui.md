# comfyui

ComfyUI（ポート 8188、systemd user ユニット `comfyui`、enable しない）。導入・点検・ワークフローの写し方は
`tools/comfyui/README.md` を Read する。

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
- 実機（A30、`--free-before`、`run_workflow.py run`）の結果。GLB は Blender 5.2.2（CPU）で描画して形を確かめた:
  - `viking_wolf_rune_axe.png` の既定は 167〜173 秒、memory.peak 17.5 GiB、VRAM ピーク 12,598 MiB、698,096〜698,185 面・PBR 4096px
    （同じ seed でも 89 面違う）。`D_superhero_female.png` は 111.9 秒、memory.peak 18.0 GiB、VRAM ピーク 10,426 MiB。
  - `E_copper_coin.png` の 30,000 面・2048px は 370.8 秒（memory.peak 20.4 GiB、VRAM 16,412 MiB）。29,796 面、全テクスチャ 2048px。
  - 斧の 50,000,000（間引かない）・8192px は 235.9 秒（memory.peak 22.8 GiB、VRAM 12,572 MiB）。3,353,172 面、
    ベースカラー・金属粗さ・AO 8192px、法線 2048px、GLB 239.7 MB。
  - 時間の大半は入力画像に依存する前段の KSampler で、ポリゴン数・テクスチャサイズの設定には依らない。
- 確かめていないこと: アプリモードの表示（入力欄の名前・結果の 3D）。利用者が画面で実行した記録はあるが、表示自体は見ていない。
  コードでは 3D 出力の分岐（`Preview3d`）があり、Save 3D の出力の種類（`3d`）と合う。表示がおかしければグラフ表示に切り替えて使う。

## エラーの読み方

- `CLIPLoader: Value not in list: clip_name: 'qwen_3_4b.safetensors' not in []` は、Z-Image・FLUX.2 klein などテキスト → 画像の
  テンプレートを開いたときのエラー。3D 生成には要らない。この機体には、その重み（文字エンコーダ・拡散モデル・VAE）を入れていない。
  開くワークフローを `Image_to_3D` に替える。

## 画面を使わずに通す

- `tools/comfyui/scripts/run_workflow.py` は DynamicCombo（型 `COMFY_DYNAMICCOMBO_V3`。RemeshMesh の `sign_mode` など）に対応している。
  API の入力名は「親.子」（`sign_mode` と `sign_mode.qef`）。Autogrow など他の動的な入力は未対応。
- 変換には生きたサーバーの `/object_info` が要る。`tools/comfyui/logs/object_info.json`（2026-10-03 の保存）はテンプレートより古い。
  GPU を使わずに取るなら、`main.py --cpu --listen 127.0.0.1 --port 8189` の確認用サーバーを使う。
