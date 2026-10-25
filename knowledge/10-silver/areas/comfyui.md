# comfyui

ComfyUI（ポート 8188、systemd user ユニット `comfyui`、enable しない）。導入・点検・ワークフローの写し方は
`tools/comfyui/README.md` を Read する。

## 画像 1 枚から 3D の入口（2026-10-25）

- 入口は `vendor/ComfyUI/user/default/workflows/Image_to_3D.json`。左の Workflows から開き、Load Image で画像を選んで Run を押す。
  結果は Save 3D のビューアに出て、GLB は `vendor/ComfyUI/output/3d/ComfyUI_*.glb` に残る。
- 土台は ComfyUI 同梱のテンプレート `3d_pixal3d_trellis2_image_to_model`（comfyui-workflow-templates 0.11.85）。
  `tools/comfyui/scripts/adapt_workflows.py`（`setup-server.sh` の [11/12]）が、アプリモード（入力 LoadImage.image・出力 Save3DAdvanced）・
  日本語の手順ノート・別の workflow id だけを足して書く。ノードの結線と値は変えない。
- 必要な重みは導入済みの 9 点に含まれる。追加のダウンロードは要らない。
- 実機（A30、`--free-before`、`run_workflow.py run`）の結果:
  `viking_wolf_rune_axe.png` は 172.7 秒、cgroup memory.peak 17.5 GiB、VRAM ピーク 12,598 MiB、GLB 698,185 面・PBR 4096px。
  `D_superhero_female.png` は 111.9 秒、memory.peak 18.0 GiB、VRAM ピーク 10,426 MiB。
  どちらも成功し、GLB を Blender 5.2.2（CPU）で描画して形を確かめた。
- 確かめていないこと: ブラウザでの操作。特にアプリモードの表示は、フロントエンドのコードに 3D 出力の分岐（`Preview3d`）があり、
  Save 3D の出力の種類（`3d`）と合うところまでしか確かめていない。表示がおかしければグラフ表示に切り替えて使う。

## エラーの読み方

- `CLIPLoader: Value not in list: clip_name: 'qwen_3_4b.safetensors' not in []` は、Z-Image・FLUX.2 klein などテキスト → 画像の
  テンプレートを開いたときのエラー。3D 生成には要らない。この機体には、その重み（文字エンコーダ・拡散モデル・VAE）を入れていない。
  開くワークフローを `Image_to_3D` に替える。

## 画面を使わずに通す

- `tools/comfyui/scripts/run_workflow.py` は DynamicCombo（型 `COMFY_DYNAMICCOMBO_V3`。RemeshMesh の `sign_mode` など）に対応している。
  API の入力名は「親.子」（`sign_mode` と `sign_mode.qef`）。Autogrow など他の動的な入力は未対応。
- 変換には生きたサーバーの `/object_info` が要る。`tools/comfyui/logs/object_info.json`（2026-10-03 の保存）はテンプレートより古い。
  GPU を使わずに取るなら、`main.py --cpu --listen 127.0.0.1 --port 8189` の確認用サーバーを使う。
