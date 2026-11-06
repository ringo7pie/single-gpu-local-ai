# portal-control

入口ページ（`tools/portal`、ポート 8080）の起動・停止ボタンと操作 API の事実。ポート配置と自動起動の方針は
`knowledge/10-silver/areas/web-services.md` を Read する。

## 起動・停止

- 入口ページのカードにある「起動」「停止」ボタンが `systemctl --user start|stop` を呼ぶ。
- Hunyuan3D は押してから使えるまで約 50 秒〜2 分かかる。その間は「起動中…」と表示される。
- 操作の API は `POST /api/service/<key>/<start|stop>`。**`X-Portal-Request: 1` ヘッダが必須**で、
  無いと 403 を返す。ヘッダの無い単純な POST は他サイトの JavaScript からでも送れるため、
  独自ヘッダでブラウザの preflight を強制して、外部サイトからの操作を防いでいる。
- 認証は無い。LAN 内からは誰でも操作できる。操作できるのは `open-webui`・`hunyuan3d`・`pixal3d` の
  3 つで、入口ページ自身は対象外なので自分を止められない。
