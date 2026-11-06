# comfyui-measurements

ComfyUI（`tools/comfyui`）をこの機体（A30、`--free-before`、`run_workflow.py run`）で流した実測。
入口・切り替え・毎回の運用で効く事実は `knowledge/10-silver/areas/comfyui.md` を Read する。
memory.peak は cgroup の中の Blender とファイルのキャッシュを含む。表と根拠は `tools/comfyui/README.md` を Read する。

## Image_to_3D（2026-10-25、Pixal3D）

- GLB は Blender 5.2.2（CPU）で描画して形を確かめた。
- `viking_wolf_rune_axe.png` の既定は 167〜173 秒、memory.peak 17.5 GiB、VRAM ピーク 12,598 MiB、698,096〜698,185 面・PBR 4096px
  （同じ seed でも 89 面違う）。`D_superhero_female.png` は 111.9 秒、memory.peak 18.0 GiB、VRAM ピーク 10,426 MiB。
- `E_copper_coin.png` の 30,000 面・2048px は 370.8 秒（memory.peak 20.4 GiB、VRAM 16,412 MiB）。29,796 面、全テクスチャ 2048px。
- 斧の 50,000,000（間引かない）・8192px は 235.9 秒（memory.peak 22.8 GiB、VRAM 12,572 MiB）。3,353,172 面、
  ベースカラー・金属粗さ・AO 8192px、法線 2048px、GLB 239.7 MB。

## PixelArtistry の 4 本（2026-11-03、Pixal3D。03 は TRELLIS.2）

- doctor（自己試験込み）は全項目 OK。01 は 240 秒・memory.peak 23.2 GiB、02 は 374 秒・26.9 GiB、03 は 174 秒・23.0 GiB、VRAM は 7.6〜10.9 GiB。
  最終 GLB はどれも境界辺 0・非多様体辺 0。
- 01b（2K）の WTiVo `proxy_points` は、斧のサンプルで 12M・16M・20M が成功（約 6 分、WTiVo の出力 約 39.6M 面、memory.peak 31〜35 GiB、VRAM 約 13.2 GiB）。
  25M は出力が 82.55M 面に跳び、LODTailor の Blender が RSS 32.3 GiB を使って RAM の歯止め（anon+swap 43 GiB）に当たる。

## 形を作る段を TRELLIS.2 にした実測（斧、2026-11-03）

- 01 は成功 298 秒・memory.peak 22.8 GiB・VRAM 11,677 MiB・最終 GLB は閉じている（殻 16）。
- 01b（proxy_points 20M）は成功 432 秒・31.4 GiB・13,443 MiB・閉じている（殻 49）。
- Image_to_3D は 222 秒・12.7 GiB・11,265 MiB。
- 02 は成功 520 秒だが最終 GLB が閉じない（低ポリ 境界辺 36・殻 13。Fast Merge が 100 回試して元の網のまま返した。高ポリは溶接後に非多様体辺 80）。
  Pixal3D の 02 は閉じた。
- TRELLIS.2 は最後の KSampler 80 秒・Quad 42 秒で、Pixal3D（47 秒・15 秒）より遅い。01 で約 1 分長い。

## 動画生成（2026-11-06、`--seed 42`、既定の設定で 8 本を続けて、すべて成功）

- Kandinsky6_LiteDistill の T2V・I2V は 2.4 分（864×480・121 コマ・5 秒、音声 AAC 44.1 kHz モノラル、anon 9〜10 GiB）。
  Kandinsky6_Lite の T2V・I2V は 18.6 分・19.3 分（50 ステップ × 21.6〜22.5 秒）。
- LTX25_Fast の T2V・I2V は 1.6 分・1.5 分（1280×704・121 コマ・5 秒、8 × 2.4 秒と 2 倍化の 3 × 7.0 秒、音声 AAC 48 kHz ステレオ、anon 11.0・16.5 GiB）。
- HunyuanVideo15 の T2V・I2V は 49.9 分・50.5 分（1280×720・121 コマ、20 × 140 秒、音声なし、anon 20.6・22.1 GiB）。
  33 コマは 6.3 分（20 × 16.4 秒）。コマ数が 3.7 倍で 1 ステップが 8.5 倍になる。
- VRAM ピークはどれも 23〜24 GiB。ComfyUI が空きを使い切るためで、必要量ではない。
- 表と各回の根拠は `tools/comfyui/README.md` の「動画生成」→「実機試験の結果」を Read する。

