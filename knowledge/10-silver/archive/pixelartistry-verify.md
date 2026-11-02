# pixelartistry-verify

PixelArtistry-Watertight-Meshes v1.1（`tools/comfyui/`、導入済み）を「この機体で使える」状態に仕上げる。
計画: `~/.claude/plans/lively-dazzling-pancake.md`。

## 完了条件

- `./localai test` が全部 OK。`run-comfyui doctor`（自己試験込み）が全項目 OK。
- 01・02・03 が 2026-11 の実機で成功し、結果（時間・RAM・VRAM・防水の指標）が README と `areas/comfyui.md` にある。
- 01b（2K）がこの機体で通る値で写されている（または「通らない」と根拠つきで記録されている）。
- 4 本の写しに日本語の手順ノートがあり、`adapt_workflows.py` を 2 回流すと 2 回目は「同じ内容なので触りません」。
- `comfyui.service` は停止して終える。

## 経緯

- 2026-11-02 開始。GPU を使わない点検（`CUDA_VISIBLE_DEVICES=` の doctor、単体試験 101 + 14 件）は全て OK。
  上流（PixelArtistry・WTiVo 姉妹 repo・WTiVo ノード）に 2026-10-02 以降の更新は無い（`git ls-remote` で確認）。
- 開始時、GPU は `~/Proj/USDFormat/Proj/Real2SimGenerator2` の Isaac Lab（`p2_policy_walk.py`、2 プロセス・VRAM 約 8.6 GiB・
  使用率 91%）が使っていた。GPU を使う試験は、そのプロセスが消えて使用率 0% が続いてから行う（ユーザー指示）。
  待つ処理は `tools/comfyui/logs/gpu-tests.sh`（git 管理外）。
- 2026-10-03 の 01b（2K、proxy_points 25M）は、WTiVo が 82.6M 面を作る途中で anon 40 GiB に達し、`--guard-mem-gib 42` で中断した。

## 進捗（2026-11-02 18:50 時点）

済み（GPU 不要）:
- 写しへの日本語の手順ノート・初期表示・01b の `proxy_points`（暫定 12,000,000）を `cfui/workflows.py`（`localize_pixelartistry` など）・
  `cfui/notes_ja.py`・`scripts/adapt_workflows.py` に実装。本物の写し 4 本を更新（予備案 3 本と Image_to_3D は不変を sha256 で確認）。
- 単体試験 119 件 + メッシュ計測 14 件が通る。`./localai test` は comfyui を含む 10 件が OK、model-eval だけ FAIL
  （`Documents/3D問題_実施モデル一覧_2026-10-08.md` が `Documents/Back/` へ移されたまま。今回の変更とは無関係）。
- `test_run_workflow.py::test_real_workflows_convert_when_vendor_exists` が導入済みの 01b の 25M を決め打ちしていたので、期待値を
  `config.WTIVO_2K_PROXY_POINTS` に合わせた。

待ち: 18:41 ごろ USDFormat が新しいジョブを 3 本（`isaac_watchdog.sh` + `p2_policy_walk.py`、各 VRAM 約 3.6 GiB、MPS サーバーも作り直し）始め、
GPU 使用率は 97%。`tools/comfyui/logs/gpu-tests.sh wait`（ログは `logs/gpu-tests-20261102.log`）が空くのを待っている。

残り: doctor 自己試験 → 起動 → 01・02・03 → 01b の梯子（12M → 16M → 20M）→ 最終確認 → 停止 → 実測の反映 → アーカイブ。

## 進捗（2026-11-03 05:48 時点）

- 2026-11-03 05:46 に USDFormat の最後のプロセスが消え、05:47:38 に使用率 0% が 3 回続いた（待ちは 2026-11-02 18:50 から約 11 時間）。
- 05:48 から doctor（自己試験込み）→ 起動 → 01・02・03 を流している。

## 結果（2026-11-03 06:45）

- doctor（自己試験込み）全項目 OK。01・02・03 は成功（240 / 374 / 174 秒、最終 GLB は境界辺 0・非多様体辺 0）。
- 01b の梯子: 12M・16M・20M は成功、配布元の 25M は WTiVo の出力が 82.55M 面に跳び LODTailor の Blender（RSS 32.3 GiB）で RAM の歯止めに当たった。
  `WTIVO_2K_PROXY_POINTS = 20_000_000` にして写し直し、その写しをそのまま流して成功（361 秒、memory.peak 34.3 GiB）。
- 日本語ノートの目安を実測に更新し、`adapt_workflows.py` を 2 回流して 2 回目は 4 本とも「同じ内容なので触りません」。予備案 3 本と Image_to_3D は sha256 で不変。
- 単体試験 119 件 + メッシュ計測 14 件が通る。`./localai test` は OK 10 / FAIL 1 / SKIP 3。FAIL は model-eval（`Documents/3D問題_実施モデル一覧_2026-10-08.md` が
  利用者の手で `Documents/Back/` へ移されたため。作業の前から同じで、この作業の変更とは関係しない）。完了条件の「全部 OK」はこの 1 件を除いて満たす。
- 事実は `areas/comfyui.md`（PixelArtistry の 4 本）・`tools/comfyui/README.md`・`docs/hardware-requirements.md` に反映。`comfyui.service` は停止済み。
