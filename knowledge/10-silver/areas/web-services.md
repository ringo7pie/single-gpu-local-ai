# web-services

このサーバーでブラウザから使うサービスの構成。手順は `tools/portal/README.md` を Read する。

## ポート配置

| ポート | サービス | systemd user ユニット | PC 起動時 |
|---|---|---|---|
| 8080 | 入口ページ（`tools/portal`） | `portal` | **起動する** |
| 8081 | Open WebUI | `open-webui` | 起動しない |
| 7860 | Hunyuan3D | `hunyuan3d` | 起動しない |
| 7861 | Pixal3D | `pixal3d` | 起動しない |
| 7862 | 音楽・効果音生成（AudioCraft。`areas/audiocraft.md`） | `audiocraft` | 起動しない |
| 8188 | ComfyUI | `comfyui` | 起動しない |
| 11434 | Ollama API | `ollama`（system） | 起動する |

- 8188 は ComfyUI（`tools/comfyui`）。enable はしていない。使い方と「画像 1 枚から 3D」の入口は
  `knowledge/10-silver/areas/comfyui.md` を Read する。
- Ollama だけは 127.0.0.1 で待受し、LAN に公開していない。入口ページ・Open WebUI・Hunyuan3D・Pixal3D は
  0.0.0.0 で待受する。

## 自動起動の方針（2026-09-22 決定）

- **PC 起動時に立ち上がるのは入口ページだけ。** Open WebUI・Hunyuan3D・Pixal3D は `disable` してある。
- 理由は RAM の枯渇でデスクトップがハングしたこと。数値と経緯は
  `knowledge/10-silver/areas/hunyuan3d.md` を Read する。
- 入口ページと Open WebUI は VRAM を使わない。VRAM を使うのは Hunyuan3D・Pixal3D と、Ollama がモデルを
  載せたときだけ。量は `hunyuan3d.md`・`pixal3d.md`・`env.md` を Read する。
- systemd の user ユニットで動く。`loginctl` の `Linger=yes` を設定済みなので、デスクトップにログインしなくても
  入口ページは立ち上がる。

## 入口ページからの起動・停止

- カードの「起動」「停止」が `systemctl --user start|stop` を呼ぶ。操作の API は `POST /api/service/<key>/<start|stop>` で、
  **`X-Portal-Request: 1` ヘッダが必須**（無いと 403）。ヘッダの理由・認証・操作できる対象・起動にかかる時間は
  `knowledge/10-silver/resources/portal-control.md` を Read する。

## 他の PC から使う

- 他の PC のブラウザで `http://<server-ip>:8080/`（例: 192.168.1.10）を開く。入口ページのリンクは開いた PC の
  アドレスをもとに組み立てるので、どの PC から開いても正しいホストを指す。
- 入口ページは使い方のページも配る（`GET /guides/<名前>.html` → `tools/portal/guides/`。2026-11-03 から ComfyUI の `pixelartistry.html`、2026-11-05 から動画生成の `video-generation.html`。ComfyUI のカードに 2 本のリンクが出る）。
  `portal.py` を変えたら `systemctl --user restart portal` が要る。HTML はリクエストのたびに読むので再起動は要らない。
- **サービスを足したら ufw でそのポートを開ける。** 開けないと、サーバー自身からは動くのに
  他の PC からは見えない。手順と切り分けは
  `knowledge/10-silver/resources/lan-access.md` を Read する。
- GPU を使う 2 つ（Hunyuan3D・Pixal3D）は排他で、切り替えに数分かかる。詳細は
  `knowledge/10-silver/resources/gpu-exclusive-switching.md` を Read する。
- Open WebUI のモデル一覧や改名の注意、8080 から 8081 へ移した経緯と戻し方は `knowledge/10-silver/resources/open-webui.md` を Read する。
