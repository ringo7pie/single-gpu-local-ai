#!/usr/bin/env python3
"""ゲームのエフェクト（VFX）の写し 2 本を、ComfyUI の利用者ワークフロー（user/default/workflows/VFX/）へ書く。

- WanAlpha_T2V.json（土台は同梱テンプレート video_wan2.1_alpha_t2v_14B.json）
- LayerDiffuse_SDXL_RGBA.json（土台は ComfyUI-layerdiffuse の example_workflows/layer_diffusion_fg_example_rgba.json）

--api を付けると、API 形式（画面の Save (API Format) と同じ。POST /prompt の prompt）の写し *_api.json も書く。
API 形式への変換には /object_info が要るので、ComfyUI が動いていること（--server）か、--object-info のファイルが要る。
変えるところは cfui/vfx_workflows.py の先頭に書いてある。

既に置いてあるファイルが、これから書く内容と違うときは上書きしない（画面で手直しして保存したものを守るため）。--force で上書きする。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from adapt_video_workflows import find_template  # noqa: E402
from adapt_workflows import _STATUS_LABELS, write_if_safe  # noqa: E402
from cfui import config, vfx_workflows  # noqa: E402
from cfui.workflows import dump_workflow  # noqa: E402
from run_workflow import DEFAULT_SERVER, convert_workflow, fetch_object_info  # noqa: E402

LAYERDIFFUSE_EXAMPLE = (
    config.CUSTOM_NODES_DIR / "ComfyUI-layerdiffuse" / "example_workflows" / vfx_workflows.LAYERDIFFUSE_EXAMPLE_FILE
)


def api_name(file_name: str) -> str:
    return file_name[: -len(".json")] + "_api.json"


def build_all() -> dict[str, dict]:
    template = find_template(vfx_workflows.WAN_TEMPLATE_FILE)
    if template is None:
        raise SystemExit(f"同梱テンプレート {vfx_workflows.WAN_TEMPLATE_FILE} がありません（comfyui-workflow-templates-json）")
    if not LAYERDIFFUSE_EXAMPLE.is_file():
        raise SystemExit(f"LayerDiffuse の手本がありません: {LAYERDIFFUSE_EXAMPLE}（custom_nodes/ComfyUI-layerdiffuse）")
    return {
        vfx_workflows.WAN_WORKFLOW_FILE: vfx_workflows.make_wan_alpha(json.loads(template.read_text(encoding="utf-8"))),
        vfx_workflows.LAYERDIFFUSE_WORKFLOW_FILE: vfx_workflows.make_layerdiffuse(
            json.loads(LAYERDIFFUSE_EXAMPLE.read_text(encoding="utf-8"))
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dest", type=Path, default=config.VFX_WORKFLOWS_DIR)
    parser.add_argument("--force", action="store_true", help="手直しされた写しも上書きする")
    parser.add_argument("--api", action="store_true", help="API 形式の写し（*_api.json）も書く")
    parser.add_argument("--server", default=os.environ.get("COMFYUI_SERVER", DEFAULT_SERVER))
    parser.add_argument("--object-info", type=Path, help="/object_info の JSON（無ければ --server から取る）")
    args = parser.parse_args(argv)

    workflows = build_all()
    object_info = None
    if args.api:
        object_info = (
            json.loads(args.object_info.read_text(encoding="utf-8")) if args.object_info else fetch_object_info(args.server)
        )
    failures = 0
    for file_name, workflow in workflows.items():
        path = args.dest / file_name
        status = write_if_safe(path, dump_workflow(workflow), args.force)
        print(f"  VFX/{file_name}: {_STATUS_LABELS[status]}")
        if object_info is None:
            continue
        prompt, report = convert_workflow(workflow, object_info)
        if report.get("warnings"):
            print(f"  VFX/{api_name(file_name)}: 変換の警告 {report['warnings']}", file=sys.stderr)
            failures += 1
        api_path = args.dest / api_name(file_name)
        status = write_if_safe(api_path, json.dumps(prompt, ensure_ascii=False, indent=2) + "\n", args.force)
        print(f"  VFX/{api_path.name}: {_STATUS_LABELS[status]}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
