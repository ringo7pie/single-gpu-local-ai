# audiocraft

文章から作曲・効果音を作る AudioCraft（`tools/audiocraft`、systemd user ユニット `audiocraft`、enable しない）。
導入と使い方の手順は `tools/audiocraft/README.md` を Read する。ポートは `areas/web-services.md` の表を Read する。
プログラムから呼ぶ API（REST・Gradio）の事実は `knowledge/10-silver/areas/audiocraft-api.md` を Read する。
2026-10-27 に導入した。実測値は下の「実測」の節にある。

## モデルとライセンス

- 作曲は MusicGen Medium（`facebook/musicgen-medium`、1.5B、32 kHz モノラル、1〜30 秒）、効果音は AudioGen Medium
  （`facebook/audiogen-medium`、1.5B、16 kHz）。1 つの Gradio 画面のタブ 2 つで使い、起動時に 2 つとも VRAM へ載せる。
- コードは MIT、**重みは CC-BY-NC 4.0（非商用のみ）**。作った音を収益化する用途には使わない。
- 説明文は英語で書く（モデルは英語の説明で学習されている）。上流の文書は Medium に 16 GB 以上の GPU を要ると書く。

## 古い torch スタックの専用 venv

- 上流 facebookresearch/audiocraft は固定コミット `896ec7c47f5e5d1e5aa1e4b260c4405328bf009d`（2025-03-13 の main の先頭）。
  タグ v1.3.0 は 2024-06 で、以後上流の更新は無い。
- 上流が `torch==2.1.0`・`xformers<0.0.23`・`numpy<2` に固定し、`audiocraft/modules/transformer.py` が xformers を
  先頭で無条件に import する。ほかのツールの torch とは共存できないので専用の venv を作る。
- torch 2.1.0 の wheel は cp311 までで cp312 が無い。venv は uv の Python 3.11 にする。
- torch・torchaudio・torchvision は `https://download.pytorch.org/whl/cu121` の `2.1.0+cu121`・`2.1.0+cu121`・`0.16.0+cu121`。
  wheel が CUDA 12.1 の runtime を同梱するので、システムの CUDA Toolkit（13.2・12.6）は使わない。
  xformers は PyPI の `0.0.22.post7`（cp311 の manylinux wheel）。
- `av` は 12.0.0 にする。上流の 11.0.0 には cp311 の wheel が無く、ソースからのビルドに ffmpeg の開発ヘッダが要る。
  `av` は音声ファイルの読み込みにしか使わず、文章からの生成では呼ばれない。
- 学習・評価用の依存（demucs・dora-search・treetable・pesq・pystoi）は入れない。

## ffmpeg が無いので wav は soundfile で書く

- この機体に `ffmpeg` の実行ファイルは無く、sudo も使えないので入れられない。
- 上流の `audio_write` は `ffmpeg` を subprocess で呼ぶので使えない。wav は soundfile で書き、音量の正規化だけ
  上流の `audiocraft.data.audio_utils.normalize_audio`（ラウドネス）を使う。mp3 は作らない。

## 重みの置き場

- `AUDIOCRAFT_CACHE_DIR` を `tools/audiocraft/models/audiocraft` に向ける。上流の `hf_hub_download` が
  1 モデルにつき `state_dict.bin` と `compression_state_dict.bin` をここへ取る（HF のキャッシュ形式
  `models--facebook--musicgen-medium/snapshots/<rev>/`）。
- 文章の符号化器 T5（`t5-base`）は transformers が `HF_HOME`（`tools/audiocraft/models/hf`）へ取る。
- 重みを取得する `run-audiocraft fetch-models` は、上流（vendor）のコードを実行せずに動く。

## 入口ページと排他

- 入口ページでは GPU 排他グループの 9 つ目で、起動前に Ollama のモデルを降ろす対象にも入れた（Strata と同じ扱い）。
- unit は `Conflicts=` と `After=` に GPU ユニット 8 つ（hunyuan3d・pixal3d・comfyui・Strata 5 種）を持つ。
  `After=` は audiocraft 側だけに書く。ほかの 8 つの unit は変えていない。
- メモリ上限の既定は MemoryHigh 16G / MemoryMax 20G（下の実測の約 2.6 倍）。自動起動しない。

## 実測（A30、2026-10-27。重みがメモリのキャッシュにある状態）

- 入口ページから起動して、ポート 7862 が開くまで 14.2〜14.7 秒（3 回）。MusicGen の読み込み 4.4 秒、AudioGen 3.8 秒。
  ディスクが冷えた状態の起動時間は測っていない。
- VRAM は、2 モデルを載せた待機時にプロセスで 9,800 MiB（torch の割り当て 9,266 MiB）。生成中のピークは torch の割り当てで
  9.5〜10.4 GiB（30 秒の作曲で 10,422 MiB）、nvidia-smi の全体で 11,512 MiB。
- RAM は cgroup の memory.peak が、起動直後 5.46 GiB、作曲 2 回・効果音 1 回の生成後 6.06 GiB。
- 生成時間は実時間と同程度。作曲 10 秒が 10.5 秒、30 秒が 33.2 秒、効果音 5 秒が 5.1 秒。
- `generate --device cpu` は GPU を使わず（`CUDA_VISIBLE_DEVICES` を空にする）、2 秒の生成に 22〜25 秒かかる（実時間の約 11〜12 倍）。
- 出力の wav は指定どおりの長さ・モノラル・32000 / 16000 Hz で、無音ではない。聴感上の質と日本語の説明文の結果は確かめていない。
- GPU の排他は 2 層とも実機で効いた。audiocraft の稼働中に入口ページから hunyuan3d を起動すると audiocraft が止まり、
  hunyuan3d の稼働中に `systemctl --user start audiocraft` すると systemd の `Conflicts=` で hunyuan3d が止まった。
  Ollama に gemma4:e4b が載った状態で入口ページから起動すると、応答に `unloaded_models` が入り、起動後の `ollama ps` は空だった。

## 他の PC から使う

- ufw でポート 7862 を LAN 2 セグメント分開ける必要がある（sudo。2026-10-27 時点で未実施）。
  手順は `knowledge/10-silver/resources/lan-access.md` を Read する。
