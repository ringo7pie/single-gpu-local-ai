#!/usr/bin/env python3
"""PixelArtistry のワークフロー 4 本を、ComfyUI の利用者ワークフローへ写す（setup-server.sh の [11/12]）。

配布元 bat の [6/8] の置き換え。写すときに変えるのは Blender のパスのノード（PrimitiveString、
既定値 "blender"）の値だけで、/snap/blender/current/blender にする（/usr/bin/blender は apt の 4.0.2 なので
使わない。/snap/bin/blender は snap run が comfyui の cgroup の外へ子を移すので使わない）。
それ以外は変えない。書式は配布元と同じ indent=2・末尾改行なしで、ensure_ascii=False で書く。

そのほかに、画像を 1 枚入れて Run を押すだけの入口 Image_to_3D.app.json も書く。ComfyUI 同梱のテンプレート
（comfyui-workflow-templates の 3d_pixal3d_trellis2_image_to_model.json）を土台に、アプリモードと手順のノートを足す
（cfui.workflows.make_easy_image_to_3d）。--no-easy で書かない。
画面で保存し直した Image_to_3D.app.json（手直しがあるので上書きしない）に今の版の変更だけを当てるには --upgrade-easy
（入力画像などの手直しは残す。何度流しても同じ結果になる）。

--fallback を付けると、予備案の写しも書く（WTiVo を外し、直後の LODTailor の Voxel Remesh を
relative_voxel_size=1/1536 で使う）。WTiVo を含まない 03 は予備案を作らない。

既に置いてあるファイルが、これから書く内容と違うときは上書きしない（UI で手直しして保存したものを
守るため）。--force で上書きする。ただし、以前の既定の Blender のパス（config.LEGACY_BLENDER_PATHS）で
書いたままの内容と一致するファイルは、手直しされていないので今の既定値へ書き換える。
標準ライブラリだけで動く。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections.abc import Sequence
from pathlib import Path

# scripts/ の 1 つ上（tools/comfyui）を import 先に足す。cfui を editable で入れていない python でも動くように。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cfui import config  # noqa: E402
from cfui.workflows import (  # noqa: E402
    FALLBACK_RELATIVE_VOXEL_SIZE,
    WorkflowFormatError,
    dump_workflow,
    has_wtivo,
    make_easy_image_to_3d,
    make_fallback,
    set_blender_path,
)

FALLBACK_SUFFIX = "_noWTiVo"


def fallback_name(file_name: str) -> str:
    stem, dot, extension = file_name.rpartition(".")
    if not dot:
        return file_name + FALLBACK_SUFFIX
    return f"{stem}{FALLBACK_SUFFIX}.{extension}"


def write_if_safe(path: Path, text: str, force: bool, replaceable: Sequence[str] = ()) -> str:
    """書いた / 以前の既定値のまま書き換えた / 同じなので触らない / 違うので触らない、のどれかを返す。

    replaceable は「手直しされていない古い写し」の内容。既存の内容がこのどれかと一致するなら上書きしてよい。
    """

    status = "written"
    if path.exists():
        current = path.read_text(encoding="utf-8")
        if current == text:
            return "unchanged"
        if current in replaceable:
            status = "migrated"
        elif not force:
            return "kept"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return status


_STATUS_LABELS = {
    "written": "書きました",
    "migrated": "以前の既定の Blender のパスのままだったので、今の既定値に書き換えました",
    "unchanged": "同じ内容なので触りません",
    "kept": "内容が違うので上書きしません（UI で手直しした可能性。--force で上書き）",
    "upgraded": "今の版の変更を当てました（手直しした内容は残しています）",
}


def adapt_all(
    source_dir: Path,
    dest_dir: Path,
    fallback_dir: Path | None,
    blender_path: str,
    relative_voxel_size: float,
    force: bool,
    file_names: tuple[str, ...] = config.WORKFLOW_FILES,
    legacy_blender_paths: Sequence[str] = config.LEGACY_BLENDER_PATHS,
) -> list[tuple[Path, str]]:
    """file_names を source_dir から読み、dest_dir（と fallback_dir）へ書く。結果の一覧を返す。

    legacy_blender_paths は以前の既定の Blender のパス。そのパスで書いたままの写しは、手直しされて
    いないものとして blender_path の内容に書き換える。
    """

    results = []
    for file_name in file_names:
        source_path = source_dir / file_name
        workflow = json.loads(source_path.read_text(encoding="utf-8"))
        adapted = set_blender_path(workflow, blender_path)
        legacy = [
            set_blender_path(workflow, legacy_path)
            for legacy_path in legacy_blender_paths
            if legacy_path != blender_path
        ]
        dest_path = dest_dir / file_name
        results.append(
            (
                dest_path,
                write_if_safe(dest_path, dump_workflow(adapted), force, [dump_workflow(item) for item in legacy]),
            )
        )

        if fallback_dir is not None and has_wtivo(adapted):
            fallback = make_fallback(adapted, relative_voxel_size)
            fallback_path = fallback_dir / fallback_name(file_name)
            legacy_fallbacks = [dump_workflow(make_fallback(item, relative_voxel_size)) for item in legacy]
            results.append(
                (fallback_path, write_if_safe(fallback_path, dump_workflow(fallback), force, legacy_fallbacks))
            )
    return results


def find_easy_template(template_file: str = config.EASY_TEMPLATE_FILE) -> Path | None:
    """ComfyUI 同梱のテンプレートの場所を返す。comfyui-workflow-templates-json が見つからなければ None。"""

    spec = importlib.util.find_spec("comfyui_workflow_templates_json")
    if spec is None or not spec.submodule_search_locations:
        return None
    for location in spec.submodule_search_locations:
        candidate = Path(location) / "templates" / template_file
        if candidate.is_file():
            return candidate
    return None


def write_easy_workflow(template_path: Path, dest_path: Path, force: bool) -> tuple[Path, str]:
    """入口ワークフロー（画像を入れて Run を押すだけ）を dest_path へ書く。結果は write_if_safe と同じ。"""

    template = json.loads(template_path.read_text(encoding="utf-8"))
    easy = make_easy_image_to_3d(template)
    return dest_path, write_if_safe(dest_path, dump_workflow(easy), force)


def upgrade_easy_workflow(path: Path) -> tuple[Path, str]:
    """画面で保存し直した入口ワークフローに、今の版の変更（make_easy_image_to_3d）を当てる。

    結果は "upgraded"（書き換えた）か "unchanged"（すでに今の版）。書式は dump_workflow に揃うので、
    画面が保存した詰めた書式のファイルは、読みやすい書式（indent=2）に変わる。
    """

    current = json.loads(path.read_text(encoding="utf-8"))
    upgraded = make_easy_image_to_3d(current)
    if upgraded == current:
        return path, "unchanged"
    path.write_text(dump_workflow(upgraded), encoding="utf-8")
    return path, "upgraded"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PixelArtistry のワークフローを ComfyUI へ写す")
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=config.PIXELARTISTRY_DIR / "workflows",
        help="配布元のワークフローの場所（既定: vendor/PixelArtistry-Watertight-Meshes/workflows）",
    )
    parser.add_argument(
        "--dest-dir",
        type=Path,
        default=config.WORKFLOWS_DIR,
        help="書き出し先（既定: vendor/ComfyUI/user/default/workflows/PixelArtistry）",
    )
    parser.add_argument("--blender-path", default=config.BLENDER_PATH, help="Blender の実行ファイル")
    parser.add_argument(
        "--fallback", action="store_true", help="予備案（WTiVo を外したもの）の写しも書く"
    )
    parser.add_argument(
        "--fallback-dir",
        type=Path,
        default=config.FALLBACK_WORKFLOWS_DIR,
        help="予備案の書き出し先（既定: vendor/ComfyUI/user/default/workflows/PixelArtistry_noWTiVo）",
    )
    parser.add_argument(
        "--fallback-voxel-size",
        type=float,
        default=FALLBACK_RELATIVE_VOXEL_SIZE,
        help="予備案の LODTailor の relative_voxel_size（既定: 1/1536）",
    )
    parser.add_argument(
        "--no-easy", action="store_true", help="入口ワークフロー（Image_to_3D.app.json）を書かない"
    )
    parser.add_argument(
        "--easy-template",
        type=Path,
        default=None,
        help="入口ワークフローの土台（既定: comfyui-workflow-templates の 3d_pixal3d_trellis2_image_to_model.json）",
    )
    parser.add_argument(
        "--easy-dest",
        type=Path,
        default=config.EASY_WORKFLOW_PATH,
        help="入口ワークフローの書き出し先（既定: vendor/ComfyUI/user/default/workflows/Image_to_3D.app.json）",
    )
    parser.add_argument(
        "--upgrade-easy",
        action="store_true",
        help="書いてあるファイルは上書きせず、今の版の変更だけを当てる（画面で手直し・保存し直したあとの更新用）",
    )
    parser.add_argument("--force", action="store_true", help="内容が違っても上書きする")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        results = adapt_all(
            source_dir=args.source_dir,
            dest_dir=args.dest_dir,
            fallback_dir=args.fallback_dir if args.fallback else None,
            blender_path=args.blender_path,
            relative_voxel_size=args.fallback_voxel_size,
            force=args.force,
        )
    except (OSError, json.JSONDecodeError, WorkflowFormatError) as exc:
        print(f"ワークフローを写せませんでした: {exc}", file=sys.stderr)
        return 1
    for path, status in results:
        print(f"  {path.name}: {_STATUS_LABELS[status]}（{path.parent}）")
    if args.no_easy:
        return 0

    if args.upgrade_easy:
        if not args.easy_dest.is_file():
            print(f"今の版へ直せません: {args.easy_dest} がありません（--upgrade-easy を外して書き出す）", file=sys.stderr)
            return 1
        try:
            path, status = upgrade_easy_workflow(args.easy_dest)
        except (OSError, json.JSONDecodeError, WorkflowFormatError) as exc:
            print(f"入口ワークフロー（{args.easy_dest.name}）を直せませんでした: {exc}", file=sys.stderr)
            return 1
        print(f"  {path.name}: {_STATUS_LABELS[status]}（{path.parent}）")
        return 0

    template_path = args.easy_template or find_easy_template()
    if template_path is None:
        print(
            "入口ワークフロー（Image_to_3D.app.json）を書けませんでした: ComfyUI 同梱のテンプレート"
            f"（{config.EASY_TEMPLATE_FILE}）が見つかりません。venv の python で実行してください",
            file=sys.stderr,
        )
        return 1
    try:
        path, status = write_easy_workflow(template_path, args.easy_dest, args.force)
    except (OSError, json.JSONDecodeError, WorkflowFormatError) as exc:
        print(f"入口ワークフロー（Image_to_3D.app.json）を書けませんでした: {exc}", file=sys.stderr)
        return 1
    print(f"  {path.name}: {_STATUS_LABELS[status]}（{path.parent}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
