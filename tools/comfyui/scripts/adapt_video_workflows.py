#!/usr/bin/env python3
"""動画生成の写し 8 本を、ComfyUI の利用者ワークフロー（user/default/workflows/Video/）へ書く（setup-server.sh の [11/12]）。

土台は ComfyUI 同梱のテンプレート（comfyui-workflow-templates-json の video_hunyuan_video_1.5_720p_*・video_ltx2_5_*）と、
Kandinsky 6.0 の拡張の example_workflows/（vendor/kandinsky-6/comfyui/example_workflows/）。
変えるところは cfui.video_workflows の先頭に書いてある（A30 で gated でない重みだけで動くようにする変更と、日本語の手順ノート）。

既に置いてあるファイルが、これから書く内容と違うときは上書きしない（画面で手直しして保存したものを守るため）。
ただし、日本語ノートの文面だけが今と違う写し（このスクリプトの以前の版が書いたまま）は今の内容へ書き換える。--force で上書きする。
標準ライブラリだけで動く（venv の python で流すのは、同梱テンプレートを import で探すため）。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from adapt_workflows import _STATUS_LABELS, write_if_safe  # noqa: E402
from cfui import config, notes_ja  # noqa: E402
from cfui.video_workflows import JA_NOTE_TITLE, make_video_workflow  # noqa: E402
from cfui.workflows import WorkflowFormatError, dump_workflow, upsert_markdown_note  # noqa: E402


def find_template(template_file: str) -> Path | None:
    """同梱テンプレートの場所。comfyui-workflow-templates-json が無いか、その名前が無ければ None。"""

    spec = importlib.util.find_spec("comfyui_workflow_templates_json")
    locations = list(spec.submodule_search_locations or []) if spec is not None else []
    # venv の外の python（uvx の試験など）からでも、この clone の venv に入っているものを使う。
    locations += sorted(str(path) for path in (config.TOOL_DIR / ".venv" / "lib").glob("python*/site-packages/comfyui_workflow_templates_json"))
    for location in locations:
        candidate = Path(location) / "templates" / template_file
        if candidate.is_file():
            return candidate
    return None


def find_base(base_name: str, kandinsky_dir: Path = config.KANDINSKY6_EXAMPLE_WORKFLOWS_DIR) -> Path | None:
    if base_name.startswith("Kandinsky "):
        candidate = kandinsky_dir / base_name
        return candidate if candidate.is_file() else None
    return find_template(base_name)


def _note_refresher(kind: str) -> Callable[[str], str | None]:
    """既存の写しの日本語ノートの文面だけを今の版にした文字列（読めなければ None）。"""

    def refresh(current: str) -> str | None:
        try:
            workflow = json.loads(current)
            return dump_workflow(upsert_markdown_note(workflow, JA_NOTE_TITLE, notes_ja.video_note_for(kind)))
        except (ValueError, WorkflowFormatError):
            return None

    return refresh


def adapt_all(dest_dir: Path, force: bool, find: Callable[[str], Path | None] = find_base) -> tuple[list[tuple[Path, str]], list[str]]:
    """8 本を書く。(書いた結果の一覧, 土台が見つからない・形が違う などの問題) を返す。"""

    results: list[tuple[Path, str]] = []
    problems: list[str] = []
    for file_name, base_name, kind in config.VIDEO_WORKFLOWS:
        base_path = find(base_name)
        if base_path is None:
            problems.append(f"{file_name}: 土台 {base_name} が見つかりません")
            continue
        try:
            base = json.loads(base_path.read_text(encoding="utf-8"))
            adapted = make_video_workflow(base, kind)
        except (ValueError, KeyError, WorkflowFormatError) as error:
            problems.append(f"{file_name}: 土台 {base_path} を変換できません（{error}）")
            continue
        dest = dest_dir / file_name
        results.append((dest, write_if_safe(dest, dump_workflow(adapted), force, refresh=_note_refresher(kind))))
    return results, problems


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="動画生成の写し（HunyuanVideo 1.5 / LTX 2.5 Fast / Kandinsky 6.0 Lite）を ComfyUI へ書く")
    parser.add_argument("--dest", type=Path, default=config.VIDEO_WORKFLOWS_DIR, help="書き出し先（既定: user/default/workflows/Video）")
    parser.add_argument("--force", action="store_true", help="内容が違っても上書きする")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    results, problems = adapt_all(args.dest, args.force)
    for path, status in results:
        print(f"  {path.name}: {_STATUS_LABELS[status]}")
    for problem in problems:
        print(f"  {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
