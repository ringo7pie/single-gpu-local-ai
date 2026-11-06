"""動画生成の写し（Workflows → Video）を作る純粋関数（scripts/adapt_video_workflows.py が使う）。

土台は ComfyUI 同梱のテンプレート（HunyuanVideo 1.5・LTX 2.5）と、Kandinsky 6.0 の拡張の example_workflows/。
A30（VRAM 24 GiB、FP8 の演算器なし）でゲートの無い重みだけで動くように、次だけを変える（ほかのノードの値・結線は変えない）。

- HunyuanVideo 1.5: 1080p 化の後段（既定で bypass）の変換器を fp8_scaled にする。
- LTX 2.5 Fast: subgraph の中の UNETLoader を Unet Loader (GGUF) にし、文字エンコーダを bf16、動画の VAE を畳み込み版にする
  （外のノードの widgets_values も同じ値にする。画面と API 変換はそちらを使う）。
- Kandinsky 6.0 Lite / Lite distill: Pro distill を Lite に、プロンプトの書き足し（Qwen3.5 9B）と超解像（kandinsky6-sr）を外し、
  MagCache（Pro 専用）を bypass にし、サンプラーと音声 VAE の倍率を上流の README の Lite の値にする。
- どれも日本語の手順ノートを足し、保存先の名前（SaveVideo の filename_prefix）を video/<写しの名前> にし、別の workflow id を付ける。

ファイルシステムにもネットワークにも触れない。何度当てても同じ結果になる。
"""

from __future__ import annotations

import copy
import uuid
from typing import Any

from . import config, notes_ja
from .workflows import WorkflowFormatError, upsert_markdown_note

JA_NOTE_TITLE = "使い方（日本語・この機体）"
JA_NOTE_SIZE = (760, 760)
# ノートを置く位置は、グラフの左上の角から左へずらしたところ（土台ごとに違うので計算する）。
JA_NOTE_GAP = 80
VIEW_SCALE = 0.5
_VIDEO_UUID_NAMESPACE = uuid.UUID("2d8c4a51-7f3e-4b9a-8e61-5c0f9b7a3d24")

BYPASS_MODE = 4
HF_RESOLVE = config.HF_BASE_URL + "/{repo}/resolve/main/{path}"

HUNYUAN_SR_FP16 = "hunyuanvideo1.5_1080p_sr_distilled_fp16.safetensors"
LTX_TEMPLATE_UNET = "ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors"
LTX_TEMPLATE_TEXT_ENCODER = "gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors"
LTX_TEMPLATE_VIDEO_VAE = "ltx-2.5-video-vae-bf16.safetensors"
KANDINSKY_UPSTREAM_QWEN = "qwen_2.5_vl_7b.safetensors"
# Kandinsky 6 の拡張で外すノード（プロンプトの書き足しと、別の拡張 kandinsky6-sr の超解像）。
KANDINSKY_REMOVED_TYPES = frozenset(
    {"Kandinsky6BeautifyPrompt", "PreviewAny", "Kandinsky6VSRUpscale", "Kandinsky6SRVAELoader", "Kandinsky6LatentUpscalerLoader"}
)
# 上流 README（kandinsky-6/comfyui/README.md の Defaults）の値。
KANDINSKY_SAMPLER = {
    "lite": {"steps": 50, "cfg": 5.0, "audio_scaling": 0.5302},
    "distill": {"steps": 10, "cfg": 1.0, "audio_scaling": 0.417},
}


def _model_entry(file_name: str) -> dict[str, Any]:
    """properties.models に書く 1 件（画面の「足りない重み」の案内に使われる）。VIDEO_MODEL_FILES から引く。"""

    for _group, subdir, name, repo, path_in_repo, _size, _sha in config.VIDEO_MODEL_FILES:
        if name == file_name:
            return {"name": name, "url": HF_RESOLVE.format(repo=repo, path=path_in_repo), "directory": subdir.split("/")[0]}
    raise KeyError(file_name)


def _set_models(node: dict[str, Any], file_names: list[str]) -> None:
    node.setdefault("properties", {})["models"] = [_model_entry(name) for name in file_names]


def _nodes_of(container: dict[str, Any], node_type: str) -> list[dict[str, Any]]:
    return [node for node in container.get("nodes") or [] if node.get("type") == node_type]


def _single(container: dict[str, Any], node_type: str, where: str) -> dict[str, Any]:
    found = _nodes_of(container, node_type)
    if len(found) != 1:
        raise WorkflowFormatError(f"{where}: {node_type} がちょうど 1 つではない（{len(found)} 個）")
    return found[0]


def _set_save_prefix(workflow: dict[str, Any], prefix: str) -> None:
    saves = _nodes_of(workflow, "SaveVideo")
    if not saves:
        raise WorkflowFormatError("SaveVideo が無い")
    for index, node in enumerate(sorted(saves, key=lambda item: int(item["id"]))):
        values = node.get("widgets_values")
        if not isinstance(values, list) or not values:
            raise WorkflowFormatError(f"#{node['id']} SaveVideo に widgets_values が無い")
        # 2 つ目以降（1080p 化の後段など）は _sr、_2 … を付ける。
        suffix = "" if index == 0 else ("_sr" if "_sr" in str(values[0]) else f"_{index + 1}")
        values[0] = prefix + suffix


def _place_note(workflow: dict[str, Any], kind: str) -> dict[str, Any]:
    """左上の角の左に日本語ノートを置き（すでにあれば文面だけ直す）、開いたときにノートが見える表示にする。"""

    nodes = [node for node in workflow.get("nodes") or [] if node.get("title") != JA_NOTE_TITLE]
    if not nodes:
        raise WorkflowFormatError("nodes が空")
    left = min(float(node["pos"][0]) for node in nodes)
    top = min(float(node["pos"][1]) for node in nodes)
    pos = (left - JA_NOTE_SIZE[0] - JA_NOTE_GAP, top)
    noted = upsert_markdown_note(workflow, JA_NOTE_TITLE, notes_ja.video_note_for(kind), pos=pos, size=JA_NOTE_SIZE)
    note = next(node for node in noted["nodes"] if node.get("title") == JA_NOTE_TITLE)
    note_x, note_y = float(note["pos"][0]), float(note["pos"][1])
    noted.setdefault("extra", {})["ds"] = {"scale": VIEW_SCALE, "offset": [-note_x + 160.0, -note_y + 60.0]}
    return noted


def _retarget_id(workflow: dict[str, Any], kind: str) -> None:
    """土台と別の workflow id（画面が別のワークフローとして扱う）。土台の id と種類から決める（何度当てても同じ）。"""

    base_id = str(workflow.get("extra", {}).get("localai_base_id") or workflow.get("id") or "")
    workflow.setdefault("extra", {})["localai_base_id"] = base_id
    workflow["id"] = str(uuid.uuid5(_VIDEO_UUID_NAMESPACE, f"{base_id}:{kind}"))


# ---------------------------------------------------------------- HunyuanVideo 1.5


def adapt_hunyuan(base: dict[str, Any], kind: str) -> dict[str, Any]:
    workflow = copy.deepcopy(base)
    sr_loaders = [
        node for node in _nodes_of(workflow, "UNETLoader")
        if (node.get("widgets_values") or [None])[0] in (HUNYUAN_SR_FP16, config.HUNYUAN15_SR_FILE)
    ]
    if len(sr_loaders) != 1:
        raise WorkflowFormatError(f"1080p 化の UNETLoader がちょうど 1 つではない（{len(sr_loaders)} 個）")
    sr_loaders[0]["widgets_values"][0] = config.HUNYUAN15_SR_FILE
    _set_models(sr_loaders[0], [config.HUNYUAN15_SR_FILE])
    _set_save_prefix(workflow, f"video/{_stem(kind)}")
    return workflow


# ---------------------------------------------------------------- LTX 2.5 Fast


def _ltx_subgraph(workflow: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    definitions = (workflow.get("definitions") or {}).get("subgraphs") or []
    ids = {str(item.get("id")) for item in definitions}
    instances = [node for node in workflow.get("nodes") or [] if str(node.get("type")) in ids]
    if len(instances) != 1 or len(definitions) != 1:
        raise WorkflowFormatError(f"LTX の subgraph がちょうど 1 つではない（定義 {len(definitions)}・使う側 {len(instances)}）")
    return instances[0], definitions[0]


def _promoted_index(definition: dict[str, Any], input_name: str) -> int:
    """外のノードの widgets_values のうち、subgraph の入力 input_name の値の位置（widget につながる入力の順）。"""

    inner = {str(node["id"]): node for node in definition.get("nodes") or []}
    first_target: dict[int, tuple[str, int]] = {}
    for link in definition.get("links") or []:
        if int(link["origin_id"]) == -10 and int(link["origin_slot"]) not in first_target:
            first_target[int(link["origin_slot"])] = (str(link["target_id"]), int(link["target_slot"]))
    position = 0
    for index, item in enumerate(definition.get("inputs") or []):
        target = first_target.get(index)
        if target is None or target[0] not in inner:
            continue
        target_inputs = inner[target[0]].get("inputs") or []
        if not (target[1] < len(target_inputs) and target_inputs[target[1]].get("widget")):
            continue
        if item.get("name") == input_name:
            return position
        position += 1
    raise WorkflowFormatError(f"subgraph の入力 {input_name} が widget につながっていない")


def _inner_loader(definition: dict[str, Any], node_types: tuple[str, ...], values: tuple[str, ...]) -> dict[str, Any]:
    found = [
        node for node in definition.get("nodes") or []
        if node.get("type") in node_types and (node.get("widgets_values") or [None])[0] in values
    ]
    if len(found) != 1:
        raise WorkflowFormatError(f"subgraph の中に {node_types}（{values[0]}）がちょうど 1 つではない（{len(found)} 個）")
    return found[0]


def adapt_ltx(base: dict[str, Any], kind: str) -> dict[str, Any]:
    workflow = copy.deepcopy(base)
    instance, definition = _ltx_subgraph(workflow)
    values = instance.get("widgets_values")
    if not isinstance(values, list):
        raise WorkflowFormatError("LTX の subgraph のノードに widgets_values が無い")

    unet = _inner_loader(definition, ("UNETLoader", "UnetLoaderGGUF"), (LTX_TEMPLATE_UNET, config.LTX25_GGUF_FILE))
    unet["type"] = "UnetLoaderGGUF"
    unet["widgets_values"] = [config.LTX25_GGUF_FILE]
    properties = unet.setdefault("properties", {})
    properties["Node name for S&R"] = "UnetLoaderGGUF"
    properties["cnr_id"] = "comfyui-gguf"
    properties["ver"] = dict((name, commit) for name, _url, commit in config.CUSTOM_NODES)[config.GGUF_NODE_NAME]
    _set_models(unet, [config.LTX25_GGUF_FILE])

    text_encoder = _inner_loader(
        definition, ("CLIPLoader",), (LTX_TEMPLATE_TEXT_ENCODER, config.LTX25_TEXT_ENCODER_FILE)
    )
    text_encoder["widgets_values"][0] = config.LTX25_TEXT_ENCODER_FILE
    _set_models(text_encoder, [config.LTX25_TEXT_ENCODER_FILE])

    video_vae = _inner_loader(definition, ("VAELoader",), (LTX_TEMPLATE_VIDEO_VAE, config.LTX25_VIDEO_VAE_FILE))
    video_vae["widgets_values"][0] = config.LTX25_VIDEO_VAE_FILE
    _set_models(video_vae, [config.LTX25_VIDEO_VAE_FILE])

    # 外のノードの値（画面はこちらを中のノードへ渡す）も同じにする。
    for input_name, value in (
        ("unet_name", config.LTX25_GGUF_FILE),
        ("clip_name", config.LTX25_TEXT_ENCODER_FILE),
        ("vae_name", config.LTX25_VIDEO_VAE_FILE),
    ):
        index = _promoted_index(definition, input_name)
        if index >= len(values):
            raise WorkflowFormatError(f"LTX の subgraph のノードの widgets_values に {input_name} の値が無い")
        values[index] = value
    _set_save_prefix(workflow, f"video/{_stem(kind)}")
    return workflow


# ---------------------------------------------------------------- Kandinsky 6.0 Lite


def _remove_nodes(workflow: dict[str, Any], node_ids: set[int]) -> None:
    """ノードと、それにつながるリンクを消す。消したリンクを受けていた入力は外す（widget の入力は widget に戻る）。"""

    removed_links = {link[0] for link in workflow.get("links") or [] if link[1] in node_ids or link[3] in node_ids}
    workflow["links"] = [link for link in workflow.get("links") or [] if link[0] not in removed_links]
    workflow["nodes"] = [node for node in workflow.get("nodes") or [] if node["id"] not in node_ids]
    for node in workflow["nodes"]:
        for item in node.get("inputs") or []:
            if item.get("link") in removed_links:
                item["link"] = None
        for item in node.get("outputs") or []:
            if item.get("links"):
                item["links"] = [link_id for link_id in item["links"] if link_id not in removed_links]


def _connect(workflow: dict[str, Any], origin: dict[str, Any], origin_slot: int, target: dict[str, Any], input_name: str) -> None:
    """origin の出力 origin_slot を target の入力 input_name へつなぐ（前のリンクは消す）。"""

    slot = next((index for index, item in enumerate(target.get("inputs") or []) if item.get("name") == input_name), None)
    if slot is None:
        raise WorkflowFormatError(f"#{target['id']} {target.get('type')} に入力 {input_name} が無い")
    previous = target["inputs"][slot].get("link")
    if previous is not None and any(
        link[0] == previous and link[1] == origin["id"] and link[2] == origin_slot for link in workflow["links"]
    ):
        return  # すでにつながっている（何度当てても同じ結果にする）
    if previous is not None:
        workflow["links"] = [link for link in workflow["links"] if link[0] != previous]
        for node in workflow["nodes"]:
            for item in node.get("outputs") or []:
                if item.get("links"):
                    item["links"] = [link_id for link_id in item["links"] if link_id != previous]
    link_id = max([int(workflow.get("last_link_id") or 0)] + [int(link[0]) for link in workflow["links"]]) + 1
    link_type = (origin.get("outputs") or [])[origin_slot].get("type")
    workflow["links"].append([link_id, origin["id"], origin_slot, target["id"], slot, link_type])
    workflow["last_link_id"] = link_id
    target["inputs"][slot]["link"] = link_id
    origin["outputs"][origin_slot].setdefault("links", [])
    if origin["outputs"][origin_slot]["links"] is None:
        origin["outputs"][origin_slot]["links"] = []
    origin["outputs"][origin_slot]["links"].append(link_id)


def adapt_kandinsky(base: dict[str, Any], kind: str) -> dict[str, Any]:
    workflow = copy.deepcopy(base)
    variant = "distill" if "_distill_" in kind else "lite"
    model_file = config.KANDINSKY6_LITE_DISTILL_FILE if variant == "distill" else config.KANDINSKY6_LITE_FILE
    settings = KANDINSKY_SAMPLER[variant]

    beautify = _nodes_of(workflow, "Kandinsky6BeautifyPrompt")
    captions = list((beautify[0].get("widgets_values") or [])[:2]) if beautify else []
    # プロンプトの書き足し用の CLIPLoader（Qwen3.5 9B）は、外す Beautify にだけつながっている。
    beautify_ids = {node["id"] for node in beautify}
    beautify_clip = {
        link[1] for link in workflow.get("links") or [] if link[3] in beautify_ids
        and any(node["id"] == link[1] and node.get("type") == "CLIPLoader" for node in workflow["nodes"])
    }
    vsr_unets = {
        node["id"] for node in _nodes_of(workflow, "UNETLoader")
        if "VSR" in str((node.get("widgets_values") or [""])[0])
    }
    removed = {node["id"] for node in workflow["nodes"] if node.get("type") in KANDINSKY_REMOVED_TYPES} | beautify_clip | vsr_unets
    if removed:
        _remove_nodes(workflow, removed)

    unet = _single(workflow, "UNETLoader", "Kandinsky")
    unet["widgets_values"] = [model_file, "default"]
    _set_models(unet, [model_file])

    clip = _single(workflow, "DualCLIPLoader", "Kandinsky")
    if clip["widgets_values"][0] in (KANDINSKY_UPSTREAM_QWEN, config.QWEN25_VL_FILE):
        clip["widgets_values"][0] = config.QWEN25_VL_FILE
    _set_models(clip, [config.QWEN25_VL_FILE, "clip_l.safetensors"])

    for magcache in _nodes_of(workflow, "Kandinsky6MagCache"):
        magcache["mode"] = BYPASS_MODE

    sampler = _single(workflow, "Kandinsky6Sampler", "Kandinsky")
    # widgets_values: seed, control_after_generate, steps, cfg, sampler_name, scheduler, denoise
    sampler["widgets_values"][2] = settings["steps"]
    sampler["widgets_values"][3] = settings["cfg"]

    audio_loader = _single(workflow, "Kandinsky6AudioVAELoader", "Kandinsky")
    audio_loader["widgets_values"][3] = settings["audio_scaling"]

    # 書き足しを外したので、映像と音のプロンプトは TextEncode の widget に直接書く（上流の例の短い説明文を使う）。
    encoders = [
        node for node in _nodes_of(workflow, "Kandinsky6TextEncode")
        if any(item.get("name") == "video_caption" and item.get("widget") for item in node.get("inputs") or [])
        and not str(node.get("title") or "").lower().startswith("negative")
    ]
    if len(encoders) != 1:
        raise WorkflowFormatError(f"正のプロンプトの Kandinsky6TextEncode がちょうど 1 つではない（{len(encoders)} 個）")
    positive = encoders[0]
    positive["title"] = config.KANDINSKY6_PROMPT_TITLE
    if captions:
        positive["widgets_values"] = [str(captions[0]), str(captions[1]) if len(captions) > 1 else ""]

    # 超解像を外したので、デコードした映像・音と EmptyLatent の fps を CreateVideo へ直につなぐ。
    create = _single(workflow, "CreateVideo", "Kandinsky")
    decode = _single(workflow, "VAEDecode", "Kandinsky")
    audio_decode = _single(workflow, "Kandinsky6AudioVAEDecode", "Kandinsky")
    empty = _single(workflow, "Kandinsky6EmptyLatent", "Kandinsky")
    _connect(workflow, decode, 0, create, "images")
    _connect(workflow, audio_decode, 0, create, "audio")
    _connect(workflow, empty, 1, create, "fps")

    _set_save_prefix(workflow, f"video/{_stem(kind)}")
    workflow["last_node_id"] = max(int(node["id"]) for node in workflow["nodes"])
    for key in ("name", "description"):
        workflow.pop(key, None)
    workflow.setdefault("extra", {}).pop("name", None)
    workflow["extra"].pop("description", None)
    return workflow


# ---------------------------------------------------------------- まとめ


def _stem(kind: str) -> str:
    for file_name, _base, entry_kind in config.VIDEO_WORKFLOWS:
        if entry_kind == kind:
            return file_name.rsplit(".", 1)[0]
    raise KeyError(kind)


def make_video_workflow(base: dict[str, Any], kind: str) -> dict[str, Any]:
    """土台（テンプレートか拡張の例）から、種類 kind（config.VIDEO_WORKFLOWS の 3 つ目）の写しを作る。"""

    if not isinstance(base.get("nodes"), list):
        raise WorkflowFormatError("nodes が無い（ComfyUI の UI 形式のワークフローではない）")
    if kind.startswith("hunyuan_"):
        adapted = adapt_hunyuan(base, kind)
    elif kind.startswith("ltx_"):
        adapted = adapt_ltx(base, kind)
    elif kind.startswith("kandinsky_"):
        adapted = adapt_kandinsky(base, kind)
    else:
        raise KeyError(kind)
    adapted = _place_note(adapted, kind)
    _retarget_id(adapted, kind)
    return adapted
