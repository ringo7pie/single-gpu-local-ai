# qwen3-tts — 声の特徴を文章で指定して読み上げる（Qwen3-TTS VoiceDesign）

Qwen の [Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS) のうち、声の特徴を文章（自然言語）で指定して声を作る
**VoiceDesign** モデル（`Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign`）を、この A30 サーバーで動かす。

- 画面は上流の Gradio 画面 `qwen-tts-demo` をそのまま使う（ポート 7863、systemd user ユニット `qwen3-tts`、enable しない）。
- 入口ページ（`http://<server-ip>:8080/`）の「音声合成（Qwen3-TTS VoiceDesign）」のカードから起動・停止する。
- 日本語・英語・中国語・韓国語・ドイツ語・フランス語・ロシア語・ポルトガル語・スペイン語・イタリア語の 10 言語。
- コード（PyPI の `qwen-tts` 0.1.1）もモデルも Apache-2.0。

## 導入

```bash
tools/qwen3-tts/setup-server.sh            # 冪等。venv（Python 3.12）・torch・依存・重み（約 4.3 GB）・ユニット・点検
tools/qwen3-tts/setup-server.sh --skip-models --skip-gpu-check   # 重みを後回し・GPU を触らない点検
tools/qwen3-tts/run-qwen3-tts fetch-models # 重みだけ取る（HF の sha 5ecdb673… に固定）
tools/qwen3-tts/run-qwen3-tts doctor       # 点検（--skip-gpu で CUDA を初期化しない）
```

- venv は uv の Python 3.12。torch は PyTorch 公式の `2.9.1+cu126`（wheel が CUDA 12.6 を同梱。システムの CUDA Toolkit は使わない）。
  上流は torch の版を指定せず `transformers==4.57.3` に固定しているので、ComfyUI と同じ torch にした。
- それ以外の依存は `requirements.lock.txt`（2026-11-21 に freeze。torch・torchaudio・triton・nvidia-* は除く）。
- 重みは `models/Qwen3-TTS-12Hz-1.7B-VoiceDesign/`。音声の符号化器（`speech_tokenizer/`）も同じリポジトリに入っている。
  `HF_HOME` も `models/hf` に向け、`~/.cache` を使わない。
- ディスクは venv 約 6.6 GB + 重み 約 4.3 GB。
- flash-attn は入れていない。上流の画面は既定で flash-attn を使おうとするので、`run-qwen3-tts serve` は必ず `--no-flash-attn` を渡す
  （注意機構は PyTorch の sdpa になる）。起動時に「flash-attn is not installed」の警告が出るが、動作に支障はない。
- `sox` の実行ファイルはこの機体に無く、起動時に「SoX could not be found!」と出る。上流で sox を使うのは 25Hz 版の符号化器
  （`qwen_tts/core/tokenizer_25hz/`）だけで、このモデル（12Hz 版）の生成では使わない。

## 使い方

1. 入口ページで「起動」を押す（ほかの GPU サービスは止まり、Ollama のモデルは降ろされる）。
2. 「開く」で `http://<server-ip>:7863/` の画面が出る。表記は英語と中国語。
3. **Text**（読ませる文）、**Language**（Auto でもよい）、**Voice Design Instruction**（声の特徴。例: 「落ち着いた低めの声の三十代の男性。ゆっくり、はっきりと話す。」）
   を書き、**Generate (生成)** を押す。結果の wav は画面から再生・保存できる。
- 同じ説明でも生成のたびに声が少し変わる。同時に受け付ける生成は 1 件（`--concurrency 1`。2 件目以降は順番待ち）。
- 声の特徴の説明は日本語でも受け付け、音声が出ることは確かめた。聴いての質（説明どおりの声か）は確かめていない。

### プログラムから

Gradio の API（`/run_voice_design`）で呼べる。認証は無い。

```python
from gradio_client import Client
client = Client("http://<server-ip>:7863/")
wav_path, status = client.predict(text="こんにちは。", lang_disp="Japanese",
                                  design="明るく元気な二十代の女性。", api_name="/run_voice_design")
```

## 設定（`~/.config/qwen3-tts/qwen3-tts.env`）

| 変数 | 既定 | 意味 |
|---|---|---|
| `QWEN3TTS_DTYPE` | `bfloat16` | 重みの型（`bfloat16` / `float16` / `float32`）。知らない値は既定に戻す |
| `QWEN3TTS_FLASH_ATTN` | `0` | `1` で flash-attn を使う（venv に flash-attn を自分で入れたときだけ） |

変えたら `systemctl --user restart qwen3-tts`。

## 実測（A30、2026-11-21。重みがメモリのキャッシュにある状態）

- 起動からポート 7863 が開くまで 5.7〜6.4 秒（systemctl から 1 回、入口ページから 1 回）。ディスクが冷えた状態は測っていない。
- VRAM（nvidia-smi のプロセス）は待機時 4,376 MiB、生成 3 回の後 5,478 MiB。実機試験での torch の割り当てのピークは 4,289 MiB。
- RAM は cgroup の memory.peak が、起動と生成 3 回の後で 1.61 GiB。メモリ上限の既定はその約 4〜5 倍の MemoryHigh 6G / MemoryMax 8G。
- 生成時間（API 経由）: 日本語 44 文字 → 音声 7.84 秒を 6.9 秒、英語 70 文字 → 6.80 秒を 5.6 秒、日本語 80 文字 → 23.36 秒を 19.4 秒。
  出力は 24,000 Hz。実機試験（`tests/test_generate_real.py`）では日本語 1 文で音声 10.0 秒を 8.2 秒。
- 排他は 2 層とも実機で効いた。入口ページから audiocraft を起動すると qwen3-tts が止まり、Ollama に gemma4:e4b を載せた状態で
  入口ページから qwen3-tts を起動すると、audiocraft が止まり、応答の `unloaded_models` に gemma4:e4b が入った。
  `systemctl --user start audiocraft` / `start qwen3-tts` でも `Conflicts=` で相手が止まった。

## 試験

```bash
uvx --with pytest pytest tools/qwen3-tts/tests/test_units.py -q        # GPU・torch を使わない
systemctl --user stop qwen3-tts
tools/qwen3-tts/.venv/bin/python tools/qwen3-tts/tests/test_generate_real.py   # GPU と重みを使う。1 分ほど
```

## 他の PC から使う

ufw でポート 7863 を LAN の 2 つのセグメントに開ける（sudo。手順は `knowledge/10-silver/resources/lan-access.md`）。
