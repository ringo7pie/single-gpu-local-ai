"""画面に出す日本語の説明文。標準ライブラリのみ（試験で、書いた事実が config・params と合うか確かめる）。"""

from __future__ import annotations

from . import config, params

PAGE_TITLE = "音楽・効果音生成（AudioCraft Medium）"

HEADER_MD = f"""\
# {PAGE_TITLE}

文章（説明文）から、**作曲**（MusicGen Medium、1.5B）と **効果音**（AudioGen Medium、1.5B）を作ります。
このサーバーの GPU（A30）で動いています。ほかの GPU サービス（Qwen・Strata・3D 生成）とは同時に動かせません。
"""

LICENSE_MD = """\
**ライセンス**: モデルの重みは CC-BY-NC 4.0（**非商用のみ**）です。作った音を収益化する用途には使えません。
個人の試作・研究・社内の検討用として使ってください。コードは MIT です（上流: facebookresearch/audiocraft）。
"""

PROMPT_TIPS_MD = """\
**説明文は英語で書いてください。** モデルは英語の説明で学習されていて、日本語だと精度が大きく落ちます。
ジャンル・楽器・雰囲気・テンポのように、短い名詞句を並べると安定します。
"""

KIND_INTRO = {
    params.KIND_MUSIC: (
        "曲を作ります（32 kHz・モノラルの wav）。歌声は上手く作れません。"
        "曲の終わりが無音に崩れることがあります。長さは 1〜30 秒で、10 秒前後が安定します。"
    ),
    params.KIND_SOUND: (
        "効果音・環境音を作ります（16 kHz・モノラルの wav）。足音・ドア・動物・乗り物・天気などに向きます。"
        "長さは 1〜20 秒です。10 秒を超える分は前の音をつないで延ばすため、継ぎ目が出ることがあります。"
    ),
}

KIND_EXAMPLES = {
    params.KIND_MUSIC: [
        "calm lo-fi piano melody with soft drums, relaxing, 80 bpm",
        "energetic 8-bit chiptune boss battle music, fast tempo",
        "epic orchestral trailer music with big brass and taiko drums",
    ],
    params.KIND_SOUND: [
        "footsteps on gravel, slow walking pace",
        "heavy wooden door creaking open in an empty hallway",
        "thunderstorm with heavy rain and distant thunder",
    ],
}

DETAIL_HELP = {
    "seed": "同じ説明文・同じ値で同じ seed なら、ほぼ同じ音になります。-1 は毎回乱数で、決まった値は詳細に出ます。",
    "top_k": "次の音の候補を、確率の高い上位何個に絞るか。小さいほど無難、大きいほど多様です（上流の既定 250）。",
    "top_p": "0 より大きくすると、top_k の代わりに累積確率で絞ります（0 は top_k を使う）。",
    "temperature": "大きいほど冒険的で、小さいほど平凡で安定します（既定 1.0）。",
    "cfg_coef": "説明文にどれだけ従わせるか。大きいほど説明文に忠実ですが、音が不自然になることがあります（既定 3.0）。",
}

RECENT_HEADERS = ["作成", "種類", "説明文", "長さ（秒）", "seed", "ファイル"]

API_MD = f"""\
このサーバーの別のプログラムや別の PC から、画面を使わずに生成できます。説明文を JSON で POST すると、**wav がそのまま返ります**。

```bash
curl -X POST http://<このサーバーのアドレス>:{config.PORT}/api/v1/music \\
  -H "Content-Type: application/json" \\
  -d '{{"prompt": "calm lo-fi piano melody, 80 bpm", "duration": 10}}' -o music.wav
```

- 作曲は `/api/v1/music`、効果音は `/api/v1/sound`。状態は `GET /api/v1/health`。
- 本文の項目: `prompt`（必須・英語）・`duration`・`seed`・`top_k`・`top_p`・`temperature`・`cfg_coef`・`keep`。省略すると既定になります。
  `keep` を `false` にすると、サーバーの `outputs/` に wav と json を残しません。
- 実際に使われた seed・長さ・所要時間は、応答のヘッダ（`X-Acraft-Seed`・`X-Acraft-Duration-Sec`・`X-Acraft-Elapsed-Sec`）にあります。
- 生成は 1 度に 1 件です（この画面の分も含む）。作曲 30 秒で約 33 秒かかるので、クライアントの待ち時間は 2 分以上にしてください。
  混み合うと 503（`Retry-After` 付き）を返します。
- 入力の誤りは 400 と `{{"error": "理由"}}`。`Content-Type` が `application/json` でないと 415 です。認証はありません。
"""


def output_location_note() -> str:
    """出力の置き場の案内。"""

    return f"作った音は、サーバーの {config.OUTPUTS_DIR} に wav と json（入力と所要時間）で残ります。"


def format_metadata_text(metadata: dict) -> str:
    """生成結果の詳細を画面に出す文字列にする。"""

    kind = metadata.get("kind", "")
    spec = params.KIND_SPECS.get(kind)
    label = spec.label if spec else kind
    lines = [
        f"種類: {label}（{metadata.get('model', '')}）",
        f"説明文: {metadata.get('prompt', '')}",
        f"長さ: {metadata.get('duration_actual_sec')} 秒（指定 {metadata.get('duration_requested_sec')} 秒）、"
        f"{metadata.get('sample_rate')} Hz",
        f"seed: {metadata.get('seed')}",
        f"生成時間: {metadata.get('elapsed_sec')} 秒（device: {metadata.get('device')}）",
    ]
    peak = metadata.get("peak_vram_mib")
    if peak is not None:
        lines.append(f"VRAM ピーク（torch の割り当て）: {peak} MiB")
    lines.append(f"ファイル: {metadata.get('wav', '')}")
    return "\n".join(lines)
