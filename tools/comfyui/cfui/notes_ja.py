"""PixelArtistry の写しに足す、日本語の手順ノートの本文（scripts/adapt_workflows.py が cfui.workflows.localize_pixelartistry で足す）。

配布元の英語のノートは残したまま、この機体での使い方・前提・実測の目安を日本語で書く。
実測の数字を変えたら、写しを作り直す（adapt_workflows.py。手直しされていない写しは自動で書き換わる）。
ファイルシステムにもネットワークにも触れない。
"""

from __future__ import annotations

from . import config

# どの写しにも入れる、この機体での前提。
_COMMON_PREMISES = """\
- 実行の前に、入口ページ（ポート 8080）から ComfyUI を起動する。ほかの GPU のサービスは自動で止まる。
  Ollama にモデルが載っていれば、入口ページの案内に従って降ろす。
- 紫の「Blender path」は設定済み（`/snap/blender/current/blender`）。変えない。
- 結果の GLB はサーバーの `tools/comfyui/vendor/ComfyUI/output/3d/` に残る。
- 詳しい使い方（Pixal3D と TRELLIS.2 の違い・切り替え方・実測の目安）は、入口ページの ComfyUI のカードにある「使い方」のリンクから開く。"""

_SWITCH = (
    "「Switch Model Here」のグループのノード「Boolean (Switch to Trellis2)」で形を作るモデルを選ぶ。"
    "false = Pixal3D（既定）、true = TRELLIS.2。"
)
_LOAD_IMAGE = (
    "**Load Image**（すぐ下の「Upload Input Image Here」）で画像を選ぶ。1 つの物体が大きく写った画像が向く。"
    "背景は自動で取り除く。"
)

_NOTE_01 = f"""\
## 使い方（この機体）

画像 1 枚から、閉じた（防水の）3D メッシュを作る。3D プリントの元にも使える。

1. {_LOAD_IMAGE}
2. {_SWITCH}
3. **Run**（実行）を押す。
4. 結果は右端の「Watertight model」のビューアに出る。ファイルは `output/3d/PixelArtistry_watertight_*.glb`。

### この機体での前提
{_COMMON_PREMISES}
- 目安（斧のサンプル、2026-11-03）: 約 4 分。RAM のピーク約 23 GiB（Blender とファイルのキャッシュを含む）、VRAM のピーク約 11 GiB。

### 流れと調整
- 形を作る → Quad Reconstruct で整える → WTiVo で閉じる → LODTailor（Blender）で 600 万面以下に減らし、閉じたまま保つ →
  Fast Merge で重なった頂点をまとめる。
- 穴が残ったら、もう一度 Run を押す（構造の seed は毎回変わる）。
- 細かい設定（WTiVo の `lambda_fill`・`component_mode` など）は、右の紫のグループにある英語のノート「PixelArtistry · Settings」。
- 2048 で細かく作るなら 01b（2K）。時間と RAM がかかる。
"""

_NOTE_01B_HEAD = f"""\
## 使い方（この機体・2K）

01 と同じ流れを 2048 の細かさで行う。01 より時間と RAM がかかる。

1. {_LOAD_IMAGE}
2. {_SWITCH}
3. **Run**（実行）を押す。
4. 結果は右端の「Watertight 2K model」のビューアに出る。ファイルは `output/3d/PixelArtistry_watertight_2K_*.glb`。

### この機体での前提
{_COMMON_PREMISES}
"""

_NOTE_01B_UPSTREAM_FAILED = (
    "上流の値（25,000,000）では、WTiVo の出力が 8,255 万面に跳び、続く LODTailor の Blender が RAM を 32 GiB 使って、"
    "この機体（RAM 62.5 GiB、ComfyUI の上限 48 GiB）では最後まで通らなかった（2026-10-03・2026-11-03）。"
)

# 斧のサンプルで測った 01b の目安（2026-11-03、proxy_points ごと。どれも WTiVo の出力は約 3,960 万面で、最終 GLB は閉じている）。
_MEASURED_2K = {
    12_000_000: "約 6 分。RAM のピーク約 31 GiB（Blender とファイルのキャッシュを含む）、VRAM のピーク約 13 GiB",
    16_000_000: "約 6 分。RAM のピーク約 34 GiB（Blender とファイルのキャッシュを含む）、VRAM のピーク約 13 GiB",
    20_000_000: "約 6 分。RAM のピーク約 35 GiB（Blender とファイルのキャッシュを含む）、VRAM のピーク約 13 GiB",
}

_NOTE_01B_TAIL = """
### 調整
- RAM が足りなくなったら（ComfyUI が止まる・極端に遅くなる）、WTiVo の `proxy_points` をさらに下げるか、01（1536）を使う。
- Quad Reconstruct は上流どおり bypass（無効）のまま。
- 細かい設定は、右の紫のグループにある英語のノート「PixelArtistry · Settings (2K preset)」。
"""

_NOTE_02 = f"""\
## 使い方（この機体・ゲーム用）

画像 1 枚から、テクスチャ付きの高ポリと、それを焼き込んだゲーム用の低ポリを作る。

1. {_LOAD_IMAGE}
2. {_SWITCH}
3. 「A · LODTailor low-poly」の `target_tris` で低ポリの面数を決める（既定 30,000。キャラクターは 2〜3 万、小物は 500〜1,000）。
4. **Run**（実行）を押す。
5. 結果は `output/3d/PixelArtistry_highpoly_*.glb`（テクスチャ付きの高ポリ）と `PixelArtistry_lowpoly_*.glb`（焼き込んだ低ポリ）。

### この機体での前提
{_COMMON_PREMISES}
- 目安（斧のサンプル、2026-11-03）: 約 6 分。RAM のピーク約 27 GiB（Blender とファイルのキャッシュを含む）、VRAM のピーク約 11 GiB。
  機体を起動して最初の 1 回は、Bake Forger が Blender の OptiX の準備で 3〜4 分長くかかることがある。

### 調整
- 斧のサンプルでは、TRELLIS.2（スイッチを true）にすると低ポリが閉じなかった（境界辺 36。2026-11-03）。閉じた低ポリが要るときは Pixal3D（既定）を使う。
- Bake Forger の各マップは 4096px。`*_resolution` で 2048 や 8192 にできる。
- 低ポリに白い斑点が出たら `tight_cage_extrusion_factor` を上げる（キャラクター 0.0019、硬い物 0.019）。
- Blender で閉じているかを確かめるときは、GLB を「Merge Vertices」を付けて読み込む（UV の継ぎ目が開いた辺に見えるため）。
- 詳しくは、紫のグループにある英語のノート「PixelArtistry · Settings (low-poly)」。
"""

_NOTE_03 = f"""\
## 使い方（この機体・手持ちのメッシュ）

手持ちのメッシュに画像からテクスチャを付け、ゲーム用の低ポリも作る。

1. メッシュ（`.glb` か `.obj`）をサーバーの `tools/comfyui/vendor/ComfyUI/input/` に置く（scp などで送る）。
2. 「Your mesh」ノードの `mesh_filename` にファイル名（または絶対パス）を書く。
3. **Load Image** に、そのメッシュの元になった画像を選ぶ（テクスチャはこの画像から作る）。
4. 「Switch Model Here」は true（TRELLIS.2）のまま。Pixal3D は自分で作った形にしか色を付けられず、手持ちのメッシュでは黒いテクスチャになる。
5. **Run**（実行）を押す。
6. 結果は `output/3d/PixelArtistry_textured_*.glb`（テクスチャを付けたメッシュ）と `PixelArtistry_lowpoly_*.glb`（焼き込んだ低ポリ）。

### この機体での前提
{_COMMON_PREMISES}
- 目安（01 の結果を入力にした例、2026-11-03）: 約 3 分。RAM のピーク約 23 GiB（Blender とファイルのキャッシュを含む）、VRAM のピーク約 7.5 GiB。

### 調整
- 600 万面を超えるメッシュは、先に 600 万面へ減らす（UV 展開を速くするため。それ以下は変えない）。
- 低ポリが要らなければ、「2 Low-Poly + Bake」のグループを bypass（Ctrl+B）する。
- 詳しくは、英語のノート「PixelArtistry · Your mesh」と「PixelArtistry · Settings (low-poly)」。
"""


def _note_01b(proxy_points_2k: int | None) -> str:
    if proxy_points_2k is None:
        change = (
            "\n### この機体では 2K は最後まで通らない\n"
            f"- {_NOTE_01B_UPSTREAM_FAILED}\n"
            "- `proxy_points` を下げても通らなかった。01（1536）を使う。\n"
        )
    else:
        millions = f"{proxy_points_2k / 1_000_000:g}"
        change = (
            "\n### 上流との違い（この写しだけの変更）\n"
            f"- WTiVo の `proxy_points` を 25,000,000（上流の値）から {proxy_points_2k:,} に下げている。"
            f"{_NOTE_01B_UPSTREAM_FAILED}\n"
            f"- 右の英語のノートにある「25M」は上流の値。この写しでは {millions}M。\n"
            f"- 目安（斧のサンプル、2026-11-03）: {_MEASURED_2K.get(proxy_points_2k, 'この値では未計測')}。\n"
            "- 斧のサンプルでは 12M・16M・20M のどれでも WTiVo の出力は約 3,960 万面だった。25M で約 2 倍に跳ぶ。\n"
        )
    return _NOTE_01B_HEAD + change + _NOTE_01B_TAIL


_NOTES = {
    "PixelArtistry_01_Image_to_Watertight_Mesh.json": _NOTE_01,
    "PixelArtistry_02_Image_to_GameReady_Asset.json": _NOTE_02,
    "PixelArtistry_03_Your_Mesh_to_GameReady_Asset.json": _NOTE_03,
}


def note_for(file_name: str, proxy_points_2k: int | None = config.WTIVO_2K_PROXY_POINTS) -> str:
    """写しのファイル名に対応する日本語ノートの本文。知らない名前は KeyError。"""

    if file_name == config.WORKFLOW_2K_FILE:
        return _note_01b(proxy_points_2k)
    return _NOTES[file_name]


# ---------------------------------------------------------------- 動画生成（2026-11-05）
# scripts/adapt_video_workflows.py が cfui.video_workflows で、動画の写し（Workflows → Video）に足す。

_VIDEO_COMMON = """\
### この機体での前提
- 実行の前に、入口ページ（ポート 8080）から ComfyUI を起動する。ほかの GPU のサービスは自動で止まる。
  Ollama にモデルが載っていれば、入口ページの案内に従って降ろす。
- 結果の mp4 はサーバーの `tools/comfyui/vendor/ComfyUI/output/video/` に残る。右端の Save Video にも出る。
- 1 本ずつ流す。2 本を続けて流すと、前の重みが RAM に残って遅くなることがある。
- 3 つのモデルの違い・実測の目安・ライセンスは、入口ページの ComfyUI のカードにある「使い方（動画生成）」のリンクから開く。"""

_VIDEO_HUNYUAN = """\
## 使い方（この機体）— HunyuanVideo 1.5（{mode}）

{summary}

1. {input_step}
2. **CLIP Text Encode (Positive Prompt)** に、作りたい動画を英語か中国語で書く（日本語は弱い）。
3. **Run**（実行）を押す。既定は 1280×720・121 フレーム（24 fps で約 5 秒）・20 ステップ。音声は無い。
   この機体で約 50 分かかる（2026-11-06 の実測）。
4. 短く試すときは {latent_node} の `length` を 33（約 1.4 秒。この機体で約 6 分）にする。`length` は 4 の倍数 + 1。

### この写しで変えたところ
- 1080p にする後段（グループ「Video Upscale 1080P (Ctrl-B to enable)」。既定で bypass）の変換器を fp8_scaled（8.3 GB）にした。
  A30 は FP8 の演算器を持たないが、ComfyUI が計算の前に戻すので動く。使うときはグループを選んで Ctrl+B で有効にする。
  出力は 1920×1072。この機体では 33 フレームで約 11 分（720p の本体を含む。2026-11-06 の実測）。1080p の VAE デコードは
  VRAM が足りず、ComfyUI が自動でタイル分割に切り替えて続ける（警告が出るが止まらない）。
- それ以外はテンプレート（{template}）のまま。720p の変換器は fp16（16.6 GB）。

{common}
"""

_VIDEO_LTX = """\
## 使い方（この機体）— LTX 2.5 Fast（{mode}）

{summary}

1. {input_step}
2. 中央の大きなノード（subgraph「{sg_name}」）の **prompt** に、作りたい動画と音を英語で書く。
3. **Run**（実行）を押す。既定は 1280×720 相当（左の Resolution Selector、0.9 メガピクセル）・5 秒・24 fps。
   **音声も同時に作る**（mp4 に音が入る）。低い解像度で作ってから 2 倍に上げる 2 段の処理。この機体で約 1.5 分（2026-11-06 の実測）。
4. 短く試すときは同じノードの **duration**（秒数）を 2 にする。幅・高さは左の Resolution Selector が決める。

### この写しで変えたところ（A30 向け）
- 公式の変換器（int8 21.5 GB）は gated（HF でライセンス同意が要る）で A30 には大きいので、
  unsloth の **GGUF {gguf}**（14.0 GB、Unet Loader (GGUF)）に替えた。
- 文字エンコーダは Gemma 4 12B の bf16（26.3 GB。公式の int8 版は gated）。ComfyUI が VRAM に入る分だけ載せる。
- 動画の VAE は軽い畳み込み版（`{video_vae}`。公式の拡散デコーダ版は gated）。
- 「Enable Prompt Enhance」（Gemma 4 E2B でプロンプトを膨らませる）はテンプレートの既定どおり切ってある。
- 英語のノートにある「HF で access を申請する」は、この写しには要らない（使う重みはどれも ungated）。

{common}
"""

_VIDEO_KANDINSKY = """\
## 使い方（この機体）— Kandinsky 6.0 {variant}（{mode}）

{summary}

1. {input_step}
2. **{prompt_title}**（Kandinsky6TextEncode）の `video_caption` に映像、`audio_caption` に音を英語で書く。
   その下の Negative のノードは、出てほしくないもの。
3. **Run**（実行）を押す。既定は 864×480・121 フレーム（24 fps で約 5 秒）。**音声も同時に作る**（44.1 kHz）。
   この機体で約 {elapsed}（2026-11-06 の実測）。
4. {sampler_step}

### この写しで変えたところ
- 上流の例（Pro distill・Qwen3.5 9B によるプロンプトの書き足し・2.25 倍の超解像）から、
  変換器を **{variant}**（3B、bf16 {size}）に替え、プロンプトの書き足し（19 GB）と超解像（別の拡張 kandinsky6-sr）を外した。
- 文字エンコーダ Qwen2.5-VL 7B は fp8_scaled（9.4 GB。HunyuanVideo 1.5 と共用）にした。
- MagCache（Pro 専用の高速化）は bypass にした（Lite に付けると止まる）。
- 音声 VAE（MMAudio の `v1-44.pth`）は CC BY-NC 4.0。作った音は非商用に限る。

{common}
"""

_VIDEO_TEXT = {
    "hunyuan_t2v": dict(
        mode="文章 → 動画",
        summary="文章から 720p の動画を作る（Tencent、8.3B）。",
        input_step="入力画像は要らない。",
        latent_node="**EmptyHunyuanVideo15Latent**",
        template="video_hunyuan_video_1.5_720p_t2v",
    ),
    "hunyuan_i2v": dict(
        mode="画像 → 動画",
        summary="1 枚の画像を最初のコマにして、720p の動画を作る（Tencent、8.3B）。",
        input_step="**Load Image** で最初のコマにする画像を選ぶ（既定はテンプレートのサンプル）。",
        latent_node="**HunyuanVideo15ImageToVideo**",
        template="video_hunyuan_video_1.5_720p_i2v",
    ),
    "ltx_t2v": dict(
        mode="文章 → 動画＋音声",
        summary="文章から音声付きの動画を作る（Lightricks、22B の distilled = Fast）。",
        input_step="入力画像は要らない。",
        sg_name="Text to Video (LTX-2.5)",
    ),
    "ltx_i2v": dict(
        mode="画像 → 動画＋音声",
        summary="1 枚の画像を最初のコマにして、音声付きの動画を作る（Lightricks、22B の distilled = Fast）。",
        input_step="**Load First Frame** で最初のコマにする画像を選ぶ（既定はテンプレートのサンプル）。",
        sg_name="Image to Video (LTX-2.5)",
    ),
    "kandinsky_lite_t2v": dict(
        variant="Lite",
        mode="文章 → 動画＋音声",
        summary="文章から音声付きの動画を作る（Kandinsky Lab、3B）。50 ステップで丁寧に作る版。",
        input_step="入力画像は要らない。",
        sampler_step="**Kandinsky 6 Sampler** は 50 ステップ・CFG 5。速く試すときは LiteDistill の写しを使う。",
        size="7.5 GB",
        elapsed="19 分",
    ),
    "kandinsky_lite_i2v": dict(
        variant="Lite",
        mode="画像 → 動画＋音声",
        summary="1 枚の画像を最初のコマにして、音声付きの動画を作る（Kandinsky Lab、3B）。50 ステップの版。",
        input_step="**Load Image** で最初のコマにする画像を選ぶ（既定は拡張のサンプル。864×480 に合わせて切り抜かれる）。",
        sampler_step="**Kandinsky 6 Sampler** は 50 ステップ・CFG 5。速く試すときは LiteDistill の写しを使う。",
        size="7.5 GB",
        elapsed="19 分",
    ),
    "kandinsky_distill_t2v": dict(
        variant="Lite distill",
        mode="文章 → 動画＋音声",
        summary="文章から音声付きの動画を作る（Kandinsky Lab、3B）。10 ステップで速く作る版。",
        input_step="入力画像は要らない。",
        sampler_step="**Kandinsky 6 Sampler** は 10 ステップ・CFG 1（distill は CFG 1・denoise 1 でないと止まる）。",
        size="6.4 GB",
        elapsed="2.5 分",
    ),
    "kandinsky_distill_i2v": dict(
        variant="Lite distill",
        mode="画像 → 動画＋音声",
        summary="1 枚の画像を最初のコマにして、音声付きの動画を作る（Kandinsky Lab、3B）。10 ステップの版。",
        input_step="**Load Image** で最初のコマにする画像を選ぶ（既定は拡張のサンプル。864×480 に合わせて切り抜かれる）。",
        sampler_step="**Kandinsky 6 Sampler** は 10 ステップ・CFG 1（distill は CFG 1・denoise 1 でないと止まる）。",
        size="6.4 GB",
        elapsed="2.5 分",
    ),
}


def video_note_for(kind: str) -> str:
    """動画の写しの種類（config.VIDEO_WORKFLOWS の 3 つ目）に対応する日本語ノートの本文。知らない種類は KeyError。"""

    values = _VIDEO_TEXT[kind]
    if kind.startswith("hunyuan_"):
        template = _VIDEO_HUNYUAN
    elif kind.startswith("ltx_"):
        template = _VIDEO_LTX
        values = dict(values, gguf=config.LTX25_GGUF_FILE, video_vae=config.LTX25_VIDEO_VAE_FILE)
    else:
        template = _VIDEO_KANDINSKY
        values = dict(values, prompt_title=config.KANDINSKY6_PROMPT_TITLE)
    return template.format(common=_VIDEO_COMMON, **values)
