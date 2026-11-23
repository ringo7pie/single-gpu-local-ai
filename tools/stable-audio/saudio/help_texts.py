"""画面に出す日本語の説明文。標準ライブラリのみ（試験で、書いた事実が config・params と合うか確かめる）。"""

from __future__ import annotations

from . import config, params

PAGE_TITLE = "音楽・効果音生成（Stable Audio 3.0 Medium）"

HEADER_MD = f"""\
# {PAGE_TITLE}

文章（説明文）から、曲や効果音を作ります（Stability AI の Stable Audio 3.0 Medium。{params.SAMPLE_RATE // 1000}.1 kHz・ステレオ、最長 {params.MAX_DURATION:g} 秒）。
元の音を指定して作り変えたり（音 → 音）、一部だけを作り直したり、続きを作ったりもできます。
このサーバーの GPU（A30）で動いています。ほかの GPU サービス（Qwen・Strata・3D 生成・AudioCraft など）とは同時に動かせません。
"""

LICENSE_MD = """\
**ライセンス**: モデルの重みは Stability AI Community License です。年間の収益が 100 万米ドル未満の個人・組織は商用にも使えます
（それを超える場合は Stability AI の Enterprise License が要ります）。文章の符号化器 T5Gemma は Gemma の利用規約に従います。
上流のコード（Stability-AI/stable-audio-3）は MIT です。
"""

PROMPT_TIPS_MD = """\
**説明文は英語で書いてください。** ジャンル・楽器・雰囲気・テンポ（BPM）・構成を文で書くと安定します
（例: `An anthemic Pop Rock instrumental with driving drums and bright guitars, 120 BPM`）。効果音にも使えます。
"""

MODE_INTRO = {
    params.MODE_TEXT: "説明文だけから作ります。",
    params.MODE_AUDIO: (
        "元の音にノイズを足してから、説明文に沿って作り直します（曲調・ジャンル・雰囲気の作り変え）。"
        "「作り変える強さ（init_noise_level）」は元の音に足すノイズの量で、小さいほど元に近く、1.0 だと元の音は使われず説明文だけから作ります（元の音を残したいときは小さくします）。"
        "元の音は「長さ」に合わせて切られるか、無音で延ばされます。"
    ),
    params.MODE_INPAINT: (
        "元の音の［開始, 終了）秒だけを説明文に沿って作り直し、ほかは残します。"
        "**続きを作る**ときは、開始を元の音の長さにし、「長さ」を元の音より長くします（終了を空にすると「長さ」まで）。"
    ),
}

DETAIL_HELP = {
    "seed": "同じ説明文・同じ値で同じ seed なら、ほぼ同じ音になります。-1 は毎回乱数で、決まった値は詳細に出ます。",
    "steps": f"作る手順の回数（既定 {params.DEFAULT_STEPS}）。減らすと速くなり質が下がります。{params.DEFAULT_STEPS} より増やしても良くなるとは限りません。",
}

EXAMPLES = [
    "An anthemic Pop Rock instrumental that fills your head with nostalgic thoughtfulness",
    "Lo-fi hip hop beat with dusty vinyl crackle, mellow Rhodes chords and soft boom bap drums, 84 BPM",
    "Epic orchestral trailer music with thundering taiko drums and soaring brass",
    "Heavy rain on a tin roof with distant rolling thunder",
]

RECENT_HEADERS = ["作成", "モード", "説明文", "長さ（秒）", "seed", "ファイル"]

API_MD = f"""\
このサーバーの別のプログラムや別の PC から、画面を使わずに生成できます。JSON を POST すると、**wav がそのまま返ります**。

```bash
curl -X POST http://<このサーバーのアドレス>:{config.PORT}/api/v1/generate \\
  -H "Content-Type: application/json" \\
  -d '{{"prompt": "Lo-fi hip hop beat with mellow Rhodes chords, 84 BPM", "duration": 30}}' -o music.wav

# 続き（元の音 10 秒の後ろを 30 秒まで作る）。base64 はコマンドラインに載せず、標準入力から流す。
{{ printf '{{"prompt": "dreamy synthpop", "duration": 30, "mode": "inpaint", "inpaint_start": 10, "audio_base64": "'; base64 -w0 input.wav; printf '"}}'; }} \\
  | curl -X POST http://<このサーバーのアドレス>:{config.PORT}/api/v1/generate -H "Content-Type: application/json" -d @- -o continued.wav
```

- 生成は `POST /api/v1/generate`。状態は `GET /api/v1/health`。
- 本文の項目: `prompt`（必須・英語）・`duration`（{params.MIN_DURATION:g}〜{params.MAX_DURATION:g} 秒、既定 {params.DEFAULT_DURATION:g}）・`seed`・`steps`・`mode`・`audio_base64`・
  `init_noise_level`・`inpaint_start`・`inpaint_end`・`keep`。省略すると既定になります。
- `mode`: `text`（既定）・`audio`（音 → 音。`audio_base64` と `init_noise_level`）・`inpaint`（部分の作り直し・続き。`audio_base64` と `inpaint_start`、
  `inpaint_end` は省略すると `duration` まで）。元の音は wav・flac・ogg・mp3、{params.SOURCE_MAX_BYTES // (1024 * 1024)} MB まで。
- `keep` を `false` にすると、サーバーの `outputs/` に wav と json を残しません。
- 実際に使われた seed・長さ・所要時間は、応答のヘッダ（`X-Saudio-Seed`・`X-Saudio-Duration-Sec`・`X-Saudio-Elapsed-Sec`）にあります。
- 生成は 1 度に 1 件です（この画面の分も含む）。混み合うと 503（`Retry-After` 付き）を返します。
- 入力の誤りは 400 と `{{"error": "理由"}}`。`Content-Type` が `application/json` でないと 415 です。認証はありません。
"""


def output_location_note() -> str:
    """出力の置き場の案内。"""

    return f"作った音は、サーバーの {config.OUTPUTS_DIR} に wav と json（入力と所要時間）で残ります。"


def format_metadata_text(metadata: dict) -> str:
    """生成結果の詳細を画面に出す文字列にする。"""

    mode = metadata.get("mode") or params.MODE_TEXT
    lines = [
        f"モード: {params.MODE_LABELS.get(mode, mode)}（{metadata.get('model', '')}）",
        f"説明文: {metadata.get('prompt', '')}",
        f"長さ: {metadata.get('duration_actual_sec')} 秒（指定 {metadata.get('duration_requested_sec')} 秒）、"
        f"{metadata.get('sample_rate')} Hz・{metadata.get('channels')} ch",
        f"seed: {metadata.get('seed')}、steps: {metadata.get('steps')}",
    ]
    if mode == params.MODE_AUDIO:
        lines.append(f"作り変える強さ（init_noise_level）: {metadata.get('init_noise_level')}")
    if mode == params.MODE_INPAINT:
        lines.append(f"作り直した範囲: {metadata.get('inpaint_start_sec')} 〜 {metadata.get('inpaint_end_sec')} 秒")
    source = metadata.get("source")
    if isinstance(source, dict):
        lines.append(
            f"元の音: {source.get('name')}（{source.get('duration_sec')} 秒、{source.get('sample_rate')} Hz、"
            f"{source.get('channels')} ch。写し {source.get('wav')}）"
        )
    lines.append(f"生成時間: {metadata.get('elapsed_sec')} 秒（device: {metadata.get('device')}）")
    peak = metadata.get("peak_vram_mib")
    if peak is not None:
        lines.append(f"VRAM ピーク（torch の割り当て）: {peak} MiB")
    lines.append(f"ファイル: {metadata.get('wav', '')}")
    return "\n".join(lines)
