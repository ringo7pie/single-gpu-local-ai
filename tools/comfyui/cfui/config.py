"""パス・ポート・固定コミット・モデルの一覧を一元管理する。

このモジュールはファイルシステムに書き込まず、ネットワークにも触れない。
定数を並べるだけなので、素の python からでも torch なしで import できる
（px3d/config.py と同じ設計方針）。
setup-server.sh と scripts/build-wtivo.sh は venv を作る前に git を操作するため、
同じ値を直値でも持っている。片方を変えたらもう片方も変えること。
"""

from __future__ import annotations

from pathlib import Path

# このファイルは tools/comfyui/cfui/config.py なので、2 つ上が tools/comfyui。
TOOL_DIR = Path(__file__).resolve().parents[1]

VENDOR_DIR = TOOL_DIR / "vendor"
COMFYUI_DIR = VENDOR_DIR / "ComfyUI"
CUSTOM_NODES_DIR = COMFYUI_DIR / "custom_nodes"
MODELS_DIR = COMFYUI_DIR / "models"
INPUT_DIR = COMFYUI_DIR / "input"
WORKFLOWS_DIR = COMFYUI_DIR / "user" / "default" / "workflows" / "PixelArtistry"
# 予備案（WTiVo を外したもの）の写しの置き場。scripts/adapt_workflows.py --fallback が書く。
FALLBACK_WORKFLOWS_DIR = COMFYUI_DIR / "user" / "default" / "workflows" / "PixelArtistry_noWTiVo"
# 「画像を 1 枚入れるだけ」で 3D を作る入口。ComfyUI 同梱のテンプレート（comfyui-workflow-templates）を土台に、
# scripts/adapt_workflows.py が写す。探しやすいように、フォルダに入れず直下に置く。
# 名前は .app.json。アプリモードのワークフローを画面で保存し直すと、この名前に変わる（2026-10-25 に確認）。
EASY_TEMPLATE_FILE = "3d_pixal3d_trellis2_image_to_model.json"
EASY_WORKFLOW_PATH = COMFYUI_DIR / "user" / "default" / "workflows" / "Image_to_3D.app.json"
EXTENSIONS_DIR = VENDOR_DIR / "extensions"
PIXELARTISTRY_DIR = VENDOR_DIR / "PixelArtistry-Watertight-Meshes"
WTIVO_SRC_DIR = VENDOR_DIR / "WTiVo-WatertightVoxel"
WTIVO_NODE_DIR = CUSTOM_NODES_DIR / "WTiVo-WatertightVoxel-ComfyuiNode"
# ノードの wtivo.py は ROOT と ROOT/build を sys.path に足してから wtivo_core / wtivo_gpupr /
# wtivo_vdb を import する（WTiVo-WatertightVoxel-ComfyuiNode/wtivo.py:44-49, 86-89）。
# Linux でビルドした .so はここへ置く。
WTIVO_NATIVE_DIR = WTIVO_NODE_DIR / "build"
TRELLIS2_NODE_DIR = CUSTOM_NODES_DIR / "ComfyUI-Trellis2"
PATCHES_DIR = TOOL_DIR / "patches"
SCRIPTS_DIR = TOOL_DIR / "scripts"
LOGS_DIR = TOOL_DIR / "logs"
VENV_PYTHON = TOOL_DIR / ".venv" / "bin" / "python"

HOST = "0.0.0.0"
PORT = 8188

# CUDA 拡張は 12.6 でビルドする（/usr/local/cuda は 13.2 で Hunyuan3D 用。触らない）。
CUDA_HOME = "/usr/local/cuda-12.6"
EXPECTED_NVCC_RELEASE = "12.6"
EXPECTED_TORCH_VERSION = "2.9.1+cu126"
EXPECTED_TORCH_CUDA = "12.6"
EXPECTED_GPU_SUBSTRING = "A30"

# Blender は snap の 5.x を使う。/usr/bin/blender は apt の 4.0.2 で、ワークフローの
# LODTailor / Bake Forger が前提にする版より古いので使わない。
# /snap/bin/blender（= /usr/bin/snap）は使わない。snap run が子を snap.blender.*.scope という別の
# cgroup に移すため、comfyui.service の MemoryHigh / MemoryMax も memory.peak も Blender に届かない。
# snap は classic confinement なので、本体を直接起動すれば呼び出し元の cgroup に残る（2026-10-03 実測）。
# /snap/blender/current は snap refresh で版が変わっても同じパスのまま。
BLENDER_PATH = "/snap/blender/current/blender"
# snap run 経由になるパス。doctor がこれを指していないか確かめる。
BLENDER_SNAP_LAUNCHER = "/usr/bin/snap"
# 以前の既定値。adapt_workflows.py は、この値で書いたままの（手直しされていない）写しだけを
# 今の既定値へ書き換える。
LEGACY_BLENDER_PATHS = ("/snap/bin/blender",)
BLENDER_MAJOR_REQUIRED = 5
# アドオンの干渉を避けるため、ComfyUI から呼ぶ Blender の利用者設定を空の場所へ向ける。
BLENDER_CLEAN_DIR = Path.home() / ".config" / "comfyui" / "blender-clean"
BLENDER_ENV_DIRS = {
    "BLENDER_USER_CONFIG": "config",
    "BLENDER_USER_SCRIPTS": "scripts",
    # Blender 4.2 以降、アドオンの多くは extensions に入る。scripts だけでは外せないので足す。
    "BLENDER_USER_EXTENSIONS": "extensions",
}

ENV_FILE = Path.home() / ".config" / "comfyui" / "comfyui.env"
# serve が main.py の後ろに足す追加引数（例: --lowvram）。env ファイルから読む想定。
EXTRA_ARGS_ENV_VAR = "COMFYUI_EXTRA_ARGS"

SERVICE_NAME = "comfyui"

COMFYUI_URL = "https://github.com/Comfy-Org/ComfyUI.git"
# GitHub の Releases 画面の最新は v0.41.0 だが、その後に版上げコミットの付いたタグ
# v0.41.1 がある。差分は API ノード（Krea）とテンプレートの更新だけ（2026-10-16 確認）。
COMFYUI_TAG = "v0.41.1"
COMFYUI_COMMIT = "45bf90d1c91eecd782643346dcfb73cb0017667f"

PIXELARTISTRY_URL = "https://github.com/pixelartistry/PixelArtistry-Watertight-Meshes.git"
PIXELARTISTRY_COMMIT = "5b3a0e9d2c5783ed05ba45bbcc157b9509fb267b"

WTIVO_SRC_URL = "https://github.com/Mstafa-awad/WTiVo-WatertightVoxel.git"
# タグは無い。VERSION ファイルが 1.0.0 の main の HEAD（2026-09-11）。
WTIVO_SRC_COMMIT = "7a0ac7cb24fd0588f2f7a78c787779d760d36f48"

# 動画生成で足した custom_node のフォルダ名（下の CUSTOM_NODES に入る）。
GGUF_NODE_NAME = "ComfyUI-GGUF"

# custom_nodes（フォルダ名, URL, コミット）。Mstafa-awad の 9 個は配布元 bat の SHA_* と同じ。
CUSTOM_NODES = (
    (
        "ComfyUI-Trellis2",
        "https://github.com/visualbruno/ComfyUI-Trellis2.git",
        "b0e84c0f29857a444e8bcb5b9be2ea218e96efd0",
    ),
    (
        "WTiVo-WatertightVoxel-ComfyuiNode",
        "https://github.com/Mstafa-awad/WTiVo-WatertightVoxel-ComfyuiNode.git",
        "fb9e9ea6a7deec965d3a4bf43ba7a4a9ff3856f3",
    ),
    (
        "ComfyUI-Mesh-Quad-Reconstruct",
        "https://github.com/Mstafa-awad/ComfyUI-Mesh-Quad-Reconstruct.git",
        "74048b3415537e26cc72b46b082a010cd0e357ce",
    ),
    (
        "ComfyUI-CuMesh-Decimate",
        "https://github.com/Mstafa-awad/ComfyUI-CuMesh-Decimate.git",
        "c4edeb96bf637239cecb731d2869469a3025d742",
    ),
    (
        "ComfyUI-Memory-Cleaner",
        "https://github.com/Mstafa-awad/ComfyUI-Memory-Cleaner.git",
        "3357282290278c96ffa0da180d43bc5eac5f2286",
    ),
    (
        "LODTailor-The-Mesh-Trimmer-ComfyuiNode",
        "https://github.com/Mstafa-awad/LODTailor-The-Mesh-Trimmer-ComfyuiNode.git",
        "3d25b7d4aa382fa5dac210eb5d8d0eadc4a4f183",
    ),
    (
        "LODTailor-Bake-Forger",
        "https://github.com/Mstafa-awad/LODTailor-Bake-Forger.git",
        "f13589c22558e3ca49dfa87dce709233eb3cc86b",
    ),
    (
        "Trellis2-Mesh-Encoder",
        "https://github.com/Mstafa-awad/Trellis2-Mesh-Encoder.git",
        "860313e67f0f69d402310dc325707bab8bec2fe9",
    ),
    (
        "ComfyUI_LODsmith_Merge_Watertight",
        "https://github.com/Mstafa-awad/ComfyUI_LODsmith_Merge_Watertight.git",
        "5da6dd69c02d0f3d4ae743d090cfac79aefe7f96",
    ),
    (
        "WTiVo-FastMergeByDistance",
        "https://github.com/Mstafa-awad/WTiVo-FastMergeByDistance.git",
        "5392949165ceed1e74448b1949d6e0dc9b34dd92",
    ),
    # 動画生成（LTX 2.5 の GGUF を読む Unet Loader (GGUF)）。2026-01-12 の HEAD。
    # ComfyUI v0.41.1 の Linear は input_act をキーワードで渡さない（comfy/ops.py の linear_input_act は
    # INT8 の重み以外で linear(x) を呼ぶ）ので、issue #488 の当て物は要らない。
    (
        GGUF_NODE_NAME,
        "https://github.com/city96/ComfyUI-GGUF.git",
        "6ea2651e7df66d7585f6ffee804b20e92fb38b8a",
    ),
)

# CUDA 拡張のソース（フォルダ名, URL, コミット）。vendor/extensions に置く。
# ComfyUI-Trellis2 の README（Custom Build）が指す visualbruno のフォークを使う。
# 同梱の wheels/Linux/Torch291 は cumesh が sm_86、他が sm_120 の SASS しか持たず、
# A30（sm_80）では "no kernel image is available" で落ちた（2026-10-02 実測）。
EXTENSION_SOURCES = (
    ("CuMesh", "https://github.com/visualbruno/CuMesh.git", "d10e54c30ddd03d11472c1431693f985501c7966"),
    ("FlexGEMM", "https://github.com/visualbruno/FlexGEMM.git", "db388bd17c34abc697792aa718dca780734c2783"),
    ("TRELLIS.2", "https://github.com/visualbruno/TRELLIS.2.git", "65d1e13b4a92296036044df0633242bb9e95abf6"),
    ("nvdiffrast", "https://github.com/NVlabs/nvdiffrast.git", "253ac4fcea7de5f396371124af597e6cc957bfae"),
)

# doctor が import を確かめる拡張（表示名, import 文）。
EXTENSION_IMPORTS = (
    ("cumesh", "import cumesh"),
    ("o_voxel", "import o_voxel"),
    ("flex_gemm", "import flex_gemm"),
    ("nvdiffrast", "import nvdiffrast.torch"),
)
WTIVO_MODULES = ("wtivo_core", "wtivo_gpupr", "wtivo_vdb")

# WTiVo の core / vdb のビルドに要る apt パッケージ（sudo が要るので利用者が入れる）。
WTIVO_APT_PACKAGES = ("libcgal-dev", "libopenvdb-dev", "libtbb-dev", "libeigen3-dev")

HF_BASE_URL = "https://huggingface.co"

# モデル（置き先, ファイル名, repo, repo 内のパス, バイト数, sha256）。
# 置き先は vendor/ComfyUI/models/ からの相対パス。一覧は配布元 bat の :models（L407-417）と同じ。
# バイト数と sha256 は 2026-10-02 に HF の resolve/main の応答（x-linked-size / x-linked-etag）で確認した。
# json は LFS ではないため、取得したファイルの sha256 を書いている。
MODEL_FILES = (
    (
        "diffusion_models",
        "trellis_2_int8_convrot.safetensors",
        "Comfy-Org/TRELLIS.2",
        "diffusion_models/trellis_2_int8_convrot.safetensors",
        5253048192,
        "d01952ad137213f6a868f86b6b877026276f84af5eec23069217475a0bad3a31",
    ),
    (
        "diffusion_models",
        "pixal3d_int8_convrot.safetensors",
        "Comfy-Org/Pixal3D",
        "diffusion_models/pixal3d_int8_convrot.safetensors",
        5584555824,
        "4621eac3b715484f79303c7152af641fe0b2b14f4d0e3d394fd6922d00f955ec",
    ),
    (
        "vae",
        "trellis_2_shape_vae_bf16.safetensors",
        "Comfy-Org/Pixal3D",
        "vae/trellis_2_shape_vae_bf16.safetensors",
        1095844024,
        "de0cb4949a76c59ee5c091a995a69bcc8c51d5aeda939f0c641a50d2a72341f4",
    ),
    (
        "vae",
        "trellis_2_texture_vae_bf16.safetensors",
        "Comfy-Org/Pixal3D",
        "vae/trellis_2_texture_vae_bf16.safetensors",
        948461364,
        "714e5ebf094a610e12a8e3b5175c18a62f37f6ea4218acb6073644456b73ab0e",
    ),
    (
        "clip_vision",
        "dino_v3_L_naf_fp32.safetensors",
        "Comfy-Org/Pixal3D",
        "clip_vision/dino_v3_L_naf_fp32.safetensors",
        1215214176,
        "4ad2ec4e0879a5b5b04cd97325cc37da954a7b6edca5170b86510f17f2b2290f",
    ),
    (
        "geometry_estimation",
        "moge_2_vitl_normal_fp16.safetensors",
        "Comfy-Org/MoGe",
        "geometry_estimation/moge_2_vitl_normal_fp16.safetensors",
        661859924,
        "cb1a692d03235671e959e81360d7b4d9f44aefadb1f852d6ca6aa17799d5e31f",
    ),
    (
        "background_removal",
        "birefnet.safetensors",
        "Comfy-Org/BiRefNet",
        "background_removal/birefnet.safetensors",
        444473596,
        "9ab37426bf4de0567af6b5d21b16151357149139362e6e8992021b8ce356a154",
    ),
    (
        "Trellis2/encoders",
        "shape_enc_next_dc_f16c32_fp16.safetensors",
        "microsoft/TRELLIS.2-4B",
        "ckpts/shape_enc_next_dc_f16c32_fp16.safetensors",
        708797208,
        "f37c5ff5b983b68e9946060000f09bc131f3e84318a2c8b7430a81e4b4636c41",
    ),
    (
        "Trellis2/encoders",
        "shape_enc_next_dc_f16c32_fp16.json",
        "microsoft/TRELLIS.2-4B",
        "ckpts/shape_enc_next_dc_f16c32_fp16.json",
        653,
        "5a960a5c419d70f8d4a7a6b6e52ae689cd948c88bd7ed92346adfbf82cd12007",
    ),
)

# サンプル画像（配布元 bat の L179 と同じ URL）。input/ に置く。
SAMPLE_IMAGE_NAME = "viking_wolf_rune_axe.png"
SAMPLE_IMAGE_URL = (
    "https://raw.githubusercontent.com/Comfy-Org/workflow_templates/refs/heads/main/input/"
    "viking_wolf_rune_axe.png"
)

# 配布元のワークフロー 4 本（vendor/PixelArtistry-Watertight-Meshes/workflows/ のファイル名）。
WORKFLOW_FILES = (
    "PixelArtistry_01_Image_to_Watertight_Mesh.json",
    "PixelArtistry_01b_Image_to_Watertight_Mesh_2K.json",
    "PixelArtistry_02_Image_to_GameReady_Asset.json",
    "PixelArtistry_03_Your_Mesh_to_GameReady_Asset.json",
)
# 2K（2048）の写し。WTiVo の proxy_points だけ、この機体で通る値に下げて写す（scripts/adapt_workflows.py）。
WORKFLOW_2K_FILE = "PixelArtistry_01b_Image_to_Watertight_Mesh_2K.json"
# 斧のサンプル（2026-11-03）: 12M・16M・20M は WTiVo の出力が約 39.6M 面で通り、cgroup の memory.peak は 31〜35 GiB。
# 上流の値 25M は WTiVo の出力が 82.6M 面に跳び、続く LODTailor の Blender が RSS 32 GiB まで使って RAM の歯止め
# （anon+swap 43 GiB）に当たった（MemoryMax 48 GiB・RAM 62.5 GiB）。通った最大の 20M を使う。None なら上流の値のまま写す。
WTIVO_2K_PROXY_POINTS: int | None = 20_000_000

# WTiVo の自己試験（配布元 bat の L457-484 と同じ条件）。
SELFTEST_SUBDIVISIONS = 4
SELFTEST_RADIUS = 0.5
SELFTEST_REMOVED_FACES = 60
SELFTEST_ARGS = {
    "input_res": 256,
    "final_res": 256,
    "proxy_points": 200_000,
    "threads": 4,
}

VRAM_FREE_THRESHOLD_MIB = 1024


# ---------------------------------------------------------------- 動画生成（2026-11-05 追加）
# HunyuanVideo 1.5・LTX 2.5（Fast = distilled）は ComfyUI 本体（v0.41.1）のノードで動く。
# Kandinsky 6.0 は本体に無いので、公式の ComfyUI 拡張（kandinskylab/kandinsky-6 の comfyui/）を
# vendor/kandinsky-6 に固定コミットで取り、custom_nodes/kandinsky6 からシンボリックリンクで指す。
KANDINSKY6_SRC_URL = "https://github.com/kandinskylab/kandinsky-6.git"
KANDINSKY6_SRC_COMMIT = "7ceeefad7fb625011f7db602c43893a989f19c15"  # 2026-10-25 の HEAD（拡張 v1.0.2）
KANDINSKY6_SRC_DIR = VENDOR_DIR / "kandinsky-6"
KANDINSKY6_NODE_LINK = CUSTOM_NODES_DIR / "kandinsky6"
# リンクの中身（custom_nodes/ から見た相対パス）。
KANDINSKY6_NODE_LINK_TARGET = "../../kandinsky-6/comfyui"
KANDINSKY6_EXAMPLE_WORKFLOWS_DIR = KANDINSKY6_SRC_DIR / "comfyui" / "example_workflows"

# 動画のワークフローの写しの置き場（画面の Workflows → Video）。scripts/adapt_video_workflows.py が書く。
VIDEO_WORKFLOWS_DIR = COMFYUI_DIR / "user" / "default" / "workflows" / "Video"
VIDEO_OUTPUT_DIR = COMFYUI_DIR / "output" / "video"
# 写しのファイル名（ファイル名, 土台, 種類）。土台は同梱テンプレート（comfyui-workflow-templates）の名前か、
# Kandinsky 6 の拡張の example_workflows/ のファイル名。
VIDEO_WORKFLOWS = (
    ("HunyuanVideo15_T2V.json", "video_hunyuan_video_1.5_720p_t2v.json", "hunyuan_t2v"),
    ("HunyuanVideo15_I2V.json", "video_hunyuan_video_1.5_720p_i2v.json", "hunyuan_i2v"),
    ("LTX25_Fast_T2V.json", "video_ltx2_5_t2v.json", "ltx_t2v"),
    ("LTX25_Fast_I2V.json", "video_ltx2_5_i2v.json", "ltx_i2v"),
    ("Kandinsky6_Lite_T2V.json", "Kandinsky 6.0 Text to Video+Audio.json", "kandinsky_lite_t2v"),
    ("Kandinsky6_Lite_I2V.json", "Kandinsky 6.0 Image to Video+Audio.json", "kandinsky_lite_i2v"),
    ("Kandinsky6_LiteDistill_T2V.json", "Kandinsky 6.0 Text to Video+Audio.json", "kandinsky_distill_t2v"),
    ("Kandinsky6_LiteDistill_I2V.json", "Kandinsky 6.0 Image to Video+Audio.json", "kandinsky_distill_i2v"),
)
VIDEO_WORKFLOW_FILES = tuple(entry[0] for entry in VIDEO_WORKFLOWS)
# I2V（画像 → 動画）の写しの既定の入力画像。同梱テンプレートと同じ画像（プロンプトがこの画像に合わせてある）を
# input/ に置く（3D の SAMPLE_IMAGE_URL と同じ置き場所）。Kandinsky 6 の拡張は起動時に自分の例を input/kandinsky6/ へ写す。
VIDEO_SAMPLE_IMAGES = (
    (
        "video_hunyuan_video_1.5_720p_i2v_input_image.png",
        "https://raw.githubusercontent.com/Comfy-Org/workflow_templates/refs/heads/main/input/"
        "video_hunyuan_video_1.5_720p_i2v_input_image.png",
    ),
    (
        "neon_cyborg_portrait.png",
        "https://raw.githubusercontent.com/Comfy-Org/workflow_templates/refs/heads/main/input/neon_cyborg_portrait.png",
    ),
)
KANDINSKY6_SAMPLE_IMAGE = "kandinsky6/kandinsky6_i2va_portrait.png"
# 写しで、映像と音のプロンプトを書く Kandinsky6TextEncode に付ける題名（日本語ノート・ガイドもこの名前で指す）
KANDINSKY6_PROMPT_TITLE = "Prompt（映像 video_caption と音 audio_caption）"

# 動画の重み（グループ, 置き先, ファイル名, repo, repo 内のパス, バイト数, sha256）。どれも gated ではない。
# バイト数と sha256 は 2026-11-05 に HF の tree API（?expand=true の size と lfs.oid）で確認した。
# config.json（LFS ではない）は取得したファイルの sha256。
# A30 は FP8 の演算器を持たないので、fp8_scaled・GGUF の重みは ComfyUI が計算の前に bf16 へ戻す。
VIDEO_MODEL_GROUPS = ("hunyuanvideo15", "ltx25", "kandinsky6")
VIDEO_MODEL_GROUP_LABELS = {
    "hunyuanvideo15": "HunyuanVideo 1.5",
    "ltx25": "LTX 2.5 Fast",
    "kandinsky6": "Kandinsky 6.0 Lite",
}
# LTX 2.5 の変換器。unsloth の推奨 UD-Q4_K_XL（14.0 GB）。Q5_K_XL（16.0 GB）に替えるときは
# この値と VIDEO_MODEL_FILES の行を差し替え、adapt_video_workflows.py --force で写しを作り直す。
LTX25_GGUF_FILE = "ltx-2.5-22b-distilled-UD-Q4_K_XL.gguf"
LTX25_TEXT_ENCODER_FILE = "gemma4-12b-with-proj-ltx-2.5-bf16.safetensors"
# 公式の拡散デコーダ版（ltx-2.5-video-vae-bf16）は gated。畳み込み版は ungated で軽い。
LTX25_VIDEO_VAE_FILE = "ltx-2.5-video-vae-conv-bf16.safetensors"
HUNYUAN15_SR_FILE = "hunyuanvideo1.5_1080p_sr_distilled_fp8_scaled.safetensors"
# HunyuanVideo 1.5 と Kandinsky 6 で共用する文字エンコーダ（Qwen2.5-VL 7B の fp8_scaled）。
QWEN25_VL_FILE = "qwen_2.5_vl_7b_fp8_scaled.safetensors"
KANDINSKY6_LITE_FILE = "Kandinsky-6.0-Lite-5s-Diffusers/transformer/diffusion_pytorch_model.safetensors"
KANDINSKY6_LITE_DISTILL_FILE = "Kandinsky-6.0-Lite-distill-5s-Diffusers/transformer/diffusion_pytorch_model.safetensors"

VIDEO_MODEL_FILES = (
    # ---- HunyuanVideo 1.5（Comfy-Org/HunyuanVideo_1.5_repackaged は Comfy-Org/Hunyuan-Video-1.5 へ改名されている）
    (
        "hunyuanvideo15",
        "diffusion_models",
        "hunyuanvideo1.5_720p_t2v_fp16.safetensors",
        "Comfy-Org/Hunyuan-Video-1.5",
        "split_files/diffusion_models/hunyuanvideo1.5_720p_t2v_fp16.safetensors",
        16653368128,
        "4f05e2a31b51b7ddee3ef336d234e0932001cbdc85887da15451069fda3e1d05",
    ),
    (
        "hunyuanvideo15",
        "diffusion_models",
        "hunyuanvideo1.5_720p_i2v_fp16.safetensors",
        "Comfy-Org/Hunyuan-Video-1.5",
        "split_files/diffusion_models/hunyuanvideo1.5_720p_i2v_fp16.safetensors",
        16653368128,
        "96883b154e4231e6106515bdef53a091085d5821518c387eb28609fde77e4bf1",
    ),
    (
        "hunyuanvideo15",
        "diffusion_models",
        HUNYUAN15_SR_FILE,
        "Comfy-Org/Hunyuan-Video-1.5",
        "split_files/diffusion_models/" + HUNYUAN15_SR_FILE,
        8335262258,
        "242642db5bb227389724e4c0b20a2224534ac4087597246175421492319698eb",
    ),
    (
        "hunyuanvideo15",
        "latent_upscale_models",
        "hunyuanvideo15_latent_upsampler_1080p.safetensors",
        "Comfy-Org/Hunyuan-Video-1.5",
        "split_files/latent_upscale_models/hunyuanvideo15_latent_upsampler_1080p.safetensors",
        201404760,
        "a3630a7e0ab21084aa69e5a649f59fba34a7110733c9ca3d4fee781323758198",
    ),
    (
        "hunyuanvideo15",
        "text_encoders",
        QWEN25_VL_FILE,
        "Comfy-Org/Hunyuan-Video-1.5",
        "split_files/text_encoders/" + QWEN25_VL_FILE,
        9384670680,
        "cb5636d852a0ea6a9075ab1bef496c0db7aef13c02350571e388aea959c5c0b4",
    ),
    (
        "hunyuanvideo15",
        "text_encoders",
        "byt5_small_glyphxl_fp16.safetensors",
        "Comfy-Org/Hunyuan-Video-1.5",
        "split_files/text_encoders/byt5_small_glyphxl_fp16.safetensors",
        438643184,
        "516910bb4c9b225370290e40585d1b0e6c8cd3583690f7eec2f7fb593990fb48",
    ),
    (
        "hunyuanvideo15",
        "vae",
        "hunyuanvideo15_vae_fp16.safetensors",
        "Comfy-Org/Hunyuan-Video-1.5",
        "split_files/vae/hunyuanvideo15_vae_fp16.safetensors",
        2521292758,
        "e7c3091949c27e2d55ae6d5df917b99dadfebbf308e5a50d0ade0d16c90297ae",
    ),
    (
        "hunyuanvideo15",
        "clip_vision",
        "sigclip_vision_patch14_384.safetensors",
        "Comfy-Org/sigclip_vision_384",
        "sigclip_vision_patch14_384.safetensors",
        856505640,
        "1fee501deabac72f0ed17610307d7131e3e9d1e838d0363aa3c2b97a6e03fb33",
    ),
    # ---- LTX 2.5 Fast（distilled 22B）。公式 Lightricks/LTX-2.5 は gated なので unsloth の ungated な写しから取る。
    (
        "ltx25",
        "diffusion_models",
        LTX25_GGUF_FILE,
        "unsloth/LTX-2.5-GGUF",
        "distilled/" + LTX25_GGUF_FILE,
        14000234880,
        "55442750eb0013d94fffaa4dadb577c5f4dc07107ec04389d41444f6e517d980",
    ),
    (
        "ltx25",
        "text_encoders",
        LTX25_TEXT_ENCODER_FILE,
        "unsloth/LTX-2.5-GGUF",
        "text_encoders/" + LTX25_TEXT_ENCODER_FILE,
        26263858182,
        "ef7243612fdae7a75cb4d5cee9433e81380675fb6c213bd98ae74a9cd16561d1",
    ),
    (
        "ltx25",
        "vae",
        LTX25_VIDEO_VAE_FILE,
        "unsloth/LTX-2.5-GGUF",
        "vae/" + LTX25_VIDEO_VAE_FILE,
        1452269922,
        "685b06ee3d9b2039647698fc4ea33175112462fc374e2777312c907897dfce8d",
    ),
    (
        "ltx25",
        "vae",
        "ltx-2.5-audio-vae-bf16.safetensors",
        "unsloth/LTX-2.5-GGUF",
        "vae/ltx-2.5-audio-vae-bf16.safetensors",
        364866540,
        "c52733d37f6a7fb7949c3dc0fb468c6cb2169e4d836983a73babb9f0d54837a5",
    ),
    (
        "ltx25",
        "latent_upscale_models",
        "ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors",
        "unsloth/LTX-2.5-GGUF",
        "latent_upscale_models/ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors",
        995778752,
        "eb5a71fe4068ee87ccdb1c3aa635e547ca76bd2d30ae20ae889f2c325c0677e8",
    ),
    # プロンプト拡張（Enable Prompt Enhance）用の小さい Gemma 4。写しでは既定で切ってある。
    (
        "ltx25",
        "text_encoders",
        "gemma4_e2b_it_int8_convrot.safetensors",
        "Comfy-Org/gemma-4",
        "text_encoders/gemma4_e2b_it_int8_convrot.safetensors",
        5199997904,
        "efeca0fcad2f863e5ed0a75e3af952b72bc963604c1dda6d20aee87a32b17566",
    ),
    # ---- Kandinsky 6.0 Lite（3B。bf16 のまま A30 に載る）。文字エンコーダの Qwen2.5-VL は上の HunyuanVideo 1.5 と共用。
    (
        "kandinsky6",
        "diffusion_models",
        KANDINSKY6_LITE_FILE,
        "kandinskylab/Kandinsky-6.0-Lite-5s-Diffusers",
        "transformer/diffusion_pytorch_model.safetensors",
        7476528464,
        "1550090ca56fed8c75dda0d30f8aa7b46775e8e1ebb086b70ad532e40eb878f9",
    ),
    (
        "kandinsky6",
        "diffusion_models",
        KANDINSKY6_LITE_DISTILL_FILE,
        "kandinskylab/Kandinsky-6.0-Lite-distill-5s-Diffusers",
        "transformer/diffusion_pytorch_model.safetensors",
        6381599096,
        "b1b52668561d9138d93bb4da13c9999e615bf569be9f8cf65732d54652447f53",
    ),
    (
        "kandinsky6",
        "text_encoders",
        "clip_l.safetensors",
        "Comfy-Org/Hunyuan-Video",
        "split_files/text_encoders/clip_l.safetensors",
        246144152,
        "660c6f5b1abae9dc498ac2d21e1347d2abdb0cf6c0c0c8576cd796491d9a6cdd",
    ),
    (
        "kandinsky6",
        "vae",
        "hunyuan_video_vae_bf16.safetensors",
        "Comfy-Org/Hunyuan-Video",
        "split_files/vae/hunyuan_video_vae_bf16.safetensors",
        492984198,
        "e8f8553275406d84ccf22e7a47601650d8f98bdb8aa9ccfdd6506b57a9701aed",
    ),
    # 音声 VAE（MMAudio の TOD デコーダ）は CC BY-NC 4.0（非商用）。
    (
        "kandinsky6",
        "audio_vae",
        "v1-44.pth",
        "hkchengrex/MMAudio",
        "ext_weights/v1-44.pth",
        1221942998,
        "ab6cc15dc31947675f75c950c41f4dcfd0d6d1817555ac871f809ec388e4651a",
    ),
    (
        "kandinsky6",
        "audio_vae/bigvgan_vocoder",
        "bigvgan_generator.pt",
        "nvidia/bigvgan_v2_44khz_128band_512x",
        "bigvgan_generator.pt",
        489041291,
        "d9fe7ec6bd0b44ed9d66973d5012d8181c1570b01e5c72df51973e241dccd357",
    ),
    (
        "kandinsky6",
        "audio_vae/bigvgan_vocoder",
        "config.json",
        "nvidia/bigvgan_v2_44khz_128band_512x",
        "config.json",
        1403,
        "65b7f487bfaf15256056a75f6667ef5f640214c981922a111927873705ea4ddb",
    ),
)
