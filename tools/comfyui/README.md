# comfyui

[ComfyUI](https://github.com/Comfy-Org/ComfyUI) と、PixelArtistry の防水メッシュ用ワークフロー 4 本
（[PixelArtistry-Watertight-Meshes](https://github.com/pixelartistry/PixelArtistry-Watertight-Meshes) v1.1）を、
このリポジトリの A30 サーバー上で動かすための一式。配布元の Windows 用インストーラ
（`vendor/PixelArtistry-Watertight-Meshes/watertightMeshes_win_installer.bat`）がやることを Linux で置き換える。

`tools/pixal3d`（Pixal3D の UI）と構成・語り口を揃えている。

## ポート

- **8188**: ComfyUI（このツール。`--listen 0.0.0.0`）
- 7860 / 7861: Hunyuan3D / Pixal3D（VRAM を取り合う。同時に生成しないこと）
- 8080: 入口ページ（`tools/portal`）

サーバー IP は `<server-ip>`（例: 192.168.1.10）。起動後は `http://<server-ip>:8188` → 左の Workflows → `Image_to_3D`（画像 1 枚から 3D。下の「画像 1 枚から 3D」）か `PixelArtistry`（防水メッシュ）。

## 固定している版

| 対象 | 版・コミット |
|---|---|
| Comfy-Org/ComfyUI | `v0.41.1` = `45bf90d1c91eecd782643346dcfb73cb0017667f` |
| pixelartistry/PixelArtistry-Watertight-Meshes | `5b3a0e9d2c5783ed05ba45bbcc157b9509fb267b`（v1.1） |
| visualbruno/ComfyUI-Trellis2 | `b0e84c0f29857a444e8bcb5b9be2ea218e96efd0`（2026-09-25 の HEAD） |
| Mstafa-awad の 9 個 | 配布元 bat の `SHA_*`（`PIN_DATE=2026-09-26`）と同じ。一覧は `cfui/config.py` の `CUSTOM_NODES` |
| Mstafa-awad/WTiVo-WatertightVoxel（WTiVo のソース） | `7a0ac7cb24fd0588f2f7a78c787779d760d36f48`（VERSION 1.0.0、タグなし） |
| visualbruno/CuMesh | `d10e54c30ddd03d11472c1431693f985501c7966` |
| visualbruno/FlexGEMM | `db388bd17c34abc697792aa718dca780734c2783` |
| visualbruno/TRELLIS.2（o-voxel のビルド元） | `65d1e13b4a92296036044df0633242bb9e95abf6` |
| NVlabs/nvdiffrast | `253ac4fcea7de5f396371124af597e6cc957bfae`（v0.4.0） |
| Python / torch | uv 管理の 3.12.14 / `torch==2.9.1+cu126`、`torchvision==0.24.1+cu126`、`torchaudio==2.9.1+cu126` |
| CUDA（拡張のビルド） | `/usr/local/cuda-12.6`、`TORCH_CUDA_ARCH_LIST=8.0` |
| Blender | snap の 5.2.2（`/snap/blender/current/blender` を直接起動）。`/usr/bin/blender` は apt の 4.0.2 なので使わない。`/snap/bin/blender` は snap run が cgroup の外へ移すので使わない（下の「実機試験の結果」） |

- ComfyUI の GitHub Releases の最新は v0.41.0 だが、その後に版上げコミットの付いたタグ v0.41.1 がある。
  差分は API ノード（Krea）とテンプレートの更新だけ（2026-10-16 確認）なので v0.41.1 にした。
  Pixal3D / TRELLIS.2 の標準ノード（`comfy_extras/nodes_trellis2.py`・`nodes_mesh_postprocess.py`）を含む。
- その他の Python 依存は `requirements.lock.txt`（直接の依存は `requirements.in`）。

## 導入

サーバー上で 1 回実行する（冪等。torch と重み合計 約 16GB をダウンロードする）。sudo は要らない。

```bash
tools/comfyui/setup-server.sh
```

オプション:

- `--skip-models`: 重みの取得を省略する（あとで `run-comfyui fetch-models`）
- `--build-only`: venv・上流取得・torch・依存・CUDA 拡張・WTiVo・cfui までで止める
- `--fallback-workflows`: 予備案（WTiVo を外したもの）の写しも書き出す（WTiVo が使えないときは付けなくても書く）

工程（括弧は配布元 bat の対応する段）:

1. CUDA 12.6 の確認
2. uv と venv（`tools/comfyui/.venv`、uv 管理の Python 3.12）
3. ComfyUI 本体を `vendor/ComfyUI` に固定コミットで取得（[1/8] の古さの判定）
4. custom_nodes 10 個を固定コミットで取得（[2/8]）
5. torch（cu126）
6. 依存（`requirements.lock.txt`。torch は入れ替えない制約を付ける）（[4/8]）
7. CUDA 拡張（CuMesh / FlexGEMM / o-voxel / nvdiffrast）をソースからビルド
8. ComfyUI-Mesh-Quad-Reconstruct の `install.py`（[4/8]）と自前パッケージ cfui
9. WTiVo の Linux ビルド（`scripts/build-wtivo.sh`）（[3/8] の WTiVo の版選び）
10. 重み 9 ファイルとサンプル画像（[7/8]、[6/8] の画像）
11. ワークフロー 4 本を写す（`scripts/adapt_workflows.py`）（[6/8]）
12. systemd ユニットの配置・Blender の空の設定・env ファイル・doctor（[5/8] の自己試験、[8/8] の Blender）

必要な apt パッケージ（WTiVo のビルド用。利用者が sudo で入れる）:
`libcgal-dev libopenvdb-dev libtbb-dev libeigen3-dev`。無ければ WTiVo のビルドだけ飛ばし、他は続ける。

2026-10-02 の実測では、CUDA 拡張 4 本のビルドが合計 約 5 分、WTiVo（core / vdb / gpupr）が 約 30 秒、
すべて揃ったあとの再実行（何もしない確認と doctor）が 約 25 秒。

## 起動と停止

```bash
tools/comfyui/run-comfyui serve                 # フォアグラウンド
tools/comfyui/run-comfyui serve -- --lowvram    # main.py に引数を足す
systemctl --user start comfyui                  # systemd 経由
systemctl --user stop comfyui
```

- `setup-server.sh` は `~/.config/systemd/user/comfyui.service` を置いて daemon-reload するだけで、
  **enable（自動起動）はしない**。
- `~/.config/comfyui/comfyui.env` の `COMFYUI_EXTRA_ARGS` は main.py に足す引数（例: `--lowvram`）。
- `serve` は Blender 用の環境変数 `BLENDER_USER_CONFIG` / `BLENDER_USER_SCRIPTS` / `BLENDER_USER_EXTENSIONS` を
  `~/.config/comfyui/blender-clean/{config,scripts,extensions}`（空）に向ける。LODTailor・Bake Forger・LODsmith は
  Blender をヘッドレスで呼び、利用者のアドオンがそれを壊すことがある（配布元 README の Troubleshooting）。
  Blender 4.2 以降、アドオンの多くは extensions に入るため、scripts だけでなく extensions も空にしている。
- Ollama（Qwen）と VRAM を分け合う。生成前に `~/.claude/scripts/vram-policy.sh status` で空きを見て、
  足りなければ Qwen を降ろす。Hunyuan3D / Pixal3D とは同時に生成しない。

## 画像 1 枚から 3D（Image_to_3D.app.json）

画像を 1 枚入れて Run を押すだけで、テクスチャ付きの 3D モデル（GLB）を作る入口。
`http://<server-ip>:8188` → 左の Workflows → `Image_to_3D`。

1. **Load Image** で 3D にしたい画像を選ぶ（ドラッグ＆ドロップでもよい）。
2. 必要なら **ポリゴン数** と **テクスチャサイズ** を変える（変えなければ 70 万面・4096px）。
3. **Run** を押す。
4. **Save 3D** のビューアに結果が出る。GLB はサーバーの `vendor/ComfyUI/output/3d/ComfyUI_*.glb` に保存される。

| アプリの入力 | 決めるもの | 指定できる値 |
|---|---|---|
| ポリゴン数（目標の面数） | `DecimateMesh.target_face_count` | 0〜50,000,000。0 は間引かない。**形を作る段階の面数より増えない**（斧で 3,353,172 面。画像で変わる） |
| テクスチャサイズ（px） | `UnwrapMesh.resolution` と `BakeTextureFromVoxel.texture_size`（ベースカラー・金属・粗さ。AO も同じ大きさで出る） | 256〜8192 の 256 の倍数（`UnwrapMesh` が 0〜8192・256 刻み。サーバーは刻みを検査しない） |

- 法線マップは 2048px（`BakeNormalMapFromMesh`）、AO の焼き込みは 1024px（`BakeAmbientOcclusion`）のまま、アプリの入力では変わらない。

- 土台は ComfyUI 同梱のテンプレート `3d_pixal3d_trellis2_image_to_model`（comfyui-workflow-templates 0.11.85）。
  Pixal3D（既定）か TRELLIS.2 で形を作り、RemeshMesh → DecimateMesh（70 万面）→ UV 展開 → PBR（ベースカラー・
  金属/粗さ・AO 4096px、法線 2048px）のベイクまで行う。必要な重みは「重みの置き場」の 9 点に含まれ、追加の
  ダウンロードは要らない（`tests/test_units.py` が、テンプレートの要求する重みが `MODEL_FILES` に入っていることを確かめる）。
- `scripts/adapt_workflows.py`（`setup-server.sh` の [11/12]）が、テンプレートから次の点だけを変えて
  `vendor/ComfyUI/user/default/workflows/Image_to_3D.app.json` に書く（`cfui.workflows.make_easy_image_to_3d`）。
  実行される処理は変えない（既定の値も同じ）。
  - アプリモード（`extra.linearMode` / `linearData`: 入力は LoadImage の `image`・ポリゴン数・テクスチャサイズの順、
    出力は Save 3D）にする。テンプレートは全体が入る縮尺 0.1 で開き、結果を出す Save 3D が入力から約 9,000 単位右にあるため。
    グラフ表示に切り替えたときの初期表示は、画像を置く場所に寄せてある。
  - ポリゴン数の入力用に PrimitiveInt（題名「ポリゴン数（目標の面数）」）を足し、`DecimateMesh.target_face_count` へつなぐ
    （値は DecimateMesh が持っていた 700000）。テクスチャサイズは上流の PrimitiveInt「Texture Resolution」（4096）を使い、題名を
    日本語にする。アプリの入力欄にできるのは「ノードの widget」で、DecimateMesh の widget を直接出さないのは、この 2 つを同じ形にして
    題名で欄の名前が決まるようにするため（欄の名前が画面でどう出るかは未確認）。
  - 手順を書いた MarkdownNote を足す（画面だけのノード）。
  - workflow の id をテンプレートの id から決まった別の UUID にする。
- ファイル名は `.app.json`。アプリモードのワークフローを画面で保存し直すと、`Image_to_3D.json` がこの名前に変わった（2026-10-25）。
  画面は `linearData` の項目も `["<workflow id>:<ノード id>:<widget 名>", widget 名]` の書式に直す（旧書式 `[ノード id, widget 名]` は
  読み込み時にこの書式へ変換される。フロントエンド 1.55.17 の `pruneLinearData` / `upgradeAndValidateInput` を読んで確かめた）。
- 既に書いたファイルを UI で手直ししていれば上書きしない（`--force` で上書き、`--no-easy` で書かない）。
  **画面で保存し直したファイルに、入力画像などの手直しを残したまま今の版の変更を当てるには `--upgrade-easy`**
  （何度流しても同じ結果。書式は indent=2 に変わる。開いている画面は保存し直すと元に戻るので、先に閉じて開き直す）。
- GPU を使う。ほかのサービス（Hunyuan3D・Pixal3D・Strata）とは同時に動かさない（入口ページから ComfyUI を起動すれば自動で止まる）。

### 実機試験の結果（2026-10-25、`--free-before`、`run_workflow.py run` で投入）

| 入力 | 結果 | 時間 | cgroup の memory.peak / anon | VRAM のピーク | GLB |
|---|---|---|---|---|---|
| `viking_wolf_rune_axe.png`（既定のサンプル。この機体で初回） | 成功 | 172.7 秒 | 17.5 / 8.0 GiB（swap 0） | 12,598 MiB | 698,185 面・446,999 頂点・69.8 MB |
| `D_superhero_female.png`（人物。背景は黒） | 成功 | 111.9 秒 | 18.0 / 7.8 GiB（swap 0） | 10,426 MiB | 47.4 MB |

- 1 回目のノードの時間: KSampler 4 本が 47.0 / 25.3 / 13.3 / 9.0 秒、Pixal3D Conditioning 17.3 秒、Bake Ambient Occlusion 13.0 秒、
  Bake Texture From Voxel 9.8 秒、Unwrap Mesh UVs 5.8 秒。2 回目が速いのは、重みがディスクキャッシュに載っていたためと考えられる（未確認）。
- 斧の GLB を Blender 5.2.2（CPU）で 2 方向から描画して目で見た。形・ルーン・革巻き・木目が入力と一致し、側面から見ても破綻がない。
  人物は全体の形・肌・下着が入力と一致する。顔は目元の模様が入力と違う（入力は無地）。
- テンプレートが RemeshMesh / DecimateMesh で使う `DynamicCombo` を `run_workflow.py` が扱えなかったため、対応を足した
  （下の「画面を使わずにワークフローを通す」）。

### ポリゴン数とテクスチャサイズを変えた実機試験（2026-10-25、`--free-before`、`--set 325.value=… --set 288.value=…`）

| 入力 | ポリゴン数 / テクスチャ | 時間 | memory.peak / anon | VRAM のピーク | 結果の GLB |
|---|---|---|---|---|---|
| `E_copper_coin.png` | 30,000 / 2048 | 370.8 秒 | 20.4 / 9.8 GiB | 16,412 MiB | 29,796 面・ベースカラー／金属粗さ／AO／法線 すべて 2048px・14.7 MB |
| `viking_wolf_rune_axe.png` | 50,000,000（間引かない）/ 8192 | 235.9 秒 | 22.8 / 11.6 GiB | 12,572 MiB | 3,353,172 面・ベースカラー／金属粗さ／AO 8192px・法線 2048px・239.7 MB |
| 同上（更新後の実ファイルを既定値のまま） | 700,000 / 4096 | 167.4 秒 | — | — | 698,096 面（前回 698,185 面）・4096px（法線 2048px） |

- 指定どおりの面数・大きさで出る。面数は指定のほぼ 100%（30,000 に対し 29,796）、既定の 70 万面は前回と 89 面（0.013%）違う
  （同じ seed でも一致しないので GPU 演算の非決定性と考えられるが、未確認）。
- 1 つ目が遅いのは設定のせいではない。時間の大半は形を作る前段の KSampler（#23 が 164.9 秒・#12 が 87.4 秒。斧は 47.0 秒・25.3 秒）で、
  ポリゴン数やテクスチャサイズより前に走る。入力画像（コインは大きく詰まった物体）で変わると考えられる（同じ画像での比較はしていない）。
- 8192px でも RAM・VRAM は余裕がある（`MemoryMax` 48 GiB、VRAM 24 GiB）。ベイクの 3 ノード（Unwrap 29.4 秒・Bake Texture 30.5 秒・
  AO 15.4 秒）が増え、GLB が 240 MB になる。

### 画面での確認について

画面（ブラウザ）での操作は確かめていない。利用者が画面でアプリモードを開いて実行した記録（`comfyui.service` のジャーナル）はあるが、
**アプリモードの表示**（入力欄の名前・ポリゴン数とテクスチャサイズの欄・3D の結果）は私は見ていない。フロントエンドのコードでは、
3D の出力の分岐（`Preview3d`）があり、Save 3D の出力の種類（`3d`）と合うことまで確かめた。表示がおかしいときは、グラフ表示に切り替えて使う。

### `qwen_3_4b.safetensors` が無いと言われたら

`Value not in list: clip_name: 'qwen_3_4b.safetensors' not in []`（CLIPLoader）は、Z-Image や FLUX.2 klein などの
**テキスト → 画像**のテンプレートを開いているときに出る（これらは文字エンコーダとして Qwen3 4B を使う）。3D 生成には要らない。
その画面は閉じて、`Image_to_3D` を開く。テキスト → 画像を使いたいなら、モデル一式（文字エンコーダ・拡散モデル・VAE。
数 GB〜十数 GB）を別に入れる必要がある（この機体には入れていない）。

## 点検（doctor）

```bash
tools/comfyui/run-comfyui doctor                  # 自己試験込み（GPU を使う。数秒〜十数秒）
tools/comfyui/run-comfyui doctor --skip-selftest  # 自己試験を省く
tools/comfyui/run-comfyui selftest                # WTiVo の自己試験だけ
```

1 行 1 項目で `OK` / `NG` を出す: nvcc 12.6、torch の版と CUDA、GPU 名 A30、ComfyUI と custom_nodes の各コミット、
パッチ、apt パッケージ、拡張の import（cumesh / o_voxel / flex_gemm / nvdiffrast）と sm_80 でのカーネル実行、
`cumesh.remeshing.reconstruct_mesh_dc_quad`、`trellis2.modules.sparse`、wtivo_core / wtivo_gpupr / wtivo_vdb、
モデル 9 ファイル（大きさ）、サンプル画像、ワークフロー（Blender のパス）、`/snap/blender/current/blender --version` が 5.x、Blender のパスが snap run を通らないこと、
ポート 8188、WTiVo の自己試験。VRAM と systemd の状態は表示のみ。

自己試験は配布元 bat の L457-484 と同じ: `trimesh.creation.icosphere(subdivisions=4, radius=0.5)` の
`faces[60:]`（60 面を消して穴にする）を
`process_arrays(verts, faces, input_res=256, final_res=256, proxy_points=200_000, threads=4)` に渡し、
`watertight` が真かつ出力の面数 > 0 なら合格。

## CUDA 拡張を wheel ではなくソースから入れた理由

ComfyUI-Trellis2 は `wheels/Linux/Torch291/` に cp312 の wheel を同梱しているが、2026-10-02 に使い捨ての
venv で試すと A30（sm_80）では使えなかった。

| wheel | 中の GPU コード（`cuobjdump --list-elf`） | A30 での結果 |
|---|---|---|
| cumesh-1.0 | sm_86 の SASS と PTX だけ | `no kernel image is available for execution on the device` |
| nvdiffrast-0.4.0 | sm_120 だけ | 同上（`cudaFuncGetAttributes` で error 209） |
| flex_gemm-0.0.1 | sm_120 だけ | import は通るが `grid_sample_3d` で同上 |
| o_voxel-0.0.1 / nvdiffrec_render | sm_120 だけ | 試していない（SASS が無いので同じになる） |

そこで ComfyUI-Trellis2 の README（Custom Build）が指す visualbruno のフォークを、Pixal3D と同じ設定
（`CUDA_HOME=/usr/local/cuda-12.6`・`TORCH_CUDA_ARCH_LIST=8.0`・`MAX_JOBS=4`・`--no-build-isolation`・`--no-deps`）で
ビルドしている。nvdiffrec_render はどのノードも import しないので入れない。

- **CuMesh には当て物が要る**（`patches/cumesh-eigen-include.patch`）。visualbruno 版の `setup.py` は cubvh の
  Eigen を `third_party/cubvh/third_party/eigen` に探すが、この版はそこを持たず repo 直下の `Eigen/`
  （5.0.1-dev）に同梱している。そのままだと `Eigen/Dense` が見つからずに失敗する。
- Quad Reconstruct の `install.py` は、`cumesh.remeshing.reconstruct_mesh_dc_quad` があれば何もせずに終わる。
  中身（2026-10-02 に読んだ）: pymeshlab を pip で入れる → 上の関数があれば終わり → 無ければ ComfyUI-Trellis2 の
  wheel を `--force-reinstall` で入れる。torch を入れ替える処理は無い。uv の venv には pip が無いので、
  setup は実行の間だけ `pip==25.2` を入れて外す。

## WTiVo を Linux で作った方法

WTiVo（`WTiVoNativeMeshToMesh`、メッシュを防水にするノード）のノード repo v1.1 は Windows 用の `.pyd` しか持たない。
ソースは姉妹の repo（WTiVo-WatertightVoxel v1.0）にあるので、そこから 3 本を作り、ノードが読む場所に置く
（`scripts/build-wtivo.sh`）。

| モジュール | ソース | 作り方 |
|---|---|---|
| wtivo_core | `native/core/wtivo_core.cpp`（CGAL 5.6 + oneTBB 2021.11 + Eigen 3.4） | cmake + pybind11 3.0.1、venv の Python 3.12 |
| wtivo_vdb | `native/vdb/wtivo_vdb.cpp`（OpenVDB 10.0.1 + TBB） | 同上 |
| wtivo_gpupr | `native/gpupr/*.cu`・`wtivo_gpupr_bindings.cpp` | 上流の `scripts/build_gpupr.py`（torch の cpp_extension）、CUDA 12.6、sm_80 |

- ノードの `wtivo.py` は自分のフォルダと `build/` を `sys.path` に足し、`wtivo_core` / `wtivo_gpupr` / `wtivo_vdb` を
  優先して import する。無ければ同梱の Windows 用 `cppmodules` などに落ちる（`wtivo.py:44-49, 86-139`）。
  そこで `.so` は `vendor/ComfyUI/custom_nodes/WTiVo-WatertightVoxel-ComfyuiNode/build/` に置く。
- ノードは `inprocess.py` から `wtivo.py` を子プロセスで起動し、配列を `.npy` で受け渡す。
- 上流の作業ツリーには手で書かず、`patches/` の差分を `git apply` で当てる:
  - `wtivo-src-linux-build.patch`: 姉妹 repo の `CMakeLists.txt` の `if(NOT WIN32) FATAL_ERROR` と、
    `scripts/build_gpupr.py` の nt ガード・`nvcc.exe`・MSVC 用の `/O2` などを Linux でも通るようにする。
  - `wtivo-src-core-vertices-lifetime.patch`: `wtivo_core.tetrahedralize_neighbors` の不具合の修正。
    引数 `Eigen::Ref<const MatrixXd>`（列優先）に `wtivo.py` が行優先の配列を渡すため、pybind11 は呼び出しの間だけの
    一時コピーを作る。上流はそれを参照のまま返しており、Linux では返した配列を読んだ時点で SIGSEGV になった
    （自己試験で再現。Windows では解放後の領域がたまたま読めていたと考えられるが、確かめてはいない）。
    Python が所有するコピーを返すように直した（値は同じ。12M 点で約 288 MB 増える）。
  - `wtivo-node-allow-linux.patch`: ノードの `wtivo.py` の `main()` にある `os.name != "nt"` の終了を外す。
- Ubuntu の `FindOpenVDB.cmake` は `find_package(Boost REQUIRED COMPONENTS iostreams)` を無条件に呼び、その cmake 設定を
  持つ `libboost-iostreams-dev` は `libopenvdb-dev` の依存に入っていない。OpenVDB の公開ヘッダは boost/iostreams を
  含まず、`libopenvdb.so` 自身が `libboost_iostreams.so.1.83.0` にリンクしているので、システムの OpenVDB 10 を指す
  最小の `cmake/openvdb-config/OpenVDBConfig.cmake` を `-DOpenVDB_DIR` で渡して避けている。
- パッチを足したり変えたりすると、`build-wtivo.sh` は記録（`vendor/WTiVo-WatertightVoxel/build/.localai-patches.sha256`）
  と比べて `.so` を作り直す。

### ノード v1.1 と姉妹 repo v1.0 の違い（2026-10-02 に読み比べた）

- ネイティブ API（`core.tetrahedralize_neighbors` / `largest_component` / `is_watertight`、`gpupr.graph_cut_fast` /
  `surface_extraction_topology_cuda`、`vdb.SparseUDF` とその `faithc_mesh` / `faithc_point_budget` /
  `sample_tet_labels` / `stats`）は、呼び出し方・引数の並びとも同じ。v1.0 のソースから作った `.so` を v1.1 の
  `wtivo.py` がそのまま使える。
- v1.1 の `wtivo.py` で増えたもの: `--proxy_points`（v1.0 は 12,000,000 固定）、`--proxy_eps_scale`（EPS = scale / R）、
  `.npy` での受け渡し、最後の trimesh による監査（`watertight` は「ネイティブの辺の検査」かつ
  「trimesh で閉じていて 1 つの塊」のとき真。v1.0 はネイティブの検査だけ）。
- 既定値: CLI の `--lambda_fill` は両方 10.0 だが、ノードの widget の既定は 20.0。`--faithc_component_mode` は
  CLI が `auto`、ノードの widget が `keep_all`、配布元のワークフローは `largest`。
- 版の表記: ノードの `CHANGELOG.md` は 1.2.0、`CMakeLists.txt` は 1.1.1、`wtivo.py` の表示は 1.1。

## 予備案（WTiVo が使えないとき）

WTiVo を外し、直後の LODTailor の voxel_rebuild（Blender の Voxel Remesh）で防水化する写しを作れる。

```bash
tools/comfyui/.venv/bin/python tools/comfyui/scripts/adapt_workflows.py --fallback
# → vendor/ComfyUI/user/default/workflows/PixelArtistry_noWTiVo/*_noWTiVo.json
```

- 変えるのは: `WTiVoNativeMeshToMesh` を消し、その入力の元（Memory Cleaner）を出力の先へ直接つなぐ。
  WTiVo の出力を受けていた `LODTailorTheMeshTrimmer` の `voxel_rebuild` を真（配布元でも真）、
  `relative_voxel_size` を 1/1536（配布元は 0.001）にする。workflow の id は元から決まる別の UUID にする。
- WTiVo を使わない 03 には作らない。
- 02 では WTiVo の出力が `BakeTextureFromVoxel` にも入っていた。予備案ではそこにも Memory Cleaner の出力
  （Quad Reconstruct 後の、防水でないメッシュ）が入る。テクスチャ付きハイポリは防水にならない。
- 01b（2K）も 1/1536 にしている（指定どおり）。2048 に合わせるなら `--fallback-voxel-size 0.00048828125`。
- `setup-server.sh` は、WTiVo のビルドが通らなかったときと `--fallback-workflows` のときにこの写しを書く。

## ワークフローの写し方

`scripts/adapt_workflows.py` は配布元の `workflows/*.json` を `vendor/ComfyUI/user/default/workflows/PixelArtistry/` に写す。
変えるのは紫の「Blender path」ノード（`PrimitiveString`、既定値 `"blender"`）の値だけで、`/snap/blender/current/blender` にする。
書式は配布元と同じ `indent=2`・末尾改行なし（配布元は `json.dumps(indent=2)` と 1 文字も違わない）で、
`ensure_ascii=False` で書く（配布元で `–` などと書かれた文字はそのままの文字になる）。
既にあるファイルの内容が違うとき（UI で手直しして保存したなど）は上書きしない。`--force` で上書きする。
ただし、以前の既定の Blender のパス（`cfui/config.py` の `LEGACY_BLENDER_PATHS` = `/snap/bin/blender`）で書いたままの
写しは、手直しされていないものとして今の既定値へ書き換える（`setup-server.sh` を再実行すれば反映される。2 回目は「同じ内容なので触りません」）。

配布元 bat はワークフローを gzip + base64 で埋め込んでいるが、ここでは repo の `workflows/*.json` を使う
（README に「JSON は手で読み込む用に同じものを置いている」とある。埋め込み側との突き合わせはしていない）。

## 重みの置き場

`vendor/ComfyUI/models/` の下（ComfyUI の既定の場所）。`run-comfyui fetch-models` が
`https://huggingface.co/<repo>/resolve/main/<path>` から取り、大きさと sha256（2026-10-02 に HF の応答で確認した値、
`cfui/config.py` の `MODEL_FILES`）を確かめる。sha256 が違えば使わずに止める（上流が差し替わった可能性）。

| 置き先 | ファイル | 取得元 |
|---|---|---|
| diffusion_models | trellis_2_int8_convrot.safetensors（4.9 GiB） | Comfy-Org/TRELLIS.2 |
| diffusion_models | pixal3d_int8_convrot.safetensors（5.2 GiB） | Comfy-Org/Pixal3D |
| vae | trellis_2_shape_vae_bf16 / trellis_2_texture_vae_bf16 | Comfy-Org/Pixal3D（vae/） |
| clip_vision | dino_v3_L_naf_fp32.safetensors | Comfy-Org/Pixal3D |
| geometry_estimation | moge_2_vitl_normal_fp16.safetensors | Comfy-Org/MoGe |
| background_removal | birefnet.safetensors | Comfy-Org/BiRefNet |
| Trellis2/encoders | shape_enc_next_dc_f16c32_fp16.safetensors / .json | microsoft/TRELLIS.2-4B（ckpts/） |

サンプル画像 `viking_wolf_rune_axe.png` は `vendor/ComfyUI/input/` に置く（bat の L179 と同じ URL）。

## torch が cu126 なので comfy_kitchen の CUDA 版が無効になる

ComfyUI v0.41.1 は `torch.version.cuda` が 13 未満だと comfy_kitchen の CUDA 版を無効にし、起動時に
`You need pytorch with cu130 or higher to use optimized CUDA operations.` と警告する（`comfy/quant_ops.py:22-28`）。
2026-10-02 の起動ログでも `backend cuda: {'available': True, 'disabled': True, ...}` だった。
このワークフローの重み（`*_int8_convrot`）は無効になった CUDA 版の `dequantize_int8_convrot_weight` /
`int8_linear` を使えず、eager 版（`torch._int_mm` など。`comfy/model_management.py:2073-2076`）で動く。
動くが、速さと VRAM への影響は測っていない。torch は指定どおり 2.9.1+cu126 のままにしている。

- 試せること（未検証）: `COMFYUI_EXTRA_ARGS=--enable-triton-backend`（comfy_kitchen の triton 版。既定は無効）。
- 根本の対処は torch を cu130 にすること（`torch==2.9.1+cu130` は index にある）。その場合は CUDA 拡張と
  WTiVo の gpupr も CUDA 13 系でビルドし直す必要があり、Pixal3D と同じ 12.6 の前提から外れる。

## 依存で上流と変えたところ

- `rembg[cpu]`（上流の requirements.txt は extras なしの `rembg`）。rembg 2.0.85 は onnxruntime が無いと
  import の時点で `sys.exit(1)` する（`rembg/bg.py:8-20`）。ComfyUI の中で呼ばれるとサーバーごと落ちるため。
- WTiVo-FastMergeByDistance は Windows の DLL しか持たず、Linux では Python 版（遅い）で動く。

## 画面を使わずにワークフローを通す（run_workflow / measure_mesh）

```bash
# UI 形式 → API 形式に変えて投入し、終わるまで待つ。記録は output/localai_runs/<tag>/
tools/comfyui/.venv/bin/python tools/comfyui/scripts/run_workflow.py run \
  tools/comfyui/vendor/ComfyUI/user/default/workflows/PixelArtistry/PixelArtistry_01_Image_to_Watertight_Mesh.json \
  --image viking_wolf_rune_axe.png --backend pixal3d --tag wf01 --free-before --monitor --guard-mem-gib 44 \
  --tap 325=3d/tap_wf01_quad --tap 327=3d/tap_wf01_wtivo --tap 328=3d/tap_wf01_lodtailor
# 変換だけ（投入しない）
tools/comfyui/.venv/bin/python tools/comfyui/scripts/run_workflow.py convert WORKFLOW.json -o api.json
# 防水さと、ひとつ前の段との距離を測る（GPU があれば CuMesh の cuBVH を使う）
tools/comfyui/.venv/bin/python tools/comfyui/scripts/measure_mesh.py OUT.glb --reference PREV.glb -o m.json
```

- ComfyUI v0.41.1 の Python 側には UI 形式 → API 形式の変換が無い（画面の `graphToPrompt` / `ExecutableNodeDTO`
  が行う）。`run_workflow.py` は comfyui_frontend_package 1.53.6 の規則（widget の並び、seed の後ろの
  control_after_generate、`image_upload` の余分な値、bypass の素通し `_getBypassSlotIndex`、muted の入力の削除、
  Note / MarkdownNote / PrimitiveNode / Reroute の除外）を書き直したもの。subgraph は未対応で、使われていたら止める。
- `DynamicCombo`（型 `COMFY_DYNAMICCOMBO_V3`。RemeshMesh の `sign_mode`、DecimateMesh の `placement_mode`）に対応している
  （2026-10-25）。選択肢のキーを 1 つの値として読み、選ばれた選択肢の子の widget をその直後に並べる。API の入力名は
  `sign_mode` と `sign_mode.qef` のように「親.子」（サーバーの `comfy_api/latest/_io.py` の `finalize_prefix` と同じ）。
  これが無いと、`sign_mode: "udf"` を次の FLOAT（`band`）の値と読み誤る。Autogrow など他の動的な入力は未対応。
  対応後、同梱テンプレートの変換は `MoGeInference` の `refine_steps`（テンプレートの保存後に増えた入力）の警告だけになり、
  サーバーの `/prompt` の検証（`node_errors` なし）と実機の実行を通った。
- 上書き: `--image`（LoadImage）、`--seed`（control_after_generate を持つ入力をすべて同じ値に）、
  `--backend pixal3d|trellis2`（題名に Trellis を含む PrimitiveBoolean）、`--mesh`（Trellis2MeshEncoder）、
  `--set NODE.INPUT=VALUE`。API から投入すると `randomize` は効かないので、`--seed` を付けなくても
  ワークフローに保存された seed（01 なら 42 / 56 / 42）で固定になる。
- `--tap NODE[:SLOT]=PREFIX` は途中の MESH を SaveGLB で `output/` に書き出すノードを足す。
- `--free-before` は投入の前に `POST /free`（モデルを降ろし、実行結果のキャッシュを捨てる）を呼ぶ。付けないと、
  前の回と入力が同じノード（01 と 02 の形状生成など）はキャッシュから使われ、時間と RAM が小さく出る。
- `--monitor` は cgroup の `memory.peak`（fd ごとに戻して読む）・anon・swap、`nvidia-smi` の VRAM、
  blender の RSS を 1 秒ごとに `resources.csv` に書く。`--guard-mem-gib` を超えたら `/interrupt` し、
  cgroup の中の子プロセス（WTiVo・Blender）に SIGTERM を送る。
- `measure_mesh.py` は位置が完全に同じ頂点を溶接してから数える（UV の継ぎ目対策。配布元 README の
  「Merge Vertices」と同じ）。溶接前の値と、同じ位置に重なる頂点の数も `unwelded` に出す。
- `--tap` で取り出せるのは MESH の出力だけ。LODTailorBakeForger の出力（`_BakedFile3D`）は ComfyUI の File3D の
  派生ではなく、SaveGLB が MESH として扱って `AttributeError` で止まり、その回の残りも実行されない
  （2026-10-03 に 02 で起きた）。ベイク後の GLB は `output/LODTailorBakeForger/latest/baked_low.glb`
  （回ごとに消して書き直される）を直接測る。

### 実機試験の結果（2026-10-03、入力 viking_wolf_rune_axe.png、seed は保存値、`--free-before`）

| | 01（1536） | 02（テクスチャ） | 03（01 の出力から） | 01b（2048 / 25M） |
|---|---|---|---|---|
| 結果 | 成功 240 秒 | 成功 392 秒 | 成功 205 秒 | WTiVo の途中で RAM の歯止め（42 GiB）で中断 |
| cgroup の memory.peak / anon | 19.3 / 18.5 GiB | 21.2 / 18.8 GiB | 10.0 / 6.1 GiB | 40.6 / 39.6 GiB（+ swap 2.8 GiB） |
| cgroup 外の Blender の RSS | 7.7 GiB | 8.3 GiB | 6.2 GiB | （Blender まで進まず） |
| VRAM のピーク | 10,870 MiB | 10,720 MiB | 7,232 MiB | 13,248 MiB |

- 01 は 1 回目なので `--free-before` なし（起動直後）。01 のノードの時間: 形状の KSampler 3 本が 12.6 / 7.6 / 47.0 秒
  （1.01 / 0.37 / 3.89 秒/step）、Quad 15 秒、WTiVo 65 秒、LODTailor 55 秒、Fast Merge（Python 版）14 秒。
  01b の 2048 の KSampler は 7.91 秒/step（95 秒）。int8_convrot が遅い経路で動いていることを示す行は、
  起動時の `You need pytorch with cu130 ...` と `backend cuda: ... 'disabled': True` のほかに無かった。
- **`/snap/bin/blender`（snap run 経由）は comfyui.service の cgroup の外で動く。** snap run が子を
  `app.slice/snap.blender.blender-<uuid>.scope` に移すため、memory.peak に入らず、MemoryHigh / MemoryMax も効かない
  （上の表の「cgroup 外の Blender の RSS」はこの状態で測った値）。
  そこで 2026-10-03 に、ワークフローと doctor のパスを本体 **`/snap/blender/current/blender` の直接起動**に変えた。
  snap は `confinement: classic` なので、直接起動しても `--background --python`・Cycles の OptiX / CUDA（A30。
  64x64 の描画で確認）が動き、子は呼び出し元の cgroup に残る（`systemd-run --user --scope` の中で
  直接起動すると `run-<id>.scope`、snap run 経由だと `snap.blender.blender-<uuid>.scope` になることを比べた）。
  `SNAP*` の環境変数は無い。`RUNPATH=$ORIGIN/lib` で同梱ライブラリを読むので、ComfyUI の環境（`LD_LIBRARY_PATH` なし）で足りる。
  `blender-launcher` の TBB の先読みは通らない（Intel のレイトレ用なので、ヘッドレスのベイクには使わない）。
- 直接起動に変えたあとの 02（同じ入力・同じ seed・`--free-before`、`wf02_direct`）: 成功 393 秒（変更前 392 秒）。
  Blender の RSS のピーク 7.5 GiB（LODTailor の段）は cgroup の中に入り（`blender_cgroups` は comfyui.service のみ。cgroup 外の
  blender は常駐の `blender-mcp`（0.05 GiB）だけ）、cgroup の memory.peak は 25.3 GiB（変更前 21.2 GiB。Blender の分が入って増えた）、
  anon のピークは 17.6 GiB。出力は同じ（低ポリ 29,986 面、境界辺 0・非多様体辺 0・殻 1、Hausdorff も前回と一致）。
  Bake Forger は 74 秒で、GPU 使用率 99%・CPU フォールバックの警告なし。
- 防水（位置で溶接したあと）: 01 は LODTailor の後と最終出力が境界辺 0・非多様体辺 0（殻 3）。
  WTiVo の直後は溶接前は閉じている（境界辺 0・非多様体辺 0・殻 1）が、689 か所で別々の頂点が同じ座標にあり
  （WTiVo のログ `[FaithC-MANIFOLD-v6.21] split_cells=683`。多様体を保つために頂点を分けている）、溶接すると
  非多様体辺 681 になる。02 は低ポリ化の LODTailor（target 30k）で境界辺 9・殻 4 になり（ノード自身も
  `watertight=False bodies=4`）、最後の Fast Merge（TEXTURE_SAFE）で境界辺 0・殻 1 に戻る。03 は全段で 0。
- 両側 Hausdorff（1/1536 単位）は、WTiVo ← Quad が 24.9（p99 は 2.4。Quad の出力は殻 169・境界辺 27,772 で、
  WTiVo が最大の殻だけを残すためと考えられるが、場所は確かめていない）、
  LODTailor ← WTiVo が 8.6（p99 0.5）で、2 ボクセル以内ではない。低ポリ（30k）は高ポリから 7〜10。
- 02 の 1 回目の Bake Forger は 270 秒、同じ入力の 2 回目は 74 秒。1 回目は Blender が GPU 使用率 0%・CPU 1 コアで
  `/var/tmp/OptixCache_mt/optix7cache.db` を開いていたので、OptiX のカーネルの初回コンパイルと考えられる（未確認）。
- 01b の WTiVo は 2048 で 41.3M 頂点 / 82.6M 面（01 の約 4 倍、殻 54 から最大を選ぶ）を作り、その後の最終段
  （00:59:59〜01:00:39）で anon が 24 → 40 GiB に増えた。そのまま進むと、この面数を LODTailor が GLB に書き出し
  cgroup 外の Blender が読むため、RAM 62.5 GiB の機体では足りない見込み（試していない）。

## 試験

```bash
uvx --with pytest pytest tools/comfyui/tests/test_units.py tools/comfyui/tests/test_run_workflow.py -q
uvx --with pytest --with numpy --with scipy --with trimesh pytest tools/comfyui/tests/test_measure_mesh.py -q
```

GPU もネットワークも使わない（ワークフローの変換・doctor の判定関数・メッシュの指標）。`.venv` には pytest を入れない。

## ライセンス

ComfyUI は GPL-3.0。Mstafa-awad のノードは GPL-3.0 か MIT、WTiVo は GPL-3.0-or-later（CGAL を GPL で使う）。
ワークフローと配布元インストーラは MIT。重みはそれぞれの配布元の条件に従う（商用に使うなら個別に確かめること）。
