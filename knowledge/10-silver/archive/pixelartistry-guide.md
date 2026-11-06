# pixelartistry-guide

入口ページ（8080）から開ける HTML「PixelArtistry の使い方（Pixal3D / TRELLIS.2）」を `tools/portal/guides/pixelartistry.html` に作る。
計画: `~/.claude/plans/lively-dazzling-pancake.md`。

## 完了条件

- `http://<server-ip>:8080/guides/pixelartistry.html` が 200 で返り、入口ページの ComfyUI のカードからリンクで開ける（`../` などは 404）。
- ガイドに、Pixal3D と TRELLIS.2 の違い・切り替え方（ノード「Boolean (Switch to Trellis2)」）・ワークフロー別の手順・実測の目安がある。
- 画像から形を作る段を TRELLIS.2 で流した実測（01・02・Image_to_3D・01b、斧のサンプル）がガイド・README・`areas/comfyui.md` にある。
- 写しの日本語ノートからガイドを案内し、`adapt_workflows.py` の 2 回目は「同じ内容なので触りません」。
- `TZ=UTC ./localai test` が全部 OK。`comfyui.service` は停止して終える。

## 経緯

- 2026-11-03 開始。TRELLIS.2 は `tools/comfyui` に導入済みだが、画像から形を作る段を TRELLIS.2 で流したことはこの機体で一度もなかった
  （`localai_runs/` の 19 回のうち TRELLIS.2 は 03 の 2 回だけ）。
- 08:32 に GPU が空いている（使用率 0%・他のプロセスなし）ことを確かめ、`logs/gpu-tests.sh trellis`（ログ `logs/gpu-tests-20261103.log`）を流し始めた。

## 結果（2026-11-03 09:15）

- TRELLIS.2 の実機試験（08:32〜08:57）: 01・01b・Image_to_3D は成功、02 は最後まで動くが低ポリが閉じない（境界辺 36）。
  数字は `tools/comfyui/README.md` の「TRELLIS.2 で形を作った実機試験」と `areas/comfyui.md`。
- `tools/portal/guides/pixelartistry.html` を作り、`portal.py` に `GET /guides/<名前>.html` を足して入口ページを再起動。
  実物で 200、`/guides/../portal.py`・`%2e%2e`・無い名前・`/guides/` は 404。ComfyUI のカードに「使い方」のリンクが出る。
- Brave のヘッドレス（`--headless --disable-gpu`。`--headless=new` は 60 秒で応答せず）で、入口ページとガイドの明・暗を PNG に写して確かめた。
- 写しの日本語ノートにガイドの案内・スイッチのノード名・02 の TRELLIS.2 の注意を足し、`adapt_workflows.py` の 2 回目は 4 本とも「触りません」。
- `TZ=UTC ./localai test` は OK 11 / FAIL 0 / SKIP 3。`comfyui.service` は停止済み。
