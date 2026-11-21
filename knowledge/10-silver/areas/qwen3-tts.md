# qwen3-tts

声の特徴を文章で指定して読み上げる Qwen3-TTS VoiceDesign（`tools/qwen3-tts`、ポート 7863、systemd user ユニット `qwen3-tts`、enable しない）。
2026-11-21 に導入した。導入・使い方・設定は `tools/qwen3-tts/README.md` を Read する。ポートは `areas/web-services.md` の表を Read する。

## 上流とモデル

- 上流は PyPI の `qwen-tts` 0.1.1（QwenLM/Qwen3-TTS、Apache-2.0）。画面は上流の `qwen-tts-demo`（Gradio 6.17.3）をそのまま使い、自作しない。
- モデルは `Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign`（Apache-2.0）。HF の sha `5ecdb67327fd37bb2e042aab12ff7391903235d3` に固定し、
  `tools/qwen3-tts/models/` に取る（4.3 GB）。音声の符号化器 `speech_tokenizer/` も同じリポジトリに入っている。
- 10 言語（日本語を含む）。出力は 24,000 Hz。声の特徴の説明は日本語でも音声が出る。聴いての質は確かめていない。

## 環境

- venv は uv の Python 3.12、torch 2.9.1+cu126・torchaudio 2.9.1+cu126（wheel が CUDA を同梱）、transformers 4.57.3。venv は 6.6 GB。
- `qwen-tts-demo` は flash-attn を既定で使おうとする。flash-attn は入れていないので、`run-qwen3-tts serve` は必ず `--no-flash-attn` を渡す（sdpa）。
- 上流の既定の同時生成数は 16。GPU 1 枚で並べないよう `--concurrency 1` を渡す。
- `sox` の実行ファイルはこの機体に無く、起動時に警告が出る。上流で sox を使うのは 25Hz 版の符号化器だけで、12Hz 版の生成には要らない。
- Gradio の API は `/run_voice_design`（引数 `text`・`lang_disp`・`design`）。認証は無い。

## 入口ページと排他

- 入口ページの GPU 排他グループの 10 番目で、起動前に Ollama のモデルを降ろす対象にも入れた。
- unit は `Conflicts=` と `After=` にほかの GPU ユニット 9 つ（hunyuan3d・pixal3d・comfyui・Strata 5 種・audiocraft）を持つ。
  `After=` は qwen3-tts 側だけに書き、ほかの unit は変えていない。
- 排他は 2 層とも実機で効いた（2026-11-21）。入口ページから audiocraft を起動すると qwen3-tts が止まり、gemma4:e4b を載せた状態で
  入口ページから qwen3-tts を起動すると audiocraft が止まり `unloaded_models` に gemma4:e4b が入った。systemctl の直接の start でも相手が止まった。

## 実測（A30、2026-11-21。重みがメモリのキャッシュにある状態）

- 起動からポート 7863 が開くまで 5.7〜6.4 秒。ディスクが冷えた状態は測っていない。
- VRAM（nvidia-smi のプロセス）は待機時 4,376 MiB、生成 3 回の後 5,478 MiB。torch の割り当てのピークは 4,289 MiB（実機試験）。
- RAM は cgroup の memory.peak 1.61 GiB（起動と生成 3 回の後）。メモリ上限の既定は MemoryHigh 6G / MemoryMax 8G。
- 生成時間は音声の長さより少し短い。日本語 44 文字 → 音声 7.84 秒を 6.9 秒、日本語 80 文字 → 23.36 秒を 19.4 秒、英語 70 文字 → 6.80 秒を 5.6 秒。

## Base（声のクローン）の手動起動

- 2026-11-21 にユーザーの指示で Base（`Qwen/Qwen3-TTS-12Hz-1.7B-Base`）を `qwen-tts-demo ... --ip 0.0.0.0 --port 7864 --no-flash-attn --concurrency 1` で手動起動した。
  重みは `HF_HOME=tools/qwen3-tts/models/hf` に取った（4.3 GB）。VRAM 約 4.4 GiB で、VoiceDesign と同時に載った。
- これは systemd のユニットでも入口ページの管理下でもない。GPU の排他に入らず、PC の再起動やセッションの終了で止まる。
- 上流は、Base の画面を別の PC のブラウザからマイクで使うには HTTPS（`--ssl-certfile`・`--ssl-keyfile`）が要ると書いている。

## 他の PC から使う

- ufw でポート 7863 を LAN 2 セグメント分開けた（2026-11-21、ユーザーが sudo で実施）。手順は `knowledge/10-silver/resources/lan-access.md` を Read する。
