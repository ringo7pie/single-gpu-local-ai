# audiocraft-portal

音楽・効果音生成（Meta AudioCraft の MusicGen Medium / AudioGen Medium）を `tools/audiocraft/`（ポート 7862、
systemd user unit `audiocraft`）として導入し、入口ページ（tools/portal）から起動・停止・利用できるようにする。
計画: `~/.claude/plans/pasted-content-id-8ddd-http-192-168-8-1-merry-gadget.md`。

## 完了条件

- `./localai test` が全部 OK（新規 `audiocraft` を含む）。
- `run-audiocraft doctor` 合格。入口ページのカードから起動・停止できる。
- 実機で music / sound の wav を生成し、VRAM ピーク・RAM・読み込み時間・生成時間を記録。
- ナレッジ（areas/audiocraft.md・web-services.md・lan-access.md）、README・NOTICE・CLAUDE.md に反映。

## 経緯

- 2026-10-27 開始。GPU は USDFormat（Isaac Lab）の別セッションが使用中のため、GPU を使う手順は空くまで待つ。

## 2026-10-27 の事実（この作業単位の中だけで要るもの）

- 導入作業の間、GPU は `~/Proj/USDFormat/Proj/Real2SimGenerator2` の Isaac Lab 学習（pid 3249136、VRAM 約 2.6 GiB、
  使用率 78%）が使っていた。GPU を使う試験（起動・生成・VRAM の測定）は、使用率が 0% になり、そのプロセスが
  消えてから行う。待つ処理は `tools/audiocraft/logs/wait-gpu-idle.sh`（0% が 3 回続くまで 30 秒おきに見る）。
- GPU を使わない確認として、`generate --device cpu`（`CUDA_VISIBLE_DEVICES` を空にする）で短い音を作れる。
- auto モードの判定が、上流（`vendor/audiocraft`）のコードを `uv pip install -e` で入れて import する操作を
  「外部のコード」として止めた。これが通らないと `setup-server.sh` の [5/8] 以降（上流の導入・doctor・生成）に進めない。
  `fetch-models`（重みの取得）と、acraft 自身の試験・入口ページの変更は止められなかった。

## 進捗（2026-10-27 時点）

済み: `tools/audiocraft/` 一式（単体試験 69 件）、入口ページ（portal.py・index.html・試験）、`localai`・scripts/tests、
README・NOTICE・CLAUDE.md・docs、`.gitignore`、重みと T5 の取得（8.2 GB）、venv に torch 2.1.0+cu121 と依存、
入口ページの再起動（カードは「未導入」で出ている）。`./localai test` は audiocraft を含む 10 件が OK。
model-eval だけ FAIL（`Documents/3D問題_実施モデル一覧_2026-10-08.md` が `Documents/Back/` へ移されたままで、今回の変更とは無関係）。

残り（上流のコードを動かす操作が止められたため、ユーザーが `! tools/audiocraft/setup-server.sh --skip-models --skip-gpu-check`
を実行するのを待つ）:
1. `setup-server.sh` の [5/8]（上流と acraft の editable 導入）・[7/8]（unit の配置）・[8/8]（doctor --skip-gpu）。
2. GPU を使わない確認: `tests/test_generate_real.py --device cpu --music-seconds 2 --sound-seconds 2`。
3. GPU が空いてから（`logs/wait-gpu-idle.sh` が知らせる）: 起動・実機試験・VRAM と RAM の実測・排他の確認。
4. 実測を `areas/audiocraft.md`・README・index.html の用語集に反映し、ufw（ユーザーが sudo で）を済ませたら `/archive-work audiocraft-portal`。

## 2026-10-27 の完了（上の「進捗」の残りの結果）

- 上流の導入を止めた判定は、ユーザーが明示的に許可した（AskUserQuestion の回答「私が実行してよい」）あとに通った。
  `setup-server.sh --skip-models --skip-gpu-check` は終了コード 0、doctor は全項目 OK。
- GPU は 15:21 ごろに空いた（USDFormat の学習が終わった）。実機試験・サービスとしての測定・排他の確認・CPU での確認を済ませた。
  実測値は `areas/audiocraft.md`。メモリ上限の既定は実測に合わせて 16 / 20 GiB にした（雛形・入口ページ・設置済みユニット）。
- 残りはユーザーの操作だけ: ufw でポート 7862 を LAN 2 セグメント分開ける（sudo）。開けたら `resources/lan-access.md` の表を直す。
