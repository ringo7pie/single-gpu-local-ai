"""acraft 自前の Gradio 画面。

`acraft.cli serve` から `python -m acraft.ui` として os.execve で起動される。
モデルは起動時に 1 度だけ読み込んで常駐させ、タブ 2 つ（作曲・効果音）から使う。
モジュール先頭で gradio を import してよいのはここだけ（acraft.cli / config / params は素の python から
import できる必要がある）。torch と audiocraft は acraft.engine が関数の中で import する。
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from typing import Any, Callable

import gradio as gr

from . import config, engine as engine_module, help_texts, params, rest


def _kind_label(kind: str) -> str:
    spec = params.KIND_SPECS.get(kind)
    return spec.label if spec else kind


def _recent_rows(limit: int = 20) -> list[list[Any]]:
    """画面の「最近の出力」の表の中身。"""

    return [
        [row["created_at"], _kind_label(row["kind"]), row["prompt"], row["duration"], row["seed"], row["wav"]]
        for row in params.list_recent_outputs(config.OUTPUTS_DIR, limit)
    ]


def _make_handler(eng: engine_module.Engine, kind: str) -> Callable[..., tuple[str, str, list[list[Any]]]]:
    def handler(prompt, duration, seed, top_k, top_p, temperature, cfg_coef):
        try:
            request = params.normalize_params(kind, prompt, duration, seed, top_k, top_p, temperature, cfg_coef)
        except params.ParamError as error:
            raise gr.Error(str(error)) from error
        try:
            result = eng.generate(request)
        except Exception as error:  # noqa: BLE001 - 画面にはそのまま原因を出す
            traceback.print_exc()
            raise gr.Error(f"生成に失敗しました: {type(error).__name__}: {error}") from error
        return str(result.wav_path), help_texts.format_metadata_text(result.metadata), _recent_rows()

    return handler


def _build_tab(eng: engine_module.Engine, kind: str) -> gr.Dataframe:
    spec = params.KIND_SPECS[kind]
    gr.Markdown(help_texts.KIND_INTRO[kind])
    prompt = gr.Textbox(
        label="説明文（英語）",
        lines=3,
        max_lines=6,
        placeholder=help_texts.KIND_EXAMPLES[kind][0],
        info=f"{params.PROMPT_MAX_CHARS} 文字まで。",
    )
    gr.Examples(examples=[[text] for text in help_texts.KIND_EXAMPLES[kind]], inputs=[prompt], label="例（クリックで入力）")
    duration = gr.Slider(
        minimum=spec.min_duration,
        maximum=spec.max_duration,
        value=spec.default_duration,
        step=1,
        label="長さ（秒）",
    )
    with gr.Accordion("詳細設定", open=False):
        seed = gr.Number(value=params.SEED_RANDOM, precision=0, label="seed（-1 で毎回乱数）", info=help_texts.DETAIL_HELP["seed"])
        top_k = gr.Slider(params.TOP_K_MIN, params.TOP_K_MAX, value=params.DEFAULT_TOP_K, step=1, label="top_k", info=help_texts.DETAIL_HELP["top_k"])
        top_p = gr.Slider(params.TOP_P_MIN, params.TOP_P_MAX, value=params.DEFAULT_TOP_P, step=0.01, label="top_p", info=help_texts.DETAIL_HELP["top_p"])
        temperature = gr.Slider(params.TEMPERATURE_MIN, params.TEMPERATURE_MAX, value=params.DEFAULT_TEMPERATURE, step=0.05, label="temperature", info=help_texts.DETAIL_HELP["temperature"])
        cfg_coef = gr.Slider(params.CFG_COEF_MIN, params.CFG_COEF_MAX, value=params.DEFAULT_CFG_COEF, step=0.5, label="cfg_coef", info=help_texts.DETAIL_HELP["cfg_coef"])
    button = gr.Button(f"{spec.label}を生成する", variant="primary")
    audio = gr.Audio(label="生成結果", type="filepath", interactive=False)
    info = gr.Textbox(label="詳細", lines=7, interactive=False)
    recent = gr.Dataframe(
        headers=help_texts.RECENT_HEADERS,
        value=_recent_rows(),
        label="最近の出力（作曲と効果音の両方）",
        interactive=False,
        wrap=True,
    )
    button.click(
        _make_handler(eng, kind),
        inputs=[prompt, duration, seed, top_k, top_p, temperature, cfg_coef],
        outputs=[audio, info, recent],
        api_name=f"generate_{kind}",
    )
    return recent


def build_demo(eng: engine_module.Engine) -> gr.Blocks:
    with gr.Blocks(title=help_texts.PAGE_TITLE) as demo:
        gr.Markdown(help_texts.HEADER_MD)
        gr.Markdown(help_texts.PROMPT_TIPS_MD)
        with gr.Tabs():
            for kind in params.KINDS:
                spec = params.KIND_SPECS[kind]
                with gr.Tab(f"{spec.label}（{spec.model_label}）"):
                    _build_tab(eng, kind)
        with gr.Accordion("API の使い方（他のプログラムから使う）", open=False):
            gr.Markdown(help_texts.API_MD)
        gr.Markdown(help_texts.LICENSE_MD + "\n\n" + help_texts.output_location_note())
    return demo


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="acraft.ui", description="音楽・効果音生成の画面を起動する")
    parser.add_argument("--host", default=config.HOST)
    parser.add_argument("--port", type=int, default=config.PORT)
    parser.add_argument("--device", default=None, help="cuda か cpu。未指定なら GPU があれば cuda")
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    music_id, sound_id = config.model_ids_from_env(os.environ)
    eng = engine_module.Engine(music_id, sound_id, device=args.device)
    print("[acraft.ui] モデルを読み込んでいます（重みがキャッシュにあれば 15 秒ほど。ディスクから読むときはもっとかかります）...", flush=True)
    eng.load()
    print("[acraft.ui] モデルの読み込みが完了しました。", flush=True)

    config.OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    demo = build_demo(eng)
    # 生成は 1 度に 1 件（GPU が 1 枚。Engine の Lock でも守っている）。
    demo.queue(default_concurrency_limit=1)

    # 画面（Gradio）と REST の口（/api/v1/...）を 1 つのポートで出す。Gradio のアプリを起動してから（スレッドは止めない）、
    # そのアプリへ REST の経路を足し、最後にここで待つ。Gradio の API（/gradio_api/...）はそのまま使える。
    # （mount_gradio_app で "/" に載せると、"/" が "//" へ転送されて画面の URL が崩れるため、この方式にした。）
    # share=True は絶対に使わない（LAN 内のサービスで、公開 URL を作る必要が無く危険）。
    # outputs/ は Gradio の既定の許可範囲（作業ディレクトリと一時ディレクトリ）の外なので allowed_paths に足す。
    demo.launch(
        server_name=args.host,
        server_port=args.port,
        share=False,
        allowed_paths=[str(config.OUTPUTS_DIR)],
        prevent_thread_lock=True,
    )
    rest.install_routes(demo.app, eng)
    print(f"[acraft.ui] REST の口（/api/v1/music・/api/v1/sound・/api/v1/health）を足しました（{args.host}:{args.port}）。", flush=True)
    demo.block_thread()


if __name__ == "__main__":
    sys.exit(main())
