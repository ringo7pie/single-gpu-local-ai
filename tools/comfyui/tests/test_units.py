"""cfui と scripts/adapt_workflows.py の単体試験。GPU もネットワークも torch も使わない。

実行: uvx --with pytest pytest tools/comfyui/tests/test_units.py -q
    （.venv には pytest を入れない。素の python + pytest だけで動く）
"""

from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import sys
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL_DIR))
sys.path.insert(0, str(TOOL_DIR / "scripts"))

import adapt_workflows  # noqa: E402
import pytest  # noqa: E402
from cfui import checks, config, notes_ja, workflows  # noqa: E402
from cfui.cli import (  # noqa: E402
    build_parser,
    build_serve_command,
    build_serve_env,
    extra_args_from_env,
)

# ---------------------------------------------------------------- 試験用のワークフロー
# 配布元の 02 と同じ形を小さく作る:
#   Quad(325) → MemoryCleaner(326) → WTiVo(327) → LODTailor(328) → Save(329)
#                                              └→ BakeTextureFromVoxel(331)
#   PrimitiveString(324) → LODTailor(328).blender_path
LODTAILOR_VALUES = ["blender", 6000000, 100, 1.05, 0.5, 0.25, 0.2, True, False, True, 0.001, 1e-06, 0.0001, 100, True, 1800]


def make_workflow(with_wtivo: bool = True) -> dict:
    nodes = [
        {"id": 325, "type": "MeshReconstructWithQuad", "mode": 0, "inputs": [],
         "outputs": [{"name": "mesh", "type": "MESH", "links": [1298]}], "widgets_values": [1.0, 1536, True, True]},
        {"id": 326, "type": "MemoryCleaner", "mode": 0,
         "inputs": [{"name": "anything", "type": "*", "link": 1298}],
         "outputs": [{"name": "anything", "type": "*", "links": [1299] if with_wtivo else [1300, 1305]}],
         "widgets_values": []},
        {"id": 324, "type": "PrimitiveString", "mode": 0, "title": "Blender path – set once", "inputs": [],
         "outputs": [{"name": "STRING", "type": "STRING", "links": [1301]}], "widgets_values": ["blender"]},
        {"id": 328, "type": "LODTailorTheMeshTrimmer", "mode": 0,
         "inputs": [{"name": "mesh", "type": "MESH", "link": 1300},
                    {"name": "blender_path", "type": "STRING", "widget": {"name": "blender_path"}, "link": 1301}],
         "outputs": [{"name": "mesh", "type": "MESH", "links": [1302]}],
         "widgets_values": list(LODTAILOR_VALUES)},
        {"id": 329, "type": "Save3DAdvanced", "mode": 0, "inputs": [{"name": "mesh", "type": "MESH", "link": 1302}],
         "outputs": [], "widgets_values": []},
        {"id": 331, "type": "BakeTextureFromVoxel", "mode": 0,
         "inputs": [{"name": "a", "type": "X", "link": None}, {"name": "b", "type": "Y", "link": None},
                    {"name": "mesh", "type": "MESH", "link": 1305}],
         "outputs": [], "widgets_values": []},
    ]
    links = [
        [1298, 325, 0, 326, 0, "MESH"],
        [1301, 324, 0, 328, 1, "STRING"],
        [1302, 328, 0, 329, 0, "MESH"],
    ]
    if with_wtivo:
        nodes.insert(2, {"id": 327, "type": "WTiVoNativeMeshToMesh", "mode": 0,
                         "inputs": [{"name": "mesh", "type": "MESH", "link": 1299}],
                         "outputs": [{"name": "mesh", "type": "MESH", "links": [1300, 1305]}],
                         "widgets_values": [1536, 1536, 12000000, 1.0, 1.5, 20.0, 16, 0.0, "largest"]})
        links += [
            [1299, 326, 0, 327, 0, "*"],
            [1300, 327, 0, 328, 0, "MESH"],
            [1305, 327, 0, 331, 2, "MESH"],
        ]
    else:
        links += [[1300, 326, 0, 328, 0, "MESH"], [1305, 326, 0, 331, 2, "MESH"]]
    return {
        "id": "772b492c-de6a-4904-a5e0-0526674f6555",
        "revision": 0,
        "last_node_id": 352,
        "last_link_id": 1331,
        "nodes": nodes,
        "links": links,
        "groups": [{"title": "PixelArtistry · 1 Watertight High-Poly"}],
        "version": 0.4,
    }


def make_easy_template() -> dict:
    """ComfyUI 同梱の「画像 → 3D」テンプレート（3d_pixal3d_trellis2_image_to_model.json）の要点だけを小さく作る。"""
    return {
        "id": "aa77013b-18f1-4679-9678-6d455d3536c5",
        "revision": 0,
        "last_node_id": 323,
        "last_link_id": 1296,
        "nodes": [
            {"id": 122, "type": "LoadImage", "pos": [-5080, 510], "size": [740, 870], "order": 9, "mode": 0,
             "widgets_values": ["viking_wolf_rune_axe.png", "image"]},
            {"id": 313, "type": "MarkdownNote", "pos": [-5860, 470], "size": [660, 950], "order": 10, "mode": 0,
             "widgets_values": ["note"]},
            {"id": 322, "type": "Save3DAdvanced", "pos": [4020, -170], "size": [1660, 2090], "order": 65, "mode": 0,
             "widgets_values": ["3d/ComfyUI", "", 1024, 1024]},
            {"id": 186, "type": "DecimateMesh", "pos": [2330, -30], "size": [390, 90], "order": 46, "mode": 0,
             "inputs": [{"name": "mesh", "type": "MESH", "link": 1071}], "outputs": [],
             "widgets_values": [700000, "midpoint"]},
            {"id": 288, "type": "PrimitiveInt", "pos": [1590, 660], "size": [240, 90], "flags": {}, "order": 2,
             "mode": 0, "inputs": [], "outputs": [{"name": "INT", "type": "INT", "links": [1185, 1186]}],
             "title": "Texture Resolution", "properties": {"Node name for S&R": "PrimitiveInt"},
             "widgets_values": [4096, "fixed"], "widgets_values_named": {"value": 4096, "fixed": "fixed"}},
        ],
        "links": [],
        "groups": [{"title": "Upload Input Image Here", "bounding": [-5090, 440, 770, 970]}],
        "config": {},
        "extra": {"ds": {"scale": 0.1, "offset": [6002.4, 3142.4]}, "frontendVersion": "1.49.6"},
        "version": 0.4,
    }


def make_saved_easy_v1() -> dict:
    """最初の版（入力は画像だけ）を、画面で開いて保存し直したあとの形。2026-10-25 に実物で確かめた変化だけを写す。

    - linearData が旧書式 [ノード id, widget 名] から ["<workflow id>:<ノード id>:<widget 名>", widget 名] に、出力が文字列になる
    - widget の入力が inputs に並び（localized_name つき）、LoadImage の画像が使った画像に変わる
    """
    saved = make_easy_template()
    saved["id"] = "d843fbf7-7e8f-51f6-a3ee-22d4c1b2f091"
    saved["extra"] = {
        "ds": {"scale": 0.48, "offset": [5619.7, -108.3]}, "frontendVersion": "1.55.17",
        "linearMode": True,
        "linearData": {"inputs": [[f"{saved['id']}:122:image", "image"]], "outputs": ["322"]},
    }
    node_of(saved, 122)["widgets_values"] = ["E_copper_coin.png", "image"]
    node_of(saved, 313)["widgets_values"] = ["note"]
    saved["nodes"].append(
        {"id": 324, "type": "MarkdownNote", "pos": [-5860, -60], "size": [660, 500], "order": 66, "mode": 0,
         "inputs": [], "outputs": [], "title": workflows.EASY_NOTE_TITLE, "properties": {},
         "widgets_values": ["古い手順"], "color": "#222", "bgcolor": "#000"}
    )
    saved["last_node_id"] = 324
    node_of(saved, 186)["inputs"] = [
        {"localized_name": "mesh", "name": "mesh", "type": "MESH", "link": 1071},
        {"localized_name": "target_face_count", "name": "target_face_count", "type": "INT",
         "widget": {"name": "target_face_count"}, "link": None},
        {"localized_name": "placement_mode", "name": "placement_mode", "type": "COMFY_DYNAMICCOMBO_V3",
         "widget": {"name": "placement_mode"}, "link": None},
    ]
    node_of(saved, 288)["inputs"] = [
        {"localized_name": "値", "name": "value", "type": "INT", "widget": {"name": "value"}, "link": None}
    ]
    return saved


def node_of(workflow: dict, node_id: int) -> dict:
    return next(node for node in workflow["nodes"] if node["id"] == node_id)


def link_of(workflow: dict, link_id: int) -> list:
    return next(link for link in workflow["links"] if link[0] == link_id)


# ---------------------------------------------------------------- workflows.set_blender_path


def test_find_blender_path_nodes_uses_link_target_name() -> None:
    workflow = make_workflow()
    # 結線先が blender_path でない PrimitiveString は数えない
    workflow["nodes"].append({"id": 999, "type": "PrimitiveString", "inputs": [],
                              "outputs": [{"name": "STRING", "links": [2000]}], "widgets_values": ["x"]})
    workflow["links"].append([2000, 999, 0, 329, 0, "STRING"])
    found = workflows.find_blender_path_nodes(workflow)
    assert [node["id"] for node in found] == [324]


def test_set_blender_path_changes_only_the_value() -> None:
    original = make_workflow()
    adapted = workflows.set_blender_path(original, config.BLENDER_PATH)
    assert node_of(adapted, 324)["widgets_values"] == [config.BLENDER_PATH]
    # 元の dict は変えない
    assert node_of(original, 324)["widgets_values"] == ["blender"]
    # それ以外は 1 つも変わっていない（LODTailor の widget の "blender" も結線で上書きされるので触らない）
    node_of(adapted, 324)["widgets_values"] = ["blender"]
    assert adapted == original


def test_set_blender_path_rejects_unexpected_value() -> None:
    workflow = make_workflow()
    node_of(workflow, 324)["widgets_values"] = ["C:/Blender/blender.exe"]
    with pytest.raises(workflows.WorkflowFormatError):
        workflows.set_blender_path(workflow, config.BLENDER_PATH)


def test_set_blender_path_rejects_missing_or_duplicated_node() -> None:
    workflow = make_workflow()
    workflow["links"] = [link for link in workflow["links"] if link[0] != 1301]
    with pytest.raises(workflows.WorkflowFormatError):
        workflows.set_blender_path(workflow, config.BLENDER_PATH)

    workflow = make_workflow()
    workflow["nodes"].append(copy.deepcopy(node_of(workflow, 324)) | {"id": 400, "outputs": [{"links": [2001]}]})
    workflow["links"].append([2001, 400, 0, 328, 1, "STRING"])
    with pytest.raises(workflows.WorkflowFormatError):
        workflows.set_blender_path(workflow, config.BLENDER_PATH)


def test_blender_path_value() -> None:
    adapted = workflows.set_blender_path(make_workflow(), config.BLENDER_PATH)
    assert workflows.blender_path_value(adapted) == config.BLENDER_PATH
    assert workflows.blender_path_value({"nodes": [], "links": []}) is None


# ---------------------------------------------------------------- workflows.make_fallback


def test_make_fallback_removes_wtivo_and_bridges_links() -> None:
    original = make_workflow()
    fallback = workflows.make_fallback(original)

    assert not workflows.has_wtivo(fallback)
    assert all(node["id"] != 327 for node in fallback["nodes"])
    # WTiVo へ入っていた link は消え、出ていた link は WTiVo の入力の元（MemoryCleaner の 0 番）から出る
    assert all(link[0] != 1299 for link in fallback["links"])
    assert link_of(fallback, 1300)[:5] == [1300, 326, 0, 328, 0]
    assert link_of(fallback, 1305)[:5] == [1305, 326, 0, 331, 2]
    assert node_of(fallback, 326)["outputs"][0]["links"] == [1300, 1305]
    # 先の入力側の link id はそのまま
    assert node_of(fallback, 328)["inputs"][0]["link"] == 1300
    assert node_of(fallback, 331)["inputs"][2]["link"] == 1305
    # 元の dict は変えない
    assert workflows.has_wtivo(original)


def test_make_fallback_sets_voxel_remesh_on_following_lodtailor_only() -> None:
    workflow = make_workflow()
    # WTiVo の後ろにない LODTailor（02 の low-poly 用）は変えない
    other = copy.deepcopy(node_of(workflow, 328)) | {"id": 335}
    other["widgets_values"] = list(other["widgets_values"])
    other["widgets_values"][10] = 0.0015
    workflow["nodes"].append(other)

    fallback = workflows.make_fallback(workflow)
    values = node_of(fallback, 328)["widgets_values"]
    names = workflows.LODTAILOR_WIDGET_NAMES
    assert values[names.index("voxel_rebuild")] is True
    assert values[names.index("relative_voxel_size")] == pytest.approx(1 / 1536)
    # 他の widget は変えない
    for index, name in enumerate(names):
        if name not in ("voxel_rebuild", "relative_voxel_size"):
            assert values[index] == LODTAILOR_VALUES[index], name
    assert node_of(fallback, 335)["widgets_values"][10] == 0.0015


def test_make_fallback_gets_distinct_deterministic_id() -> None:
    first = workflows.make_fallback(make_workflow())
    second = workflows.make_fallback(make_workflow())
    assert first["id"] != make_workflow()["id"]
    assert first["id"] == second["id"]


def test_make_fallback_custom_voxel_size() -> None:
    fallback = workflows.make_fallback(make_workflow(), relative_voxel_size=1 / 2048)
    assert node_of(fallback, 328)["widgets_values"][10] == pytest.approx(1 / 2048)


def test_make_fallback_rejects_workflow_without_wtivo() -> None:
    with pytest.raises(workflows.WorkflowFormatError):
        workflows.make_fallback(make_workflow(with_wtivo=False))


def test_lodtailor_widget_names_match_node_source() -> None:
    # 上流のノード定義が手元にあるときだけ、INPUT_TYPES の並びと一致するかを確かめる
    source = config.CUSTOM_NODES_DIR / "LODTailor-The-Mesh-Trimmer-ComfyuiNode" / "__init__.py"
    if not source.is_file():
        pytest.skip("vendor が無い")
    text = source.read_text(encoding="utf-8")
    block = text[text.index("def INPUT_TYPES"):text.index("RETURN_TYPES")]
    names = re.findall(r'^\s{16}"(\w+)": \(', block, flags=re.MULTILINE)
    assert tuple(name for name in names if name != "mesh") == workflows.LODTAILOR_WIDGET_NAMES


# ---------------------------------------------------------------- workflows.dump_workflow


def test_dump_workflow_matches_upstream_layout() -> None:
    workflow = make_workflow()
    text = workflows.dump_workflow(workflow)
    assert text == json.dumps(workflow, indent=2, ensure_ascii=False)
    assert not text.endswith("\n")
    # ensure_ascii=False: 「–」「·」は \u エスケープにしない
    assert "–" in text and "·" in text and "\\u2013" not in text
    assert json.loads(text) == workflow


def test_upstream_workflows_round_trip_when_vendor_exists() -> None:
    source_dir = config.PIXELARTISTRY_DIR / "workflows"
    if not source_dir.is_dir():
        pytest.skip("vendor が無い")
    for file_name in config.WORKFLOW_FILES:
        raw = (source_dir / file_name).read_text(encoding="utf-8")
        workflow = json.loads(raw)
        # 配布元は ensure_ascii=True の indent=2 と完全に同じ（書式を「元に近く」保てる根拠）
        assert json.dumps(workflow, indent=2) == raw, file_name
        adapted = workflows.set_blender_path(workflow, config.BLENDER_PATH)
        assert workflows.blender_path_value(adapted) == config.BLENDER_PATH
        if workflows.has_wtivo(adapted):
            fallback = workflows.make_fallback(adapted)
            assert not workflows.has_wtivo(fallback)
            # 予備案で変わるノードは WTiVo の削除と LODTailor だけ
            before = {node["id"]: node for node in adapted["nodes"]}
            changed = [node["id"] for node in fallback["nodes"] if node != before[node["id"]]]
            changed_types = {before[node_id]["type"] for node_id in changed}
            assert changed_types <= {"LODTailorTheMeshTrimmer", "MemoryCleaner"}, (file_name, changed_types)
        # この機体向けの変更で変わるのは、足した日本語ノートと 01b の WTiVo だけ
        is_2k = file_name == config.WORKFLOW_2K_FILE
        if is_2k:
            assert workflows.wtivo_proxy_points(adapted) == workflows.UPSTREAM_2K_PROXY_POINTS
        proxy_points = config.WTIVO_2K_PROXY_POINTS if is_2k else None
        localized = workflows.localize_pixelartistry(adapted, notes_ja.note_for(file_name), proxy_points)
        before = {node["id"]: node for node in adapted["nodes"]}
        added = [node for node in localized["nodes"] if node["id"] not in before]
        assert [node["title"] for node in added] == [workflows.JA_NOTE_TITLE], file_name
        changed_types = {before[node["id"]]["type"] for node in localized["nodes"]
                         if node["id"] in before and node != before[node["id"]]}
        assert changed_types == ({"WTiVoNativeMeshToMesh"} if is_2k and proxy_points is not None else set()), file_name
        assert localized["links"] == adapted["links"] and localized.get("groups") == adapted.get("groups")
        # 日本語ノートは既存のノードとグループに重ならない
        x0, y0 = workflows.JA_NOTE_POS
        x1, y1 = x0 + workflows.JA_NOTE_SIZE[0], y0 + workflows.JA_NOTE_SIZE[1]
        for node in adapted["nodes"]:
            (px, py), (sx, sy) = node["pos"], node.get("size") or (0, 0)
            assert not (px < x1 and px + sx > x0 and py < y1 and py + sy > y0), (file_name, node["id"])
        for group in adapted.get("groups", []):
            gx, gy, gw, gh = group["bounding"]
            assert not (gx < x1 and gx + gw > x0 and gy < y1 and gy + gh > y0), (file_name, group["title"])


# ---------------------------------------------------------------- adapt_workflows.adapt_all


def write_source(directory: Path) -> tuple[str, str]:
    directory.mkdir(parents=True, exist_ok=True)
    with_wtivo = "PixelArtistry_01_Image_to_Watertight_Mesh.json"
    without_wtivo = "PixelArtistry_03_Your_Mesh_to_GameReady_Asset.json"
    (directory / with_wtivo).write_text(json.dumps(make_workflow(), indent=2), encoding="utf-8")
    (directory / without_wtivo).write_text(json.dumps(make_workflow(with_wtivo=False), indent=2), encoding="utf-8")
    return with_wtivo, without_wtivo


def test_adapt_all_writes_and_is_idempotent(tmp_path: Path) -> None:
    names = write_source(tmp_path / "src")
    dest = tmp_path / "dest"
    results = adapt_workflows.adapt_all(tmp_path / "src", dest, None, config.BLENDER_PATH, 1 / 1536, False, names)
    assert [status for _path, status in results] == ["written", "written"]
    written = json.loads((dest / names[0]).read_text(encoding="utf-8"))
    assert workflows.blender_path_value(written) == config.BLENDER_PATH

    again = adapt_workflows.adapt_all(tmp_path / "src", dest, None, config.BLENDER_PATH, 1 / 1536, False, names)
    assert [status for _path, status in again] == ["unchanged", "unchanged"]


def test_adapt_all_keeps_user_edits_unless_forced(tmp_path: Path) -> None:
    names = write_source(tmp_path / "src")
    dest = tmp_path / "dest"
    adapt_workflows.adapt_all(tmp_path / "src", dest, None, config.BLENDER_PATH, 1 / 1536, False, names)
    (dest / names[0]).write_text("{}", encoding="utf-8")

    kept = adapt_workflows.adapt_all(tmp_path / "src", dest, None, config.BLENDER_PATH, 1 / 1536, False, names)
    assert kept[0][1] == "kept"
    assert (dest / names[0]).read_text(encoding="utf-8") == "{}"

    forced = adapt_workflows.adapt_all(tmp_path / "src", dest, None, config.BLENDER_PATH, 1 / 1536, True, names)
    assert forced[0][1] == "written"


def test_adapt_all_migrates_untouched_legacy_blender_path(tmp_path: Path) -> None:
    names = write_source(tmp_path / "src")
    dest = tmp_path / "dest"
    fallback_dir = tmp_path / "fallback"
    legacy = config.LEGACY_BLENDER_PATHS[0]
    # 以前の既定値で写した状態を作る（予備案も）
    adapt_workflows.adapt_all(
        tmp_path / "src", dest, fallback_dir, legacy, 1 / 1536, False, names, legacy_blender_paths=()
    )
    assert workflows.blender_path_value(json.loads((dest / names[0]).read_text(encoding="utf-8"))) == legacy

    migrated = adapt_workflows.adapt_all(tmp_path / "src", dest, fallback_dir, config.BLENDER_PATH, 1 / 1536, False, names)
    assert [status for _path, status in migrated] == ["migrated", "migrated", "migrated"]
    assert workflows.blender_path_value(json.loads((dest / names[0]).read_text(encoding="utf-8"))) == config.BLENDER_PATH
    fallback_name_01 = "PixelArtistry_01_Image_to_Watertight_Mesh_noWTiVo.json"
    fallback = json.loads((fallback_dir / fallback_name_01).read_text(encoding="utf-8"))
    assert workflows.blender_path_value(fallback) == config.BLENDER_PATH

    # 2 回目は何も変えない（冪等）
    again = adapt_workflows.adapt_all(tmp_path / "src", dest, fallback_dir, config.BLENDER_PATH, 1 / 1536, False, names)
    assert [status for _path, status in again] == ["unchanged", "unchanged", "unchanged"]


def test_adapt_all_does_not_migrate_edited_legacy_file(tmp_path: Path) -> None:
    names = write_source(tmp_path / "src")
    dest = tmp_path / "dest"
    legacy = config.LEGACY_BLENDER_PATHS[0]
    adapt_workflows.adapt_all(tmp_path / "src", dest, None, legacy, 1 / 1536, False, names, legacy_blender_paths=())
    # 以前の既定値のファイルを UI で手直しした想定: 末尾に 1 文字足す
    edited = (dest / names[0]).read_text(encoding="utf-8") + "\n"
    (dest / names[0]).write_text(edited, encoding="utf-8")

    results = adapt_workflows.adapt_all(tmp_path / "src", dest, None, config.BLENDER_PATH, 1 / 1536, False, names)
    assert results[0][1] == "kept"
    assert (dest / names[0]).read_text(encoding="utf-8") == edited


def test_adapt_all_fallback_only_for_wtivo_workflows(tmp_path: Path) -> None:
    names = write_source(tmp_path / "src")
    fallback_dir = tmp_path / "fallback"
    adapt_workflows.adapt_all(tmp_path / "src", tmp_path / "dest", fallback_dir, config.BLENDER_PATH, 1 / 1536, False, names)
    assert sorted(path.name for path in fallback_dir.iterdir()) == ["PixelArtistry_01_Image_to_Watertight_Mesh_noWTiVo.json"]
    fallback = json.loads((fallback_dir / "PixelArtistry_01_Image_to_Watertight_Mesh_noWTiVo.json").read_text(encoding="utf-8"))
    assert not workflows.has_wtivo(fallback)
    assert workflows.blender_path_value(fallback) == config.BLENDER_PATH


def test_adapt_main_reports_format_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = tmp_path / "src"
    source.mkdir()
    for file_name in config.WORKFLOW_FILES:
        (source / file_name).write_text(json.dumps({"nodes": [], "links": []}), encoding="utf-8")
    assert adapt_workflows.main(["--source-dir", str(source), "--dest-dir", str(tmp_path / "dest")]) == 1
    assert "写せませんでした" in capsys.readouterr().err


# ---------------------------------------------------------------- 日本語ノートと 2K（01b）の proxy_points


def make_2k_workflow() -> dict:
    """配布元の 01b と同じく、WTiVo が 2048 / 25M で、題名に「25M proxy points」を持つ形。"""
    workflow = make_workflow()
    wtivo = node_of(workflow, 327)
    wtivo["title"] = "3 · WTiVo 2048 / 25M proxy points"
    wtivo["widgets_values"] = [2048, 2048, 25000000, 1.0, 1.5, 20.0, 16, 0.0, "largest"]
    return workflow


def test_wtivo_widget_names_match_node_source() -> None:
    source = config.CUSTOM_NODES_DIR / "WTiVo-WatertightVoxel-ComfyuiNode" / "comfy_node.py"
    if not source.is_file():
        pytest.skip("vendor が無い")
    text = source.read_text(encoding="utf-8")
    block = text[text.index("def INPUT_TYPES"):text.index("RETURN_TYPES")]
    names = re.findall(r'^\s{16}"(\w+)": \(', block, flags=re.MULTILINE)
    assert tuple(name for name in names if name != "mesh") == workflows.WTIVO_WIDGET_NAMES


def test_set_wtivo_proxy_points_changes_value_and_title_only() -> None:
    original = make_2k_workflow()
    before = copy.deepcopy(original)
    adapted = workflows.set_wtivo_proxy_points(original, 12_000_000)
    assert original == before  # 元の dict は変えない
    wtivo = node_of(adapted, 327)
    assert wtivo["widgets_values"] == [2048, 2048, 12000000, 1.0, 1.5, 20.0, 16, 0.0, "largest"]
    assert wtivo["title"] == "3 · WTiVo 2048 / 12M proxy points"
    assert workflows.wtivo_proxy_points(adapted) == 12_000_000
    # WTiVo 以外は 1 つも変わらない
    assert [node for node in adapted["nodes"] if node["id"] != 327] == [
        node for node in before["nodes"] if node["id"] != 327
    ]
    assert adapted["links"] == before["links"]
    # 端数のある値は小数で書く。何度当てても同じ
    half = workflows.set_wtivo_proxy_points(adapted, 12_500_000)
    assert node_of(half, 327)["title"] == "3 · WTiVo 2048 / 12.5M proxy points"
    assert workflows.set_wtivo_proxy_points(half, 12_500_000) == half


def test_set_wtivo_proxy_points_keeps_title_without_count() -> None:
    adapted = workflows.set_wtivo_proxy_points(make_workflow(), 8_000_000)
    assert "title" not in node_of(adapted, 327)
    assert workflows.wtivo_proxy_points(adapted) == 8_000_000


@pytest.mark.parametrize("value", [0, -1, True, 1.5e7])
def test_set_wtivo_proxy_points_rejects_bad_value(value: object) -> None:
    with pytest.raises(workflows.WorkflowFormatError):
        workflows.set_wtivo_proxy_points(make_2k_workflow(), value)  # type: ignore[arg-type]


def test_set_wtivo_proxy_points_rejects_unexpected_shape() -> None:
    with pytest.raises(workflows.WorkflowFormatError):
        workflows.set_wtivo_proxy_points(make_workflow(with_wtivo=False), 12_000_000)
    doubled = make_2k_workflow()
    doubled["nodes"].append({**copy.deepcopy(node_of(doubled, 327)), "id": 400})
    with pytest.raises(workflows.WorkflowFormatError):
        workflows.set_wtivo_proxy_points(doubled, 12_000_000)
    short = make_2k_workflow()
    node_of(short, 327)["widgets_values"] = [2048, 2048, 25000000]
    with pytest.raises(workflows.WorkflowFormatError):
        workflows.set_wtivo_proxy_points(short, 12_000_000)
    assert workflows.wtivo_proxy_points(short) is None


def test_upsert_markdown_note_adds_once_then_updates_text_only() -> None:
    original = make_workflow()
    added = workflows.upsert_markdown_note(original, "使い方", "1 版目")
    assert all(node["id"] != 353 for node in original["nodes"])  # 元の dict は変えない
    note = node_of(added, 353)  # last_node_id 352 の次
    assert note["type"] == "MarkdownNote" and note["title"] == "使い方"
    assert note["widgets_values"] == ["1 版目"]
    assert note["pos"] == list(workflows.JA_NOTE_POS) and note["size"] == list(workflows.JA_NOTE_SIZE)
    assert note["inputs"] == [] and note["outputs"] == []
    assert added["last_node_id"] == 353
    assert added["nodes"][:-1] == original["nodes"] and added["links"] == original["links"]

    # 画面で動かした位置は残し、文面だけ直す。足し直さない
    note["pos"] = [100, 200]
    updated = workflows.upsert_markdown_note(added, "使い方", "2 版目")
    assert len(updated["nodes"]) == len(added["nodes"])
    assert node_of(updated, 353)["widgets_values"] == ["2 版目"]
    assert node_of(updated, 353)["pos"] == [100, 200]
    assert workflows.upsert_markdown_note(updated, "使い方", "2 版目") == updated


def test_upsert_markdown_note_rejects_non_workflow() -> None:
    with pytest.raises(workflows.WorkflowFormatError):
        workflows.upsert_markdown_note({}, "使い方", "本文")
    with pytest.raises(workflows.WorkflowFormatError):
        workflows.upsert_markdown_note({"nodes": {}}, "使い方", "本文")


def test_localize_pixelartistry_changes_only_note_view_and_proxy() -> None:
    original = workflows.set_blender_path(make_2k_workflow(), config.BLENDER_PATH)
    localized = workflows.localize_pixelartistry(original, "本文", 12_000_000)
    assert localized["extra"]["ds"] == workflows.PIXELARTISTRY_VIEW
    assert workflows.wtivo_proxy_points(localized) == 12_000_000
    notes = [node for node in localized["nodes"] if node.get("title") == workflows.JA_NOTE_TITLE]
    assert len(notes) == 1 and notes[0]["widgets_values"] == ["本文"]
    before = {node["id"]: node for node in original["nodes"]}
    changed = {node["id"] for node in localized["nodes"] if node["id"] not in before or node != before[node["id"]]}
    assert changed == {327, notes[0]["id"]}
    assert localized["links"] == original["links"]
    # 何度当てても同じ。proxy_points=None なら WTiVo に触れない
    assert workflows.localize_pixelartistry(localized, "本文", 12_000_000) == localized
    untouched = workflows.localize_pixelartistry(original, "本文")
    assert workflows.wtivo_proxy_points(untouched) == workflows.UPSTREAM_2K_PROXY_POINTS


def test_notes_ja_has_a_note_for_every_workflow() -> None:
    for file_name in config.WORKFLOW_FILES:
        text = notes_ja.note_for(file_name)
        assert text.startswith("## 使い方") and "Run" in text and "/snap/blender/current/blender" in text, file_name
    with pytest.raises(KeyError):
        notes_ja.note_for("unknown.json")


def test_notes_ja_2k_note_follows_proxy_points() -> None:
    text = notes_ja.note_for(config.WORKFLOW_2K_FILE, 16_000_000)
    assert "16,000,000" in text and "16M" in text and "25,000,000" in text
    failed = notes_ja.note_for(config.WORKFLOW_2K_FILE, None)
    assert "通らない" in failed and "16,000,000" not in failed
    # 目安は測った値のときだけ出す（測っていない値に別の値の数字を書かない）
    assert "約 35 GiB" in notes_ja.note_for(config.WORKFLOW_2K_FILE, 20_000_000)
    unmeasured = notes_ja.note_for(config.WORKFLOW_2K_FILE, 13_000_000)
    assert "この値では未計測" in unmeasured and "GiB（Blender" not in unmeasured.split("### 調整")[0].split("### 上流との違い")[1]


PORTAL_GUIDE = TOOL_DIR.parent / "portal" / "guides" / "pixelartistry.html"
SWITCH_TITLE = "Boolean (Switch to Trellis2)"


def test_portal_guide_matches_config() -> None:
    """入口ページの使い方のページ（tools/portal/guides/pixelartistry.html）が、写しの設定と食い違わない。"""
    text = PORTAL_GUIDE.read_text(encoding="utf-8")
    assert SWITCH_TITLE in text
    if config.WTIVO_2K_PROXY_POINTS is not None:
        assert f"{config.WTIVO_2K_PROXY_POINTS:,}" in text
    assert "測定中" not in text, "実測の欄が埋まっていない"
    # 日本語ノートもスイッチを同じ名前で案内し、ガイドへの道を書く
    for file_name in config.WORKFLOW_FILES:
        note = notes_ja.note_for(file_name)
        assert "「使い方」のリンク" in note, file_name
    assert SWITCH_TITLE in notes_ja.note_for("PixelArtistry_01_Image_to_Watertight_Mesh.json")


def test_switch_title_in_guide_exists_in_upstream_workflows_when_vendor_exists() -> None:
    source_dir = config.PIXELARTISTRY_DIR / "workflows"
    if not source_dir.is_dir():
        pytest.skip("vendor が無い")
    for file_name in config.WORKFLOW_FILES:
        workflow = json.loads((source_dir / file_name).read_text(encoding="utf-8"))
        titles = [node.get("title") for node in workflow["nodes"] if node.get("type") == "PrimitiveBoolean"]
        assert titles == [SWITCH_TITLE], file_name


def test_config_2k_settings_are_consistent() -> None:
    assert config.WORKFLOW_2K_FILE in config.WORKFLOW_FILES
    value = config.WTIVO_2K_PROXY_POINTS
    assert value is None or (isinstance(value, int) and 0 < value < workflows.UPSTREAM_2K_PROXY_POINTS)


def write_2k_source(directory: Path) -> tuple[str, str]:
    """01b（2K）と 01 の 2 本を、配布元と同じ書式（indent=2）で置く。"""
    directory.mkdir(parents=True, exist_ok=True)
    with_2k = config.WORKFLOW_2K_FILE
    standard = "PixelArtistry_01_Image_to_Watertight_Mesh.json"
    (directory / with_2k).write_text(json.dumps(make_2k_workflow(), indent=2), encoding="utf-8")
    (directory / standard).write_text(json.dumps(make_workflow(), indent=2), encoding="utf-8")
    return with_2k, standard


def test_adapt_all_adds_japanese_note_and_lowers_2k_proxy_points_only(tmp_path: Path) -> None:
    names = write_2k_source(tmp_path / "src")
    dest, fallback_dir = tmp_path / "dest", tmp_path / "fallback"
    adapt_workflows.adapt_all(
        tmp_path / "src", dest, fallback_dir, config.BLENDER_PATH, 1 / 1536, False, names, proxy_points_2k=12_000_000
    )
    written_2k = json.loads((dest / names[0]).read_text(encoding="utf-8"))
    written_01 = json.loads((dest / names[1]).read_text(encoding="utf-8"))
    assert workflows.wtivo_proxy_points(written_2k) == 12_000_000
    assert workflows.wtivo_proxy_points(written_01) == 12_000_000  # 01 は元から 12M。値は触らない
    assert node_of(written_01, 327) == node_of(make_workflow(), 327)
    for written, file_name in ((written_2k, names[0]), (written_01, names[1])):
        notes = [node for node in written["nodes"] if node.get("title") == workflows.JA_NOTE_TITLE]
        assert len(notes) == 1 and notes[0]["widgets_values"] == [notes_ja.note_for(file_name, 12_000_000)]
        assert written["extra"]["ds"] == workflows.PIXELARTISTRY_VIEW
        assert workflows.blender_path_value(written) == config.BLENDER_PATH

    # 予備案は Blender のパスだけを変えたものから作る（ノートも 2K の変更も当てない）
    for file_name in names:
        fallback = json.loads((fallback_dir / adapt_workflows.fallback_name(file_name)).read_text(encoding="utf-8"))
        expected = workflows.make_fallback(
            workflows.set_blender_path(json.loads((tmp_path / "src" / file_name).read_text(encoding="utf-8")),
                                       config.BLENDER_PATH)
        )
        assert fallback == expected

    # proxy_points_2k=None なら 01b も上流の値のまま
    upstream = tmp_path / "upstream"
    adapt_workflows.adapt_all(tmp_path / "src", upstream, None, config.BLENDER_PATH, 1 / 1536, False, names,
                              proxy_points_2k=None)
    kept_2k = json.loads((upstream / names[0]).read_text(encoding="utf-8"))
    assert workflows.wtivo_proxy_points(kept_2k) == workflows.UPSTREAM_2K_PROXY_POINTS


def test_adapt_all_migrates_copies_written_by_earlier_versions(tmp_path: Path) -> None:
    names = write_2k_source(tmp_path / "src")
    dest = tmp_path / "dest"
    dest.mkdir()
    source = {name: json.loads((tmp_path / "src" / name).read_text(encoding="utf-8")) for name in names}
    # 1) 日本語ノートを足す前の版（Blender のパスだけ変えた写し）
    (dest / names[0]).write_text(
        workflows.dump_workflow(workflows.set_blender_path(source[names[0]], config.BLENDER_PATH)), encoding="utf-8"
    )
    # 2) ノートの文面と 2K の値が今と違う版（このスクリプトの以前の版が書いたまま）
    older = workflows.localize_pixelartistry(
        workflows.set_blender_path(source[names[1]], config.BLENDER_PATH), "古い文面"
    )
    (dest / names[1]).write_text(workflows.dump_workflow(older), encoding="utf-8")

    results = adapt_workflows.adapt_all(
        tmp_path / "src", dest, None, config.BLENDER_PATH, 1 / 1536, False, names, proxy_points_2k=12_000_000
    )
    assert [status for _path, status in results] == ["migrated", "migrated"]
    again = adapt_workflows.adapt_all(
        tmp_path / "src", dest, None, config.BLENDER_PATH, 1 / 1536, False, names, proxy_points_2k=16_000_000
    )
    # 2K の値を変えると 01b のノートと値が変わる（以前の版のまま → 書き換える）。01 はそのまま
    assert [status for _path, status in again] == ["migrated", "unchanged"]
    assert workflows.wtivo_proxy_points(json.loads((dest / names[0]).read_text(encoding="utf-8"))) == 16_000_000


def test_adapt_all_keeps_copy_saved_again_from_the_screen(tmp_path: Path) -> None:
    names = write_2k_source(tmp_path / "src")
    dest = tmp_path / "dest"
    adapt_workflows.adapt_all(tmp_path / "src", dest, None, config.BLENDER_PATH, 1 / 1536, False, names)
    # 画面で保存し直すと、詰めた JSON になり frontendVersion が入る（2026-10-25 に Image_to_3D で確かめた変化）
    saved = json.loads((dest / names[1]).read_text(encoding="utf-8"))
    saved.setdefault("extra", {})["frontendVersion"] = "1.55.17"
    (dest / names[1]).write_text(json.dumps(saved), encoding="utf-8")
    results = adapt_workflows.adapt_all(
        tmp_path / "src", dest, None, config.BLENDER_PATH, 1 / 1536, False, names, proxy_points_2k=16_000_000
    )
    assert {path.name: status for path, status in results}[names[1]] == "kept"
    assert json.loads((dest / names[1]).read_text(encoding="utf-8")) == saved


def test_adapt_main_proxy_points_2k_option(tmp_path: Path) -> None:
    names = write_2k_source(tmp_path / "src")
    for name in config.WORKFLOW_FILES:
        if name not in names:
            (tmp_path / "src" / name).write_text(json.dumps(make_workflow(with_wtivo=False), indent=2), encoding="utf-8")
    common = ["--source-dir", str(tmp_path / "src"), "--no-easy"]
    assert adapt_workflows.main([*common, "--dest-dir", str(tmp_path / "a"), "--proxy-points-2k", "20000000"]) == 0
    assert workflows.wtivo_proxy_points(json.loads((tmp_path / "a" / names[0]).read_text(encoding="utf-8"))) == 20_000_000
    assert adapt_workflows.main([*common, "--dest-dir", str(tmp_path / "b"), "--proxy-points-2k", "0"]) == 0
    assert (
        workflows.wtivo_proxy_points(json.loads((tmp_path / "b" / names[0]).read_text(encoding="utf-8")))
        == workflows.UPSTREAM_2K_PROXY_POINTS
    )
    assert adapt_workflows.main([*common, "--dest-dir", str(tmp_path / "c"), "--proxy-points-2k", "-5"]) == 1


# ---------------------------------------------------------------- 入口ワークフロー（Image_to_3D.app.json）


def test_make_easy_image_to_3d_sets_app_mode_with_three_inputs_and_adds_note() -> None:
    template = make_easy_template()
    easy = workflows.make_easy_image_to_3d(template)

    assert easy["extra"]["linearMode"] is True
    # アプリの入力は、画像・ポリゴン数（足した PrimitiveInt）・テクスチャサイズ（上流の PrimitiveInt）の順
    assert easy["extra"]["linearData"] == {
        "inputs": [[122, "image"], [324, "value"], [288, "value"]], "outputs": [322],
    }
    assert easy["extra"]["ds"] == workflows.EASY_VIEW
    assert easy["extra"]["frontendVersion"] == "1.49.6"  # 触らない項目は残る

    note = node_of(easy, 325)
    assert note["type"] == "MarkdownNote" and note["title"] == workflows.EASY_NOTE_TITLE
    assert note["widgets_values"] == [workflows.EASY_NOTE_TEXT]
    assert note["order"] > node_of(easy, 324)["order"] and easy["last_node_id"] == 325
    # LoadImage の左上（既存のノート 313 とは重ならない位置）
    assert note["pos"] == [-5080 - 780, 510 - 570]
    assert note["pos"][1] + note["size"][1] <= node_of(template, 313)["pos"][1]


def test_make_easy_image_to_3d_wires_polygon_count_into_decimate_mesh() -> None:
    easy = workflows.make_easy_image_to_3d(make_easy_template())

    control = node_of(easy, 324)
    assert control["type"] == "PrimitiveInt" and control["title"] == workflows.POLYGON_COUNT_TITLE
    assert control["widgets_values"] == [700000, "fixed"]  # DecimateMesh が持っていた値
    assert control["widgets_values_named"]["value"] == 700000
    assert control["outputs"] == [{"name": "INT", "type": "INT", "links": [1297]}]
    # DecimateMesh の真上（同じ x、グループの中）
    assert control["pos"] == [2330, -30 - 120]

    decimate = node_of(easy, 186)
    assert decimate["inputs"][-1] == {
        "name": "target_face_count", "type": "INT", "widget": {"name": "target_face_count"}, "link": 1297,
    }
    assert link_of(easy, 1297) == [1297, 324, 0, 186, len(decimate["inputs"]) - 1, "INT"]
    assert easy["last_link_id"] == 1297
    assert decimate["widgets_values"] == [700000, "midpoint"]  # widget の値は変えない

    assert node_of(easy, 288)["title"] == workflows.TEXTURE_SIZE_TITLE
    assert node_of(easy, 288)["widgets_values"] == [4096, "fixed"]  # 既定の大きさは変えない


def test_make_easy_image_to_3d_does_not_touch_the_template_or_other_nodes() -> None:
    template = make_easy_template()
    snapshot = copy.deepcopy(template)
    easy = workflows.make_easy_image_to_3d(template)

    assert template == snapshot
    added = {324, 325}
    changed = {186, 288}  # DecimateMesh の結線・テクスチャサイズの題名だけ
    unchanged = [node for node in easy["nodes"] if node["id"] not in added | changed]
    assert unchanged == [node for node in snapshot["nodes"] if node["id"] not in changed]
    assert easy["groups"] == snapshot["groups"]
    before_288 = {key: value for key, value in node_of(snapshot, 288).items() if key != "title"}
    assert {key: value for key, value in node_of(easy, 288).items() if key != "title"} == before_288


def test_make_easy_image_to_3d_is_idempotent() -> None:
    once = workflows.make_easy_image_to_3d(make_easy_template())
    assert workflows.make_easy_image_to_3d(once) == once
    # 画面で保存し直した形でも、2 回目は何も変えない
    saved = workflows.make_easy_image_to_3d(make_saved_easy_v1())
    assert workflows.make_easy_image_to_3d(saved) == saved


def test_make_easy_image_to_3d_upgrades_a_saved_first_version_in_place() -> None:
    saved = make_saved_easy_v1()
    snapshot = copy.deepcopy(saved)
    upgraded = workflows.make_easy_image_to_3d(saved)

    assert saved == snapshot
    assert upgraded["id"] == snapshot["id"]  # すでに入口なので id は変えない
    assert upgraded["extra"]["ds"] == snapshot["extra"]["ds"]  # 画面で動かした表示も変えない
    assert node_of(upgraded, 122)["widgets_values"] == ["E_copper_coin.png", "image"]  # 選んでいた画像を残す

    # 入力は、画面が保存した書式のまま 1 つ目を残し、足す項目も同じ書式にする
    wid = snapshot["id"]
    assert upgraded["extra"]["linearData"] == {
        "inputs": [
            [f"{wid}:122:image", "image"], [f"{wid}:325:value", "value"], [f"{wid}:288:value", "value"],
        ],
        "outputs": ["322"],
    }
    # ノートはもう 324 にあるので足さず、文面だけ今の版に直す。ポリゴン数のノードは次の id（325）
    assert [node["id"] for node in upgraded["nodes"] if node["type"] == "MarkdownNote"] == [313, 324]
    assert node_of(upgraded, 324)["widgets_values"] == [workflows.EASY_NOTE_TEXT]
    assert node_of(upgraded, 325)["title"] == workflows.POLYGON_COUNT_TITLE
    assert node_of(upgraded, 325)["inputs"][0]["localized_name"] == "値"  # 上流の PrimitiveInt の写し

    # 画面が widget の入力として持っていた項目に、つなぎ先を入れる（項目を増やさない）
    decimate_inputs = node_of(upgraded, 186)["inputs"]
    assert [item["name"] for item in decimate_inputs] == ["mesh", "target_face_count", "placement_mode"]
    assert decimate_inputs[1]["link"] == 1297
    assert link_of(upgraded, 1297) == [1297, 325, 0, 186, 1, "INT"]


def test_make_easy_image_to_3d_keeps_graph_view_choice() -> None:
    saved = make_saved_easy_v1()
    saved["extra"]["linearMode"] = False  # 画面でグラフ表示に戻した
    assert workflows.make_easy_image_to_3d(saved)["extra"]["linearMode"] is False


def test_make_easy_image_to_3d_gets_distinct_deterministic_id() -> None:
    template = make_easy_template()
    first = workflows.make_easy_image_to_3d(template)
    second = workflows.make_easy_image_to_3d(template)
    assert first["id"] == second["id"] != template["id"]
    assert re.fullmatch(r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}", first["id"])


@pytest.mark.parametrize("node_type", ["LoadImage", "Save3DAdvanced", "DecimateMesh"])
def test_make_easy_image_to_3d_rejects_missing_or_duplicated_node(node_type: str) -> None:
    missing = make_easy_template()
    missing["nodes"] = [node for node in missing["nodes"] if node["type"] != node_type]
    with pytest.raises(workflows.WorkflowFormatError, match=node_type):
        workflows.make_easy_image_to_3d(missing)

    originals = {"LoadImage": 122, "Save3DAdvanced": 322, "DecimateMesh": 186}
    duplicated = make_easy_template()
    duplicated["nodes"].append({**node_of(duplicated, originals[node_type]), "id": 400})
    with pytest.raises(workflows.WorkflowFormatError, match=node_type):
        workflows.make_easy_image_to_3d(duplicated)


def test_make_easy_image_to_3d_rejects_template_without_texture_size_node() -> None:
    template = make_easy_template()
    template["nodes"] = [node for node in template["nodes"] if node["id"] != 288]
    with pytest.raises(workflows.WorkflowFormatError, match="Texture Resolution"):
        workflows.make_easy_image_to_3d(template)


def test_make_easy_image_to_3d_rejects_decimate_input_linked_elsewhere() -> None:
    template = make_easy_template()
    node_of(template, 186)["inputs"].append(
        {"name": "target_face_count", "type": "INT", "widget": {"name": "target_face_count"}, "link": 555}
    )
    with pytest.raises(workflows.WorkflowFormatError, match="別のノード"):
        workflows.make_easy_image_to_3d(template)


def test_make_easy_image_to_3d_rejects_polygon_count_node_that_lost_its_link() -> None:
    easy = workflows.make_easy_image_to_3d(make_easy_template())
    for item in node_of(easy, 186)["inputs"]:
        item["link"] = None  # 画面でつなぎ先を外した
    with pytest.raises(workflows.WorkflowFormatError, match="つながっていない"):
        workflows.make_easy_image_to_3d(easy)


def test_write_easy_workflow_is_idempotent_and_keeps_user_edits(tmp_path: Path) -> None:
    template_path = tmp_path / "template.json"
    template_path.write_text(json.dumps(make_easy_template()), encoding="utf-8")
    dest = tmp_path / "workflows" / "Image_to_3D.app.json"

    assert adapt_workflows.write_easy_workflow(template_path, dest, False) == (dest, "written")
    written = json.loads(dest.read_text(encoding="utf-8"))
    assert written["extra"]["linearData"]["outputs"] == [322]
    assert adapt_workflows.write_easy_workflow(template_path, dest, False)[1] == "unchanged"

    dest.write_text("{}", encoding="utf-8")  # UI で手直しして保存した想定
    assert adapt_workflows.write_easy_workflow(template_path, dest, False)[1] == "kept"
    assert dest.read_text(encoding="utf-8") == "{}"
    assert adapt_workflows.write_easy_workflow(template_path, dest, True)[1] == "written"


def test_adapt_main_writes_easy_workflow_unless_disabled(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    for file_name in config.WORKFLOW_FILES:
        (source / file_name).write_text(json.dumps(make_workflow(), indent=2), encoding="utf-8")
    template_path = tmp_path / "template.json"
    template_path.write_text(json.dumps(make_easy_template()), encoding="utf-8")
    easy_dest = tmp_path / "easy" / "Image_to_3D.app.json"
    base = [
        "--source-dir", str(source), "--dest-dir", str(tmp_path / "dest"),
        "--easy-template", str(template_path), "--easy-dest", str(easy_dest),
    ]

    assert adapt_workflows.main([*base, "--no-easy"]) == 0
    assert not easy_dest.exists()
    assert adapt_workflows.main(base) == 0
    assert easy_dest.is_file()


def test_adapt_main_reports_missing_easy_template(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "src"
    source.mkdir()
    for file_name in config.WORKFLOW_FILES:
        (source / file_name).write_text(json.dumps(make_workflow(), indent=2), encoding="utf-8")
    monkeypatch.setattr(adapt_workflows, "find_easy_template", lambda *_args, **_kwargs: None)
    exit_code = adapt_workflows.main(
        ["--source-dir", str(source), "--dest-dir", str(tmp_path / "dest"), "--easy-dest", str(tmp_path / "e.json")]
    )
    assert exit_code == 1
    assert "見つかりません" in capsys.readouterr().err


def test_upgrade_easy_workflow_applies_current_changes_once(tmp_path: Path) -> None:
    saved = make_saved_easy_v1()
    path = tmp_path / "Image_to_3D.app.json"
    path.write_text(json.dumps(saved, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")  # 画面は詰めて書く
    path.chmod(0o600)

    assert adapt_workflows.upgrade_easy_workflow(path) == (path, "upgraded")
    assert json.loads(path.read_text(encoding="utf-8")) == workflows.make_easy_image_to_3d(saved)
    assert path.stat().st_mode & 0o777 == 0o600  # 権限は変えない
    assert adapt_workflows.upgrade_easy_workflow(path) == (path, "unchanged")


def test_adapt_main_upgrade_easy_keeps_user_choices(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = tmp_path / "src"
    source.mkdir()
    for file_name in config.WORKFLOW_FILES:
        (source / file_name).write_text(json.dumps(make_workflow(), indent=2), encoding="utf-8")
    easy_dest = tmp_path / "easy" / "Image_to_3D.app.json"
    base = ["--source-dir", str(source), "--dest-dir", str(tmp_path / "dest"), "--easy-dest", str(easy_dest)]

    # 書いたものが無いときは、作らずに止まる（--upgrade-easy は直すためのもの）
    assert adapt_workflows.main([*base, "--upgrade-easy"]) == 1
    assert "今の版へ直せません" in capsys.readouterr().err
    assert not easy_dest.exists()

    easy_dest.parent.mkdir()
    easy_dest.write_text(json.dumps(make_saved_easy_v1()), encoding="utf-8")
    assert adapt_workflows.main([*base, "--upgrade-easy"]) == 0
    assert "今の版の変更を当てました" in capsys.readouterr().out
    upgraded = json.loads(easy_dest.read_text(encoding="utf-8"))
    assert node_of(upgraded, 122)["widgets_values"] == ["E_copper_coin.png", "image"]
    assert len(upgraded["extra"]["linearData"]["inputs"]) == 3

    assert adapt_workflows.main([*base, "--upgrade-easy"]) == 0
    second = capsys.readouterr().out
    assert "当てました" not in second  # 2 回目はすでに今の版
    assert "Image_to_3D.app.json: 同じ内容なので触りません" in second
    assert json.loads(easy_dest.read_text(encoding="utf-8")) == upgraded


def test_real_easy_template_matches_expected_shape_when_installed() -> None:
    candidates = sorted(
        (config.TOOL_DIR / ".venv" / "lib").glob(
            f"python*/site-packages/comfyui_workflow_templates_json/templates/{config.EASY_TEMPLATE_FILE}"
        )
    )
    if not candidates:
        pytest.skip("comfyui-workflow-templates-json が入っていない（setup-server.sh の前）")
    template = json.loads(candidates[0].read_text(encoding="utf-8"))
    easy = workflows.make_easy_image_to_3d(template)
    nodes = {node["id"]: node for node in easy["nodes"]}
    (image, polygon_count, texture_size) = easy["extra"]["linearData"]["inputs"]
    (output_id,) = easy["extra"]["linearData"]["outputs"]
    assert nodes[image[0]]["type"] == "LoadImage" and image[1] == "image"
    assert nodes[polygon_count[0]]["type"] == "PrimitiveInt" and polygon_count[1] == "value"
    assert nodes[texture_size[0]]["type"] == "PrimitiveInt" and texture_size[1] == "value"
    assert nodes[output_id]["type"] == "Save3DAdvanced"
    # ポリゴン数の入力が DecimateMesh へ、テクスチャサイズの入力が UnwrapMesh と BakeTextureFromVoxel へつながっている
    consumers = {
        (nodes[link[3]]["type"], nodes[link[3]]["inputs"][link[4]]["name"])
        for link in easy["links"]
        if link[1] in (polygon_count[0], texture_size[0])
    }
    assert consumers == {
        ("DecimateMesh", "target_face_count"), ("UnwrapMesh", "resolution"), ("BakeTextureFromVoxel", "texture_size"),
    }
    # 重みの名前は config の取得対象に含まれる（テンプレートが別の重みを要求していないこと）
    required = {
        model["name"]
        for node in template["nodes"]
        for model in (node.get("properties") or {}).get("models") or []
    }
    assert required, "テンプレートに models の記述が無い"
    installed = {entry[1] for entry in config.MODEL_FILES}
    assert required <= installed, sorted(required - installed)


def test_fallback_name() -> None:
    assert adapt_workflows.fallback_name("A.json") == "A_noWTiVo.json"
    assert adapt_workflows.fallback_name("A") == "A_noWTiVo"


# ---------------------------------------------------------------- checks


def test_parse_last_json_line() -> None:
    assert checks.parse_last_json_line('[WTiVo] log\n{"a": 1}\n\n') == {"a": 1}
    assert checks.parse_last_json_line("not json") is None
    assert checks.parse_last_json_line("") is None
    assert checks.parse_last_json_line("[1, 2]") is None


def test_classify_nvcc() -> None:
    output = "nvcc: NVIDIA (R) Cuda compiler driver\nCuda compilation tools, release 12.6, V12.6.85\nBuild cuda_12.6.r12.6"
    assert checks.classify_nvcc(output, "12.6") == (True, "Build cuda_12.6.r12.6")
    assert checks.classify_nvcc(output.replace("12.6", "13.2"), "12.6")[0] is False
    assert checks.classify_nvcc(None, "12.6")[0] is False
    assert checks.classify_nvcc("  ", "12.6")[0] is False


def test_classify_torch_all_ok() -> None:
    probe = {"version": "2.9.1+cu126", "cuda_version": "12.6", "cuda_available": True,
             "device_name": "NVIDIA A30", "capability": "8.0"}
    rows = checks.classify_torch(probe, "2.9.1+cu126", "12.6", "A30")
    assert all(ok for _label, ok, _detail in rows)
    assert len(rows) == 5
    assert "8.0" in rows[-1][2]


def test_classify_torch_failures() -> None:
    rows = checks.classify_torch({"import_error": "No module named torch"}, "2.9.1+cu126", "12.6", "A30")
    assert rows == [("torch の import", False, "No module named torch")]

    probe = {"version": "2.9.1+cu128", "cuda_version": "12.8", "cuda_available": False}
    rows = checks.classify_torch(probe, "2.9.1+cu126", "12.6", "A30")
    assert [ok for _label, ok, _detail in rows] == [True, False, False, False, False]


def test_classify_commit_and_patch() -> None:
    assert checks.classify_commit("abc", "abc") == (True, "HEAD=abc")
    assert checks.classify_commit("abd", "abc")[0] is False
    assert checks.classify_commit(None, "abc")[0] is False
    assert checks.classify_patch("applied", "x.patch")[0] is True
    for state in ("applicable", "conflict", "missing"):
        assert checks.classify_patch(state, "x.patch")[0] is False


def test_classify_import_and_wtivo_module() -> None:
    assert checks.classify_import(None, "/x/cumesh/__init__.py") == (True, "/x/cumesh/__init__.py")
    assert checks.classify_import("ImportError: libc10.so") == (False, "ImportError: libc10.so")

    native = "/t/build"
    assert checks.classify_wtivo_module(None, "/t/build/wtivo_core.cpython-312-x86_64-linux-gnu.so", native)[0]
    # Windows 用の .pyd や別の場所から読まれたものは NG
    assert not checks.classify_wtivo_module(None, "/t/build/wtivo_core.pyd", native)[0]
    assert not checks.classify_wtivo_module(None, "/other/wtivo_core.so", native)[0]
    assert not checks.classify_wtivo_module(None, "/t/buildx/wtivo_core.so", native)[0]
    assert not checks.classify_wtivo_module("ImportError", None, native)[0]
    assert not checks.classify_wtivo_module(None, None, native)[0]


def test_classify_apt_packages() -> None:
    ok, detail = checks.classify_apt_packages({"libcgal-dev": True, "libtbb-dev": True})
    assert ok and "libcgal-dev" in detail
    ok, detail = checks.classify_apt_packages({"libcgal-dev": True, "libopenvdb-dev": False})
    assert not ok and "libopenvdb-dev" in detail and "sudo apt install" in detail


def test_classify_model_file_and_url() -> None:
    assert checks.classify_model_file(10, 10) == (True, "10 バイト")
    assert checks.classify_model_file(None, 10)[0] is False
    assert checks.classify_model_file(9, 10)[0] is False
    assert (
        checks.model_url("https://huggingface.co/", "Comfy-Org/Pixal3D", "vae/x.safetensors")
        == "https://huggingface.co/Comfy-Org/Pixal3D/resolve/main/vae/x.safetensors"
    )


def test_model_table_matches_installer_urls() -> None:
    # 配布元 bat の :models（L407-417）と同じ 9 ファイル・同じ URL
    bat = config.PIXELARTISTRY_DIR / "watertightMeshes_win_installer.bat"
    urls = [checks.model_url(config.HF_BASE_URL, repo, path) for _subdir, _name, repo, path, _size, _sha in config.MODEL_FILES]
    assert len(urls) == 9 and len(set(urls)) == 9
    for subdir, name, _repo, path, _size, sha in config.MODEL_FILES:
        assert path.endswith("/" + name)
        assert re.fullmatch(r"[0-9a-f]{64}", sha), name
    if bat.is_file():
        text = bat.read_text(encoding="utf-8").replace("%HF%", config.HF_BASE_URL)
        for url in urls:
            assert url in text, url


def test_classify_workflows() -> None:
    values = {"a.json": config.BLENDER_PATH, "b.json": config.BLENDER_PATH}
    assert checks.classify_workflows(values, config.BLENDER_PATH)[0] is True
    assert checks.classify_workflows(values | {"c.json": None}, config.BLENDER_PATH)[0] is False
    assert checks.classify_workflows(values | {"c.json": "blender"}, config.BLENDER_PATH)[0] is False


def test_classify_blender() -> None:
    output = "Blender 5.2.2 LTS\n\tbuild date: 2026-09-15\n"
    assert checks.parse_blender_version(output) == (5, 2, 2)
    assert checks.classify_blender(output, 5) == (True, "Blender 5.2.2")
    assert checks.classify_blender("Blender 4.0.2\n", 5)[0] is False
    assert checks.classify_blender("Blender 5.1\n", 5) == (True, "Blender 5.1.0")
    assert checks.classify_blender("snap: command not found", 5)[0] is False
    assert checks.classify_blender(None, 5)[0] is False
    assert checks.classify_blender("", 5)[0] is False


def test_classify_blender_launcher() -> None:
    assert checks.classify_blender_launcher("/snap/blender/7803/blender", config.BLENDER_SNAP_LAUNCHER)[0] is True
    assert checks.classify_blender_launcher("/usr/bin/snap", config.BLENDER_SNAP_LAUNCHER)[0] is False
    assert checks.classify_blender_launcher(None, config.BLENDER_SNAP_LAUNCHER)[0] is False


def test_default_blender_path_avoids_snap_run() -> None:
    # /snap/bin/blender は /usr/bin/snap へのリンクで、snap run が子を別の cgroup へ移す
    assert config.BLENDER_PATH == "/snap/blender/current/blender"
    assert not config.BLENDER_PATH.startswith("/snap/bin/")
    assert config.BLENDER_PATH not in config.LEGACY_BLENDER_PATHS
    assert "/snap/bin/blender" in config.LEGACY_BLENDER_PATHS


def test_classify_port_status() -> None:
    assert checks.classify_port_status(8188, False, False)[0] is True
    assert checks.classify_port_status(8188, True, True)[0] is True
    assert checks.classify_port_status(8188, True, False)[0] is False


def test_classify_selftest() -> None:
    passed = {"faces_in": 5060, "faces_out": 12000, "watertight": True, "seconds": 12.3}
    ok, detail = checks.classify_selftest(passed)
    assert ok and "5060" in detail and "12000" in detail
    assert checks.classify_selftest(passed | {"watertight": False})[0] is False
    assert checks.classify_selftest(passed | {"faces_out": 0})[0] is False
    assert checks.classify_selftest(passed | {"watertight": "True"})[0] is False
    assert checks.classify_selftest({"error": "WTiVo を読み込めません"}) == (False, "WTiVo を読み込めません")
    assert checks.classify_selftest({"skipped": True, "reason": "wtivo_* を import できないため"})[0] is False
    assert checks.classify_selftest(None)[0] is False


def test_classify_vram_is_informational() -> None:
    for used, active in ((None, False), (100, False), (20000, False), (20000, True)):
        assert checks.classify_vram_status(used, 1024, active)[0] is True


def test_summarize() -> None:
    overall, lines = checks.summarize([("a", True, "x"), ("b", False, "y")])
    assert overall is False
    assert lines == ["OK  a: x", "NG  b: y"]
    assert checks.summarize([("a", True, "x")])[0] is True


# ---------------------------------------------------------------- cli（純粋関数）


def test_build_serve_command() -> None:
    command = build_serve_command(python_bin="/v/python", main_py="/c/main.py", host="0.0.0.0", port=8188, extra_args=["--lowvram"])
    assert command == ["/v/python", "/c/main.py", "--listen", "0.0.0.0", "--port", "8188", "--lowvram"]


def test_build_serve_env_points_blender_to_clean_dirs() -> None:
    base = {"PATH": "/usr/bin", "BLENDER_USER_CONFIG": "/home/user/.config/blender"}
    env = build_serve_env(base, Path("/h/.config/comfyui/blender-clean"))
    assert env["BLENDER_USER_CONFIG"] == "/h/.config/comfyui/blender-clean/config"
    assert env["BLENDER_USER_SCRIPTS"] == "/h/.config/comfyui/blender-clean/scripts"
    assert env["BLENDER_USER_EXTENSIONS"] == "/h/.config/comfyui/blender-clean/extensions"
    assert env["PATH"] == "/usr/bin"
    # 元の環境は変えない
    assert base["BLENDER_USER_CONFIG"] == "/home/user/.config/blender"


def test_extra_args_from_env() -> None:
    assert extra_args_from_env({}) == []
    assert extra_args_from_env({"COMFYUI_EXTRA_ARGS": "--lowvram --preview-method 'none'"}) == ["--lowvram", "--preview-method", "none"]


def test_parser_defaults() -> None:
    parser = build_parser()
    serve = parser.parse_args(["serve"])
    assert (serve.host, serve.port) == ("0.0.0.0", 8188)
    doctor = parser.parse_args(["doctor", "--skip-selftest"])
    assert doctor.skip_selftest is True


# ---------------------------------------------------------------- 直値の食い違い
# setup-server.sh / build-wtivo.sh は venv を作る前に git を操作するので、config.py と同じ値を直値で持つ。


def test_setup_script_pins_match_config() -> None:
    text = (TOOL_DIR / "setup-server.sh").read_text(encoding="utf-8")
    assert f'COMFYUI_COMMIT="{config.COMFYUI_COMMIT}"' in text
    assert f'COMFYUI_TAG="{config.COMFYUI_TAG}"' in text
    assert f'PIXELARTISTRY_COMMIT="{config.PIXELARTISTRY_COMMIT}"' in text
    for node_name, url, commit in config.CUSTOM_NODES:
        assert f"{node_name} {url} {commit}" in text, node_name
    for name, url, commit in config.EXTENSION_SOURCES:
        assert url in text and commit in text, name


def test_build_wtivo_pins_match_config() -> None:
    text = (TOOL_DIR / "scripts" / "build-wtivo.sh").read_text(encoding="utf-8")
    assert f'SRC_COMMIT="{config.WTIVO_SRC_COMMIT}"' in text
    assert f'SRC_URL="{config.WTIVO_SRC_URL}"' in text
    assert f'APT_PACKAGES="{" ".join(config.WTIVO_APT_PACKAGES)}"' in text


def test_patches_target_expected_files() -> None:
    expected = {
        "wtivo-src-linux-build.patch": {"CMakeLists.txt", "scripts/build_gpupr.py"},
        "wtivo-src-core-vertices-lifetime.patch": {"native/core/wtivo_core.cpp"},
        "wtivo-node-allow-linux.patch": {"wtivo.py"},
        "cumesh-eigen-include.patch": {"setup.py"},
    }
    for patch_name, files in expected.items():
        text = (TOOL_DIR / "patches" / patch_name).read_text(encoding="utf-8")
        assert set(re.findall(r"^\+\+\+ b/(\S+)$", text, flags=re.MULTILINE)) == files, patch_name


def test_service_example() -> None:
    text = (TOOL_DIR / "systemd" / "comfyui.service.example").read_text(encoding="utf-8")
    for line in (
        "EnvironmentFile=-%h/.config/comfyui/comfyui.env",
        "ExecStart=@REPO_ROOT@/tools/comfyui/run-comfyui serve",
        "Restart=on-failure",
        "TimeoutStartSec=600",
        "MemoryHigh=44G",
        "MemoryMax=48G",
    ):
        assert line in text.splitlines(), line


# ---------------------------------------------------------------- 動画生成（2026-11-05）


def test_video_model_table_is_consistent() -> None:
    targets = set()
    for group, subdir, file_name, repo, path_in_repo, size, sha in config.VIDEO_MODEL_FILES:
        assert group in config.VIDEO_MODEL_GROUPS, file_name
        assert group in config.VIDEO_MODEL_GROUP_LABELS, group
        assert re.fullmatch(r"[0-9a-f]{64}", sha), file_name
        assert size > 0, file_name
        assert "/" in repo and not repo.startswith("/"), repo
        # repo 内のファイル名と置き先のファイル名の末尾が同じ（Kandinsky の Diffusers 形式は置き先が入れ子）
        assert path_in_repo.rsplit("/", 1)[-1] == file_name.rsplit("/", 1)[-1], file_name
        target = f"{subdir}/{file_name}"
        assert target not in targets, target
        targets.add(target)
    # 3D の重みと置き先が重ならない
    assert not targets & {f"{entry[0]}/{entry[1]}" for entry in config.MODEL_FILES}
    groups = {entry[0] for entry in config.VIDEO_MODEL_FILES}
    assert groups == set(config.VIDEO_MODEL_GROUPS)


def test_video_model_table_names_used_by_config_constants() -> None:
    names = {entry[2] for entry in config.VIDEO_MODEL_FILES}
    for name in (
        config.LTX25_GGUF_FILE,
        config.LTX25_TEXT_ENCODER_FILE,
        config.LTX25_VIDEO_VAE_FILE,
        config.HUNYUAN15_SR_FILE,
        config.QWEN25_VL_FILE,
        config.KANDINSKY6_LITE_FILE,
        config.KANDINSKY6_LITE_DISTILL_FILE,
    ):
        assert name in names, name
    assert config.LTX25_GGUF_FILE.endswith(".gguf")


def test_selected_video_entries() -> None:
    from cfui.cli import selected_video_entries

    assert selected_video_entries(False, None) == []
    assert selected_video_entries(True, None) == list(config.VIDEO_MODEL_FILES)
    only_ltx = selected_video_entries(False, ["ltx25"])
    assert only_ltx and {entry[0] for entry in only_ltx} == {"ltx25"}
    both = selected_video_entries(True, ["ltx25", "kandinsky6"])
    assert {entry[0] for entry in both} == {"ltx25", "kandinsky6"}


def test_fetch_models_video_arguments() -> None:
    parser = build_parser()
    plain = parser.parse_args(["fetch-models"])
    assert (plain.video, plain.group, plain.skip_3d, plain.verify) == (False, None, False, False)
    video = parser.parse_args(["fetch-models", "--video", "--group", "ltx25", "--group", "kandinsky6", "--skip-3d"])
    assert (video.video, video.group, video.skip_3d) == (True, ["ltx25", "kandinsky6"], True)
    with pytest.raises(SystemExit):
        parser.parse_args(["fetch-models", "--group", "wan"])


def test_video_model_rows_without_any_file_is_a_single_ok_row() -> None:
    sizes = {f"{entry[1]}/{entry[2]}": None for entry in config.VIDEO_MODEL_FILES}
    rows = checks.video_model_rows(config.VIDEO_MODEL_FILES, sizes, config.VIDEO_MODEL_GROUP_LABELS)
    assert len(rows) == 1
    assert rows[0][1] is True and "fetch-models --video" in rows[0][2]


def test_video_model_rows_with_some_files_checks_every_file() -> None:
    entries = config.VIDEO_MODEL_FILES
    sizes: dict[str, int | None] = {f"{entry[1]}/{entry[2]}": None for entry in entries}
    first, second = entries[0], entries[1]
    sizes[f"{first[1]}/{first[2]}"] = first[5]
    sizes[f"{second[1]}/{second[2]}"] = second[5] - 1  # 取りかけ
    rows = checks.video_model_rows(entries, sizes, config.VIDEO_MODEL_GROUP_LABELS)
    assert len(rows) == len(entries)
    assert rows[0][1] is True
    assert rows[1][1] is False and "大きさが違います" in rows[1][2]
    missing = [row for row in rows[2:] if not row[1]]
    assert missing and all("--group" in row[2] for row in missing)
    assert config.VIDEO_MODEL_GROUP_LABELS[first[0]] in rows[0][0]


def test_classify_node_link_and_video_workflows() -> None:
    assert checks.classify_node_link(None, "/x")[0] is False
    assert checks.classify_node_link("/y", "/x")[0] is False
    assert checks.classify_node_link("/x", "/x")[0] is True
    expected = config.VIDEO_WORKFLOW_FILES
    ok, detail = checks.classify_video_workflows(list(expected), expected)
    assert ok and str(len(expected)) in detail
    ok, detail = checks.classify_video_workflows(list(expected[1:]), expected)
    assert not ok and expected[0] in detail


def test_video_workflow_names_are_unique_and_point_to_known_bases() -> None:
    names = [entry[0] for entry in config.VIDEO_WORKFLOWS]
    assert len(names) == len(set(names)) == 8
    assert all(name.endswith(".json") and "/" not in name for name in names)
    kinds = [entry[2] for entry in config.VIDEO_WORKFLOWS]
    assert len(kinds) == len(set(kinds))


def test_setup_script_pins_kandinsky6_like_config() -> None:
    text = (TOOL_DIR / "setup-server.sh").read_text(encoding="utf-8")
    assert f'KANDINSKY6_URL="{config.KANDINSKY6_SRC_URL}"' in text
    assert f'KANDINSKY6_COMMIT="{config.KANDINSKY6_SRC_COMMIT}"' in text
    assert f'KANDINSKY6_LINK_TARGET="{config.KANDINSKY6_NODE_LINK_TARGET}"' in text
    # リンクの中身は custom_nodes/ から見て vendor/kandinsky-6/comfyui を指す
    assert (config.CUSTOM_NODES_DIR / config.KANDINSKY6_NODE_LINK_TARGET).resolve() == (
        config.KANDINSKY6_SRC_DIR / "comfyui"
    ).resolve()
    assert "adapt_video_workflows.py" in text
    assert "fetch-models --video" in text


def test_requirements_in_reads_video_node_requirements() -> None:
    text = (TOOL_DIR / "requirements.in").read_text(encoding="utf-8")
    assert f"-r vendor/ComfyUI/custom_nodes/{config.GGUF_NODE_NAME}/requirements.txt" in text
    assert "-r vendor/kandinsky-6/comfyui/requirements.txt" in text
    lock = (TOOL_DIR / "requirements.lock.txt").read_text(encoding="utf-8")
    for package in ("gguf", "librosa", "loguru", "omegaconf"):
        assert re.search(rf"^{package}==", lock, flags=re.MULTILINE), package


# ---------------------------------------------------------------- 動画の写し（cfui.video_workflows / adapt_video_workflows.py）

import adapt_video_workflows  # noqa: E402
from cfui import video_workflows  # noqa: E402


def _video_base(base_name: str) -> dict | None:
    path = adapt_video_workflows.find_base(base_name)
    return None if path is None else json.loads(path.read_text(encoding="utf-8"))


def _all_nodes(workflow: dict) -> list[dict]:
    nodes = list(workflow["nodes"])
    for definition in (workflow.get("definitions") or {}).get("subgraphs") or []:
        nodes.extend(definition.get("nodes") or [])
    return nodes


@pytest.mark.parametrize("file_name,base_name,kind", config.VIDEO_WORKFLOWS)
def test_video_workflow_from_real_base_when_installed(file_name: str, base_name: str, kind: str) -> None:
    base = _video_base(base_name)
    if base is None:
        pytest.skip("土台（同梱テンプレートか vendor/kandinsky-6）が無い（setup-server.sh の前）")
    original = copy.deepcopy(base)
    adapted = video_workflows.make_video_workflow(base, kind)
    assert base == original, "土台を書き換えた"
    assert video_workflows.make_video_workflow(adapted, kind) == adapted, "2 回当てると変わる"
    assert adapted["id"] != base["id"]
    nodes = _all_nodes(adapted)
    notes = [node for node in adapted["nodes"] if node.get("title") == video_workflows.JA_NOTE_TITLE]
    assert len(notes) == 1 and notes[0]["widgets_values"] == [notes_ja.video_note_for(kind)]
    stem = file_name.rsplit(".", 1)[0]
    saves = [node for node in adapted["nodes"] if node["type"] == "SaveVideo"]
    assert any(node["widgets_values"][0] == f"video/{stem}" for node in saves)
    # 写しが使う重みの名前は、どれも fetch-models --video が取るもの（gated な公式の重みを指さない）
    downloaded = {entry[2] for entry in config.VIDEO_MODEL_FILES}
    loader_values = {
        str(value)
        for node in nodes
        if node.get("mode", 0) != 4 and node["type"] in ("UNETLoader", "UnetLoaderGGUF", "CLIPLoader", "DualCLIPLoader", "VAELoader", "CLIPVisionLoader", "LatentUpscaleModelLoader")
        for value in (node.get("widgets_values") or [])
        if str(value).endswith((".safetensors", ".gguf"))
    }
    assert loader_values and loader_values <= downloaded, sorted(loader_values - downloaded)
    # properties.models（画面の「足りない重み」の案内）も、置き先からの相対パスで取得対象に含まれる
    downloaded_paths = {f"{entry[1]}/{entry[2]}" for entry in config.VIDEO_MODEL_FILES}
    referenced = {
        f"{model['directory']}/{model['name']}"
        for node in nodes
        for model in (node.get("properties") or {}).get("models") or []
    }
    assert referenced <= downloaded_paths, sorted(referenced - downloaded_paths)


def test_ltx_copy_uses_gguf_and_ungated_files_when_installed() -> None:
    base = _video_base("video_ltx2_5_t2v.json")
    if base is None:
        pytest.skip("同梱テンプレートが無い")
    adapted = video_workflows.make_video_workflow(base, "ltx_t2v")
    definition = adapted["definitions"]["subgraphs"][0]
    types = [node["type"] for node in definition["nodes"]]
    assert "UNETLoader" not in types and types.count("UnetLoaderGGUF") == 1
    instance = next(node for node in adapted["nodes"] if node["type"] == definition["id"])
    values = instance["widgets_values"]
    assert config.LTX25_GGUF_FILE in values
    assert config.LTX25_TEXT_ENCODER_FILE in values
    assert config.LTX25_VIDEO_VAE_FILE in values
    assert values[video_workflows._promoted_index(definition, "value")] is False  # Prompt Enhance は切ったまま


@pytest.mark.parametrize("kind,steps,cfg,scaling", [
    ("kandinsky_lite_t2v", 50, 5.0, 0.5302),
    ("kandinsky_distill_i2v", 10, 1.0, 0.417),
])
def test_kandinsky_copy_removes_beautify_and_sr_when_installed(kind: str, steps: int, cfg: float, scaling: float) -> None:
    base_name = next(entry[1] for entry in config.VIDEO_WORKFLOWS if entry[2] == kind)
    base = _video_base(base_name)
    if base is None:
        pytest.skip("vendor/kandinsky-6 が無い")
    adapted = video_workflows.make_video_workflow(base, kind)
    types = [node["type"] for node in adapted["nodes"]]
    assert not set(types) & video_workflows.KANDINSKY_REMOVED_TYPES
    assert types.count("UNETLoader") == 1 and types.count("CLIPLoader") == 0
    by_type = {node["type"]: node for node in adapted["nodes"]}
    expected_model = config.KANDINSKY6_LITE_DISTILL_FILE if "distill" in kind else config.KANDINSKY6_LITE_FILE
    assert by_type["UNETLoader"]["widgets_values"] == [expected_model, "default"]
    assert by_type["DualCLIPLoader"]["widgets_values"][0] == config.QWEN25_VL_FILE
    assert by_type["Kandinsky6MagCache"]["mode"] == 4
    assert by_type["Kandinsky6Sampler"]["widgets_values"][2:4] == [steps, cfg]
    assert by_type["Kandinsky6AudioVAELoader"]["widgets_values"][3] == scaling
    # どのリンクも残っているノードどうしを結び、CreateVideo はデコードと EmptyLatent の fps を直に受ける
    ids = {node["id"] for node in adapted["nodes"]}
    assert all(link[1] in ids and link[3] in ids for link in adapted["links"])
    links = {link[0]: link for link in adapted["links"]}
    create = by_type["CreateVideo"]
    sources = {item["name"]: by_type_of(adapted, links[item["link"]][1]) for item in create["inputs"] if item.get("link") is not None}
    assert sources == {"images": "VAEDecode", "audio": "Kandinsky6AudioVAEDecode", "fps": "Kandinsky6EmptyLatent"}
    positive = next(node for node in adapted["nodes"] if node.get("title") == config.KANDINSKY6_PROMPT_TITLE)
    assert positive["type"] == "Kandinsky6TextEncode"
    assert all(item.get("link") is None for item in positive["inputs"] if item["name"] in ("video_caption", "audio_caption"))
    assert positive["widgets_values"][0] and len(positive["widgets_values"]) == 2


def by_type_of(workflow: dict, node_id: int) -> str:
    return next(node["type"] for node in workflow["nodes"] if node["id"] == node_id)


def test_make_video_workflow_rejects_unexpected_shapes() -> None:
    with pytest.raises(workflows.WorkflowFormatError):
        video_workflows.make_video_workflow({"links": []}, "hunyuan_t2v")
    with pytest.raises(workflows.WorkflowFormatError):
        video_workflows.make_video_workflow({"nodes": [{"id": 1, "type": "SaveVideo", "pos": [0, 0], "widgets_values": ["x"]}]}, "hunyuan_t2v")
    with pytest.raises(workflows.WorkflowFormatError):
        video_workflows.make_video_workflow({"nodes": [{"id": 1, "type": "SaveVideo", "pos": [0, 0]}]}, "ltx_t2v")
    with pytest.raises(KeyError):
        video_workflows.make_video_workflow({"nodes": []}, "wan_t2v")


def test_video_notes_cover_every_copy_and_name_the_files() -> None:
    for _file_name, _base, kind in config.VIDEO_WORKFLOWS:
        note = notes_ja.video_note_for(kind)
        assert note.startswith("## 使い方（この機体）")
        assert "使い方（動画生成）" in note
    assert config.LTX25_GGUF_FILE in notes_ja.video_note_for("ltx_t2v")
    assert "CC BY-NC 4.0" in notes_ja.video_note_for("kandinsky_lite_t2v")
    # Kandinsky のノートは、写しで付けたプロンプトのノードの題名で指す（上流の題名は写しに残らない）
    for kind in ("kandinsky_lite_t2v", "kandinsky_lite_i2v", "kandinsky_distill_t2v", "kandinsky_distill_i2v"):
        note = notes_ja.video_note_for(kind)
        assert f"**{config.KANDINSKY6_PROMPT_TITLE}**" in note
        assert "Encode expanded" not in note


def _fake_bases(tmp_path: Path) -> dict[str, Path]:
    """土台の代わりに、各種類の変換が通る最小の形を置く（実物が無くても adapt_all を試せるように）。"""

    paths = {}
    for _file_name, base_name, kind in config.VIDEO_WORKFLOWS:
        real = adapt_video_workflows.find_base(base_name)
        if real is None:
            return {}
        paths[base_name] = real
    return paths


def test_adapt_video_all_writes_keeps_user_edits_and_refreshes_notes(tmp_path: Path) -> None:
    bases = _fake_bases(tmp_path)
    if not bases:
        pytest.skip("土台が無い（setup-server.sh の前）")
    dest = tmp_path / "Video"
    results, problems = adapt_video_workflows.adapt_all(dest, force=False)
    assert not problems and [status for _path, status in results] == ["written"] * 8
    results, _problems = adapt_video_workflows.adapt_all(dest, force=False)
    assert [status for _path, status in results] == ["unchanged"] * 8
    # 画面で手直し（プロンプトを変えた）した写しは上書きしない。--force なら上書き
    edited = dest / config.VIDEO_WORKFLOW_FILES[0]
    workflow = json.loads(edited.read_text(encoding="utf-8"))
    workflow["nodes"][0]["widgets_values"] = ["edited"] + list(workflow["nodes"][0].get("widgets_values") or [])[1:]
    edited.write_text(workflows.dump_workflow(workflow), encoding="utf-8")
    statuses = dict((path.name, status) for path, status in adapt_video_workflows.adapt_all(dest, force=False)[0])
    assert statuses[edited.name] == "kept"
    assert dict((path.name, status) for path, status in adapt_video_workflows.adapt_all(dest, force=True)[0])[edited.name] == "written"
    # 日本語ノートの文面だけが古い写しは、今の版へ書き換える
    stale = dest / config.VIDEO_WORKFLOW_FILES[1]
    workflow = json.loads(stale.read_text(encoding="utf-8"))
    note = next(node for node in workflow["nodes"] if node.get("title") == video_workflows.JA_NOTE_TITLE)
    note["widgets_values"] = ["古いノート"]
    stale.write_text(workflows.dump_workflow(workflow), encoding="utf-8")
    statuses = dict((path.name, status) for path, status in adapt_video_workflows.adapt_all(dest, force=False)[0])
    assert statuses[stale.name] == "migrated"


def test_adapt_video_reports_missing_base(tmp_path: Path) -> None:
    results, problems = adapt_video_workflows.adapt_all(tmp_path, force=False, find=lambda _name: None)
    assert results == [] and len(problems) == 8
    assert adapt_video_workflows.main(["--dest", str(tmp_path)]) in (0, 1)


def test_portal_video_guide_matches_config() -> None:
    """入口ページの動画生成の使い方（tools/portal/guides/video-generation.html）が、写しと重みの設定と食い違わない。"""
    text = (PORTAL_GUIDE.parent / "video-generation.html").read_text(encoding="utf-8")
    for file_name in config.VIDEO_WORKFLOW_FILES:
        stem = file_name.rsplit(".", 1)[0]
        # 表は T2V の名前と「_I2V」の略記を使うので、種類の名前（_T2V / _I2V を除いた部分）が出ていればよい
        assert stem.rsplit("_", 1)[0] in text, stem
    assert "UD-Q4_K_XL" in config.LTX25_GGUF_FILE and "UD-Q4_K_XL" in text
    assert "fetch-models --video" in text
    assert f"「{config.KANDINSKY6_PROMPT_TITLE}」" in text
    for group in config.VIDEO_MODEL_GROUPS:
        assert f"--group {group}" in text or f"<code>{group}</code>" in text, group
    for _file_name, _base, kind in config.VIDEO_WORKFLOWS:
        assert "使い方（動画生成）" in notes_ja.video_note_for(kind)


# ---------------------------------------------------------------- scripts/wait-gpu-idle.sh（偽の nvidia-smi で呼ぶ）
def _fake_nvidia_smi(bin_dir: Path, pid: int) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    fake = bin_dir / "nvidia-smi"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        "case \"$*\" in\n"
        "  *utilization.gpu*) echo 0 ;;\n"
        f"  *query-compute-apps*) echo '{pid}, python, 452 MiB' ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)


def _run_wait_gpu_idle(bin_dir: Path, *allow: str) -> subprocess.CompletedProcess[str]:
    args = ["bash", str(TOOL_DIR / "scripts" / "wait-gpu-idle.sh"), "--interval", "0.1", "--required", "2"]
    for item in allow:
        args += ["--allow", item]
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"}
    try:
        return subprocess.run(args, env=env, capture_output=True, text=True, timeout=3, check=False)
    except subprocess.TimeoutExpired as expired:
        out = expired.stdout.decode() if isinstance(expired.stdout, bytes) else (expired.stdout or "")
        return subprocess.CompletedProcess(args, 124, out, "")


def test_wait_gpu_idle_allow_matches_command_line(tmp_path: Path) -> None:
    """nvidia-smi のプロセス名が `python` としか出ない常駐プロセスも、コマンドラインの文字列で許可できる。"""

    marker = "localai-test-resident-scheduler"
    resident = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", marker])
    try:
        _fake_nvidia_smi(tmp_path / "bin", resident.pid)
        allowed = _run_wait_gpu_idle(tmp_path / "bin", marker)
        assert allowed.returncode == 0
        assert allowed.stdout.rstrip().endswith("GPU_IDLE")
        assert "idle_count=2/2" in allowed.stdout

        blocked = _run_wait_gpu_idle(tmp_path / "bin", "ai_finance")
        assert blocked.returncode == 124
        assert "GPU_IDLE" not in blocked.stdout
        assert "idle_count=0/2" in blocked.stdout
    finally:
        resident.kill()
        resident.wait()
