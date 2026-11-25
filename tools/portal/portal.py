#!/usr/bin/env python3
"""ローカル AI サーバーの入口ページ（8080 番）。標準ライブラリだけで動く。

使い方:
    /usr/bin/python3 portal.py [--host 0.0.0.0] [--port 8080]

エンドポイント:
    GET  /                                    index.html を返す（このファイルと同じディレクトリから読む）
    GET  /api                                 この API の一覧（エンドポイント・サービス・ポート・GPU の排他・curl の例）を JSON で返す
    GET  /api/status                          GPU / Ollama / 各サービスの状態を JSON で返す
    GET  /api/ollama                          Ollama に入っているモデル（installed）と VRAM に載っているモデル（loaded）
    POST /api/ollama/<model>/<load|unload>    Ollama のモデルを VRAM に載せる / 降ろす（Ollama 11434 は LAN に公開していないため、
                                               他の PC からはここを通す）。<model> は /api/tags にあるものだけ。load は載せ終わるまで
                                               待ち、GPU を排他的に使うサービスが動いていれば先に止める。?keep_alive=<秒|30m|2h|-1>
    （POST /api/service/<key>/start には ?wait=<秒> を付けられる。ポートが応答するまで待ってから ready・waited_sec を返す）
    GET  /guides/<名前>.html                  使い方のページ（guides/ の中の HTML）を返す。名前は英小文字・数字・
                                               ハイフンだけで、guides/ の直下に無いものは 404（../ などで外を読ませない）
    POST /api/service/<key>/<action>          Open WebUI・Strata（汎用版・Coder 版・公式版）・Hunyuan3D・Pixal3D・ComfyUI・
                                               音楽・効果音生成（AudioCraft）の
                                               起動 (start) / 停止 (stop)。
                                               GET では 405。未導入の key・action 以外は 404/400。
                                               Hunyuan3D・Pixal3D・ComfyUI・AudioCraft・Strata（5 つとも）は GPU を同時に使えない
                                               ため、どれかの起動時に他が動いていれば自動で止めてから起動する。
                                               Strata（5 つとも）と AudioCraft の起動前には Ollama に載っているモデルも降ろし、
                                               応答の JSON に "unloaded_models" として入れる。
    POST /api/option/<key>/low-vram/<on|off>  低 VRAM モードの切り替え（Hunyuan3D・Pixal3D のみ）。
                                               稼働中・起動中は 409（systemd の EnvironmentFile は
                                               起動時にしか読まれないため）。GET では 405。
    POST /api/memory/<key>/<high_gib>/<max_gib>  systemd のメモリ上限（MemoryHigh / MemoryMax）を
                                               GiB 単位の整数で設定する（Open WebUI・Strata（5 つ）・
                                               Hunyuan3D・Pixal3D・ComfyUI・AudioCraft のみ。portal は 404）。稼働中でも再起動なしで
                                               即座に効く。GET では 405。

方針: nvidia-smi・Ollama・各ポート・各 systemd ユニット・/proc/meminfo への疎通確認はどれも
失敗しうる前提で書く。失敗しても /api/status は 500 を返さず、取れなかった項目を
available/reachable=false にして返す。/api/service/... や /api/option/... も同様に、systemctl や
設定ファイルの読み書きが失敗しても 500 ではなく {"ok": false, ...} を返す
（/api/memory/... も systemctl の失敗は同様。ただし /proc/meminfo が読めない場合のみ 500 を返す）。
"""
from __future__ import annotations

import argparse
import http.client
import json
import math
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socket import create_connection
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
INDEX_FILE = BASE_DIR / "index.html"
# 使い方のページ（GET /guides/<名前>.html）。入口ページの各カードからリンクする。
GUIDES_DIR = BASE_DIR / "guides"
GUIDE_PATH_PREFIX = "/guides/"
GUIDE_NAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]*\.html")

OLLAMA_STATUS_URL = "http://127.0.0.1:11434/api/ps"
OLLAMA_GENERATE_URL = "http://127.0.0.1:11434/api/generate"
OLLAMA_TAGS_URL = "http://127.0.0.1:11434/api/tags"
NVIDIA_SMI_TIMEOUT_SEC = 2.0
OLLAMA_TIMEOUT_SEC = 2.0
# モデルを降ろす要求は、生成中のモデルがあるとその終わりを待たされることがあるため、状態の確認より長くする。
OLLAMA_UNLOAD_TIMEOUT_SEC = 20.0
# モデルを VRAM へ載せる要求は、17 GB の読み込みに約 60 秒かかる（knowledge の env.md）。余裕を見て 5 分。
OLLAMA_LOAD_TIMEOUT_SEC = 300.0
PORT_CHECK_TIMEOUT_SEC = 0.3
MIB_BYTES = 1024 * 1024
GIB_BYTES = 1024 * 1024 * 1024
MEMINFO_PATH = Path("/proc/meminfo")

# メモリ上限（MemoryHigh / MemoryMax）の既定値・許容範囲。単位はすべて GiB。
# 根拠は knowledge/10-silver/areas/env.md（実測62.5 GiB の RAM。デスクトップ等で約10.5 GiB、
# Qwen が GPU に入りきらないときさらに約6 GiB を消費する）。
MEMORY_DEFAULT_HIGH_GIB = 44
MEMORY_DEFAULT_MAX_GIB = 48
MEMORY_MIN_GIB = 4
# 許容上限 = RAM 総量 - MEMORY_RESERVE_GIB（切り捨て）。これを超えると systemd-oomd が
# デスクトップごと止めるおそれがあるため、画面からもこれより大きい値は送れない。
MEMORY_RESERVE_GIB = 4
# 推奨上限 = RAM 総量 - MEMORY_RECOMMENDED_MARGIN_GIB（切り捨て）。デスクトップ等の常駐分と
# Qwen が GPU からあふれた分を差し引いた値。超えても拒否はしないが画面で警告する。
MEMORY_RECOMMENDED_MARGIN_GIB = 14

# 入口ページが表示するサービス一覧（ポート配置は knowledge/10-silver/areas/env.md の決定に従う）
# unit は systemd --user のユニット名。ユニットを持たないサービスを足すときは None にし、
# 起動・停止・メモリ上限の対象から外す（今は全サービスがユニットを持つ）。
# portal 自身は絶対にここへ入れない（自分を止めたらページが死ぬ）。
#
# options を持つサービスは、入口ページから切り替えられる設定を持つ（今のところ低 VRAM モードのみ）。
# env_file はサービスの systemd ユニットが EnvironmentFile として読む設定ファイル、var はその中の
# 変数名、default はファイル・変数が無いときにみなす値。
SERVICES = [
    {"key": "open-webui", "name": "Open WebUI", "port": 8081, "unit": "open-webui"},
    # Strata は推論エンジン（OpenAI 互換 API）。チャット系なので Open WebUI の次に並べる。
    # 同じエンジンで動くモデルが 5 つあり、汎用版（strata）・コード向け（strata-coder）・公式量子化版（strata-official）・
    # 公式版の軽い量子化（strata-official-iq3s）・Swift 1.5（strata-swift）を別のサービスとして持つ。
    # どちらも低 VRAM のスイッチは持たない（options 無し）。GPU の排他グループに入り、起動前に
    # Ollama のモデルを降ろす（GPU_EXCLUSIVE_KEYS・OLLAMA_UNLOAD_BEFORE_START_KEYS）。
    # 汎用版は 125B の MoE のエキスパートを RAM に置く（約 50 GiB 以上）。既定の 44 / 48 GiB では足りず
    # 絞られるので、メモリ上限の既定値だけ別に持つ（memory_default。値は実機の計測で見直す）。
    {
        "key": "strata",
        "name": "Strata（Qwen3.8 Flash Next）",
        "port": 8082,
        "unit": "strata",
        "memory_default": {"high_gib": 50, "max_gib": 54},
    },
    # コード向け版は 512 エキスパートを半分に削ったモデルで、RAM を約 36 GiB 使う。
    # memory_default の 36 / 40 GiB は実測（2026-10-07、cgroup の memory.peak 36.00 GiB。32/36 と 40/44 で
    # 起動・prefill・生成に差が無かった）。根拠は tools/strata/README.md。
    {
        "key": "strata-coder",
        "name": "Strata Flash-Next Coder",
        "port": 8083,
        "unit": "strata-coder",
        "memory_default": {"high_gib": 36, "max_gib": 40},
    },
    # 公式版は汎用版と同じ Qwen3.8-Flash-Next（512 エキスパートのまま）の ISTA-DASLab 公式量子化（GSQ-RCO）IQ3_XXS で、
    # abliterated ではない（汎用版は abliterated の RVN 版 IQ3_S）。数学などで汎用版と答えを比べる用。
    # memory_default は汎用版 strata と同じ 50 / 54 GiB。2026-10-17 の実測で、エキスパートを全部 RAM に写す通常のモード
    # （resident 版より 8〜20% 速い）に変えた。落とせない分は約 42.3 GiB（anon 41.45 + shmem 0.49 + kernel）で 50 GiB に収まる
    # （memory.peak 50.00 GiB = MemoryHigh。resident 版のときは約 30.2 GiB）ので、値は変えていない。根拠は tools/strata/README.md。
    {
        "key": "strata-official",
        "name": "Strata 公式版（IQ3_XXS）",
        "port": 8084,
        "unit": "strata-official",
        "memory_default": {"high_gib": 50, "max_gib": 54},
    },
    # 公式版の軽い量子化 IQ3_S（同じ公式モデル・同じ配布元。Q8_0 との KL が 0.202 → 0.144）。2026-10-16 に、AI モデル評価で Strata の
    # 思考が終わらない症状に量子化の側から当たるために足した。エキスパート 46.84 GiB は RVN と同じ resident 版で動かすので、
    # memory_default は汎用版と同じ 50 / 54 GiB（2026-10-17 の実測で、落とせない分 約 38.2 GiB に余裕があり、このまま。根拠は tools/strata/README.md）。
    {
        "key": "strata-official-iq3s",
        "name": "Strata 公式版 IQ3_S（軽い量子化）",
        "port": 8085,
        "unit": "strata-official-iq3s",
        "memory_default": {"high_gib": 50, "max_gib": 54},
    },
    # Swift 1.5（UkisAI が Qwen3.8-Flash-Next を思考が短く終わるように post-training した派生、IQ3_XXS。非 abliterated）。
    # 同じ IQ3_XXS の割り当てなので memory_default は公式版と同じ 50 / 54 GiB。2026-10-18 から公式版と同じ通常のモード（落とせない分 約 42.3 GiB。
    # resident 版だった 10-16〜17 は約 30.7 GiB）で、50 GiB に収まる。根拠は tools/strata/README.md の「速さの設定と文脈長 262144」。
    {
        "key": "strata-swift",
        "name": "Strata Swift 1.5（IQ3_XXS）",
        "port": 8086,
        "unit": "strata-swift",
        "memory_default": {"high_gib": 50, "max_gib": 54},
    },
    {
        "key": "hunyuan3d",
        "name": "Hunyuan3D",
        "port": 7860,
        "unit": "hunyuan3d",
        "options": {
            "low_vram": {
                "env_file": "~/.config/hunyuan3d/hunyuan3d.env",
                "var": "HY3D_LOW_VRAM",
                "default": True,
            }
        },
    },
    {
        "key": "pixal3d",
        "name": "Pixal3D",
        "port": 7861,
        "unit": "pixal3d",
        "options": {
            "low_vram": {
                "env_file": "~/.config/pixal3d/pixal3d.env",
                "var": "PIXAL3D_LOW_VRAM",
                "default": True,
            }
        },
    },
    # 低 VRAM のスイッチは持たない（options 無し）。GPU の排他グループには入る（GPU_EXCLUSIVE_KEYS）。
    {"key": "comfyui", "name": "ComfyUI", "port": 8188, "unit": "comfyui"},
    # 音楽（MusicGen Medium）と効果音（AudioGen Medium）の生成。元の音の続き・メロディ指定（MusicGen Melody）もある。
    # 3 つのモデルを起動時に VRAM へ載せる Gradio 画面（tools/audiocraft）。2026-11-23 の memory.peak は 4.99 GiB で、上限はそのまま。
    # 低 VRAM のスイッチは持たない（options 無し）。メモリ上限の既定値 16 / 20 GiB は実測（2026-10-27、cgroup の memory.peak 6.06 GiB。
    # 起動と、作曲 2 回・効果音 1 回の生成を通した値）。根拠は tools/audiocraft/README.md。GPU の排他グループに入り、
    # 起動前に Ollama のモデルを降ろす（GPU_EXCLUSIVE_KEYS・OLLAMA_UNLOAD_BEFORE_START_KEYS）。
    {
        "key": "audiocraft",
        "name": "音楽・効果音生成（AudioCraft Medium）",
        "port": 7862,
        "unit": "audiocraft",
        "memory_default": {"high_gib": 16, "max_gib": 20},
    },
    # 声の特徴を文章で指定する読み上げ（Qwen3-TTS VoiceDesign 1.7B）。上流の Gradio 画面 qwen-tts-demo をそのまま使う（tools/qwen3-tts）。
    # 2026-11-21 に足した。低 VRAM のスイッチは持たない。GPU の排他グループに入り、起動前に Ollama のモデルを降ろす
    # （Qwen 27B の 22 GiB と同居できない）。メモリ上限の既定値 6 / 8 GiB は実測（2026-11-21、cgroup の memory.peak 1.61 GiB）の約 4〜5 倍。
    # 根拠は tools/qwen3-tts/README.md。
    {
        "key": "qwen3-tts",
        "name": "音声合成（Qwen3-TTS VoiceDesign）",
        "port": 7863,
        "unit": "qwen3-tts",
        "memory_default": {"high_gib": 6, "max_gib": 8},
    },
    # 文章から曲・効果音を作る Stable Audio 3.0 Medium（自前の Gradio 画面と REST、tools/stable-audio）。2026-11-23 に足した。
    # 低 VRAM のスイッチは持たない。GPU の排他グループに入り、起動前に Ollama のモデルを降ろす（Qwen 27B の 22 GiB と同居できない）。
    # メモリ上限の既定値 16 / 20 GiB は実測（2026-11-23、cgroup の memory.peak 9.70 GiB。読み込み中が最大）の約 1.6 倍。根拠は tools/stable-audio/README.md。
    {
        "key": "stable-audio",
        "name": "音楽・効果音生成（Stable Audio 3.0 Medium）",
        "port": 7864,
        "unit": "stable-audio",
        "memory_default": {"high_gib": 16, "max_gib": 20},
    },
]
SERVICES_BY_KEY = {service["key"]: service for service in SERVICES}

# GPU（VRAM）を排他的に使うサービス。どれかを起動するとき、他が動いていれば先に止める
# （A30 は 1 枚しかなく、同時に載せると RAM 含めて枯れるため）。ComfyUI は Pixal3D / TRELLIS.2 の
# 3D 生成で GPU を大きく使うので、Hunyuan3D・Pixal3D と同じグループに入れる。Strata の汎用版は VRAM を約 20GB 以上、
# RAM を約 50GB 以上、Coder 版は VRAM を大きく・RAM を約 32GB 級、公式版は汎用版と同じ程度（2026-10-07 の実測で、
# RAM は memory.peak 50 GiB、VRAM は生成中 23,899 MiB）使うので、
# これらとは同時に動かせない（Strata の 5 つどうしも同時には動かせない。9 つのうち同時に動くのは 1 つだけ）。
# AudioCraft（音楽・効果音生成）は 3 つの 1.5B モデルを VRAM に載せる（2026-11-23 の実測で待機時 約 13.2 GiB、生成中 最大 約 16 GiB）ので、同じ扱いにする。
# Qwen3-TTS（音声合成、2026-11-21）も 1.7B を VRAM に載せ、Qwen 27B と同居できないので同じ扱いにする。
# Stable Audio 3.0 Medium（2026-11-23）も同じ扱いにする（11 のうち同時に動くのは 1 つ）。
# 公式版 IQ3_S（strata-official-iq3s）・Swift 1.5（strata-swift）は 2026-10-16 に足した（RAM・VRAM の使い方は汎用版・公式版と同じ程度）。
GPU_EXCLUSIVE_KEYS = ("hunyuan3d", "pixal3d", "comfyui", "strata", "strata-coder", "strata-official",
                      "strata-official-iq3s", "strata-swift", "audiocraft", "qwen3-tts", "stable-audio")

# 起動する前に、Ollama に載っているモデルを VRAM から降ろすサービス。Strata（汎用版・Coder 版・公式版・公式版 IQ3_S・Swift 1.5 とも）は
# VRAM を大きく使い、Qwen（実測 22 GiB）と同居できない。Hunyuan3D は、当て物 0001 を当てた環境では生成の直前に
# 自分で降ろす（当て物は公開版に含まない。knowledge の hunyuan3d.md）。
# Pixal3D・ComfyUI は、これまでの動作（画面の警告で利用者が降ろす）を変えないため対象にしない。
# AudioCraft は新しいサービスなので、Strata と同じく起動前に降ろす（Qwen の 22 GiB と同居できない）。Qwen3-TTS・Stable Audio も同じ。
OLLAMA_UNLOAD_BEFORE_START_KEYS = ("strata", "strata-coder", "strata-official", "strata-official-iq3s", "strata-swift",
                                   "audiocraft", "qwen3-tts", "stable-audio")

# POST /api/service/<key>/<action> で許可する action と、それぞれの操作の性質。
ALLOWED_ACTIONS = ("start", "stop")
ACTION_LABELS = {"start": "起動", "stop": "停止"}

SYSTEMCTL_SHOW_TIMEOUT_SEC = 5.0
SYSTEMCTL_START_TIMEOUT_SEC = 30.0  # --no-block で待たずに返る（build_control_command）
SYSTEMCTL_STOP_TIMEOUT_SEC = 90.0  # Hunyuan3D は終了に時間がかかることがある
ACTION_TIMEOUTS = {"start": SYSTEMCTL_START_TIMEOUT_SEC, "stop": SYSTEMCTL_STOP_TIMEOUT_SEC}

SERVICE_ACTION_PATH_PREFIX = "/api/service/"

# POST /api/option/<key>/<option>/<value> の設定関連。
# URL 上は "low-vram"（ハイフン区切り）だが、SERVICES の options 辞書のキーは Python の識別子として
# "low_vram"（アンダースコア）を使っている。両者の対応をここで持つ。
OPTION_PATH_PREFIX = "/api/option/"
OPTION_URL_TO_KEY = {"low-vram": "low_vram"}
OPTION_VALUES = {"on": True, "off": False}

# POST /api/memory/<key>/<high_gib>/<max_gib> の設定関連。
MEMORY_ACTION_PATH_PREFIX = "/api/memory/"

# POST /api/service/<key>/start?wait=<秒> の設定関連。wait を付けると、ポートが応答する（state が running になる）まで
# 待ってから返す。Strata は API が応答するまで最大 15 分かかる作りなので、上限は 30 分にする。
WAIT_MAX_SEC = 1800
WAIT_POLL_SEC = 2.0
# systemctl start --no-block の直後は、まだジョブが走る前で ActiveState が inactive のことがある。
# 起動してからこの秒数が過ぎても stopped なら、起動に失敗した（プロセスが落ちた）とみなして待つのをやめる。
START_FAILURE_GRACE_SEC = 10.0

# GET /api/ollama と POST /api/ollama/<model>/<load|unload> の設定関連。Ollama（11434）は LAN に公開していないので、
# 他の PC からのモデルの読み込み・解放はここを通す。<model> は Ollama に入っているモデル名だけを受け付ける（/api/tags と照合）。
OLLAMA_PATH = "/api/ollama"
OLLAMA_PATH_PREFIX = "/api/ollama/"
OLLAMA_ACTIONS = ("load", "unload")
# keep_alive は Ollama の書式（秒の整数か、末尾に s/m/h を付けた長さ。-1 は無期限）。0 は載せてすぐ降ろすことになるので受けない。
KEEP_ALIVE_PATTERN = re.compile(r"-1|[1-9][0-9]{0,5}[smh]?")

# GET /api（API の一覧）。
API_CATALOG_PATHS = ("/api", "/api/")

# GET /api の comfyui に載せる、ComfyUI（8188）の HTTP API と、他の PC から使う API 形式のワークフロー。
# ワークフローの実体は tools/comfyui/vendor/ComfyUI/user/default/workflows/ にあり、tools/comfyui/scripts/adapt_vfx_workflows.py が書く。
# inputs は「何を変えるか → ノード id と入力名」（POST /prompt の prompt[<id>]["inputs"][<入力名>] に入れる）。
COMFYUI_API_WORKFLOWS = (
    {
        "name": "VFX/WanAlpha_T2V_api.json",
        "description": "Wan 2.1 Alpha T2V 14B。透明度つきの動画（RGBA の PNG の連番と、RGBA のアニメ WebP）。LightX2V で 4 ステップ・cfg 1.0",
        "inputs": {
            "prompt": "6.text",
            "negative_prompt": "7.text",
            "seed": "3.seed",
            "steps": "3.steps",
            "cfg": "3.cfg",
            "width": "40.width",
            "height": "40.height",
            "frames": "40.length",
            "png_filename_prefix": "93.filename_prefix",
            "webp_filename_prefix": "80.filename_prefix",
            "webp_fps": "80.fps",
        },
    },
    {
        "name": "VFX/LayerDiffuse_SDXL_RGBA_api.json",
        "description": "LayerDiffuse（SDXL base 1.0）。透明な 1 枚絵（RGBA の PNG）。幅と高さは 64 の倍数",
        "inputs": {
            "prompt": "6.text",
            "negative_prompt": "7.text",
            "seed": "3.seed",
            "steps": "3.steps",
            "cfg": "3.cfg",
            "width": "5.width",
            "height": "5.height",
            "filename_prefix": "27.filename_prefix",
        },
    },
)

# 起動・停止の要求に必須のヘッダ。入口ページ自身の JavaScript だけが付ける。
# 単純な POST（ヘッダ無し）は他サイトからでもブラウザが送れてしまうため、独自ヘッダを必須にして
# クロスオリジンの事前確認（preflight）を強制する。こちらは preflight に応答しないので、
# 他サイトの JavaScript からは操作できない。curl から叩くときは -H で付ける。
REQUIRED_ACTION_HEADER = "X-Portal-Request"
REQUIRED_ACTION_HEADER_VALUE = "1"


def port_is_open(port: int, host: str = "127.0.0.1", timeout: float = PORT_CHECK_TIMEOUT_SEC) -> bool:
    """指定ポートが待受中かどうかを実際に接続して確かめる。失敗はすべて False。"""
    try:
        connection = create_connection((host, port), timeout=timeout)
    except OSError:
        return False
    connection.close()
    return True


def parse_nvidia_smi(output_text: str) -> dict[str, int] | None:
    """`nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader,nounits` の出力を解釈する。

    例: "4, 24576" -> {"used_mib": 4, "total_mib": 24576}
    壊れた入力・空文字では例外を投げず None を返す。
    """
    if not output_text or not output_text.strip():
        return None
    first_line = output_text.strip().splitlines()[0]
    fields = [field.strip() for field in first_line.split(",")]
    if len(fields) < 2:
        return None
    try:
        used_mib = int(fields[0])
        total_mib = int(fields[1])
    except ValueError:
        return None
    return {"used_mib": used_mib, "total_mib": total_mib}


def read_nvidia_smi(timeout: float = NVIDIA_SMI_TIMEOUT_SEC) -> dict[str, int] | None:
    """nvidia-smi を短いタイムアウトで呼び出す。コマンドが無い・失敗した場合は None。"""
    try:
        process = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if process.returncode != 0:
        return None
    return parse_nvidia_smi(process.stdout)


def parse_ollama_ps(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Ollama `/api/ps` の JSON を `[{"name":..., "size_vram_mib":...}, ...]` へ変換する。

    `payload` の形が想定と違っても例外を投げず、読み取れた分だけ返す。
    """
    models: list[dict[str, Any]] = []
    entries = payload.get("models") if isinstance(payload, dict) else None
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        model_name = entry.get("name") or entry.get("model") or "?"
        size_vram_bytes = entry.get("size_vram") or 0
        try:
            size_vram_mib = round(int(size_vram_bytes) / MIB_BYTES)
        except (TypeError, ValueError):
            size_vram_mib = 0
        models.append({"name": model_name, "size_vram_mib": size_vram_mib})
    return models


def read_ollama_ps(timeout: float = OLLAMA_TIMEOUT_SEC) -> list[dict[str, Any]] | None:
    """Ollama の `/api/ps` を短いタイムアウトで呼び出す。到達できなければ None。"""
    try:
        with urllib.request.urlopen(OLLAMA_STATUS_URL, timeout=timeout) as response:
            response_body = response.read()
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None
    try:
        payload = json.loads(response_body)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return parse_ollama_ps(payload)


def ollama_models_to_unload(models: list[dict[str, Any]] | None) -> list[str]:
    """`read_ollama_ps` の戻り値から、降ろすモデル名を載っている順・重複なしで返す（純粋関数）。

    None（Ollama に到達できない）と、名前が分からない項目（parse_ollama_ps が "?" にしたもの）は対象にしない。
    """
    model_names: list[str] = []
    for model in models or []:
        model_name = model.get("name") if isinstance(model, dict) else None
        if not model_name or model_name == "?" or model_name in model_names:
            continue
        model_names.append(model_name)
    return model_names


def build_ollama_unload_body(model_name: str) -> bytes:
    """`POST /api/generate` でモデルを VRAM から降ろす本文を作る（純粋関数）。

    prompt を付けず keep_alive=0 だけを送ると、Ollama は生成せずにそのモデルを降ろす。
    """
    return json.dumps({"model": model_name, "keep_alive": 0}).encode("utf-8")


def request_ollama_unload(model_name: str, timeout: float = OLLAMA_UNLOAD_TIMEOUT_SEC) -> bool:
    """Ollama へモデル 1 つを降ろす要求を送る。受け付けられれば True、失敗はすべて False（例外は投げない）。

    要求が受け付けられたことだけを見る。降ろし終えて VRAM が空いたことまでは確かめない。
    """
    request = urllib.request.Request(
        OLLAMA_GENERATE_URL,
        data=build_ollama_unload_body(model_name),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response.read()
    except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError, ValueError):
        return False
    return True


def unload_ollama_models() -> dict[str, Any]:
    """Ollama に載っているモデルをすべて VRAM から降ろす。例外は外に投げない。

    戻り値は {"unloaded": [降ろせたモデル名], "failed": [降ろせなかったモデル名], "checked": bool}。
    checked が False のときは /api/ps に到達できず、載っているモデルを確認できなかった。
    """
    models = read_ollama_ps()
    if models is None:
        return {"unloaded": [], "failed": [], "checked": False}
    unloaded: list[str] = []
    failed: list[str] = []
    for model_name in ollama_models_to_unload(models):
        if request_ollama_unload(model_name):
            unloaded.append(model_name)
        else:
            failed.append(model_name)
    return {"unloaded": unloaded, "failed": failed, "checked": True}


def describe_ollama_unload(result: dict[str, Any]) -> str:
    """`unload_ollama_models` の結果を、起動の応答の message に足す日本語の文にする（純粋関数）。

    降ろしたものも注意すべきこともなければ空文字。失敗しても起動は続けるので、
    ここでは「VRAM が足りないと起動に失敗するおそれがある」という注意だけを返す。
    """
    sentences: list[str] = []
    if result["unloaded"]:
        sentences.append(f"Ollama のモデル（{'、'.join(result['unloaded'])}）を VRAM から降ろしました。")
    if not result["checked"]:
        sentences.append(
            "注意: Ollama に接続できず、載っているモデルを確認できませんでした。"
            "VRAM が足りないと起動に失敗するおそれがあります（`ollama ps` で確認してください）。"
        )
    if result["failed"]:
        sentences.append(
            f"注意: Ollama のモデル（{'、'.join(result['failed'])}）を降ろせませんでした。"
            "VRAM が足りないと起動に失敗するおそれがあります（`ollama stop <モデル名>` で手動で降ろせます）。"
        )
    return "".join(sentences)


def parse_ollama_tags(payload: Any) -> list[str]:
    """Ollama `/api/tags`（入っているモデルの一覧）の JSON からモデル名を順に・重複なしで返す（純粋関数）。

    形が想定と違っても例外を投げず、読み取れた分だけ返す。
    """
    model_names: list[str] = []
    entries = payload.get("models") if isinstance(payload, dict) else None
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        model_name = entry.get("name") or entry.get("model")
        if isinstance(model_name, str) and model_name and model_name not in model_names:
            model_names.append(model_name)
    return model_names


def read_ollama_tags(timeout: float = OLLAMA_TIMEOUT_SEC) -> list[str] | None:
    """Ollama の `/api/tags` を短いタイムアウトで呼び出す。到達できなければ None。"""
    try:
        with urllib.request.urlopen(OLLAMA_TAGS_URL, timeout=timeout) as response:
            response_body = response.read()
    except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError, ValueError):
        return None
    try:
        payload = json.loads(response_body)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return parse_ollama_tags(payload)


def parse_ollama_action_path(path: str) -> tuple[str, str] | None:
    """`/api/ollama/<model>/<action>` を解釈する（純粋関数）。形が違えば None。

    モデル名は `hf.co/owner/name:tag` のように `/` を含むことがあるので、最後の `/` で action と分ける。
    `%3A` などのパーセント符号は戻す（curl で `qwen3.8:27b` をそのまま書いても、符号化して送っても同じに扱う）。
    """
    if not path.startswith(OLLAMA_PATH_PREFIX):
        return None
    remainder = path[len(OLLAMA_PATH_PREFIX):]
    model_part, separator, action = remainder.rpartition("/")
    if not separator or not model_part or not action:
        return None
    model_name = urllib.parse.unquote(model_part)
    if not model_name or any(character.isspace() for character in model_name):
        return None
    return model_name, action


def parse_keep_alive(raw_value: str | None) -> tuple[int | str | None, str | None]:
    """クエリの `keep_alive` を Ollama に渡す値へ直す（純粋関数）。戻り値は (値, エラー文)。

    無指定は (None, None)（Ollama の既定 5 分）。数字だけなら秒の整数、末尾に s/m/h があれば文字列のまま渡す。
    """
    if raw_value is None or raw_value == "":
        return None, None
    if not KEEP_ALIVE_PATTERN.fullmatch(raw_value):
        return None, (
            "keep_alive は秒の整数か、末尾に s / m / h を付けた長さ（例 300・30m・2h）、"
            "無期限は -1 です（0 は受け付けません）。"
        )
    if raw_value.lstrip("-").isdigit():
        return int(raw_value), None
    return raw_value, None


def build_ollama_load_body(model_name: str, keep_alive: int | str | None) -> bytes:
    """`POST /api/generate` でモデルを VRAM に載せる本文を作る（純粋関数）。

    prompt を付けずに送ると、Ollama は生成せずにモデルを載せるだけで返る。keep_alive が None なら付けない（Ollama の既定）。
    """
    body: dict[str, Any] = {"model": model_name}
    if keep_alive is not None:
        body["keep_alive"] = keep_alive
    return json.dumps(body).encode("utf-8")


def request_ollama_load(
    model_name: str, keep_alive: int | str | None, timeout: float = OLLAMA_LOAD_TIMEOUT_SEC
) -> tuple[bool, str]:
    """Ollama へモデル 1 つを載せる要求を送り、載せ終わるまで待つ。戻り値は (成功か, 失敗の理由)。例外は投げない。"""
    request = urllib.request.Request(
        OLLAMA_GENERATE_URL,
        data=build_ollama_load_body(model_name, keep_alive),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response.read()
    except urllib.error.HTTPError as error:
        try:
            detail = error.read().decode("utf-8", errors="replace").strip()
        except OSError:
            detail = ""
        return False, f"Ollama が HTTP {error.code} を返しました。{detail}"
    except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError, ValueError) as error:
        return False, f"Ollama への要求に失敗しました: {error}"
    return True, ""


def find_loaded_model(models: list[dict[str, Any]] | None, model_name: str) -> dict[str, Any] | None:
    """`read_ollama_ps` の戻り値から `model_name` の項目を探す（純粋関数）。

    `qwen3.8` と書かれたら Ollama と同じく `:latest` を補って照合する。
    """
    candidates = {model_name}
    if ":" not in model_name.rsplit("/", 1)[-1]:
        candidates.add(f"{model_name}:latest")
    for model in models or []:
        if isinstance(model, dict) and model.get("name") in candidates:
            return model
    return None


def resolve_installed_model(model_name: str, installed: list[str]) -> str | None:
    """利用者が書いたモデル名を、Ollama に入っている正式な名前に直す（純粋関数）。入っていなければ None。

    タグを省いた `qwen3.8` は Ollama と同じく `qwen3.8:latest` とみなす。
    """
    if model_name in installed:
        return model_name
    if ":" not in model_name.rsplit("/", 1)[-1] and f"{model_name}:latest" in installed:
        return f"{model_name}:latest"
    return None


def parse_wait_seconds(raw_value: str | None) -> tuple[int | None, str | None]:
    """クエリの `wait`（起動完了まで待つ秒数）を解釈する（純粋関数）。戻り値は (秒, エラー文)。無指定は (None, None)。"""
    if raw_value is None or raw_value == "":
        return None, None
    if not raw_value.isdigit() or not 0 <= int(raw_value) <= WAIT_MAX_SEC:
        return None, f"wait は 0〜{WAIT_MAX_SEC} の整数（秒）です。"
    return int(raw_value), None


def split_path_and_query(raw_path: str) -> tuple[str, dict[str, str]]:
    """`/api/service/x/start?wait=60` を `("/api/service/x/start", {"wait": "60"})` に分ける（純粋関数）。

    同じ名前が複数あれば最後の値を使う。
    """
    path, _, query = raw_path.partition("?")
    parameters: dict[str, str] = {}
    for name, value in urllib.parse.parse_qsl(query, keep_blank_values=True):
        parameters[name] = value
    return path, parameters


def parse_env_file(text: str) -> dict[str, str]:
    """env ファイルの中身から `KEY=VALUE` を読む（純粋関数）。

    `#` で始まる行と空行は無視する。壊れた行（`=` が無い行）も無視し、例外は投げない。
    """
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped_line = line.strip()
        if not stripped_line or stripped_line.startswith("#"):
            continue
        key, separator, value = stripped_line.partition("=")
        if not separator:
            continue
        values[key.strip()] = value.strip()
    return values


def render_env_file(text: str, var: str, value: str) -> str:
    """env ファイルの中身に `var=value` を反映した文字列を返す（純粋関数。書き込みはしない）。

    既存の行・コメントはそのまま保つ。`var` の行があれば値だけ差し替え、無ければ末尾に追記する。
    `text` が空文字（ファイルが存在しない場合も含む）なら、見出しコメント付きで新規に組み立てる。
    """
    if not text.strip():
        header = (
            f"# {var} の設定。systemd の EnvironmentFile として読み込まれるため、\n"
            "# 反映にはサービスの再起動が要る（稼働中に書き換えても効かない）。\n"
        )
        return f"{header}{var}={value}\n"

    lines = text.splitlines()
    replaced = False
    for index, line in enumerate(lines):
        stripped_line = line.strip()
        if not stripped_line or stripped_line.startswith("#"):
            continue
        existing_key, separator, _ = stripped_line.partition("=")
        if separator and existing_key.strip() == var:
            lines[index] = f"{var}={value}"
            replaced = True
            break
    if not replaced:
        lines.append(f"{var}={value}")
    return "\n".join(lines) + "\n"


def expand_env_file_path(env_file: str) -> Path:
    """設定に書かれた `~` 始まりのパスを実際のパスへ展開する。"""
    return Path(env_file).expanduser()


def read_option_value(option_config: dict[str, Any]) -> bool:
    """SERVICES の options 1 件の現在値を env ファイルから読む（真偽値。既定は on）。

    ファイルが存在しない・変数が書かれていない場合は option_config["default"] を返す。
    読み込み失敗はすべて「未設定」として扱い、例外は投げない。
    """
    path = expand_env_file_path(option_config["env_file"])
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        text = ""
    values = parse_env_file(text)
    raw_value = values.get(option_config["var"])
    if raw_value is None:
        return bool(option_config["default"])
    return raw_value.strip() == "1"


def write_option_value(option_config: dict[str, Any], enabled: bool) -> None:
    """SERVICES の options 1 件の値を env ファイルへ書く。

    同じディレクトリの一時ファイルへ書いてから `os.replace` で置き換える。書き込みの途中で
    プロセスが落ちても、既存の設定ファイルが半端な状態で壊れて残らないようにするため。
    親ディレクトリが無ければ作る。
    """
    path = expand_env_file_path(option_config["env_file"])
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        text = ""
    value_text = "1" if enabled else "0"
    rendered = render_env_file(text, option_config["var"], value_text)
    temporary_path = path.with_name(path.name + ".tmp")
    temporary_path.write_text(rendered, encoding="utf-8")
    os.replace(temporary_path, path)


def unit_state(active_state: str, load_state: str, port_open: bool) -> str:
    """systemd の ActiveState / LoadState とポート開放状況から画面表示用の 4 状態を決める（純粋関数）。

    - "running"       ポートが開いている（すぐ使える）
    - "starting"      ユニットは active/activating だが、ポートはまだ開いていない
                       （Hunyuan3D はモデルの読み込みに 50〜140 秒かかるため、この状態が長く続く）
    - "stopped"       ユニットは動いていない
    - "not_installed" ユニットが存在しない（load_state が "loaded" 以外。ユニットを作る前など）
    """
    if load_state != "loaded":
        return "not_installed"
    if port_open:
        return "running"
    if active_state in ("active", "activating"):
        return "starting"
    return "stopped"


def is_controllable(service: dict[str, Any]) -> bool:
    """このサービスを起動・停止できるか（純粋関数）。

    `service` は少なくとも "unit" と "state" を持つ辞書。unit が None、または
    state が "not_installed" のときは操作できない。
    """
    if service.get("unit") is None:
        return False
    if service.get("state") == "not_installed":
        return False
    return True


def services_to_stop_before_start(key: str, states: dict[str, str]) -> list[str]:
    """`key` を起動する前に止めるべき、同じ GPU 占有グループの他サービスの key 一覧を返す（純粋関数）。

    `states` は {"hunyuan3d": "running", "pixal3d": "stopped", ...} のように、キーが
    サービスの key、値が unit_state() の戻り値（4 状態のどれか）の辞書。
    `key` が GPU_EXCLUSIVE_KEYS に属さないときは常に空リスト。自分自身は対象に含めない。
    """
    if key not in GPU_EXCLUSIVE_KEYS:
        return []
    return [
        other_key
        for other_key in GPU_EXCLUSIVE_KEYS
        if other_key != key and states.get(other_key) in ("running", "starting")
    ]


def find_service_option(service: dict[str, Any], option_key: str) -> dict[str, Any] | None:
    """サービスが `option_key`（例 "low_vram"）という設定を持つならその定義を返す（純粋関数）。

    持たないサービス（Open WebUI・ComfyUI など）や、未知の option_key では None。
    """
    options = service.get("options")
    if not options:
        return None
    return options.get(option_key)


def read_unit_state(unit: str, timeout: float = SYSTEMCTL_SHOW_TIMEOUT_SEC) -> tuple[str, str]:
    """`systemctl --user show <unit> --property=ActiveState --property=LoadState` を実行する。

    出力は `Key=Value` 形式で 1 行ずつ受け取り、キー名で拾う。`--value` は使わない。実機で確認したところ、
    systemd は `--property` を指定した順序どおりには出力しない（コマンドラインでの並びに関係なく
    LoadState を先に出す）ため、行の位置ではなくキー名で照合する必要がある。
    失敗（コマンドが無い・タイムアウト・想定外の出力）した場合は例外を投げず ("unknown", "not-found") を返す。
    """
    try:
        process = subprocess.run(
            [
                "systemctl",
                "--user",
                "show",
                unit,
                "--property=ActiveState",
                "--property=LoadState",
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return ("unknown", "not-found")
    if process.returncode != 0:
        return ("unknown", "not-found")
    properties: dict[str, str] = {}
    for line in process.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            properties[key.strip()] = value.strip()
    active_state = properties.get("ActiveState")
    load_state = properties.get("LoadState")
    if not active_state or not load_state:
        return ("unknown", "not-found")
    return (active_state, load_state)


def build_control_command(unit: str, action: str) -> list[str]:
    """`systemctl --user <action> <unit>` のコマンド列を作る（純粋関数）。

    start は `--no-block` を付けて、ジョブの完了を待たずに返す。Strata のユニット（汎用版・Coder 版・公式版）は
    API が応答するまで（最大 15 分）`systemctl start` が戻らない作り（ExecStartPost で待つ）なので、待つと
    タイムアウトして、実際には起動できているのに失敗と表示してしまう。起動の進み具合は
    `/api/status` のポーリングで「起動中…」→「稼働中」と見える。ユニットが無い・systemctl が
    拒否した場合は --no-block でも即座にエラーで返る。ただし起動ジョブそのものの失敗
    （プロセスがすぐ落ちる等）はここでは分からず、状態が「停止中」に戻ることで分かる。
    """
    if action == "start":
        return ["systemctl", "--user", "start", "--no-block", unit]
    return ["systemctl", "--user", action, unit]


def control_unit(unit: str, action: str, timeout: float) -> subprocess.CompletedProcess[str]:
    """`systemctl --user <action> <unit>` を実行する。呼び出し元がタイムアウトを選ぶ。

    ここに渡す unit は必ず SERVICES 由来のもの。利用者が渡した文字列をそのまま渡してはならない。
    """
    return subprocess.run(
        build_control_command(unit, action),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def perform_service_action(service: dict[str, Any], action: str) -> dict[str, Any]:
    """SERVICES の 1 件に対して start/stop を実行し、結果を JSON 化できる辞書で返す。

    どんな失敗（タイムアウト・systemctl 自体の異常終了）でも例外を外に投げない。
    戻り値は常に {"ok": bool, "message": str, "state": str}。
    """
    unit = service["unit"]
    timeout = ACTION_TIMEOUTS[action]
    action_label = ACTION_LABELS[action]

    try:
        process = control_unit(unit, action, timeout)
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "message": f"{action_label}がタイムアウトしました（{timeout:.0f} 秒以内に終わりませんでした）。",
            "state": "unknown",
        }
    except (OSError, subprocess.SubprocessError) as error:
        return {"ok": False, "message": f"systemctl の実行に失敗しました: {error}", "state": "unknown"}

    port_open = port_is_open(service["port"])
    active_state, load_state = read_unit_state(unit)
    state = unit_state(active_state, load_state, port_open)

    if process.returncode == 0:
        if action == "start":
            message = (
                f"{service['name']} の起動を要求しました。準備できるまで数十秒〜数分かかる場合があります。"
            )
        else:
            message = f"{service['name']} の停止を要求しました。"
        return {"ok": True, "message": message, "state": state}

    error_text = (process.stderr or process.stdout or "").strip() or "詳細不明のエラーです。"
    return {
        "ok": False,
        "message": f"{service['name']} の{action_label}に失敗しました: {error_text}",
        "state": state,
    }


def current_service_state(service: dict[str, Any]) -> str:
    """このサービスの現在の 4 状態（running/starting/stopped/not_installed）を実測する。"""
    unit = service["unit"]
    port_open = port_is_open(service["port"])
    if unit:
        active_state, load_state = read_unit_state(unit)
    else:
        active_state, load_state = ("unknown", "not-found")
    return unit_state(active_state, load_state, port_open)


def wait_until_ready(
    service: dict[str, Any],
    wait_sec: float,
    state_reader: Any = None,
    clock: Any = time.monotonic,
    sleep: Any = time.sleep,
    poll_sec: float = WAIT_POLL_SEC,
    grace_sec: float = START_FAILURE_GRACE_SEC,
) -> dict[str, Any]:
    """起動を要求したサービスが running（ポートが応答する）になるまで待つ。例外は投げない。

    戻り値は {"ready": bool, "failed": bool, "state": str, "waited_sec": float}。
    - ready=True: 待ち時間のうちに running になった
    - failed=True: grace_sec を過ぎても stopped / not_installed のまま（起動に失敗してプロセスが落ちた）
    - どちらも False: 待ち時間が尽きた（まだ starting。Strata や Hunyuan3D は読み込みが長い）
    state_reader・clock・sleep は試験で差し替えるための引数。
    """
    read_state = state_reader or current_service_state
    started_at = clock()
    deadline = started_at + wait_sec
    while True:
        state = read_state(service)
        elapsed = clock() - started_at
        if state == "running":
            return {"ready": True, "failed": False, "state": state, "waited_sec": round(elapsed, 1)}
        if state in ("stopped", "not_installed") and elapsed >= grace_sec:
            return {"ready": False, "failed": True, "state": state, "waited_sec": round(elapsed, 1)}
        remaining = deadline - clock()
        if remaining <= 0:
            return {"ready": False, "failed": False, "state": state, "waited_sec": round(elapsed, 1)}
        sleep(min(poll_sec, remaining))


def describe_wait_result(service: dict[str, Any], wait_result: dict[str, Any]) -> str:
    """`wait_until_ready` の結果を、起動の応答の message に足す日本語の文にする（純粋関数）。"""
    waited = wait_result["waited_sec"]
    if wait_result["ready"]:
        return f"{service['name']} は使える状態になりました（{waited:.0f} 秒待ちました）。"
    if wait_result["failed"]:
        return (
            f"{service['name']} は起動に失敗したようです（{waited:.0f} 秒後に停止中）。"
            f"サーバーで `journalctl --user -u {service['unit']} -n 50` を確認してください。"
        )
    return (
        f"{waited:.0f} 秒待ちましたが、まだ準備中です（state={wait_result['state']}）。"
        "GET /api/status で state が running になるのを待ってください。"
    )


def host_name_from_header(host_header: str | None, fallback: str = "<server-ip>") -> str:
    """HTTP の Host ヘッダ（`192.168.1.10:8080` など）からホスト名だけを取り出す（純粋関数）。"""
    if not host_header:
        return fallback
    try:
        host_name = urllib.parse.urlsplit(f"//{host_header}").hostname
    except ValueError:
        return fallback
    if not host_name:
        return fallback
    return f"[{host_name}]" if ":" in host_name else host_name


def build_comfyui_catalog(host_name: str) -> dict[str, Any]:
    """`GET /api` の comfyui（純粋関数）。ComfyUI 自身の HTTP API の使い方と、保存してある API 形式のワークフロー。"""
    port = next(service["port"] for service in SERVICES if service["key"] == "comfyui")
    base_url = f"http://{host_name}:{port}"
    return {
        "base_url": base_url,
        "start_first": "POST /api/service/comfyui/start?wait=600（入口ページ。GPU を使うほかのサービスは止まる）",
        "endpoints": [
            {"method": "GET", "path": "/api/userdata/workflows%2F<名前を URL 符号化>", "description": "保存したワークフローを読む（例 workflows%2FVFX%2FWanAlpha_T2V_api.json）"},
            {"method": "GET", "path": "/api/userdata?dir=workflows/VFX", "description": "フォルダの中の一覧"},
            {"method": "POST", "path": "/prompt", "description": '本文 {"prompt": <API 形式>, "client_id": <任意の文字列>}。応答の prompt_id で後を追う'},
            {"method": "GET", "path": "/history/<prompt_id>", "description": "終わると outputs に出力のファイル名（filename・subfolder・type）が入る"},
            {"method": "GET", "path": "/view?filename=<名前>&subfolder=<サブフォルダ>&type=output", "description": "出力のファイルを取る"},
            {"method": "GET", "path": "/queue", "description": "実行中・待ちの一覧"},
            {"method": "POST", "path": "/interrupt", "description": "実行中のものを止める"},
        ],
        "workflows": [
            {**workflow, "url": f"{base_url}/api/userdata/{urllib.parse.quote('workflows/' + workflow['name'], safe='')}"}
            for workflow in COMFYUI_API_WORKFLOWS
        ],
    }


def build_api_catalog(host_name: str, portal_port: int) -> dict[str, Any]:
    """`GET /api` が返す、この API の一覧（純粋関数）。他の PC から仕様を調べるためのもの。"""
    base_url = f"http://{host_name}:{portal_port}"
    services = []
    for service in SERVICES:
        services.append(
            {
                "key": service["key"],
                "name": service["name"],
                "port": service["port"],
                "url": f"http://{host_name}:{service['port']}/",
                "controllable": service["unit"] is not None,
                "gpu_exclusive": service["key"] in GPU_EXCLUSIVE_KEYS,
                "unloads_ollama_before_start": service["key"] in OLLAMA_UNLOAD_BEFORE_START_KEYS,
                "options": [
                    url_name
                    for url_name, option_key in OPTION_URL_TO_KEY.items()
                    if find_service_option(service, option_key) is not None
                ],
            }
        )
    header = f"{REQUIRED_ACTION_HEADER}: {REQUIRED_ACTION_HEADER_VALUE}"
    return {
        "name": "localai portal API",
        "base_url": base_url,
        "required_header": {
            "name": REQUIRED_ACTION_HEADER,
            "value": REQUIRED_ACTION_HEADER_VALUE,
            "applies_to": "POST のすべて（無いと 403）",
        },
        "endpoints": [
            {"method": "GET", "path": "/api", "description": "この一覧"},
            {"method": "GET", "path": "/api/status", "description": "GPU・RAM・Ollama・各サービスの状態"},
            {
                "method": "POST",
                "path": "/api/service/<key>/start",
                "description": (
                    "サービスを起動する。GPU を排他的に使うサービスは、同じグループで動いているものを止めてから起動する。"
                    f"?wait=<秒>（0〜{WAIT_MAX_SEC}）を付けると、ポートが応答するまで待ってから返す"
                    "（応答に ready・waited_sec が入る。HTTP クライアントのタイムアウトを wait より長くすること）"
                ),
            },
            {"method": "POST", "path": "/api/service/<key>/stop", "description": "サービスを止める（止まるまで待ってから返る）"},
            {
                "method": "POST",
                "path": "/api/option/<key>/low-vram/<on|off>",
                "description": "低 VRAM モードの切り替え（options に low-vram があるサービスだけ。停止中のみ）",
            },
            {
                "method": "POST",
                "path": "/api/memory/<key>/<high_gib>/<max_gib>",
                "description": "systemd のメモリ上限（MemoryHigh / MemoryMax、GiB の整数）",
            },
            {"method": "GET", "path": OLLAMA_PATH, "description": "Ollama に入っているモデルと、VRAM に載っているモデル"},
            {
                "method": "POST",
                "path": "/api/ollama/<model>/load",
                "description": (
                    "Ollama のモデルを VRAM に載せる（載せ終わるまで待つ。初回は約 60 秒）。"
                    "GPU を排他的に使うサービスが動いていれば先に止める。"
                    "?keep_alive=<秒 | 30m | 2h | -1> で載せておく長さ（無指定は Ollama の既定 5 分）"
                ),
            },
            {"method": "POST", "path": "/api/ollama/<model>/unload", "description": "Ollama のモデルを VRAM から降ろす"},
        ],
        "services": services,
        "comfyui": build_comfyui_catalog(host_name),
        "gpu_exclusive_keys": list(GPU_EXCLUSIVE_KEYS),
        "ollama": {
            "note": (
                "Ollama（11434）は LAN に公開していない。モデルの読み込み・解放はこの API を、"
                f"チャットは Open WebUI（http://{host_name}:8081/）か Strata の OpenAI 互換 API を使う"
            ),
        },
        "examples": [
            f"curl {base_url}/api/status",
            f'curl -X POST -H "{header}" "{base_url}/api/service/strata/start?wait=900"',
            f'curl -X POST -H "{header}" {base_url}/api/service/strata/stop',
            f"curl {base_url}{OLLAMA_PATH}",
            f'curl -X POST -H "{header}" "{base_url}/api/ollama/qwen3.8:27b/load?keep_alive=30m"',
            f'curl -X POST -H "{header}" {base_url}/api/ollama/qwen3.8:27b/unload',
        ],
    }


def resolve_guide_path(path: str, guides_dir: Path = GUIDES_DIR) -> Path | None:
    """`/guides/<名前>.html` を guides/ の直下のファイルに直す。形が違う・無いときは None。

    名前は英小文字・数字・ハイフンだけを許し（`..`・`/`・`%` を含むものは形で落ちる）、さらに
    resolve() したパスの親が guides/ そのものであることを確かめる（シンボリックリンクで外を指していても読まない）。
    """
    if not path.startswith(GUIDE_PATH_PREFIX):
        return None
    file_name = path[len(GUIDE_PATH_PREFIX):].split("?", 1)[0]
    if not GUIDE_NAME_PATTERN.fullmatch(file_name):
        return None
    try:
        base = guides_dir.resolve()
        candidate = (base / file_name).resolve()
    except OSError:
        return None
    if candidate.parent != base or not candidate.is_file():
        return None
    return candidate


def parse_service_action_path(path: str) -> tuple[str, str] | None:
    """`/api/service/<key>/<action>` を解釈する（純粋関数）。形が違えば None。"""
    if not path.startswith(SERVICE_ACTION_PATH_PREFIX):
        return None
    remainder = path[len(SERVICE_ACTION_PATH_PREFIX):]
    segments = remainder.split("/")
    if len(segments) != 2:
        return None
    key, action = segments
    if not key or not action:
        return None
    return key, action


def parse_option_path(path: str) -> tuple[str, str, str] | None:
    """`/api/option/<key>/<option>/<value>` を解釈する（純粋関数）。形が違えば None。

    ここではパスの形だけを見る。key・option・value がそれぞれ実在するかどうかの判定は
    呼び出し元（ハンドラ側）の役目。
    """
    if not path.startswith(OPTION_PATH_PREFIX):
        return None
    remainder = path[len(OPTION_PATH_PREFIX):]
    segments = remainder.split("/")
    if len(segments) != 3:
        return None
    key, option_name, value = segments
    if not key or not option_name or not value:
        return None
    return key, option_name, value


def parse_meminfo_total_gib(text: str) -> float | None:
    """`/proc/meminfo` の本文から MemTotal を GiB で返す（純粋関数）。

    MemTotal は kB（1024 バイト単位）で書かれている。行が無い・壊れている場合は None。
    """
    for line in text.splitlines():
        stripped_line = line.strip()
        if not stripped_line.startswith("MemTotal"):
            continue
        fields = stripped_line.split()
        if len(fields) < 2:
            return None
        try:
            total_kib = int(fields[1])
        except ValueError:
            return None
        return total_kib / (1024 * 1024)
    return None


def memory_bounds(total_gib: float) -> dict[str, Any]:
    """RAM 総量（GiB）から、メモリ上限の許容範囲・推奨範囲を組み立てる（純粋関数）。

    - allowed_max_gib: これを超える MemoryMax は画面からも設定させない（systemd-oomd が
      デスクトップごと止めるおそれがあるため）。RAM 総量 - MEMORY_RESERVE_GIB を切り捨てた値。
    - recommended_max_gib: 超えても拒否はしないが警告を出す目安。RAM 総量 -
      MEMORY_RECOMMENDED_MARGIN_GIB を切り捨てた値。
    """
    return {
        "total_gib": total_gib,
        "allowed_max_gib": math.floor(total_gib - MEMORY_RESERVE_GIB),
        "recommended_max_gib": math.floor(total_gib - MEMORY_RECOMMENDED_MARGIN_GIB),
        "min_gib": MEMORY_MIN_GIB,
    }


def validate_memory_limits(high_gib: Any, max_gib: Any, bounds: dict[str, Any]) -> str | None:
    """MemoryHigh / MemoryMax の指定値が妥当かを確かめる（純粋関数）。

    問題が無ければ None、あれば日本語の理由を返す。規則: 整数であること、
    `min_gib <= high <= max <= allowed_max_gib`。

    理由の文には**どちらの値が大きすぎる（小さすぎる）のか**と、いま入っている値、
    許される範囲を必ず入れる。「MemoryHigh は MemoryMax 以下にしてください」のように
    数値の無い文だけを返すと、利用者がどちらをいくつに直せばよいか分からないため。
    """
    for label, value in (("MemoryHigh", high_gib), ("MemoryMax", max_gib)):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return f"{label} は整数で指定してください（いまの値: {value!r}）。"
        if float(value) != int(value):
            return f"{label} は整数で指定してください（いまの値: {value}）。"

    high_value = int(high_gib)
    max_value = int(max_gib)
    min_gib = bounds["min_gib"]
    allowed_max_gib = bounds["allowed_max_gib"]

    if high_value < min_gib:
        return (
            f"MemoryHigh が小さすぎます（いまの値: {high_value} GiB）。"
            f"{min_gib} GiB 以上にしてください。"
        )
    if high_value > max_value:
        return (
            f"MemoryHigh が MemoryMax より大きすぎます"
            f"（MemoryHigh {high_value} GiB > MemoryMax {max_value} GiB）。"
            f"MemoryHigh を {max_value} GiB 以下に下げるか、MemoryMax を {high_value} GiB 以上に上げてください。"
        )
    if max_value > allowed_max_gib:
        return (
            f"MemoryMax が大きすぎます（いまの値: {max_value} GiB）。"
            f"{allowed_max_gib} GiB 以下にしてください"
            f"（RAM 総量 {bounds['total_gib']:.1f} GiB から {MEMORY_RESERVE_GIB} GiB を残す上限です）。"
        )
    return None


def memory_warning(max_gib: Any, bounds: dict[str, Any]) -> str | None:
    """`max_gib` が推奨上限を超えているときに見せる警告文を返す（純粋関数）。拒否はしない。"""
    recommended_max_gib = bounds.get("recommended_max_gib")
    if recommended_max_gib is None:
        return None
    if max_gib > recommended_max_gib:
        return (
            f"MemoryMax（{int(max_gib)} GiB）が推奨上限（{recommended_max_gib} GiB）を超えています。"
            "使いすぎたときにデスクトップごと止まるおそれがあります。"
        )
    return None


def parse_memory_properties(stdout: str) -> dict[str, float | None]:
    """`systemctl show --property=MemoryHigh --property=MemoryMax --property=MemoryCurrent` の
    出力を `{"high_gib":..., "max_gib":..., "current_gib":...}`（GiB、float）へ変換する（純粋関数）。

    値はバイト数の整数、`infinity`（上限無し）、`[not set]`（停止中の MemoryCurrent）のいずれか。
    `infinity` と `[not set]` は None にする。行の順序には依存しない（read_unit_state と同じ理由）。
    """
    properties: dict[str, str] = {}
    for line in stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            properties[key.strip()] = value.strip()

    def to_gib(raw_value: str | None) -> float | None:
        if raw_value is None or raw_value in ("infinity", "[not set]"):
            return None
        try:
            return int(raw_value) / GIB_BYTES
        except ValueError:
            return None

    return {
        "high_gib": to_gib(properties.get("MemoryHigh")),
        "max_gib": to_gib(properties.get("MemoryMax")),
        "current_gib": to_gib(properties.get("MemoryCurrent")),
    }


def build_set_property_commands(unit: str, high_gib: Any, max_gib: Any) -> list[list[str]]:
    """`systemctl --user set-property` のコマンド列を 2 本組み立てる（純粋関数）。

    1 本目（`--runtime` 無し）は恒久設定（PC 再起動後も残る）、2 本目（`--runtime` 付き）は
    ランタイム設定（再起動で消えるが、恒久設定より優先度が高い）。両方に同じ値を書くことで、
    以前 `--runtime` で書かれた値が残っていても画面で決めた値が必ず効くようにする。
    どちらも稼働中のユニットへ再起動なしで即座に効く。
    """
    high_value = f"MemoryHigh={int(high_gib)}G"
    max_value = f"MemoryMax={int(max_gib)}G"
    return [
        ["systemctl", "--user", "set-property", unit, high_value, max_value],
        ["systemctl", "--user", "set-property", "--runtime", unit, high_value, max_value],
    ]


def parse_memory_path(path: str) -> tuple[str, str, str] | None:
    """`/api/memory/<key>/<high>/<max>` を解釈する（純粋関数）。形だけを見る。

    `key`・`high`・`max` が実在する値か、整数として解釈できるかの判定は呼び出し元（ハンドラ側）の役目。
    """
    if not path.startswith(MEMORY_ACTION_PATH_PREFIX):
        return None
    remainder = path[len(MEMORY_ACTION_PATH_PREFIX):]
    segments = remainder.split("/")
    if len(segments) != 3:
        return None
    key, high_raw, max_raw = segments
    if not key or not high_raw or not max_raw:
        return None
    return key, high_raw, max_raw


def build_memory_entry(service: dict[str, Any], memory_values: dict[str, Any] | None) -> dict[str, Any] | None:
    """services[] の "memory" を組み立てる（純粋関数）。unit を持たない（None）ならそのまま None。

    現在値に、画面の「既定に戻す」が使う既定値（`default_high_gib` / `default_max_gib`）を添える。
    SERVICES の `memory_default` があればそれ、無ければ全サービス共通の定数。
    """
    if memory_values is None:
        return None
    memory_default = service.get("memory_default") or {}
    entry = dict(memory_values)
    entry["default_high_gib"] = memory_default.get("high_gib", MEMORY_DEFAULT_HIGH_GIB)
    entry["default_max_gib"] = memory_default.get("max_gib", MEMORY_DEFAULT_MAX_GIB)
    return entry


def build_service_status(
    service: dict[str, Any],
    port_open: bool,
    active_state: str,
    load_state: str,
    option_values: dict[str, Any] | None = None,
    memory_values: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """SERVICES の 1 件と実測値から `/api/status` の services[] に載せる 1 件を組み立てる（純粋関数）。

    `option_values` はそのサービスが持つ設定の現在値（例 {"low_vram": True}）。設定ファイルを
    読むのは呼び出し元（collect_status）の役目で、この関数はそれをそのまま "options" として
    素通しするだけ。渡さなければ空の辞書になる（設定を持たないサービス向け）。
    `memory_values` は unit を持つサービスの現在のメモリ上限（parse_memory_properties の戻り値）。
    unit を持たないサービスでは None のまま渡す。
    """
    state = unit_state(active_state, load_state, port_open)
    controllable = is_controllable({"unit": service["unit"], "state": state})
    return {
        "key": service["key"],
        "name": service["name"],
        "port": service["port"],
        "up": port_open,
        "state": state,
        "controllable": controllable,
        "options": dict(option_values) if option_values else {},
        "memory": build_memory_entry(service, memory_values),
    }


def build_ram_status(total_gib: float | None) -> dict[str, Any]:
    """`/api/status` の `ram` フィールドを組み立てる（純粋関数）。

    `total_gib` が None（/proc/meminfo を読めなかった）なら available: false で残りは None。
    `default_high_gib` / `default_max_gib` は RAM 総量に関係なく常に定数を返す
    （画面の「既定に戻す」ボタンが使うため）。
    """
    if total_gib is None:
        return {
            "total_gib": None,
            "allowed_max_gib": None,
            "recommended_max_gib": None,
            "min_gib": MEMORY_MIN_GIB,
            "default_high_gib": MEMORY_DEFAULT_HIGH_GIB,
            "default_max_gib": MEMORY_DEFAULT_MAX_GIB,
            "available": False,
        }
    bounds = memory_bounds(total_gib)
    return {
        "total_gib": bounds["total_gib"],
        "allowed_max_gib": bounds["allowed_max_gib"],
        "recommended_max_gib": bounds["recommended_max_gib"],
        "min_gib": bounds["min_gib"],
        "default_high_gib": MEMORY_DEFAULT_HIGH_GIB,
        "default_max_gib": MEMORY_DEFAULT_MAX_GIB,
        "available": True,
    }


def build_status(
    gpu: dict[str, int] | None,
    ollama_models: list[dict[str, Any]] | None,
    services: list[dict[str, Any]],
    total_ram_gib: float | None = None,
) -> dict[str, Any]:
    """`/api/status` が返す JSON 形状を組み立てる（純粋関数。ネットワークにも GPU にも触らない）。

    `gpu` は `parse_nvidia_smi` の戻り値（取れなければ None）。
    `ollama_models` は `parse_ollama_ps` の戻り値（到達できなければ None）。
    `services` は `build_service_status` が返す辞書（"key","name","port","up","state","controllable",
    "options","memory"）のリスト。この関数はそれをそのまま素通しするだけで、内容の加工はしない。
    `total_ram_gib` は `parse_meminfo_total_gib` の戻り値（取れなければ None）。
    """
    if gpu is not None:
        gpu_status = {
            "used_mib": gpu["used_mib"],
            "total_mib": gpu["total_mib"],
            "available": True,
        }
    else:
        gpu_status = {"used_mib": None, "total_mib": None, "available": False}

    if ollama_models is not None:
        ollama_status = {"reachable": True, "models": ollama_models}
    else:
        ollama_status = {"reachable": False, "models": []}

    return {
        "gpu": gpu_status,
        "ollama": ollama_status,
        "services": list(services),
        "ram": build_ram_status(total_ram_gib),
    }


def read_meminfo_total_gib() -> float | None:
    """`/proc/meminfo` を読んで MemTotal を GiB で返す。読めなければ None。"""
    try:
        text = MEMINFO_PATH.read_text(encoding="utf-8")
    except OSError:
        return None
    return parse_meminfo_total_gib(text)


def read_memory_properties(unit: str, timeout: float = SYSTEMCTL_SHOW_TIMEOUT_SEC) -> dict[str, float | None]:
    """`systemctl --user show <unit> --property=MemoryHigh --property=MemoryMax
    --property=MemoryCurrent` を実行し、`parse_memory_properties` で解釈する。

    失敗（コマンドが無い・タイムアウト・異常終了）した場合は例外を投げず、すべて None にする。
    """
    try:
        process = subprocess.run(
            [
                "systemctl",
                "--user",
                "show",
                unit,
                "--property=MemoryHigh",
                "--property=MemoryMax",
                "--property=MemoryCurrent",
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return {"high_gib": None, "max_gib": None, "current_gib": None}
    if process.returncode != 0:
        return {"high_gib": None, "max_gib": None, "current_gib": None}
    return parse_memory_properties(process.stdout)


def set_unit_memory_limits(
    unit: str, high_gib: Any, max_gib: Any, timeout: float = SYSTEMCTL_SHOW_TIMEOUT_SEC
) -> list[dict[str, Any]]:
    """`build_set_property_commands` の 2 本を順に実行する。

    ここに渡す unit は必ず SERVICES 由来のもの。利用者が渡した文字列をそのまま渡してはならない。
    戻り値は 2 件のリスト（恒久設定 → `--runtime`）、各要素は `{"ok": bool, "stderr": str}`。
    どちらも例外を外に投げない。
    """
    results: list[dict[str, Any]] = []
    for command in build_set_property_commands(unit, high_gib, max_gib):
        try:
            process = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.SubprocessError) as error:
            results.append({"ok": False, "stderr": str(error)})
            continue
        if process.returncode == 0:
            results.append({"ok": True, "stderr": ""})
        else:
            results.append({"ok": False, "stderr": (process.stderr or process.stdout or "").strip()})
    return results


def collect_status() -> dict[str, Any]:
    """実際に nvidia-smi・Ollama・各ポート・各 systemd ユニット・各設定ファイル・/proc/meminfo へ
    問い合わせて `/api/status` の中身を作る。
    """
    gpu = read_nvidia_smi()
    ollama_models = read_ollama_ps()
    total_ram_gib = read_meminfo_total_gib()
    services = []
    for service in SERVICES:
        port_open = port_is_open(service["port"])
        unit = service["unit"]
        if unit:
            active_state, load_state = read_unit_state(unit)
            memory_values = read_memory_properties(unit)
        else:
            active_state, load_state = ("unknown", "not-found")
            memory_values = None
        option_values = {
            option_key: read_option_value(option_config)
            for option_key, option_config in service.get("options", {}).items()
        }
        services.append(
            build_service_status(
                service, port_open, active_state, load_state, option_values, memory_values
            )
        )
    return build_status(gpu, ollama_models, services, total_ram_gib)


class PortalRequestHandler(BaseHTTPRequestHandler):
    server_version = "PortalHTTP/1.0"

    def log_message(self, format_string: str, *args: Any) -> None:
        # /api/status は 5 秒ごとに来るのでログを出さない（うるさいため）。
        if self.path.startswith("/api/status"):
            return
        message = format_string % args
        sys.stderr.write(f"{self.address_string()} - - [{self.log_date_time_string()}] {message}\n")

    def do_GET(self) -> None:
        path, _ = split_path_and_query(self.path)
        if self.path == "/" or self.path == "/index.html":
            self._serve_index()
        elif self.path == "/api/status":
            self._serve_status()
        elif path in API_CATALOG_PATHS:
            self._serve_api_catalog()
        elif path == OLLAMA_PATH:
            self._serve_ollama_models()
        elif self.path.startswith(GUIDE_PATH_PREFIX):
            self._serve_guide()
        elif (
            self.path.startswith(SERVICE_ACTION_PATH_PREFIX)
            or self.path.startswith(OPTION_PATH_PREFIX)
            or self.path.startswith(MEMORY_ACTION_PATH_PREFIX)
            or self.path.startswith(OLLAMA_PATH_PREFIX)
        ):
            # 起動・停止・設定変更はどれも POST 専用。ブラウザのプリフェッチや履歴で誤って
            # 実行されないように、GET でこれらのパスを叩かれても何もせず 405 を返す。
            self._send_json(405, {"ok": False, "message": "GET では操作できません。POST を使ってください。"})
        else:
            self.send_error(404, "Not Found")

    def do_POST(self) -> None:
        path, query = split_path_and_query(self.path)
        if path.startswith(SERVICE_ACTION_PATH_PREFIX):
            parsed_path = parse_service_action_path(path)
            if parsed_path is None:
                self._send_json(404, {"ok": False, "message": "パスが不正です。"})
                return
            key, action = parsed_path
            self._handle_service_action(key, action, query)
            return
        if path.startswith(OLLAMA_PATH_PREFIX):
            parsed_ollama_path = parse_ollama_action_path(path)
            if parsed_ollama_path is None:
                self._send_json(404, {"ok": False, "message": "パスが不正です。"})
                return
            model_name, action = parsed_ollama_path
            self._handle_ollama_action(model_name, action, query)
            return
        if self.path.startswith(OPTION_PATH_PREFIX):
            parsed_option_path = parse_option_path(self.path)
            if parsed_option_path is None:
                self._send_json(404, {"ok": False, "message": "パスが不正です。"})
                return
            key, option_name, value = parsed_option_path
            self._handle_option_action(key, option_name, value)
            return
        if self.path.startswith(MEMORY_ACTION_PATH_PREFIX):
            parsed_memory_path = parse_memory_path(self.path)
            if parsed_memory_path is None:
                self._send_json(404, {"ok": False, "message": "パスが不正です。"})
                return
            key, high_raw, max_raw = parsed_memory_path
            self._handle_memory_action(key, high_raw, max_raw)
            return
        self.send_error(404, "Not Found")

    def _serve_index(self) -> None:
        try:
            body = INDEX_FILE.read_bytes()
        except OSError as error:
            self.send_error(500, f"index.html を読めません: {error}")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_guide(self) -> None:
        guide_path = resolve_guide_path(self.path)
        if guide_path is None:
            self.send_error(404, "Not Found")
            return
        try:
            body = guide_path.read_bytes()
        except OSError as error:
            self.send_error(500, f"使い方のページを読めません: {error}")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_status(self) -> None:
        try:
            status = collect_status()
        except Exception:
            # ここに来るのは想定外の不具合のとき。それでも 500 は返さず空の状態を返す。
            status = build_status(None, None, [])
        self._send_json(200, status)

    def _serve_api_catalog(self) -> None:
        portal_port = self.server.server_address[1]
        self._send_json(200, build_api_catalog(host_name_from_header(self.headers.get("Host")), portal_port))

    def _serve_ollama_models(self) -> None:
        installed = read_ollama_tags()
        loaded = read_ollama_ps()
        self._send_json(
            200,
            {
                "reachable": installed is not None,
                "installed": installed or [],
                "loaded": loaded or [],
            },
        )

    def _current_service_state(self, service: dict[str, Any]) -> str:
        """このサービスの現在の 4 状態（running/starting/stopped/not_installed）を実測する。"""
        return current_service_state(service)

    def _has_required_header(self) -> bool:
        """POST に必須の独自ヘッダがあるか。無ければ 403 を返して False。"""
        if self.headers.get(REQUIRED_ACTION_HEADER) == REQUIRED_ACTION_HEADER_VALUE:
            return True
        self._send_json(
            403,
            {
                "ok": False,
                "message": (
                    f"{REQUIRED_ACTION_HEADER} ヘッダが要ります"
                    "（入口ページ以外からの操作を防ぐため）。"
                ),
            },
        )
        return False

    def _stop_gpu_exclusive_services(self, except_key: str | None) -> tuple[list[str], str | None]:
        """GPU を排他的に使うサービスのうち、動いている（running / starting）ものを止める。

        戻り値は (止めた key の一覧, 失敗したときの message)。1 つでも止められなければそこでやめ、message を返す
        （両方立ち上がって RAM・VRAM が枯れるのを防ぐため、呼び出し元は起動・読み込みを中止する）。
        """
        states = {
            exclusive_key: self._current_service_state(SERVICES_BY_KEY[exclusive_key])
            for exclusive_key in GPU_EXCLUSIVE_KEYS
            if exclusive_key in SERVICES_BY_KEY
        }
        if except_key is None:
            keys_to_stop = [key for key, state in states.items() if state in ("running", "starting")]
        else:
            keys_to_stop = services_to_stop_before_start(except_key, states)
        stopped_keys: list[str] = []
        for key_to_stop in keys_to_stop:
            other_service = SERVICES_BY_KEY[key_to_stop]
            stop_result = perform_service_action(other_service, "stop")
            if not stop_result["ok"]:
                return stopped_keys, f"{other_service['name']} の停止に失敗しました: {stop_result['message']}"
            stopped_keys.append(key_to_stop)
        return stopped_keys, None

    def _handle_service_action(self, key: str, action: str, query: dict[str, str] | None = None) -> None:
        query = query or {}
        # 5 秒ごとには来ない操作なので、/api/status と違って必ずログに残す（誰が何をしたか分かるように）。
        sys.stderr.write(
            f"portal: 操作要求 {self.address_string()} - [{self.log_date_time_string()}] "
            f"/api/service/{key}/{action}{' wait=' + query['wait'] if 'wait' in query else ''}\n"
        )

        if not self._has_required_header():
            return

        service = SERVICES_BY_KEY.get(key)
        if service is None:
            self._send_json(404, {"ok": False, "message": "そのサービスは存在しません。"})
            return

        if action not in ALLOWED_ACTIONS:
            self._send_json(400, {"ok": False, "message": "action は start か stop のみです。"})
            return

        wait_sec, wait_error = parse_wait_seconds(query.get("wait"))
        if wait_error is not None:
            self._send_json(400, {"ok": False, "message": wait_error})
            return

        current_state = self._current_service_state(service)

        if not is_controllable({"unit": service["unit"], "state": current_state}):
            self._send_json(
                409,
                {"ok": False, "message": "未導入です。操作できません。", "state": current_state},
            )
            return

        stopped_keys: list[str] = []
        if action == "start" and key in GPU_EXCLUSIVE_KEYS:
            # Hunyuan3D・Pixal3D・ComfyUI・AudioCraft・Strata（5 つ）などは GPU を排他的に使う。起動する前に、同じグループで
            # 動いている（running / starting）他サービスを止める。停止に失敗したら起動しない。
            stopped_keys, stop_error = self._stop_gpu_exclusive_services(key)
            if stop_error is not None:
                self._send_json(
                    200,
                    {
                        "ok": False,
                        "message": stop_error.replace("に失敗しました:", "に失敗したため、起動を中止しました:", 1),
                        "state": current_state,
                    },
                )
                return

        # 同じ GPU を使うサービスを止めたあと（止めるのに失敗したら上で中止している）、Ollama のモデルを降ろす。
        # すでに動いている・起動中のものへの start は何も起こらないので、停止中のときだけ降ろす
        # （無関係な Qwen の応答を途中で切らないため）。降ろすのに失敗しても起動は続ける。
        unload_result: dict[str, Any] | None = None
        if action == "start" and key in OLLAMA_UNLOAD_BEFORE_START_KEYS and current_state == "stopped":
            unload_result = unload_ollama_models()
            sys.stderr.write(
                f"portal: Ollama のモデルを降ろす処理 降ろした={unload_result['unloaded']} "
                f"失敗={unload_result['failed']} 一覧を確認できた={unload_result['checked']}\n"
            )

        # ここに来るのは SERVICES 由来の unit のみ。利用者が渡した文字列をコマンドへ渡すことはない。
        result = perform_service_action(service, action)
        if stopped_keys or unload_result is not None:
            result = dict(result)
        if stopped_keys:
            stopped_names = "、".join(SERVICES_BY_KEY[stopped_key]["name"] for stopped_key in stopped_keys)
            result["stopped"] = stopped_keys
            result["message"] = f"{stopped_names} を停止してから起動しました。{result['message']}"
        if unload_result is not None:
            result["unloaded_models"] = unload_result["unloaded"]
            result["message"] = f"{result['message']}{describe_ollama_unload(unload_result)}"
        # ?wait=<秒> が付いていれば、ポートが応答する（running）まで待ってから返す。stop は止まるまで待つ作りなので対象外。
        if action == "start" and wait_sec is not None and result["ok"]:
            wait_result = wait_until_ready(service, wait_sec)
            result = dict(result)
            result["ready"] = wait_result["ready"]
            result["waited_sec"] = wait_result["waited_sec"]
            result["state"] = wait_result["state"]
            if wait_result["failed"]:
                result["ok"] = False
            result["message"] = f"{result['message']}{describe_wait_result(service, wait_result)}"
        self._send_json(200, result)

    def _handle_ollama_action(self, model_name: str, action: str, query: dict[str, str]) -> None:
        sys.stderr.write(
            f"portal: 操作要求 {self.address_string()} - [{self.log_date_time_string()}] "
            f"/api/ollama/{model_name}/{action}\n"
        )
        if not self._has_required_header():
            return
        if action not in OLLAMA_ACTIONS:
            self._send_json(400, {"ok": False, "message": "action は load か unload のみです。"})
            return
        keep_alive, keep_alive_error = parse_keep_alive(query.get("keep_alive"))
        if keep_alive_error is not None:
            self._send_json(400, {"ok": False, "message": keep_alive_error})
            return

        # 入っているモデルだけを受け付ける（知らない名前で Ollama を叩かせない）。
        installed = read_ollama_tags()
        if installed is None:
            self._send_json(503, {"ok": False, "message": "Ollama に接続できません（ollama.service を確認してください）。"})
            return
        resolved_name = resolve_installed_model(model_name, installed)
        if resolved_name is None:
            self._send_json(
                404,
                {"ok": False, "message": f"Ollama に {model_name} は入っていません。", "installed": installed},
            )
            return

        if action == "unload":
            loaded_entry = find_loaded_model(read_ollama_ps(), resolved_name)
            if loaded_entry is None:
                self._send_json(
                    200, {"ok": True, "message": f"{resolved_name} は VRAM に載っていません。", "model": resolved_name}
                )
                return
            unloaded = request_ollama_unload(resolved_name)
            message = (
                f"{resolved_name} を VRAM から降ろしました。" if unloaded
                else f"{resolved_name} を降ろせませんでした（Ollama への要求が失敗しました）。"
            )
            self._send_json(200, {"ok": unloaded, "message": message, "model": resolved_name})
            return

        # load: Qwen 27B は VRAM を 22 GiB 使い、GPU を排他的に使うサービスと同居できない。先に止める。
        stopped_keys, stop_error = self._stop_gpu_exclusive_services(None)
        if stop_error is not None:
            self._send_json(
                200,
                {"ok": False, "message": f"{stop_error} 読み込みを中止しました。", "stopped": stopped_keys},
            )
            return
        loaded_ok, load_error = request_ollama_load(resolved_name, keep_alive)
        loaded_models = read_ollama_ps()
        loaded_entry = find_loaded_model(loaded_models, resolved_name)
        messages: list[str] = []
        if stopped_keys:
            messages.append(f"{'、'.join(SERVICES_BY_KEY[stopped_key]['name'] for stopped_key in stopped_keys)} を停止しました。")
        if loaded_ok:
            messages.append(f"{resolved_name} を VRAM に載せました。")
        else:
            messages.append(f"{resolved_name} を載せられませんでした: {load_error}")
        self._send_json(
            200,
            {
                "ok": loaded_ok,
                "message": "".join(messages),
                "model": resolved_name,
                "stopped": stopped_keys,
                "size_vram_mib": loaded_entry["size_vram_mib"] if loaded_entry else None,
                "loaded_models": loaded_models or [],
            },
        )

    def _handle_option_action(self, key: str, option_name: str, value: str) -> None:
        # 起動・停止と同じく、設定変更も必ずログに残す。
        sys.stderr.write(
            f"portal: 設定変更要求 {self.address_string()} - [{self.log_date_time_string()}] "
            f"/api/option/{key}/{option_name}/{value}\n"
        )

        if self.headers.get(REQUIRED_ACTION_HEADER) != REQUIRED_ACTION_HEADER_VALUE:
            self._send_json(
                403,
                {
                    "ok": False,
                    "message": (
                        f"{REQUIRED_ACTION_HEADER} ヘッダが要ります"
                        "（入口ページ以外からの操作を防ぐため）。"
                    ),
                },
            )
            return

        service = SERVICES_BY_KEY.get(key)
        if service is None:
            self._send_json(404, {"ok": False, "message": "そのサービスは存在しません。"})
            return

        option_key = OPTION_URL_TO_KEY.get(option_name)
        if option_key is None:
            self._send_json(400, {"ok": False, "message": "その設定は存在しません。"})
            return

        if value not in OPTION_VALUES:
            self._send_json(400, {"ok": False, "message": "値は on か off のみです。"})
            return

        option_config = find_service_option(service, option_key)
        if option_config is None:
            self._send_json(400, {"ok": False, "message": f"{service['name']} にはその設定がありません。"})
            return

        # 稼働中・起動中は変更させない。systemd の EnvironmentFile は起動時にしか読まれないため、
        # 動いている間に書き換えても反映されず、利用者が「変えたのに変わらない」と誤解するため。
        current_state = self._current_service_state(service)
        if current_state in ("running", "starting"):
            self._send_json(
                409,
                {"ok": False, "message": "変更するには先に停止してください。", "state": current_state},
            )
            return

        enabled = OPTION_VALUES[value]
        try:
            write_option_value(option_config, enabled)
        except OSError as error:
            self._send_json(500, {"ok": False, "message": f"設定の書き込みに失敗しました: {error}"})
            return

        state_label = "オン" if enabled else "オフ"
        self._send_json(
            200,
            {"ok": True, "message": f"低 VRAM モードを{state_label}にしました。", "value": enabled},
        )

    def _handle_memory_action(self, key: str, high_raw: str, max_raw: str) -> None:
        # 起動・停止・設定変更と同じく、メモリ上限の変更も必ずログに残す（誰がいくつにしたか）。
        sys.stderr.write(
            f"portal: メモリ設定要求 {self.address_string()} - [{self.log_date_time_string()}] "
            f"/api/memory/{key}/{high_raw}/{max_raw}\n"
        )

        if self.headers.get(REQUIRED_ACTION_HEADER) != REQUIRED_ACTION_HEADER_VALUE:
            self._send_json(
                403,
                {
                    "ok": False,
                    "message": (
                        f"{REQUIRED_ACTION_HEADER} ヘッダが要ります"
                        "（入口ページ以外からの操作を防ぐため）。"
                    ),
                },
            )
            return

        service = SERVICES_BY_KEY.get(key)
        if service is None or service["unit"] is None:
            self._send_json(404, {"ok": False, "message": "そのサービスは存在しません。"})
            return

        # ユニットがまだ無い間（Strata の 5 つ・ComfyUI を導入する前など）は set-property の宛先が無い。
        if self._current_service_state(service) == "not_installed":
            self._send_json(
                409,
                {"ok": False, "message": f"{service['name']} は未導入です。メモリ上限は設定できません。"},
            )
            return

        try:
            high_gib = int(high_raw)
            max_gib = int(max_raw)
        except ValueError:
            self._send_json(400, {"ok": False, "message": "high と max は整数（GiB）で指定してください。"})
            return

        total_ram_gib = read_meminfo_total_gib()
        if total_ram_gib is None:
            self._send_json(500, {"ok": False, "message": "RAM の総量（/proc/meminfo）を取得できませんでした。"})
            return

        bounds = memory_bounds(total_ram_gib)
        invalid_reason = validate_memory_limits(high_gib, max_gib, bounds)
        if invalid_reason is not None:
            self._send_json(400, {"ok": False, "message": invalid_reason})
            return

        unit = service["unit"]
        persistent_result, runtime_result = set_unit_memory_limits(unit, high_gib, max_gib)
        ok = persistent_result["ok"] and runtime_result["ok"]

        if ok:
            message = (
                f"{service['name']} を MemoryHigh={high_gib}G / MemoryMax={max_gib}G にしました"
                "（再起動不要。PC を再起動しても残ります）"
            )
        elif not persistent_result["ok"]:
            message = f"{service['name']} の設定変更に失敗しました: {persistent_result['stderr']}"
        else:
            message = (
                f"{service['name']} の恒久設定は書き込みましたが、即時反映（--runtime）に失敗しました: "
                f"{runtime_result['stderr']}"
            )

        warning = memory_warning(max_gib, bounds)
        memory_values = read_memory_properties(unit)
        self._send_json(
            200,
            {"ok": ok, "message": message, "warning": warning, "memory": memory_values},
        )

    def _send_json(self, status_code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ローカル AI サーバーの入口ページ")
    parser.add_argument("--host", default="0.0.0.0", help="待受アドレス（既定 0.0.0.0）")
    parser.add_argument("--port", type=int, default=8080, help="待受ポート（既定 8080）")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    arguments = parse_args(argv)
    server = ThreadingHTTPServer((arguments.host, arguments.port), PortalRequestHandler)
    sys.stderr.write(f"portal: http://{arguments.host}:{arguments.port} で待受を開始します\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        sys.stderr.write("portal: 終了します\n")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
