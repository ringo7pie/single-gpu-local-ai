# audiocraft-source

audiocraft（`tools/audiocraft`、7862）で「元の音」を指定するモードの事実。2026-11-23 に足した。
モデル全体・排他は `areas/audiocraft.md`、REST の作りは `areas/audiocraft-api.md`、手順は `tools/audiocraft/README.md` を Read する。

## モード

- `continuation`（作曲・効果音）は上流の `generate_continuation` を使う。出力は元の音を含み、全体の長さが `duration` になる。
- `melody`（作曲のみ）は別モデル `facebook/musicgen-melody`（1.5B、重み 2.9 GB、HF snapshot `68d653a9`）の `generate_with_chroma` を使う。
  元の音は先頭から `duration` の分だけ使う。`ACRAFT_MELODY_MODEL=none` で読み込まない。
- REST の本文は `mode`（`text`・`continuation`・`melody`）と `audio_base64`、応答ヘッダは `X-Acraft-Mode`。
  効果音に `melody`、元の音が読めない、長さが決まりに合わないときは 400。

## 元の音の決まり

- ファイルは 20 MB・60 秒まで。続きの元の音は「長さ − 1 秒」以下で、作曲 29 秒・効果音 8 秒まで。
  上流は元の音が min(長さ, 1 回で作れる長さ。MusicGen 30 秒・AudioGen 10 秒) 以下であることを assert で要求する。
- 元の音は soundfile で読む（venv の libsndfile 1.2.2 は wav・flac・ogg・mp3 を読める）。ffmpeg と av は使わない。
- 元の音の写しを出力の隣に `<名前>-source.wav` で残す。
- 画面はアップロードだけにした。ブラウザは https か localhost でないとマイクを使わせない。

## demucs（メロディ用）

- `ChromaStemConditioner` は demucs の `htdemucs` でドラムとベースを除いてからクロマを取る。
- demucs 4.0.1 は `--no-deps` で入れる。import に要るのは openunmix 1.3.0（`hdemucs.py`）と lameenc 1.9.0（`audio.py`）だけ（2026-11-23 に import で確かめた）。
- htdemucs の重みは `955717e8-8726e21a.th`（81 MB、`dl.fbaipublicfiles.com/demucs/hybrid_transformer/`）。`models/torch/hub/checkpoints/` に置く。

## 実測（A30、2026-11-23。重みはキャッシュ済み、入口ページから起動）

- ポートが開くまで約 17 秒（3 モデル）。
- VRAM は待機時にプロセスで 13,514 MiB。生成中のピークは nvidia-smi の全体で 16,224 MiB。
- RAM は cgroup の memory.peak が起動直後 4.36 GiB、生成 4 件の後 4.99 GiB。MemoryHigh 16G / MemoryMax 20G のまま足りる。
- REST の生成時間は、作曲 10 秒が 9.9 秒、続き 15 秒（元 3 秒）が 11.9 秒、メロディ 30 秒が 28.1 秒、効果音の続き 8 秒が 4.9 秒。
- 生成した音の聴感（続きのつながり・メロディの似かた）は確かめていない。
