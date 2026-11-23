"""saudio 自前の Gradio 画面。

`saudio.cli serve` から `python -m saudio.ui` として os.execve で起動される。
モデルは起動時に 1 度だけ読み込んで常駐させ、タブ 3 つ（文章 → 音・音 → 音・部分の作り直し / 続き）から使う。
モジュール先頭で gradio を import してよいのはここだけ。torch と上流は saudio.engine が関数の中で import する。
"""

from __future__ import annotations

import argparse
import sys
import traceback
from typing import Any, Callable

import gradio as gr

from . import config, engine as engine_module, help_texts, params, rest


def _recent_rows(limit: int = 20) -> list[list[Any]]:
    """画面の「最近の出力」の表の中身。"""

    return [
        [row["created_at"], params.MODE_LABELS.get(row["mode"], row["mode"]), row["prompt"], row["duration"], row["seed"], row["wav"]]
        for row in params.list_recent_outputs(config.OUTPUTS_DIR, limit)
    ]


def _blank_to_none(value: Any) -> Any:
    """gr.Number の空欄（None）や空文字を None にそろえる。"""

    return None if value is None or value == "" else value


def _run(eng: engine_module.Engine, build: Callable[[], params.GenerationParams]) -> tuple[str, str, list[list[Any]]]:
    try:
        request = build()
    except params.ParamError as error:
        raise gr.Error(str(error)) from error
    try:
        result = eng.generate(request)
    except params.ParamError as error:
        raise gr.Error(str(error)) from error
    except Exception as error:  # noqa: BLE001 - 画面にはそのまま原因を出す
        traceback.print_exc()
        raise gr.Error(f"生成に失敗しました: {type(error).__name__}: {error}") from error
    return str(result.wav_path), help_texts.format_metadata_text(result.metadata), _recent_rows()


def make_handlers(eng: engine_module.Engine) -> dict[str, Callable[..., tuple[str, str, list[list[Any]]]]]:
    """タブごとの処理。Gradio の API（gradio_client）からも同じ引数の順で呼べる。"""

    def text_handler(prompt, duration, seed, steps):
        return _run(eng, lambda: params.normalize_params(prompt, duration, seed, steps, params.MODE_TEXT))

    def audio_handler(prompt, duration, seed, steps, source_audio, init_noise_level):
        return _run(
            eng,
            lambda: params.normalize_params(
                prompt, duration, seed, steps, params.MODE_AUDIO, params.source_from_path(source_audio), init_noise_level
            ),
        )

    def inpaint_handler(prompt, duration, seed, steps, source_audio, inpaint_start, inpaint_end):
        return _run(
            eng,
            lambda: params.normalize_params(
                prompt, duration, seed, steps, params.MODE_INPAINT, params.source_from_path(source_audio),
                None, _blank_to_none(inpaint_start), _blank_to_none(inpaint_end),
            ),
        )

    return {params.MODE_TEXT: text_handler, params.MODE_AUDIO: audio_handler, params.MODE_INPAINT: inpaint_handler}


def _common_inputs() -> tuple[Any, Any, Any, Any]:
    prompt = gr.Textbox(
        label="説明文（英語）", lines=3, max_lines=8, placeholder=help_texts.EXAMPLES[0],
        info=f"{params.PROMPT_MAX_CHARS} 文字まで。",
    )
    gr.Examples(examples=[[text] for text in help_texts.EXAMPLES], inputs=[prompt], label="例（クリックで入力）")
    duration = gr.Slider(
        minimum=params.MIN_DURATION, maximum=params.MAX_DURATION, value=params.DEFAULT_DURATION, step=1, label="長さ（秒）",
    )
    with gr.Accordion("詳細設定", open=False):
        seed = gr.Number(value=params.SEED_RANDOM, precision=0, label="seed（-1 で毎回乱数）", info=help_texts.DETAIL_HELP["seed"])
        steps = gr.Slider(params.STEPS_MIN, params.STEPS_MAX, value=params.DEFAULT_STEPS, step=1, label="steps", info=help_texts.DETAIL_HELP["steps"])
    return prompt, duration, seed, steps


def _source_input() -> Any:
    # マイクはブラウザが https か localhost でないと使えない（LAN の http では動かない）ので、アップロードだけにする。
    return gr.Audio(label="元の音（wav・flac・ogg・mp3）", type="filepath", sources=["upload"])


def _outputs() -> tuple[Any, Any]:
    audio = gr.Audio(label="生成結果", type="filepath", interactive=False)
    info = gr.Textbox(label="詳細", lines=9, interactive=False)
    return audio, info


def build_demo(eng: engine_module.Engine) -> gr.Blocks:
    handlers = make_handlers(eng)
    with gr.Blocks(title=help_texts.PAGE_TITLE) as demo:
        gr.Markdown(help_texts.HEADER_MD)
        gr.Markdown(help_texts.PROMPT_TIPS_MD)
        with gr.Tabs():
            with gr.Tab(params.MODE_LABELS[params.MODE_TEXT]):
                gr.Markdown(help_texts.MODE_INTRO[params.MODE_TEXT])
                prompt, duration, seed, steps = _common_inputs()
                button = gr.Button("生成する", variant="primary")
                audio, info = _outputs()
                recent = gr.Dataframe(headers=help_texts.RECENT_HEADERS, value=_recent_rows(), label="最近の出力", interactive=False, wrap=True)
                button.click(handlers[params.MODE_TEXT], inputs=[prompt, duration, seed, steps], outputs=[audio, info, recent], api_name="generate_text")
            with gr.Tab(params.MODE_LABELS[params.MODE_AUDIO]):
                gr.Markdown(help_texts.MODE_INTRO[params.MODE_AUDIO])
                source = _source_input()
                prompt, duration, seed, steps = _common_inputs()
                noise = gr.Slider(
                    params.INIT_NOISE_MIN, params.INIT_NOISE_MAX, value=params.DEFAULT_INIT_NOISE_LEVEL, step=0.05,
                    label="作り変える強さ（init_noise_level）", info="元の音に足すノイズの量。小さいほど元の音に近い（0.1 でごく近い変化、0.5 で半々、1.0 で元の音は使われず説明文だけから作る）。",
                )
                button = gr.Button("作り変える", variant="primary")
                audio, info = _outputs()
                recent = gr.Dataframe(headers=help_texts.RECENT_HEADERS, value=_recent_rows(), label="最近の出力", interactive=False, wrap=True)
                button.click(
                    handlers[params.MODE_AUDIO], inputs=[prompt, duration, seed, steps, source, noise],
                    outputs=[audio, info, recent], api_name="generate_audio",
                )
            with gr.Tab(params.MODE_LABELS[params.MODE_INPAINT]):
                gr.Markdown(help_texts.MODE_INTRO[params.MODE_INPAINT])
                source = _source_input()
                prompt, duration, seed, steps = _common_inputs()
                with gr.Row():
                    start = gr.Number(value=None, label="作り直す範囲の開始（秒）", info="続きを作るときは元の音の長さ。")
                    end = gr.Number(value=None, label="作り直す範囲の終了（秒）", info="空なら「長さ」まで。")
                button = gr.Button("作り直す", variant="primary")
                audio, info = _outputs()
                recent = gr.Dataframe(headers=help_texts.RECENT_HEADERS, value=_recent_rows(), label="最近の出力", interactive=False, wrap=True)
                button.click(
                    handlers[params.MODE_INPAINT], inputs=[prompt, duration, seed, steps, source, start, end],
                    outputs=[audio, info, recent], api_name="generate_inpaint",
                )
        with gr.Accordion("API の使い方（他のプログラムから使う）", open=False):
            gr.Markdown(help_texts.API_MD)
        gr.Markdown(help_texts.LICENSE_MD + "\n\n" + help_texts.output_location_note())
    return demo


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="saudio.ui", description="Stable Audio 3.0 Medium の画面を起動する")
    parser.add_argument("--host", default=config.HOST)
    parser.add_argument("--port", type=int, default=config.PORT)
    parser.add_argument("--device", default=None, help="cuda か cpu。未指定なら GPU があれば cuda")
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    eng = engine_module.Engine(device=args.device)
    print("[saudio.ui] モデルを読み込んでいます...", flush=True)
    eng.load()
    print("[saudio.ui] モデルの読み込みが完了しました。", flush=True)

    config.OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    demo = build_demo(eng)
    # 生成は 1 度に 1 件（GPU が 1 枚。Engine の Lock でも守っている）。
    demo.queue(default_concurrency_limit=1)
    # 画面と REST の口（/api/v1/...）を 1 つのポートで出す（tools/audiocraft の acraft/ui.py と同じ方式）。
    # share=True は使わない（上流の run_gradio.py は共有リンクを作るが、LAN 内のサービスなので不要で危険）。
    demo.launch(
        server_name=args.host,
        server_port=args.port,
        share=False,
        allowed_paths=[str(config.OUTPUTS_DIR)],
        prevent_thread_lock=True,
    )
    rest.install_routes(demo.app, eng)
    print(f"[saudio.ui] REST の口（/api/v1/generate・/api/v1/health）を足しました（{args.host}:{args.port}）。", flush=True)
    demo.block_thread()


if __name__ == "__main__":
    sys.exit(main())
