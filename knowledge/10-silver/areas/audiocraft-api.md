# audiocraft-api

audiocraft（`tools/audiocraft`、ポート 7862）を、画面を使わずプログラムから呼ぶ API の運用上の事実。
モデル・VRAM・RAM・排他は `areas/audiocraft.md` を Read する。経路・本文の項目・応答の一覧は
`tools/audiocraft/README.md` の「API」を Read する。2026-10-27 に REST の口を足した。

## 口は 2 つある

- REST（`POST /api/v1/music`・`/api/v1/sound`、`GET /api/v1/health`）は、説明文を JSON で送ると wav を本文でそのまま返す。
  実際に使った seed・長さ・所要時間は応答ヘッダ `X-Acraft-*` に入る。`keep` を `false` にすると `outputs/` に wav と json を残さない。
- Gradio の API（`/gradio_api/...`。`gradio_client` の `api_name` は `/generate_music`・`/generate_sound`）も従来どおり使える。
  REST を足したあとも、実機で `gradio_client` の呼び出しが通ることを確かめた。
- 認証は無い。LAN 内の前提で、入口ページと同じ割り切り。自動の API 文書（`/docs`）は出さない。

## 実装の決定

- REST の経路は、Gradio が `launch(prevent_thread_lock=True)` で作った FastAPI アプリの先頭へ `rest.install_routes` で足す。
  Gradio の経路と重ならないよう `/api/v1/` の下に置く。
- `mount_gradio_app` で画面を `/` に載せる方式は使わない。この方式だと `/` が `//` へ 307 で転送され、画面の URL が崩れた。
- 後ろに足す方式も使わない。Gradio のアプリは見つからない経路を自前で処理するので、REST の経路に届かない。先頭に置くと、
  GET などの誤った呼び方も画面側の 404 でなく 405（`Allow: POST`）で返せる。
- `Content-Type` が `application/json` でない POST は 415 で断る。他サイトの form や fetch が事前確認なしで送れる型を
  断るためで、入口ページが `X-Portal-Request` ヘッダを必須にしているのと同じ考え方。CORS の許可は出さない。

## 混雑の扱い（実測）

- 生成は 1 度に 1 件。画面の分も同じ Engine の Lock で直列になる。
- REST は、順番待ちを含めて同時に 4 件まで受け付ける（`config.REST_MAX_PENDING`）。超えると 503 と `Retry-After: 30` を返す。
- 実機で作曲 5 秒を同時に 6 件送ると、4 件が 5.1・10.2・15.2・20.1 秒で 200、2 件が 6 ミリ秒で 503 だった。
  終了後の `/api/v1/health` の `pending` は 0 に戻った。
- 作曲 30 秒は約 33 秒かかるので、クライアントの待ち時間は 2 分以上にする。

## 実測した応答（curl で `<server-ip>:7862`、重みがキャッシュにある状態）

- 作曲 10 秒は 10.3 秒で、640,044 バイトの wav（32 kHz・モノラル）。効果音 5 秒は 5.0 秒で、160,044 バイトの wav（16 kHz）。
- REST の呼び出しでも、RAM の cgroup `memory.peak` は約 5.2 GiB、VRAM は全体で約 11.4 GiB だった。
- 入力の誤り（説明文が空・長さが範囲外・未知の項目）は 400 と日本語の理由、`text/plain` は 415 を返した。

## 未確認

- 別の PC からの到達は確かめていない。ufw が未実施のため。サーバー自身の LAN アドレス経由では確かめた。
- ブラウザの別サイトの JavaScript から呼べないことは、Gradio の CORS の既定に頼っており、ブラウザでは確かめていない。
