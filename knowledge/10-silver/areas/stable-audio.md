# stable-audio

文章から曲・効果音を作る Stable Audio 3.0 Medium（`tools/stable-audio`、ポート 7864、systemd user ユニット `stable-audio`、enable しない）。
導入と使い方は `tools/stable-audio/README.md` を Read する。2026-11-23 に導入した。

## モデルとライセンス

- 自前の Gradio 画面と REST（`POST /api/v1/generate`）。モードは `text`、`audio`（上流の `init_audio`。`init_noise_level` 既定 0.7）、
  `inpaint`（`inpaint_start`・`inpaint_end`。開始を元の音の長さにすると続き）。長さ 1〜380 秒、44.1 kHz ステレオ、`steps` 既定 8。
- `cfg_scale`・`negative_prompt` は base モデルにしか効かない（上流 `docs/workflows/inference.md`）ので出さない。
- 重みは Stability AI Community License（年間の収益が 100 万米ドル未満は商用も可）。文章の符号化器 T5Gemma は Gemma の利用規約。上流のコードは MIT。

## 上流と venv

- 上流 Stability-AI/stable-audio-3 は固定コミット `790793b89ddceea612be9ce42ff6830d18f6fa57`（2026-11-02 の main。タグは無い）。
- 上流は `torch==2.7.1`・`torchaudio==2.7.1`（cu126）に固定し、transformers>=5.8・huggingface-hub>=1.7.1・numpy>=2.2.6 を要る。AudioCraft の venv とは共存できない。
- Medium は音声圧縮モデル SAME-L のため flash-attn が必須。venv は Python 3.12、flash-attn は公式の wheel
  v2.8.3 `cu12torch2.7cxx11abiTRUE-cp312` を使う（torch 2.7.1+cu126 の `_GLIBCXX_USE_CXX11_ABI` は True だった）。
- gradio は 5.33.0 で解決した（上流の ui extra は 6.3.0）。huggingface-hub 2.4.0 が requests を依存から外したので、requests を明示する。

## 重み（gated）

- `stabilityai/stable-audio-3-medium` は gated（`gated: auto`。規約に同意すると自動で承認される）。リビジョン `d4d3579395a8ac0df4ab13b5b1d7138ce7290f81`（lastModified 2026-10-06）。
- 取るのは `model.safetensors` 9.22 GB・`model_config.json`・`t5gemma-b-b-ul2/`（`model.safetensors` 1.18 GB とトークナイザ）。
- 上流は revision を付けずに main を読むので、`fetch-models` は固定のリビジョンを取ったあと、キャッシュの `refs/main` をそれに向ける。起動中は `HF_HUB_OFFLINE=1`。
- トークンは `~/.config/stable-audio/hf-token`（600）か環境変数 `HF_TOKEN`。2026-11-23 に重みを取得した（`models/hf` で 9.8 GB）。
- fine-grained のトークンでも「アクセス可能なゲート付きリポジトリの内容を読む」が付いていれば取れる。規約に未同意だと 403（GatedRepoError）。
  同意のフォームは氏名・メール・国・所属・利用目的を求める。

## 入口ページと排他

- 入口ページの GPU 排他グループの 11 番目で、起動前に Ollama のモデルを降ろす対象にも入れた。
- unit の `Conflicts=` と `After=` に、ほかの GPU ユニット 10 個を書く。`After=` は stable-audio の側だけ。メモリ上限は 16G / 20G。
- 両方向の排他と、起動前に Ollama のモデルを降ろすことを実機で確かめた（2026-11-23）。
- 重みが無いと `serve` は終了コード 78 で止まり、unit の `RestartPreventExitStatus=78` で再起動しない。この指定が無かったときは 5 秒おきに 462 回再起動した。

## 実測（A30、2026-11-23。重みはキャッシュ済み）

- ポートが開くまで約 17 秒（読み込み 12.9 秒）。VRAM は待機時にプロセスで 9,266 MiB、生成中のピークは全体で 9,700〜10,052 MiB。
- RAM の cgroup memory.peak は 9.70 GiB（読み込み中。生成では増えない）。
- 生成は速い。30 秒が 0.6〜1.2 秒、120 秒が 1.6 秒、380 秒が 5.5 秒。音 → 音・続き（30 秒）は約 0.8 秒。
- 上流 `generate` の既定 `sample_size` は 120 秒分（5,292,032）で、そのままだと 380 秒を頼んでも 120 秒で切れる。モデルの設定の値（16,777,216）を渡す。
- 出力は 44.1 kHz ステレオ。音楽はサンプルの最大 約 0.5% が ±1 で切れる（上流が clamp する）。聴感は確かめていない。
- `init_noise_level` は元の音に足すノイズの量で、大きいほど元から離れる（上流 `inference/sampling.py`: `init_data * (1 - sigma_max) + noise * sigma_max`）。
  2026-11-23 に 6 秒のギターを元に別の説明文で作り変えて実測した、元との波形の相関: 0.0 で 0.999（元とほぼ同じ）、0.1 で 0.949、0.3 で 0.87、0.5 で 0.41、0.7 で 0.00、1.0 で 0.00。
  画面のラベルは当初「元の音の残し具合」で向きが逆に読めたため、同じ日に「作り変える強さ」に改めた。
