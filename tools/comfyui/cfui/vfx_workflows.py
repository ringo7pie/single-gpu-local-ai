"""ゲームのエフェクト（VFX）の写し（Workflows → VFX）を作る純粋関数（scripts/adapt_vfx_workflows.py が使う）。

- WanAlpha_T2V: ComfyUI 同梱のテンプレート video_wan2.1_alpha_t2v_14B.json（Wan 2.1 Alpha T2V 14B）を土台に、
  LightX2V の LoRA（テンプレートでは bypass）を有効にして 4 ステップ・cfg 1.0 にし、832x480・81 コマにする。
  テンプレートの出力（SaveAnimatedWEBP。RGBA のアニメ WebP）に加え、同じ RGBA の画像を SaveImage で PNG の連番にも書く。
- LayerDiffuse_SDXL_RGBA: ComfyUI-layerdiffuse の example_workflows/layer_diffusion_fg_example_rgba.json を土台に、
  チェックポイントを sd_xl_base_1.0 にし、RGBA の出力の PreviewImage を SaveImage にする。

保存先の名前（filename_prefix）は dq_vfx_trial/ の下。どちらも seed は固定（fixed）にする（API で別の PC が値を入れる）。
ファイルシステムにもネットワークにも触れない。何度当てても同じ結果になる。
"""

from __future__ import annotations

import copy
import uuid
from typing import Any

from . import config
from .workflows import WorkflowFormatError, upsert_markdown_note

JA_NOTE_TITLE = "使い方（日本語・この機体）"
JA_NOTE_SIZE = (640, 560)
_VFX_UUID_NAMESPACE = uuid.UUID("6b1f0e7a-3c2d-4e58-9a41-2f7d8c5b9e13")

WAN_TEMPLATE_FILE = "video_wan2.1_alpha_t2v_14B.json"
LAYERDIFFUSE_EXAMPLE_FILE = "layer_diffusion_fg_example_rgba.json"
WAN_WORKFLOW_FILE = "WanAlpha_T2V.json"
LAYERDIFFUSE_WORKFLOW_FILE = "LayerDiffuse_SDXL_RGBA.json"
OUTPUT_SUBDIR = "dq_vfx_trial"

WAN_PROMPT = "a swirling cloud of glowing violet magic mist, soft wisps, centered, no background"
WAN_WIDTH, WAN_HEIGHT, WAN_LENGTH = 832, 480, 81
WAN_STEPS, WAN_CFG = 4, 1.0
LAYERDIFFUSE_PROMPT = "a single bright orange spark particle, glowing, centered, game vfx sprite"
LAYERDIFFUSE_SIZE = 1024
LAYERDIFFUSE_STEPS, LAYERDIFFUSE_CFG = 20, 7.0
SDXL_CHECKPOINT = "sd_xl_base_1.0.safetensors"
DEFAULT_SEED = 42

# テンプレートのノード id（comfyui-workflow-templates-json 0.1.111 の video_wan2.1_alpha_t2v_14B.json）。
WAN_IDS = {"positive": 6, "latent": 40, "sampler": 3, "lightx2v": 59, "join": 86, "webp": 80}
# 足す SaveImage（PNG の連番）の id。テンプレートの last_node_id は 92。
WAN_PNG_SAVE_ID = 93
# layer_diffusion_fg_example_rgba.json のノード id。
LD_IDS = {"checkpoint": 4, "positive": 6, "latent": 5, "sampler": 3, "apply": 13, "decode_rgba": 36, "rgba_preview": 27}


def _node(workflow: dict[str, Any], node_id: int, node_type: str) -> dict[str, Any]:
    for node in workflow.get("nodes") or []:
        if node.get("id") == node_id:
            if node.get("type") != node_type:
                raise WorkflowFormatError(f"ノード {node_id} が {node_type} ではない（{node.get('type')}）。土台の版が変わった可能性")
            return node
    raise WorkflowFormatError(f"ノード {node_id}（{node_type}）が無い。土台の版が変わった可能性")


def _model_entry(file_name: str) -> dict[str, Any]:
    for _group, subdir, name, repo, path_in_repo, _size, _sha in config.VFX_MODEL_FILES:
        if name == file_name:
            return {
                "name": name,
                "url": f"{config.HF_BASE_URL}/{repo}/resolve/main/{path_in_repo}",
                "directory": subdir,
            }
    raise KeyError(file_name)


def wan_note_text() -> str:
    return "\n".join(
        [
            f"# {JA_NOTE_TITLE}",
            "",
            "Wan 2.1 Alpha T2V 14B（透明度つきの動画）。Comfy 公式テンプレートに次を変えた写し:",
            "",
            f"- LightX2V の LoRA を有効にし、KSampler を {WAN_STEPS} ステップ・cfg {WAN_CFG} にした（テンプレートの既定は 20 ステップ・cfg 6）。",
            f"- 大きさ {WAN_WIDTH}x{WAN_HEIGHT}・{WAN_LENGTH} コマ（16 fps で約 5 秒）。",
            f"- 出力は 2 つ: `{OUTPUT_SUBDIR}/wan_alpha_*.webp`（RGBA のアニメ WebP）と `{OUTPUT_SUBDIR}/wan_alpha_png_*.png`（RGBA の PNG の連番）。",
            "- seed は fixed。毎回変えるなら KSampler の control after generate を randomize にする。",
            "",
            "重みは `tools/comfyui/run-comfyui fetch-models --skip-3d --vfx-group wanalpha` で取る（sha256 を照合する）。",
            "GPU は ComfyUI だけで使う（入口ページ 8080 で起動すると、ほかの GPU のサービスは止まる）。",
            "ライセンス（2026-11-25 に HF で確認）: Comfy-Org/Wan_2.1_ComfyUI_repackaged（本体・文字エンコーダ・Alpha の VAE / LoRA）は Apache-2.0。"
            "Kijai/WanVideo_comfy（LightX2V の LoRA）は記載なし（元の lightx2v/Wan2.1-T2V-14B-StepDistill-CfgDistill は Apache-2.0）。",
        ]
    )


def layerdiffuse_note_text() -> str:
    return "\n".join(
        [
            f"# {JA_NOTE_TITLE}",
            "",
            "LayerDiffuse（SDXL で透明な 1 枚絵）。ComfyUI-layerdiffuse の手本 layer_diffusion_fg_example_rgba に次を変えた写し:",
            "",
            f"- チェックポイントを {SDXL_CHECKPOINT} にした（ほかの SDXL は入れていない）。",
            f"- {LAYERDIFFUSE_SIZE}x{LAYERDIFFUSE_SIZE}・{LAYERDIFFUSE_STEPS} ステップ・cfg {LAYERDIFFUSE_CFG:g}。幅と高さは 64 の倍数にする。",
            "- LayeredDiffusionApply は SDXL, Conv Injection・weight 1。LayeredDiffusionDecodeRGBA は SDXL。",
            f"- RGBA の画像を SaveImage で `{OUTPUT_SUBDIR}/layerdiffuse_*.png` に書く。透けない絵は Preview に出るだけ。",
            "",
            "重みは `tools/comfyui/run-comfyui fetch-models --skip-3d --vfx-group layerdiffuse` で取る（models/layer_model/ と checkpoints/）。",
            "ライセンス（2026-11-25 に HF で確認）: SDXL base 1.0 は CreativeML Open RAIL++-M（LICENSE.md）。"
            "LayerDiffusion/layerdiffusion-v1 は CreativeML OpenRAIL-M（LICENSE のファイルは無い）。ComfyUI-layerdiffuse は Apache-2.0。",
        ]
    )


def _add_note(workflow: dict[str, Any], text: str) -> dict[str, Any]:
    xs = [node["pos"][0] for node in workflow["nodes"] if isinstance(node.get("pos"), list)]
    ys = [node["pos"][1] for node in workflow["nodes"] if isinstance(node.get("pos"), list)]
    pos = (min(xs) - JA_NOTE_SIZE[0] - 80, min(ys))
    return upsert_markdown_note(workflow, JA_NOTE_TITLE, text, pos=pos, size=JA_NOTE_SIZE)


def make_wan_alpha(template: dict[str, Any], seed: int = DEFAULT_SEED) -> dict[str, Any]:
    """テンプレートから WanAlpha_T2V の写しを作る。"""

    workflow = copy.deepcopy(template)
    _node(workflow, WAN_IDS["positive"], "CLIPTextEncode")["widgets_values"] = [WAN_PROMPT]
    latent = _node(workflow, WAN_IDS["latent"], "EmptyHunyuanLatentVideo")
    latent["widgets_values"] = [WAN_WIDTH, WAN_HEIGHT, WAN_LENGTH, 1]
    sampler = _node(workflow, WAN_IDS["sampler"], "KSampler")
    values = list(sampler["widgets_values"])
    values[0:4] = [seed, "fixed", WAN_STEPS, WAN_CFG]
    sampler["widgets_values"] = values
    _node(workflow, WAN_IDS["lightx2v"], "LoraLoaderModelOnly")["mode"] = 0
    webp = _node(workflow, WAN_IDS["webp"], "SaveAnimatedWEBP")
    webp_values = list(webp["widgets_values"])
    webp_values[0] = f"{OUTPUT_SUBDIR}/wan_alpha"
    webp["widgets_values"] = webp_values

    join = _node(workflow, WAN_IDS["join"], "JoinImageWithAlpha")
    if not any(node.get("id") == WAN_PNG_SAVE_ID for node in workflow["nodes"]):
        link_id = int(workflow["last_link_id"]) + 1
        join["outputs"][0].setdefault("links", [])
        join["outputs"][0]["links"] = list(join["outputs"][0]["links"] or []) + [link_id]
        workflow["links"].append([link_id, WAN_IDS["join"], 0, WAN_PNG_SAVE_ID, 0, "IMAGE"])
        workflow["last_link_id"] = link_id
        workflow["nodes"].append(
            {
                "id": WAN_PNG_SAVE_ID,
                "type": "SaveImage",
                "pos": [webp["pos"][0], webp["pos"][1] + 700],
                "size": [520, 420],
                "flags": {},
                "order": max(int(node.get("order", 0)) for node in workflow["nodes"]) + 1,
                "mode": 0,
                "inputs": [{"name": "images", "type": "IMAGE", "link": link_id}],
                "outputs": [],
                "title": "Save Image（RGBA の PNG の連番）",
                "properties": {"Node name for S&R": "SaveImage"},
                "widgets_values": [f"{OUTPUT_SUBDIR}/wan_alpha_png"],
            }
        )
        workflow["last_node_id"] = max(int(workflow.get("last_node_id", 0)), WAN_PNG_SAVE_ID)
    workflow = _add_note(workflow, wan_note_text())
    workflow["id"] = str(uuid.uuid5(_VFX_UUID_NAMESPACE, WAN_WORKFLOW_FILE))
    return workflow


def make_layerdiffuse(example: dict[str, Any], seed: int = DEFAULT_SEED) -> dict[str, Any]:
    """手本から LayerDiffuse_SDXL_RGBA の写しを作る。"""

    workflow = copy.deepcopy(example)
    checkpoint = _node(workflow, LD_IDS["checkpoint"], "CheckpointLoaderSimple")
    checkpoint["widgets_values"] = [SDXL_CHECKPOINT]
    checkpoint.setdefault("properties", {})["models"] = [_model_entry(SDXL_CHECKPOINT)]
    _node(workflow, LD_IDS["positive"], "CLIPTextEncode")["widgets_values"] = [LAYERDIFFUSE_PROMPT]
    _node(workflow, LD_IDS["latent"], "EmptyLatentImage")["widgets_values"] = [LAYERDIFFUSE_SIZE, LAYERDIFFUSE_SIZE, 1]
    sampler = _node(workflow, LD_IDS["sampler"], "KSampler")
    values = list(sampler["widgets_values"])
    values[0:4] = [seed, "fixed", LAYERDIFFUSE_STEPS, LAYERDIFFUSE_CFG]
    sampler["widgets_values"] = values
    _node(workflow, LD_IDS["apply"], "LayeredDiffusionApply")["widgets_values"] = ["SDXL, Conv Injection", 1]
    decode = _node(workflow, LD_IDS["decode_rgba"], "LayeredDiffusionDecodeRGBA")
    decode_values = list(decode["widgets_values"])
    decode_values[0] = "SDXL"
    decode["widgets_values"] = decode_values

    for node in workflow["nodes"]:
        if node.get("id") == LD_IDS["rgba_preview"] and node.get("type") in ("PreviewImage", "SaveImage"):
            node["type"] = "SaveImage"
            node["title"] = "Save Image（RGBA）"
            node["properties"] = {"Node name for S&R": "SaveImage"}
            node["widgets_values"] = [f"{OUTPUT_SUBDIR}/layerdiffuse"]
            break
    else:
        raise WorkflowFormatError(f"ノード {LD_IDS['rgba_preview']}（RGBA の PreviewImage）が無い")
    workflow = _add_note(workflow, layerdiffuse_note_text())
    workflow["id"] = str(uuid.uuid5(_VFX_UUID_NAMESPACE, LAYERDIFFUSE_WORKFLOW_FILE))
    return workflow
