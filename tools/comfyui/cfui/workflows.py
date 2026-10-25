"""PixelArtistry のワークフロー（ComfyUI の UI 形式 JSON）を、この機体向けに書き換える純粋関数。

ファイルシステムにもネットワークにも触れない（読み書きは scripts/adapt_workflows.py が行う）。
torch も ComfyUI も import しないので、素の python から試験できる。

UI 形式の要点（配布元の 4 本で確かめた形）:
  - nodes[] の各要素は id / type / inputs[] / outputs[] / widgets_values を持つ
  - inputs[i]["link"] は入ってくる link の id（無ければ None）
  - outputs[i]["links"] は出ていく link の id の一覧（無ければ None か []）
  - links[] の各要素は [link_id, 元 node_id, 元 slot, 先 node_id, 先 slot, 型]
"""

from __future__ import annotations

import copy
import json
import uuid
from typing import Any

BLENDER_PATH_NODE_TYPE = "PrimitiveString"
# 配布元の既定値。利用者が紫の「Blender path」ノードに自分のパスを貼る前提になっている。
UPSTREAM_BLENDER_VALUE = "blender"
BLENDER_PATH_INPUT_NAME = "blender_path"

WTIVO_NODE_TYPE = "WTiVoNativeMeshToMesh"
LODTAILOR_NODE_TYPE = "LODTailorTheMeshTrimmer"

# LODTailorTheMeshTrimmer.INPUT_TYPES の並び（LODTailor-The-Mesh-Trimmer-ComfyuiNode/__init__.py:832-920）。
# widgets_values はこの順に並ぶ（mesh は結線なので含まない）。
LODTAILOR_WIDGET_NAMES = (
    "blender_path",
    "target_tris",
    "passes",
    "tolerance",
    "intermediate_ratio",
    "last_ratio",
    "final_ratio",
    "triangulate",
    "symmetry",
    "voxel_rebuild",
    "relative_voxel_size",
    "minimum_voxel_size",
    "seal_distance",
    "seal_max_steps",
    "seal_keep_trying",
    "timeout_seconds",
)

# 予備案の Voxel Remesh の細かさ。WTiVo の input_res / final_res（1536）と同じ格子間隔にする。
FALLBACK_RELATIVE_VOXEL_SIZE = 1.0 / 1536.0

# 予備案の写しの UUID を、元の id から決まった値で作るための名前空間。
_FALLBACK_UUID_NAMESPACE = uuid.UUID("6f1d3f0e-6b0b-4c55-9d5a-2f8f3d9a6c01")
# 入口ワークフロー（make_easy_image_to_3d）の UUID を、テンプレートの id から決まった値で作るための名前空間。
_EASY_UUID_NAMESPACE = uuid.UUID("b3a6f0c2-5d1e-4a7b-9c38-0e2d7f4a1b65")

LOAD_IMAGE_NODE_TYPE = "LoadImage"
SAVE_3D_NODE_TYPE = "Save3DAdvanced"
MARKDOWN_NOTE_NODE_TYPE = "MarkdownNote"
DECIMATE_NODE_TYPE = "DecimateMesh"
PRIMITIVE_INT_NODE_TYPE = "PrimitiveInt"

# 上流テンプレートで、ベースカラー・金属・粗さの大きさ（UnwrapMesh.resolution と BakeTextureFromVoxel.texture_size）を
# 1 か所で決めている PrimitiveInt の題名。アプリの入力にするとき、日本語の題名に直す。
TEXTURE_SIZE_UPSTREAM_TITLE = "Texture Resolution"
TEXTURE_SIZE_TITLE = "テクスチャサイズ（px）"
# DecimateMesh.target_face_count へつなぐ PrimitiveInt（足すノード）の題名。アプリの入力欄の名前になる。
POLYGON_COUNT_TITLE = "ポリゴン数（目標の面数）"
DECIMATE_INPUT_NAME = "target_face_count"

EASY_NOTE_TITLE = "使い方（画像 1 枚から 3D）"
EASY_NOTE_TEXT = """## 画像 1 枚から 3D モデルを作る

1. **Load Image** で、3D にしたい画像を選ぶ（「Upload Input Image Here」の枠。ドラッグ＆ドロップでもよい）。
2. 必要なら **ポリゴン数** と **テクスチャサイズ** を変える（変えなければ 70 万面・4096px）。
3. **実行（Run）** を押す。
4. 終わると **Save 3D** のビューアに 3D モデルが出る。GLB は ComfyUI の `output/3d/` に保存される。

- ポリゴン数: 目標の面数。形を作る段階の面数（斧の例で約 335 万面。画像で変わる）より増えない。
  0 は間引かない。少ないほど軽く、多いほど細かい（約 3 万面〜335 万面で確認）。
- テクスチャサイズ: ベースカラー・金属・粗さの大きさ。256〜8192 の 256 の倍数（1024 / 2048 / 4096 / 8192 など）。
  法線マップは 2048px、AO は 1024px のまま変わらない。
- 1 つの物体が大きく写った画像が向く。背景は自動で取り除く。
- 「Switch Model Here」のスイッチ: false = Pixal3D（既定）、true = TRELLIS.2。
- 実行中は GPU を使う。入口ページ（ポート 8080）で、ほかのサービスを止めて ComfyUI に切り替えてから実行する。
"""
# グラフ表示で開いたときの初期の位置と縮尺。テンプレートは全体が入る縮尺 0.1 で開くため、画像を置く場所が小さすぎる。
# 画面の左上に映るグラフ座標は (-offset_x, -offset_y)。縮尺 0.4 なら、横 1700px で約 4250 の幅が見える。
EASY_VIEW = {"scale": 0.4, "offset": [5900.0, 150.0]}


class WorkflowFormatError(ValueError):
    """想定した形のワークフローではない（上流が変わった可能性がある）。"""


def _nodes_by_id(workflow: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {node["id"]: node for node in workflow.get("nodes", [])}


def _links_by_id(workflow: dict[str, Any]) -> dict[int, list[Any]]:
    return {link[0]: link for link in workflow.get("links", [])}


def find_blender_path_nodes(workflow: dict[str, Any]) -> list[dict[str, Any]]:
    """Blender のパスを配る PrimitiveString を返す。

    出ていく link の先が `blender_path` という名前の入力になっているものだけを数える
    （PrimitiveString は他の用途にも使われうるので、型だけでは決めない）。
    """

    nodes = _nodes_by_id(workflow)
    links = _links_by_id(workflow)
    found = []
    for node in workflow.get("nodes", []):
        if node.get("type") != BLENDER_PATH_NODE_TYPE:
            continue
        feeds_blender_path = False
        for output in node.get("outputs") or []:
            for link_id in output.get("links") or []:
                link = links.get(link_id)
                if link is None:
                    continue
                target = nodes.get(link[3])
                if target is None:
                    continue
                target_slot = link[4]
                target_inputs = target.get("inputs") or []
                if 0 <= target_slot < len(target_inputs) and (
                    target_inputs[target_slot].get("name") == BLENDER_PATH_INPUT_NAME
                ):
                    feeds_blender_path = True
        if feeds_blender_path:
            found.append(node)
    return found


def set_blender_path(workflow: dict[str, Any], blender_path: str) -> dict[str, Any]:
    """Blender のパスのノードの値を blender_path にした写しを返す。元の dict は変えない。

    配布元の 4 本は、どれも Blender のパスのノードが 1 つで、値が "blender"。
    そうでなければ上流が変わった可能性があるので、黙って進めずに止める。
    """

    adapted = copy.deepcopy(workflow)
    targets = find_blender_path_nodes(adapted)
    if len(targets) != 1:
        raise WorkflowFormatError(
            f"Blender のパスのノード（{BLENDER_PATH_NODE_TYPE} → {BLENDER_PATH_INPUT_NAME}）が "
            f"{len(targets)} 個あります（1 個を想定）"
        )
    node = targets[0]
    values = node.get("widgets_values")
    if not isinstance(values, list) or len(values) != 1 or values[0] != UPSTREAM_BLENDER_VALUE:
        raise WorkflowFormatError(
            f"Blender のパスのノード（id={node.get('id')}）の値が想定と違います: {values!r}"
            f"（[{UPSTREAM_BLENDER_VALUE!r}] を想定）"
        )
    node["widgets_values"] = [blender_path]
    return adapted


def blender_path_value(workflow: dict[str, Any]) -> str | None:
    """Blender のパスのノードの値を返す（1 個でなければ None）。doctor の確認用。"""

    targets = find_blender_path_nodes(workflow)
    if len(targets) != 1:
        return None
    values = targets[0].get("widgets_values")
    if isinstance(values, list) and len(values) == 1 and isinstance(values[0], str):
        return values[0]
    return None


def has_wtivo(workflow: dict[str, Any]) -> bool:
    return any(node.get("type") == WTIVO_NODE_TYPE for node in workflow.get("nodes", []))


def _remove_node_and_bridge(workflow: dict[str, Any], node_id: int) -> list[int]:
    """MESH → MESH のノードを 1 つ外し、入力の元を出力の先へ直接つなぐ。

    つなぎ直した先の node id の一覧を返す。workflow は直接書き換える。
    """

    nodes = _nodes_by_id(workflow)
    links = _links_by_id(workflow)
    node = nodes[node_id]

    connected_inputs = [entry for entry in node.get("inputs") or [] if entry.get("link") is not None]
    if len(connected_inputs) != 1:
        raise WorkflowFormatError(
            f"{node.get('type')}（id={node_id}）の結線された入力が {len(connected_inputs)} 本あります（1 本を想定）"
        )
    incoming = links[connected_inputs[0]["link"]]
    origin_id, origin_slot = incoming[1], incoming[2]
    origin_outputs = nodes[origin_id].get("outputs") or []
    origin_links = origin_outputs[origin_slot].get("links") or []

    outgoing_ids = []
    for output in node.get("outputs") or []:
        outgoing_ids.extend(output.get("links") or [])

    retargeted = []
    for link_id in outgoing_ids:
        link = links[link_id]
        # 出ていく link の元を、外すノードではなく、その入力の元に付け替える（先と型はそのまま）。
        link[1] = origin_id
        link[2] = origin_slot
        retargeted.append(link[3])

    # 元ノードの出力からは、外すノードへの link を抜き、付け替えた link を足す。
    new_origin_links = [link_id for link_id in origin_links if link_id != incoming[0]]
    new_origin_links.extend(outgoing_ids)
    origin_outputs[origin_slot]["links"] = new_origin_links

    workflow["links"] = [link for link in workflow["links"] if link[0] != incoming[0]]
    workflow["nodes"] = [entry for entry in workflow["nodes"] if entry["id"] != node_id]
    return retargeted


def _set_lodtailor_widget(node: dict[str, Any], name: str, value: Any) -> None:
    values = node.get("widgets_values")
    if not isinstance(values, list) or len(values) != len(LODTAILOR_WIDGET_NAMES):
        raise WorkflowFormatError(
            f"{LODTAILOR_NODE_TYPE}（id={node.get('id')}）の widgets_values の数が想定と違います"
            f"（{len(values) if isinstance(values, list) else values!r} 個、{len(LODTAILOR_WIDGET_NAMES)} 個を想定）"
        )
    values[LODTAILOR_WIDGET_NAMES.index(name)] = value


def make_fallback(
    workflow: dict[str, Any], relative_voxel_size: float = FALLBACK_RELATIVE_VOXEL_SIZE
) -> dict[str, Any]:
    """予備案の写しを返す: WTiVo を外し、直後の LODTailor の Voxel Remesh で防水化する。

    - WTiVoNativeMeshToMesh を消し、その入力の元を出力の先へ直接つなぐ
    - WTiVo の出力を受けていた LODTailorTheMeshTrimmer の voxel_rebuild を真、
      relative_voxel_size を relative_voxel_size（既定 1/1536）にする
    - workflow の id は、元の id から決まる別の UUID にする（同じ id の写しが 2 つ並ばないように）
    それ以外は変えない。WTiVo を含まないワークフローは WorkflowFormatError にする。
    """

    fallback = copy.deepcopy(workflow)
    wtivo_ids = [node["id"] for node in fallback.get("nodes", []) if node.get("type") == WTIVO_NODE_TYPE]
    if not wtivo_ids:
        raise WorkflowFormatError(f"{WTIVO_NODE_TYPE} がありません（予備案は要りません）")

    retargeted_ids: list[int] = []
    for node_id in wtivo_ids:
        retargeted_ids.extend(_remove_node_and_bridge(fallback, node_id))

    nodes = _nodes_by_id(fallback)
    lodtailor_ids = [
        target_id for target_id in retargeted_ids if nodes[target_id].get("type") == LODTAILOR_NODE_TYPE
    ]
    if not lodtailor_ids:
        raise WorkflowFormatError(f"WTiVo の出力を受ける {LODTAILOR_NODE_TYPE} がありません")
    for target_id in lodtailor_ids:
        _set_lodtailor_widget(nodes[target_id], "voxel_rebuild", True)
        _set_lodtailor_widget(nodes[target_id], "relative_voxel_size", relative_voxel_size)

    if isinstance(fallback.get("id"), str):
        fallback["id"] = str(uuid.uuid5(_FALLBACK_UUID_NAMESPACE, fallback["id"] + ":noWTiVo"))
    return fallback


def _single_node(workflow: dict[str, Any], node_type: str) -> dict[str, Any]:
    found = [node for node in workflow.get("nodes", []) if node.get("type") == node_type]
    if len(found) != 1:
        raise WorkflowFormatError(
            f"{node_type} がちょうど 1 個あるはずだが {len(found)} 個ある（上流のテンプレートが変わった可能性）"
        )
    return found[0]


def _single_titled_node(workflow: dict[str, Any], node_type: str, titles: tuple[str, ...]) -> dict[str, Any] | None:
    """型が node_type で題名が titles のどれかのノード。無ければ None、2 個以上あれば WorkflowFormatError。"""

    found = [
        node for node in workflow.get("nodes", []) if node.get("type") == node_type and node.get("title") in titles
    ]
    if len(found) > 1:
        raise WorkflowFormatError(f"{node_type}「{titles[0]}」が {len(found)} 個ある")
    return found[0] if found else None


def _next_node_id(workflow: dict[str, Any]) -> int:
    return max(int(workflow.get("last_node_id", 0)), *(int(node["id"]) for node in workflow["nodes"])) + 1


def _next_order(workflow: dict[str, Any]) -> int:
    return max(int(node.get("order", 0)) for node in workflow["nodes"]) + 1


def _ensure_polygon_count_control(workflow: dict[str, Any], texture_size: dict[str, Any]) -> dict[str, Any]:
    """DecimateMesh.target_face_count へつなぐ PrimitiveInt（題名 POLYGON_COUNT_TITLE）を返す。無ければ足す。

    アプリの入力欄にできるのは「ノードの widget」なので、DecimateMesh の widget を直接出さず、
    テクスチャサイズ（上流が PrimitiveInt で持つ）と同じ形の PrimitiveInt を 1 つ足して結ぶ。
    足すノードは texture_size の写しにして、画面の保存形式（localized_name など）をそのまま引き継ぐ。
    値は DecimateMesh が持っていた値（既定 700000）。DecimateMesh の widget の値は、つながっている間は使われない。
    """

    decimate = _single_node(workflow, DECIMATE_NODE_TYPE)
    existing = _single_titled_node(workflow, PRIMITIVE_INT_NODE_TYPE, (POLYGON_COUNT_TITLE,))
    decimate_input = next((item for item in decimate.get("inputs", []) if item.get("name") == DECIMATE_INPUT_NAME), None)

    if existing is not None:
        linked = decimate_input is not None and decimate_input.get("link") is not None
        if not linked:
            raise WorkflowFormatError(f"「{POLYGON_COUNT_TITLE}」はあるが、DecimateMesh.{DECIMATE_INPUT_NAME} につながっていない")
        return existing
    if decimate_input is not None and decimate_input.get("link") is not None:
        raise WorkflowFormatError(
            f"DecimateMesh.{DECIMATE_INPUT_NAME} がすでに別のノードにつながっている（上流のテンプレートが変わった可能性）"
        )

    widgets = decimate.get("widgets_values") or []
    if not widgets or not isinstance(widgets[0], int):
        raise WorkflowFormatError(f"DecimateMesh の widgets_values の先頭が {DECIMATE_INPUT_NAME} の整数ではない: {widgets!r}")
    polygon_count = widgets[0]

    node_id = _next_node_id(workflow)
    link_id = int(workflow.get("last_link_id", 0)) + 1
    control = copy.deepcopy(texture_size)
    control["id"] = node_id
    control["title"] = POLYGON_COUNT_TITLE
    control["order"] = _next_order(workflow)
    control["pos"] = [decimate["pos"][0], decimate["pos"][1] - 120]
    control["widgets_values"] = [polygon_count, *control.get("widgets_values", [None, "fixed"])[1:]]
    if "widgets_values_named" in control:
        control["widgets_values_named"] = {**control["widgets_values_named"], "value": polygon_count}
    control["outputs"] = [{**output, "links": [link_id]} for output in control.get("outputs", [])]
    if len(control["outputs"]) != 1:
        raise WorkflowFormatError("PrimitiveInt の出力が 1 つではない")
    for item in control.get("inputs", []):
        item["link"] = None

    if decimate_input is None:
        decimate_input = {"name": DECIMATE_INPUT_NAME, "type": "INT", "widget": {"name": DECIMATE_INPUT_NAME}}
        decimate.setdefault("inputs", []).append(decimate_input)
    decimate_input["link"] = link_id
    target_slot = decimate["inputs"].index(decimate_input)

    workflow["nodes"].append(control)
    workflow.setdefault("links", []).append([link_id, node_id, 0, decimate["id"], target_slot, "INT"])
    workflow["last_node_id"] = node_id
    workflow["last_link_id"] = link_id
    return control


def _linear_entry_node_id(entry: Any) -> int | None:
    """linearData.inputs の 1 項目が指すノードの id。形は 2 つある。

    旧書式 [ノード id, widget 名] と、画面が保存し直すと変える ["<workflow id>:<ノード id>:<widget 名>", widget 名]。
    """

    head = entry[0] if isinstance(entry, (list, tuple)) and entry else None
    if isinstance(head, int):
        return head
    if isinstance(head, str):
        parts = head.split(":")
        candidate = parts[1] if len(parts) == 3 else head
        return int(candidate) if candidate.isdigit() else None
    return None


def _set_linear_data(workflow: dict[str, Any], wanted: list[tuple[int, str]], outputs: list[int]) -> None:
    """アプリの入力欄を wanted の順にし、出力に outputs を含める。すでにある項目は書式ごとそのまま残す。"""

    extra = workflow.setdefault("extra", {})
    linear = extra.setdefault("linearData", {})
    existing = linear.get("inputs") or []
    # 画面が保存し直した書式（widget id の文字列）が 1 つでもあれば、足す項目も同じ書式にする
    upgraded = any(isinstance(item[0], str) and ":" in item[0] for item in existing if item)
    by_node = {node_id: item for item in existing if (node_id := _linear_entry_node_id(item)) is not None}

    inputs = []
    for node_id, widget_name in wanted:
        if node_id in by_node:
            inputs.append(by_node[node_id])
        elif upgraded and isinstance(workflow.get("id"), str):
            inputs.append([f"{workflow['id']}:{node_id}:{widget_name}", widget_name])
        else:
            inputs.append([node_id, widget_name])
    linear["inputs"] = inputs

    current_outputs = list(linear.get("outputs") or [])
    known = {str(item) for item in current_outputs}
    linear["outputs"] = current_outputs + [node_id for node_id in outputs if str(node_id) not in known]


def make_easy_image_to_3d(workflow: dict[str, Any]) -> dict[str, Any]:
    """ComfyUI 同梱の「画像 → 3D」テンプレートから、画像を入れて Run を押すだけの入口を作る。

    テンプレートでも、すでにこの関数で作って画面で保存し直したものでも渡せる（何度適用しても同じ結果になる）。
    変えるのは次の点だけで、実行される処理（結線と値）はテンプレートと同じ。
      - アプリモード（extra.linearMode / linearData）にする。入力は「LoadImage の image・ポリゴン数・テクスチャサイズ」の順、
        出力は Save3DAdvanced。linearMode はまだ無いときだけ真にする（画面でグラフ表示に戻した選択を変えない）。
      - ポリゴン数の入力用に PrimitiveInt を足して DecimateMesh.target_face_count につなぐ（_ensure_polygon_count_control）。
        テクスチャサイズの PrimitiveInt は題名を日本語にする。
      - 手順を書いた MarkdownNote を LoadImage の左上に足す（画面だけのノードで、実行には使われない）。すでにあれば文面を直す。
      - テンプレートのときだけ、グラフ表示の初期表示を画像を置く場所へ寄せ（EASY_VIEW）、workflow の id を
        テンプレートの id から決まった別の UUID にする。
    """

    easy = copy.deepcopy(workflow)
    load_image = _single_node(easy, LOAD_IMAGE_NODE_TYPE)
    save_3d = _single_node(easy, SAVE_3D_NODE_TYPE)
    texture_size = _single_titled_node(
        easy, PRIMITIVE_INT_NODE_TYPE, (TEXTURE_SIZE_UPSTREAM_TITLE, TEXTURE_SIZE_TITLE)
    )
    if texture_size is None:
        raise WorkflowFormatError(
            f"PrimitiveInt「{TEXTURE_SIZE_UPSTREAM_TITLE}」が無い（上流のテンプレートが変わった可能性）"
        )

    note = _single_titled_node(easy, MARKDOWN_NOTE_NODE_TYPE, (EASY_NOTE_TITLE,))
    already_easy = note is not None

    polygon_count = _ensure_polygon_count_control(easy, texture_size)
    texture_size["title"] = TEXTURE_SIZE_TITLE

    extra = easy.setdefault("extra", {})
    extra.setdefault("linearMode", True)
    _set_linear_data(
        easy,
        [(load_image["id"], "image"), (polygon_count["id"], "value"), (texture_size["id"], "value")],
        [save_3d["id"]],
    )

    if note is None:
        load_x, load_y = load_image["pos"]
        easy["nodes"].append(
            {
                "id": _next_node_id(easy),
                "type": MARKDOWN_NOTE_NODE_TYPE,
                "pos": [load_x - 780, load_y - 570],
                "size": [660, 500],
                "flags": {},
                "order": _next_order(easy),
                "mode": 0,
                "inputs": [],
                "outputs": [],
                "title": EASY_NOTE_TITLE,
                "properties": {},
                "widgets_values": [EASY_NOTE_TEXT],
                "color": "#222",
                "bgcolor": "#000",
            }
        )
        easy["last_node_id"] = easy["nodes"][-1]["id"]
    else:
        note["widgets_values"] = [EASY_NOTE_TEXT]

    if not already_easy:
        extra["ds"] = copy.deepcopy(EASY_VIEW)
        if isinstance(easy.get("id"), str):
            easy["id"] = str(uuid.uuid5(_EASY_UUID_NAMESPACE, easy["id"]))
    return easy


def dump_workflow(workflow: dict[str, Any]) -> str:
    """配布元に近い書式で文字列にする。

    配布元の 4 本は json.dumps(indent=2) と 1 文字も違わない（末尾の改行なし）。
    ensure_ascii=False にするので、配布元で \\u2013 などと書かれた文字はそのままの文字になる。
    """

    return json.dumps(workflow, indent=2, ensure_ascii=False)
