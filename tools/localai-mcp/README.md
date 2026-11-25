# localai-mcp — 他の PC の Claude Code / Codex から A30 サーバーの AI を使う

入口ページ（`http://<server-ip>:8080/`）に並ぶ AI を、他の PC の Claude Code / Codex から**起動・停止し、そのまま使う**ための MCP サーバー。
他の PC で動く（stdio）。Python 3.10 以上の**標準ライブラリだけ**の 1 ファイル `localai_mcp.py` で、依存は無い。

```
 他の PC                                            A30 サーバー（<server-ip>）
 ┌──────────────────────────┐   HTTP（LAN）        ┌─────────────────────────────────────┐
 │ Claude Code / Codex      │ ───────────────────► │ 8080 入口ページ（状態・起動・停止）    │
 │   mcp: localai           │                      │ 8082〜8086 Strata（OpenAI 互換）      │
 │   (localai_mcp.py)       │                      │ 7862 AudioCraft / 7864 Stable Audio  │
 │ 入力画像・出力の wav/glb │                      │ 7863 Qwen3-TTS / 7860 Hunyuan3D       │
 │ はこの PC のファイル     │                      │ 7861 Pixal3D / 8188 ComfyUI           │
 └──────────────────────────┘                      └─────────────────────────────────────┘
```

- 入力（画像・元の音・ワークフロー）はこの PC のファイルのパスで渡し、出力はこの PC に保存してパスを返す。
- 保存先は `LOCALAI_OUTPUT_DIR`、無ければ MCP を起動したフォルダ（Claude Code ならそのプロジェクト）の `localai-outputs/`。ツールごとに `output_dir` でも変えられる。
- 認証は無い（入口ページと同じく LAN 内の前提）。

## ツール

| ツール | すること |
|---|---|
| `localai_status` | 全サービスの状態（`running` / `starting` / `stopped`）・GPU の使用量・Ollama に載っているモデル |
| `localai_service` | 起動・停止（`key`・`action`）。`wait=true`（既定）なら使えるまで待つ（入口ページの `?wait=` で待つ。起動に失敗したら `ok: false` で返る。`?wait=` の無い古い入口ページでは MCP 側で 5 秒ごとに状態を見て待つ） |
| `localai_ollama` | Ollama のモデル（`qwen3.8:27b` など）。`action=list`（既定）で入っているもの・載っているもの、`load` で VRAM に載せる（載せ終わるまで待つ。`keep_alive` で載せておく長さ）、`unload` で降ろす |
| `localai_chat` | Strata 5 種（`strata` / `strata-coder` / `strata-official` / `strata-official-iq3s` / `strata-swift`）に質問 |
| `localai_music` / `localai_sound` | AudioCraft の作曲（1〜30 秒）・効果音（1〜20 秒）。続き・メロディ指定は `audio_path` |
| `localai_stable_audio` | Stable Audio 3.0 Medium（1〜380 秒）。音→音・部分の作り直し・続き |
| `localai_tts` | Qwen3-TTS VoiceDesign。声の特徴を文章（`design`）で指定して `text` を読み上げ |
| `localai_mesh` | Hunyuan3D / Pixal3D。正面（必須）・背面・左・右の画像から 3D メッシュ |
| `localai_comfyui` / `localai_comfyui_result` | ComfyUI に API 形式のワークフローを投入し、出力を受け取る（長いものは `wait=false` で投げて後で受け取る）。`workflow_name` でサーバーに保存したワークフローを名前で使える（例 `VFX/WanAlpha_T2V_api.json`。入力の対応は入口ページの `GET /api` の `comfyui.workflows`） |
| `localai_vfx` | ゲームのエフェクト。`kind=wan_alpha`（透明度つきの動画。RGBA の PNG の連番とアニメ WebP）か `kind=layerdiffuse`（透明な 1 枚絵。RGBA の PNG）に、`prompt`・`seed`・`steps`・`cfg`・`width`・`height`・`frames`（wan_alpha だけ）・`filename_prefix` を名前で渡す。ComfyUI が止まっていれば `start=true` で起動する（GPU のほかのサービスが動いていれば `stop_others=true` が要る）。入力の対応は入口ページの `GET /api` の `comfyui.workflows` から読む |
| `localai_gradio_info` / `localai_gradio_call` | Gradio のサービスの API を名前で呼ぶ汎用の口（専用ツールに無い機能・細かい設定用） |

Open WebUI はブラウザの画面なので、起動・停止だけ。Ollama（11434）は LAN に出していないので、この MCP からできるのは `localai_ollama` での読み込み・解放まで。
載せたモデルとの会話は Open WebUI か、`tools/qwen-worker` の SSH 経由で行う。

### GPU の取り合い

A30 は 1 枚で、GPU を使うサービス（Strata 5 種・Hunyuan3D・Pixal3D・ComfyUI・AudioCraft・Qwen3-TTS・Stable Audio）は同時に 1 つしか動かない。
入口ページは起動のたびに他を止め、Strata・AudioCraft・Qwen3-TTS・Stable Audio の前には Ollama のモデルを降ろす。
**この MCP は、止まるもの・降ろされるものがあるときは起動せず、その一覧を返す。** 止めてよければ `stop_others=true` で呼び直す
（他の人が使っているかもしれないので、モデルには利用者に確かめるよう指示している）。
`localai_ollama` の `load` も同じで、GPU を使うサービスが動いていれば `stop_others=true` が無い限り読み込まない（Qwen 27B は VRAM を 22 GiB 使う）。

## 他の PC への導入

**Windows でいちばん簡単なのは、入口ページ（`http://<server-ip>:8080/`）の「他の PC から使う」の欄から `install-localai-mcp.cmd` をダウンロードしてダブルクリックすること**（入口ページがサーバーの住所を書き込んで作る。中身は下の PowerShell 用の 3 行と同じことをする）。

コマンドで入れるなら、入口ページ（8080）から台本を取って流す（リポジトリも共有も要らない。2026-11-25 から）。
台本は `localai_mcp.py` も入口ページ（`GET /remote/localai_mcp.py`）から取る。同じコマンドで更新もできる。終わったら Claude Code を開き直す。

```powershell
# Windows の PowerShell に貼る（入れ子の powershell -c にしない。外側が "..." の中の $ を展開して壊すため）
$ProgressPreference = 'SilentlyContinue'
Invoke-WebRequest -UseBasicParsing http://<server-ip>:8080/remote/setup-remote.ps1 -OutFile $env:TEMP\setup-remote.ps1
powershell -ExecutionPolicy Bypass -File $env:TEMP\setup-remote.ps1 -ServerIp <server-ip>
```

```bat
rem Windows の cmd に貼る
powershell -ExecutionPolicy Bypass -c "$ProgressPreference='SilentlyContinue'; Invoke-WebRequest -UseBasicParsing http://<server-ip>:8080/remote/setup-remote.ps1 -OutFile $env:TEMP\setup-remote.ps1; & $env:TEMP\setup-remote.ps1 -ServerIp <server-ip>"
```

`$ProgressPreference` を切るのは、Windows PowerShell 5.1 が Invoke-WebRequest / irm の進捗バーの描画で
`out-lineoutput : インデックスが配列の境界外です`（IndexOutOfRangeException）と落ちることがあるため（2026-11-25 に他の PC で、
入れ子の powershell から irm したときに出た）。

```bash
# Linux / macOS
curl -fsSL http://<server-ip>:8080/remote/setup-remote.sh | bash -s -- <server-ip>
```

同じコマンドは入口ページの `GET /api` の `mcp`（`install_windows` が PowerShell 用、`install_windows_cmd` が cmd 用）にもある。

リポジトリがあるときは、`tools/localai-mcp/` をサーバーの共有（`\\<server-ip>\<user>\Proj\localai\tools\localai-mcp\`）か clone から取り、1 回実行してもよい。
何度実行してもよい（登録を新しい値で入れ直す）。`localai_mcp.py` は写して使うので、新しい版（0.2.0 で `localai_ollama` と入口ページの `?wait=`、
0.3.0 で `localai_vfx` と `localai_comfyui` の `workflow_name` を追加）にするときも再実行する。

```bash
# Linux / macOS
bash tools/localai-mcp/remote/setup-remote.sh 192.168.1.10
```

```powershell
# Windows
powershell -ExecutionPolicy Bypass -File tools\localai-mcp\remote\setup-remote.ps1 -ServerIp 192.168.1.10
```

やること:

1. `localai_mcp.py` を `~/.local/share/localai-mcp/`（Windows は `%LOCALAPPDATA%\localai-mcp\`）に写す（台本の隣に無ければ入口ページから取る）
2. 起動に使う Python を決める（3.10 以上の `python3` / `py -3` / `python`、無ければ `uv run --no-project --python 3.12`）
3. `claude` があれば `claude mcp add -s user localai -e LOCALAI_SERVER=<ip> -- …`、`codex` があれば `codex mcp add localai --env LOCALAI_SERVER=<ip> -- …`。
   生成は数分〜30 分かかるので、Claude Code は `~/.claude/settings.json` の `env` に `MCP_TOOL_TIMEOUT=2100000`、Codex は `[mcp_servers.localai]` に `tool_timeout_sec = 2100` を足す（既に長ければ触らない）
4. 疎通確認（`localai_mcp.py --check`）: 入口ページに届くか、稼働中のサービスのポートに届くか

手で登録するとき:

```bash
claude mcp add -s user localai -e LOCALAI_SERVER=192.168.1.10 -- python3 ~/.local/share/localai-mcp/localai_mcp.py
codex mcp add localai --env LOCALAI_SERVER=192.168.1.10 -- python3 ~/.local/share/localai-mcp/localai_mcp.py
```

## サーバー側の準備（ufw）

この MCP は各サービスのポートへ直接話すので、ポートが LAN に開いている必要がある（`knowledge/10-silver/resources/lan-access.md`）。
開いていないと `--check` で「稼働中なのに届かない」と出る。サーバーで sudo を使って、LAN の 2 つのサブネットそれぞれに足す:

```bash
for cidr in <lan-cidr-1> <lan-cidr-2>; do
  for port in 8082 8083 8084 8085 8086 7862 7864 8188; do
    sudo ufw allow from "$cidr" to any port "$port" proto tcp comment "localai $port (LAN)"
  done
done
sudo ufw status numbered
```

## 使い方の例（Claude Code / Codex に頼む文）

- 「localai の状態を見て」
- 「Stable Audio を起動して、30 秒の lo-fi のビートを作って」
- 「audiocraft を起動して（Stable Audio は止めてよい）。hum.wav のメロディで 20 秒のピアノ曲を作って」
- 「pixal3d で front.png・left.png・right.png・back.png から 3D を作って」
- 「comfyui で workflows/api.json を投げて、10 番の画像に input.png を入れて」
- 「localai_vfx で、紫の魔法の霧の透明な動画を 832x480・81 コマで作って（ComfyUI は起動してよい）」
- 「layerdiffuse で、オレンジの火花の透明なスプライトを seed を変えて 4 枚作って」
- 「Ollama に入っているモデルを見せて」「qwen3.8:27b を 30 分載せておいて（Strata は止めてよい）」

## 試験

```bash
uvx --with pytest pytest tools/localai-mcp/tests -q     # 偽の HTTP サーバーで入口ページと各サービスの口を真似る
./localai test --only localai-mcp
LOCALAI_SERVER=<server-ip> python3 tools/localai-mcp/localai_mcp.py --check
```

## 注意

- Hunyuan3D・Pixal3D の API（`localai_mesh`）は、Gradio の `/gradio_api/info` から引数と既定値を読んで呼ぶ。Hunyuan3D は
  `/shape_generation`（`texture=true` で `/generation_all`）、Pixal3D は画面の生成ボタンの関数（`/_generate`）を探す。見つからなければ
  画像の 4 引数を持つ API を探す。細かい設定は `options` に引数名で渡す（`localai_gradio_info` で一覧が出る）。
- ComfyUI は **API 形式**のワークフローが要る。画面で保存した UI 形式は断る（画面の Export (API) か、サーバーの
  `tools/comfyui/scripts/run_workflow.py convert` で変換する）。
- `GPU_EXCLUSIVE_KEYS` などは入口ページ（`tools/portal/portal.py`）の写し。試験が一致を確かめるので、入口ページにサービスを足したらここも揃える。
