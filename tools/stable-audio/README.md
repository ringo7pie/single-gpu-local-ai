# stable-audio

文章（説明文）から**曲・効果音**を作る。Stability AI の [Stable Audio 3.0](https://github.com/Stability-AI/stable-audio-3) の
**Medium**（44.1 kHz・ステレオ、最長 380 秒）を、この A30 サーバーで動かし、入口ページ（`tools/portal`、8080）から起動して
自前の Gradio 画面（ポート **7864**）で使う。同じポートの **REST の口**（`POST /api/v1/generate`）に JSON を送ると、wav がそのまま返る。

元の音を指定するモードも使える:

| モード | 画面のタブ | 内容 |
|---|---|---|
| `text` | 文章 → 音 | 説明文だけから作る |
| `audio` | 音 → 音（作り変え） | 元の音にノイズを足してから、説明文に沿って作り直す（上流の `init_audio`）。`init_noise_level` が小さいほど元に近い（0.1 でごく近い、0.5 で半々、1.0 で元の音は効かない。既定 0.7） |
| `inpaint` | 部分の作り直し・続き | 元の音の［開始, 終了）秒だけを作り直し、ほかは残す（上流の `inpaint_audio`）。**開始を元の音の長さにし、長さを元の音より長くすると「続き」**。終了を省くと長さまで |

運用上の事実（実測値・制約・決定）は `knowledge/10-silver/areas/stable-audio.md` を Read する。

## 使い方

1. 入口ページ（http://<server-ip>:8080/）の「音楽・効果音生成（Stable Audio 3.0 Medium）」で「起動」を押す。
   ほかの GPU サービスが動いていれば自動で止まり、Ollama のモデルも VRAM から降ろされる。
2. 「開く →」で画面（7864）を開く。**説明文は英語で書く**（ジャンル・楽器・雰囲気・BPM を文で）。
3. 長さを決めて生成する。作った音は画面で聴け、`outputs/` に wav と json（入力・seed・所要時間）で残る。
   元の音を使ったときは、その写しも `<名前>-source.wav` で残る。

詳細設定は `seed`（-1 で毎回乱数）と `steps`（既定 8。上流の文書では、post-trained の medium は 8 より増やしても良くなるとは限らない）。
上流の `cfg_scale`・`negative_prompt` は base モデルにしか効かない（上流 `docs/workflows/inference.md`）ので、画面にも REST にも出さない。

## API

サービス（`stable-audio`）が動いているときだけ使える。他の PC から使うには、先に ufw でポート 7864 を開ける
（`knowledge/10-silver/resources/lan-access.md`）。**認証は無い**（LAN 内の前提）。

```bash
curl -X POST http://<server-ip>:7864/api/v1/generate -H "Content-Type: application/json" \
  -d '{"prompt": "Lo-fi hip hop beat with mellow Rhodes chords, 84 BPM", "duration": 30}' -o music.wav

# 続き（元の音 10 秒の後ろを 30 秒まで）。base64 はコマンドラインに載せず標準入力から流す（引数の長さの上限を避ける）
{ printf '{"prompt": "dreamy synthpop", "duration": 30, "mode": "inpaint", "inpaint_start": 10, "audio_base64": "'; base64 -w0 input.wav; printf '"}'; } \
  | curl -X POST http://<server-ip>:7864/api/v1/generate -H "Content-Type: application/json" -d @- -o continued.wav

curl http://<server-ip>:7864/api/v1/health
```

| 項目 | 内容 |
|---|---|
| 本文の項目 | `prompt`（必須・英語）、`duration`（1〜380 秒、既定 30）、`seed`、`steps`（1〜100、既定 8）、`mode`（`text` / `audio` / `inpaint`）、`audio_base64`（元の音のファイルの base64。wav・flac・ogg・mp3、50 MB まで）、`init_noise_level`（0〜1、既定 0.7）、`inpaint_start`・`inpaint_end`（秒）、`keep`。省略と `null` は既定。未知の項目は 400 |
| 成功（200） | 本文は `audio/wav`（44.1 kHz・ステレオ・16 bit）。ヘッダ `X-Saudio-Mode`・`X-Saudio-Seed`・`X-Saudio-Duration-Sec`・`X-Saudio-Elapsed-Sec`・`X-Saudio-Sample-Rate`・`X-Saudio-Channels`・`X-Saudio-Model`、`keep` が真なら `X-Saudio-File` |
| 誤り | 400（入力の誤り。元の音が読めない・範囲が合わないも含む）・415（`Content-Type` が JSON でない。CSRF 対策）・405（GET など）・503（混雑。順番待ちを含めて 4 件まで、`Retry-After: 30`）・500（生成の失敗） |

Gradio の API（`gradio_client`）も使える。`api_name` は `/generate_text`（`prompt, duration, seed, steps`）・
`/generate_audio`（`…, source_audio, init_noise_level`）・`/generate_inpaint`（`…, source_audio, inpaint_start, inpaint_end`）。

## ライセンス

- 上流のコード（`stable-audio-3`）は MIT。
- **重みは Stability AI Community License**。年間の収益が 100 万米ドル未満の個人・組織は商用にも使える（超える場合は Enterprise License）。
  文章の符号化器 T5Gemma（`t5gemma-b-b-ul2/`）は Gemma の利用規約に従う。重みは Hugging Face で **gated**（規約への同意が要る）で、このリポジトリには含めない。

## 導入

```bash
cd ~/Proj/localai/tools/stable-audio
./setup-server.sh                  # venv 約 6 GB + 重み 約 10.5 GB。トークンが無ければ重みの手前で止まり、手順を出す
./setup-server.sh --skip-models    # 重みは後で（run-stable-audio fetch-models）
./setup-server.sh --skip-gpu-check # 最後の点検で GPU を触らない
```

`./localai install stable-audio` でも同じ。sudo は使わない。冪等。行うこと:

1. uv の Python 3.12 の venv
2. 上流を `vendor/stable-audio-3` に**固定コミット**（`790793b8…`、2026-11-02 の main。タグは無い）で取得
3. torch・torchaudio 2.7.1 を `https://download.pytorch.org/whl/cu126` から（上流の固定。wheel が CUDA 12.6 を同梱）
4. `requirements.lock.txt` の依存（gradio 5.33.0・transformers 5.x・huggingface-hub 2.x・numpy 2.x など）
5. 上流（`--no-deps`）、**flash-attn 2.8.3**（公式リリースの wheel `cu12torch2.7cxx11abiTRUE-cp312`。Medium の音声圧縮モデル SAME-L が必須とする）、`saudio`
6. 重み（`stabilityai/stable-audio-3-medium` のリビジョン `d4d35793…` の `model.safetensors` 9.2 GB・`model_config.json`・`t5gemma-b-b-ul2/`）
7. systemd user unit `stable-audio`（**enable しない**）と env ファイル `~/.config/stable-audio/stable-audio.env`
8. `run-stable-audio doctor`

### Hugging Face のトークン（初回だけ、利用者の操作）

1. https://huggingface.co/stabilityai/stable-audio-3-medium を開き、ログインして規約に同意する（自動で承認される）
2. https://huggingface.co/settings/tokens で Read 権限のトークンを作る
3. サーバーでトークンを置く（`read -s` で画面に出さない）:
   ```bash
   install -d -m 700 ~/.config/stable-audio
   ( umask 077; read -rsp 'HF token: ' token; echo; printf '%s\n' "$token" > ~/.config/stable-audio/hf-token )
   ```
4. `tools/stable-audio/run-stable-audio fetch-models`

トークンは重みを取るときだけ使う。起動中は `HF_HUB_OFFLINE=1` で Hugging Face に問い合わせない。
上流のコードは revision を付けずに `main` を読むので、`fetch-models` は固定のリビジョンを取ったあと、キャッシュの `refs/main` をそのリビジョンに向ける。

## CLI（`run-stable-audio`）

| コマンド | 内容 |
|---|---|
| `serve` | 画面と REST を起動する（systemd から呼ばれる）。VRAM が空くのを 60 秒待ち、重みが揃っていなければ中止する |
| `doctor [--skip-gpu]` | 点検。venv・torch 2.7.1+cu126・flash-attn・上流の import とコミット・重み（refs/main を含む）・VRAM・ポート・unit |
| `fetch-models` | 重みを取得する（gated。トークンが無ければ手順を出して終了コード 3） |
| `generate --prompt "..." [--duration N] [--mode text\|audio\|inpaint] [--source PATH] [--init-noise-level X] [--inpaint-start S] [--inpaint-end E] [--seed N] [--steps N] [--out PATH]` | 画面を通さずに 1 件生成する。毎回モデルを読み込む |

## 設計の決定

- **自前の Gradio 画面と REST**（`saudio/ui.py`・`saudio/rest.py`）。上流の `run_gradio.py` は共有リンク（share）を作るので使わない。
  作りは `tools/audiocraft` と同じ（REST の経路を Gradio のアプリの先頭へ足す、`Content-Type: application/json` 以外は 415）。
- **音の読み書きは soundfile。** この機体には ffmpeg が無い。元の音は soundfile で読み、上流へは `(サンプルレート, [チャンネル, サンプル])` の組で渡す
  （リサンプル・ステレオ化は上流の `prepare_audio`）。
- **専用の venv。** 上流が torch 2.7.1 に固定し、transformers 5.x・numpy 2.x を要る。AudioCraft（torch 2.1.0・numpy 1.x）とは共存できない。
- **GPU の排他は 2 層。** 入口ページ（`GPU_EXCLUSIVE_KEYS`・`OLLAMA_UNLOAD_BEFORE_START_KEYS`）と、unit の `Conflicts=`/`After=`
  （ほかの GPU ユニット 10 個。`After=` はこちら側だけ）。
- 重み・キャッシュはすべて `models/` の下（`HF_HOME`・`HF_HUB_CACHE`・`TORCH_HOME`）。トークンは `~/.config/stable-audio/hf-token`（600）。

## ファイル構成

| ファイル | 役割 |
|---|---|
| `setup-server.sh` | 導入（冪等・非対話・sudo なし） |
| `run-stable-audio` | CLI の入口（venv の python で `saudio.cli`） |
| `saudio/config.py` | パス・ポート・上流と重みのリビジョン（定数のみ） |
| `saudio/params.py` | 生成パラメータの検証・出力名・メタデータ（標準ライブラリのみ） |
| `saudio/cli.py` | `serve` / `doctor` / `fetch-models` / `generate`（標準ライブラリのみ） |
| `saudio/engine.py` | モデルの読み込みと生成・wav の書き出し（torch・上流は関数の中で import） |
| `saudio/ui.py` | Gradio 画面（gradio を import するのはここだけ）。起動後に REST の経路を足す |
| `saudio/rest.py` | REST の口（fastapi。gradio・torch は import しない） |
| `saudio/help_texts.py` | 画面の日本語の説明文 |
| `saudio/ollama.py` | VRAM の空き確認 |
| `systemd/stable-audio.service.example` | unit の雛形 |
| `requirements.lock.txt` | 依存の固定（torch・flash-attn は別に入れる） |
| `tests/test_units.py`・`tests/test_rest.py` | GPU を使わない試験 |
| `tests/test_generate_real.py` | 実機試験（GPU と重みを使う） |

`.venv/`・`vendor/`・`models/`・`outputs/`・`logs/` は追跡しない（`.gitignore`）。

## テスト

```bash
uvx --with pytest --with fastapi --with httpx pytest tools/stable-audio/tests/test_units.py tools/stable-audio/tests/test_rest.py -q
# ./localai test --only stable-audio でも

# 実機（GPU と重みを使う。先に systemctl --user stop stable-audio）
tools/stable-audio/.venv/bin/python tools/stable-audio/tests/test_generate_real.py
```

## 実測（この機体・A30、2026-11-23）

重みがメモリのキャッシュに載っている状態（取得した直後）。入口ページ・systemd から起動し、REST で生成した。

| 項目 | 値 |
|---|---|
| 起動からポート 7864 が開くまで | 約 17 秒（モデルの読み込み 12.9 秒） |
| VRAM（待機時） | プロセス 9,266 MiB（nvidia-smi）。torch の割り当て 4,449 MiB |
| VRAM（生成中のピーク） | nvidia-smi の全体で 9,700〜10,052 MiB（380 秒のとき最大）。torch のピーク 5.18 GiB（30 秒） |
| RAM（cgroup の memory.peak） | 9.70 GiB（読み込み中。重み 9.2 GB を f32 で読むため）。生成では増えない |
| 生成時間（REST） | 10 秒 0.5 秒、30 秒 0.6〜1.2 秒、120 秒 1.6 秒、380 秒 5.5 秒。音 → 音 30 秒 0.8 秒、続き（元 10 秒 → 30 秒）0.8 秒 |
| 出力の wav | 長さは指定どおり、44,100 Hz・ステレオ。音楽はサンプルの最大 約 0.5% が ±1 で切れている（上流が clamp する。音量の正規化はしない） |

- 続き（`inpaint`、開始 10 秒）の出力の先頭 10 秒は、元の音との差が RMS 0.026 で、ほぼそのまま残る。
- 上流の `generate` の既定 `sample_size`（5,292,032 = 120 秒）のままだと、長さ 380 秒を頼んでも 120 秒で切れた。モデルの設定の
  `sample_size`（16,777,216 = 約 380.4 秒）を渡して直した（`saudio/engine.py`）。
- 排他: stable-audio の稼働中に入口ページから audiocraft を起動すると `"stopped": ["stable-audio"]` で止まり、audiocraft の稼働中に
  `systemctl --user start stable-audio` すると `Conflicts=` で audiocraft が止まった。Ollama に gemma4:e4b を載せて入口ページから起動すると
  `"unloaded_models": ["gemma4:e4b"]` が入り、起動後の `ollama ps` は空だった。
- 重みが無いまま起動されると、以前は 5 秒おきに再起動を続けた（2026-11-23 に 462 回）。今は終了コード 78 で止まり、unit の
  `RestartPreventExitStatus=78` で再起動しない。
- 確かめていないこと: 生成した音の聴感、ブラウザでの画面の表示、他の PC からの到達（ufw 7864 が未実施）、ディスクが冷えた状態の起動時間。

## 元に戻す

```bash
systemctl --user stop stable-audio
rm ~/.config/systemd/user/stable-audio.service && systemctl --user daemon-reload
rm -r ~/.config/stable-audio          # トークンも消える
# venv・上流・重みは tools/stable-audio の .venv / vendor / models を消す（再作成は setup-server.sh）
```
