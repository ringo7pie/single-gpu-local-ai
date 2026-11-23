# audio-source-stable-audio

AudioCraft（7862）に「元の音」を指定するモード（続き生成: 作曲・効果音、メロディ指定: musicgen-melody）を足し、
Stable Audio 3.0 Medium を新しいツール `tools/stable-audio`（ポート 7864、unit `stable-audio`）として入れ、入口ページから使えるようにする。
計画: `~/.claude/plans/audiocraft-medium-stable-mighty-hennessy.md`。

## 完了条件

- AudioCraft の画面・REST・CLI で `continuation`（作曲・効果音）と `melody`（作曲）が使え、実機で wav を作れる。
- `tools/stable-audio` が導入でき（`doctor` 合格）、文章→音・音→音・部分差し替え/続きで wav を作れる。入口ページから起動・停止でき、GPU 排他が効く。
- GPU を使わない試験（`./localai test`）が通る。実測（VRAM・RAM・起動・生成時間）とナレッジ・README を更新。

## 経緯

- 2026-11-23 開始。Stable Audio 3.0 Medium の HF リポジトリは gated で、この機体に HF のトークンが無い（ユーザーの操作が要る）。

## 進捗（2026-11-23）

- 済み: AudioCraft の元の音（続き・メロディ）の実装・試験・導入（demucs・melody の重み）・実機試験 5 件・サービスとしての実測（`areas/audiocraft-source.md`）。
  `tools/stable-audio` 一式（コード・試験 82 件・setup-server.sh で venv・flash-attn・unit まで導入、doctor は重み以外 OK）。
  入口ページ・localai・文書の更新。`./localai test` は OK 13 / FAIL 0 / SKIP 5。入口ページ（portal）は新しいコードで再起動済み。
- 残り: (1) HF のトークン（ユーザーの操作）→ `run-stable-audio fetch-models` → doctor・実機試験・入口ページからの起動と実測・
  排他（audiocraft ⇔ stable-audio）の確認 → 実測を README・index.html・hardware-requirements・`areas/stable-audio.md` に反映、メモリ上限を見直す。
  (2) ナレッジの食い違いは承認を得て直した（2026-11-23）。7864 は Stable Audio が使い、Base の手動起動は別のポートにする。
  (3) ufw 7864（ユーザーの sudo）。

## 2026-11-23 の完了

- HF の規約への同意（ユーザーが Chrome で入力）のあと、重みを取得した。doctor は全項目 OK。
- 実機試験 3 モード合格。REST で 10〜380 秒・音 → 音・続きを生成し、実測を README・入口ページ・`areas/stable-audio.md` に反映した。
- 380 秒が 120 秒で切れる不具合（上流 `generate` の既定 `sample_size`）を直し、重みが無いときの再起動の繰り返し（462 回）を
  終了コード 78 と `RestartPreventExitStatus=78` で止めた。
- 排他（両方向）と Ollama のモデルを降ろすことを確かめた。`./localai test` は OK 13 / FAIL 0 / SKIP 5。
- 残りはユーザーの操作だけ: ufw でポート 7864 を LAN 2 セグメント分開ける（sudo）。
