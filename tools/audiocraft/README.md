# audiocraft

文章（説明文）から**作曲**と**効果音**を作る。Meta の [AudioCraft](https://github.com/facebookresearch/audiocraft) の
**MusicGen Medium**（1.5B、作曲）と **AudioGen Medium**（1.5B、効果音）を、この A30 サーバーで動かし、
入口ページ（`tools/portal`、8080）から起動して Gradio 画面（ポート **7862**）で使う。
同じポートの **REST の口**（`POST /api/v1/music`・`/api/v1/sound`）に説明文を JSON で送ると、wav がそのまま返る
（下の「API」）。

運用上の事実（実測値・制約・決定）は `knowledge/10-silver/areas/audiocraft.md` を Read する。

## 使い方

1. 入口ページ（http://<server-ip>:8080/）の「音楽・効果音生成（AudioCraft Medium）」で「起動」を押す。
   ほかの GPU サービスが動いていれば自動で止まり、Ollama のモデル（Qwen など）も VRAM から降ろされる。
   2 つのモデルを読み込むので、使えるまで時間がかかる。重みがメモリのキャッシュに載っていれば 15 秒ほど（2026-10-27 の実測）。
   ディスクから読むときはもっとかかる（冷えた状態は未計測）。
2. 「開く →」で画面（7862）を開く。タブは「作曲（MusicGen）」と「効果音（AudioGen）」。
3. **説明文は英語で書く**（モデルは英語の説明で学習されていて、日本語は精度が大きく落ちる）。
   例: `calm lo-fi piano melody with soft drums, relaxing, 80 bpm` / `footsteps on gravel, slow walking pace`
4. 長さを決めて「生成する」。作った音は画面で聴け、`outputs/` に wav と json（入力・seed・所要時間）で残る。

| | 作曲 | 効果音 |
|---|---|---|
| モデル | `facebook/musicgen-medium` | `facebook/audiogen-medium` |
| 出力 | 32 kHz・モノラルの wav | 16 kHz・モノラルの wav |
| 長さ | 1〜30 秒（既定 10 秒） | 1〜20 秒（既定 5 秒。10 秒を超える分は前の音をつないで延ばす） |
| 向き | 曲・BGM（歌声は上手く作れない。終わりが無音に崩れることがある） | 足音・ドア・動物・乗り物・天気などの効果音・環境音 |

詳細設定（`seed`・`top_k`・`top_p`・`temperature`・`cfg_coef`）は上流の `set_generation_params` の値で、既定は上流と同じ
（250 / 0 / 1.0 / 3.0）。`seed` が同じなら、ほぼ同じ音になる。`-1` は毎回乱数。

## API（他のプログラム・他の PC から使う）

サービス（`audiocraft`）が動いているときだけ使える。起動は入口ページか、
`curl -X POST -H "X-Portal-Request: 1" http://<server-ip>:8080/api/service/audiocraft/start`。ポートが開くまで約 15 秒。
他の PC から使うには、先に ufw でポート 7862 を開ける（`knowledge/10-silver/resources/lan-access.md`）。**認証は無い**（LAN 内の前提）。

### REST（wav を直接返す）

```bash
curl -X POST http://<server-ip>:7862/api/v1/music \
  -H "Content-Type: application/json" \
  -d '{"prompt": "calm lo-fi piano melody with soft drums, relaxing, 80 bpm", "duration": 10}' -o music.wav

curl -X POST http://<server-ip>:7862/api/v1/sound \
  -H "Content-Type: application/json" \
  -d '{"prompt": "footsteps on gravel, slow walking pace", "duration": 5}' -o sound.wav

curl http://<server-ip>:7862/api/v1/health     # 状態、種類ごとのモデル・サンプルレート・長さの範囲
```

| 項目 | 内容 |
|---|---|
| 本文の項目 | `prompt`（必須・英語）、`duration`・`seed`・`top_k`・`top_p`・`temperature`・`cfg_coef`、`keep`。省略と `null` は既定。範囲は画面と同じ（作曲 1〜30 秒、効果音 1〜20 秒）。未知の項目は 400 |
| `keep` | 既定 `true`（サーバーの `outputs/` に wav と json を残す）。`false` なら残さない |
| 成功（200） | 本文は `audio/wav`（モノラル、作曲 32 kHz・効果音 16 kHz）。実際に使われた値は応答ヘッダ `X-Acraft-Seed`・`X-Acraft-Duration-Sec`・`X-Acraft-Elapsed-Sec`・`X-Acraft-Sample-Rate`・`X-Acraft-Model`・`X-Acraft-Kind`、`keep` が真なら `X-Acraft-File` |
| 入力の誤り（400） | `{"error": "理由（日本語）"}` |
| `Content-Type` が `application/json` でない（415） | 他サイトの form や fetch が事前確認なしで送れる型を断る（CSRF 対策） |
| GET など（405） | `Allow: POST` |
| 混雑（503） | 順番待ちを含めて同時に 4 件まで（`REST_MAX_PENDING`）。超えると `Retry-After: 30`。モデルが読み込まれていないときも 503 |
| 失敗（500） | `{"error": "生成に失敗しました: 型: 理由"}` |

- 生成は 1 度に 1 件。画面の分も含めて順番に処理される。作曲 30 秒で約 33 秒かかるので、クライアントの待ち時間は 2 分以上にする。
- 自動の API 文書（`/docs`）は出さない（本文を自前で検証しており、文書が実態とずれるため）。この節と画面の「API の使い方」が文書。
- ブラウザの別サイトの JavaScript からは呼べない（CORS の許可を出さない。Gradio の既定のまま）。

### Gradio の API

`pip install gradio_client` して使う。引数の順は `prompt, duration, seed, top_k, top_p, temperature, cfg_coef`。

```python
from gradio_client import Client

client = Client("http://<server-ip>:7862")
audio_path, info, _ = client.predict("calm lo-fi piano melody, 80 bpm", 10, -1, 250, 0, 1.0, 3.0, api_name="/generate_music")
# 効果音は api_name="/generate_sound"。audio_path は自分の PC に保存された wav
```

## ライセンス（大事）

- コードは MIT。**モデルの重みは CC-BY-NC 4.0（非商用のみ）。** 作った音を収益化する用途には使えない。
  個人の試作・研究・社内の検討に限る。NOTICE.md の §3・§4 にも書いてある。

## 導入

```bash
cd ~/Proj/localai/tools/audiocraft
./setup-server.sh                  # 約 14 GB（venv 約 5.5 GB + 重みと T5 約 8.2 GB）。数十分かかる
./setup-server.sh --skip-models    # 重みは後で（run-audiocraft fetch-models）
./setup-server.sh --skip-gpu-check # 最後の点検で GPU を触らない（ほかのプロセスが GPU を使っている間）
```

`./localai install audiocraft` でも同じ。sudo は使わない。冪等で、既にあるものは触らない。行うこと:

1. uv の Python 3.11 の venv（`torch 2.1.0` の wheel は cp311 までしか無い）
2. 上流を `vendor/audiocraft` に**固定コミット**（`896ec7c4…`、2025-03-13 の main）で取得
3. torch・torchaudio・torchvision を `https://download.pytorch.org/whl/cu121` から（wheel が CUDA 12.1 を同梱する。
   システムの CUDA Toolkit 13.2 / 12.6 は使わない）
4. `requirements.lock.txt` の依存
5. 上流（`--no-deps`）と `acraft`（このツール）を editable で
6. 重みの取得（`facebook/musicgen-medium`・`facebook/audiogen-medium` の各 2 ファイルと T5 `t5-base`）。`models/` の下に置く
7. systemd user unit `audiocraft`（**enable しない**）と env ファイル `~/.config/audiocraft/audiocraft.env`
8. `run-audiocraft doctor`

他の PC から使うには、サーバーで ufw にポート 7862 を足す（`knowledge/10-silver/resources/lan-access.md`。sudo が要る）。

## CLI（`run-audiocraft`）

| コマンド | 内容 |
|---|---|
| `serve` | 画面を起動する（systemd から呼ばれる）。VRAM が空くのを 60 秒待ち、空かなければ中止する。モデルは起動時に 2 つとも読み込む |
| `doctor [--skip-gpu]` | 点検。venv・torch 2.1.0+cu121・xformers・audiocraft の import・上流のコミット・重み・VRAM・ポート・unit。`--skip-gpu` は CUDA を初期化せず VRAM も見ない |
| `fetch-models` | 重みと T5 を取得する（あればスキップ） |
| `generate --kind music\|sound --prompt "..." [--duration N] [--seed N] [--device auto\|cuda\|cpu] [--out PATH]` | 画面を通さずに 1 件生成する。毎回モデルを読み込む。`--device cpu` は GPU を一切使わない |

## 設計の決定

- **自前の Gradio 画面**（`acraft/ui.py`）。上流の `demos/` は使わない（常駐させて 2 つのモデルを 1 画面で使うため）。
- **REST の口は Gradio のアプリの先頭へ足す**（`acraft/rest.py` の `install_routes`）。`mount_gradio_app` で画面を `/` に載せると、
  `/` が `//` へ転送されて画面の URL が崩れたため。経路は `/api/v1/...` で、Gradio の `/gradio_api/...` とは重ならない。
  `share=True` は使わない。`GRADIO_ANALYTICS_ENABLED=False`・`HF_HUB_DISABLE_TELEMETRY=1` で利用状況を外へ送らせない。
- **wav は soundfile で書く。** 上流の `audio_write` は `ffmpeg` の実行ファイルを呼ぶが、この機体には無い
  （sudo が使えず入れられない）。音量の正規化（ラウドネス）だけ上流の `normalize_audio` を使う。mp3 は作らない。
- **古い torch スタックの専用 venv。** 上流が `torch==2.1.0`・`xformers<0.0.23`・`numpy<2` に固定していて、
  `transformer.py` が xformers を無条件に import する。ほかのツール（Pixal3D は torch 2.6.0、Hunyuan3D は 2.12.1）とは
  共存できないので、専用の venv にする。
- **`av` は 12.0.0**（上流は 11.0.0）。11.0.0 に cp311 の wheel が無く、ソースからのビルドに ffmpeg の開発ヘッダが要る。
  `av` は音声ファイルの読み込みにしか使わず、文章からの生成では呼ばれない。
- **学習・評価用の依存は入れない**（demucs・dora-search・treetable・pesq・pystoi）。生成の経路では import されない。
- **GPU の排他は 2 層。** 入口ページ（`GPU_EXCLUSIVE_KEYS`・`OLLAMA_UNLOAD_BEFORE_START_KEYS`）と、
  unit の `Conflicts=`/`After=`（`After=` は audiocraft 側だけに書く。両側に書くと順序が輪になる）。
- **メモリ上限の既定は MemoryHigh 16G / MemoryMax 20G。** 実測の memory.peak 6.06 GiB の約 2.6 倍（共通の既定 44 / 48 より小さい）。
- 重み・キャッシュはすべて `models/` の下（`AUDIOCRAFT_CACHE_DIR`・`HF_HOME`・`TORCH_HOME`）。Open WebUI と共有する `~/.cache` には混ぜない。

モデルを差し替えるときは `~/.config/audiocraft/audiocraft.env` の `ACRAFT_MUSIC_MODEL` / `ACRAFT_SOUND_MODEL` を書き、
`run-audiocraft fetch-models` で重みを取り、サービスを再起動する（例: `facebook/musicgen-stereo-medium`。
長さ・サンプルレートの表示は Medium の値のままなので、画面の説明と合わなくなる）。

## ファイル構成

| ファイル | 役割 |
|---|---|
| `setup-server.sh` | 導入（冪等・非対話・sudo なし） |
| `run-audiocraft` | CLI の入口（venv の python で `acraft.cli`） |
| `acraft/config.py` | パス・ポート・モデル ID・上流のコミット（定数のみ） |
| `acraft/params.py` | 生成パラメータの検証・出力名・メタデータ（標準ライブラリのみ） |
| `acraft/cli.py` | `serve` / `doctor` / `fetch-models` / `generate`（標準ライブラリのみ） |
| `acraft/engine.py` | モデルの読み込みと生成・wav の書き出し（torch・audiocraft は関数の中で import） |
| `acraft/ui.py` | Gradio 画面（gradio を import するのはここだけ）。起動後に REST の経路を足す |
| `acraft/rest.py` | REST の口（fastapi。gradio・torch は import しない）。wav を直接返す |
| `acraft/help_texts.py` | 画面の日本語の説明文 |
| `acraft/ollama.py` | VRAM の空き確認 |
| `systemd/audiocraft.service.example` | unit の雛形 |
| `requirements.lock.txt` | 依存の固定（torch 系は別 index なので入れない） |
| `tests/test_units.py` | 単体試験（torch・GPU を使わない） |
| `tests/test_rest.py` | REST の口の試験（偽の Engine と fastapi の TestClient。fastapi と httpx が無ければ飛ばす） |
| `tests/test_generate_real.py` | 実機試験（GPU と重みを使う。`--device cpu` なら GPU を使わない） |

`.venv/`・`vendor/`・`models/`・`outputs/`・`logs/` は追跡しない（`.gitignore`）。

## テスト

```bash
uvx --with pytest --with fastapi --with httpx pytest tools/audiocraft/tests/test_units.py tools/audiocraft/tests/test_rest.py -q
# GPU を使わない。./localai test --only audiocraft でも（fastapi と httpx を足して走らせる）

# 実機（GPU と重みを使う。数分。先に systemctl --user stop audiocraft）
tools/audiocraft/.venv/bin/python tools/audiocraft/tests/test_generate_real.py
# GPU を使わない確認（CPU で 2 秒だけ作る）
tools/audiocraft/.venv/bin/python tools/audiocraft/tests/test_generate_real.py --device cpu --music-seconds 2 --sound-seconds 2
```

## 実測（この機体・A30、2026-10-27）

重みがメモリのキャッシュに載っている状態（取得した直後）で測った。ディスクが冷えた状態の読み込みは未計測。
入口ページ経由で起動し、画面（7862）の API（`gradio_client`）で生成した。

| 項目 | 値 |
|---|---|
| 起動からポート 7862 が開くまで | 14.2〜14.7 秒（3 回）。MusicGen の読み込み 4.4 秒、AudioGen 3.8 秒 |
| VRAM（2 モデルを載せた待機時） | プロセス 9,800 MiB（nvidia-smi。全体 10,270 MiB）。torch の割り当て 9,266 MiB |
| VRAM（生成中のピーク） | torch の割り当てで作曲 10 秒 9,682 MiB・効果音 5 秒 9,553 MiB・作曲 30 秒 10,422 MiB。nvidia-smi の全体で 11,512 MiB |
| RAM（cgroup の memory.peak） | 起動直後 5.46 GiB、作曲 2 回・効果音 1 回の生成後 6.06 GiB |
| 生成時間 | 作曲 10 秒 → 10.5 秒、作曲 30 秒 → 33.2 秒、効果音 5 秒 → 5.1 秒（ほぼ実時間） |
| `generate` の 1 件（1 モデルだけ読み込み、毎回起動） | 作曲 10 秒 19.1 秒・効果音 5 秒 35.0 秒（プロセスの起動と読み込みを含む）。torch のピーク 4,619 / 5,303 MiB |
| 出力の wav | 長さは指定どおり（10.00 / 5.00 / 30.00 秒）、モノラル、32000 / 16000 Hz、無音ではなく、最大振幅 0.80〜0.996 |

- 排他: `audiocraft` が稼働中に入口ページから `hunyuan3d` を起動すると、応答に `"stopped": ["audiocraft"]` が入り止まる。
  `hunyuan3d` が動く間に `systemctl --user start audiocraft` すると、systemd の `Conflicts=` で `hunyuan3d` が止まる。
  Ollama に `gemma4:e4b` が載った状態で入口ページから起動すると、応答に `"unloaded_models": ["gemma4:e4b"]` が入り、起動後の `ollama ps` は空。
- 確かめていないこと: 生成した音の聴感上の質、日本語の説明文の結果、ブラウザでの画面の表示（この機体にブラウザが無い）、
  他の PC からの到達（ufw が未実施）、ディスクが冷えた状態の起動時間。

## 元に戻す

```bash
systemctl --user stop audiocraft
rm ~/.config/systemd/user/audiocraft.service && systemctl --user daemon-reload
rm -r ~/.config/audiocraft
# 重み・venv・上流は tools/audiocraft の .venv / vendor / models を消す（再作成は setup-server.sh）
```
