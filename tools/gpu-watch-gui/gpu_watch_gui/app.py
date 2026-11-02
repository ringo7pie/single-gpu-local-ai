#!/usr/bin/env python3
"""gpu-watch.sh の値を見るだけの小さな常駐ウィンドウ（GTK4）。

センサーの読み取りは一切行わない。tools/gpu-watch.sh を `-o raw` で子プロセスとして起動し、
その出力（INFO/HOST/FAN/GPU 行）を gpu_watch_gui.model で解析して表示するだけ。
取れない値（n/a）は推測で埋めず、そのまま n/a と出す。

書き込みはファン補正スライダーだけ。A30 ファンの自動カーブへの加減（-50〜+50）を
gpu_watch_gui.fan_offset のファイルへ 1 秒ごとに書き、a30-fan.service の fan_host.py が読む。
補正が 0 のときはファイルを置かない。窓を閉じるとファイルを消す。

GTK を import するのはこのファイルだけ（model.py は GTK 非依存の純 Python）。
子プロセスの stdout は GLib の非同期読み（Gio.Subprocess + Gio.DataInputStream.read_line_async）
で読み、UI スレッドをブロックしない。窓を閉じたら子プロセスへ SIGTERM を送って終了する。
子プロセスが（原因を問わず）落ちたら最下行に理由を表示し、5 秒後に再起動を試みる。

PC 全体の電力は、Tapo スマートプラグ（P110M）の実測を出せる。ログイン画面（「Tapo」ボタン）で TP-Link ID と
パスワードを受け取り、読み取り役 tapo_power.py（専用 venv の python）を子プロセスとして起動して標準入力で渡す。
パスワードはファイルに保存せず、このプロセスのメモリにだけ置く。実測が取れない間は推定に戻す。

起動方法は run-gpu-watch-gui を使う（システムの /usr/bin/python3 を使い、venv は作らない）。
"""
from __future__ import annotations

import argparse
import faulthandler
import json
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Dict, Optional

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk, Pango  # noqa: E402  (gi.require_version の後に import する)

from gpu_watch_gui import fan_offset, model, tapo_feed
from gpu_watch_gui.model import AlertEvent, GpuWatchModel, load_level, plain_level, temp_level

APP_ID = "jp.localai.GpuWatchGui"
GUI_DIR = Path(__file__).resolve().parent.parent
GPU_WATCH_SH = GUI_DIR.parent / "gpu-watch.sh"
CALIBRATE_SCRIPT = GUI_DIR / "calibrate-power.py"
# 較正の保存先。root で作る calibrate-power.py が利用者のホームにファイルを作らないよう、
# JSON は stdout 経由で受け取り、保存はここ（非 root のこのプロセス）が行う。
STATE_DIR = GUI_DIR / "state"
CALIBRATION_FILE = STATE_DIR / "calibration.json"

# メインループが止まったとき（ハング）の診断。1 秒ごとの tick が HANG_DUMP_SEC 秒途切れたら、
# 全スレッドの Python スタックをこのファイルに追記する。書くのは関数名・行番号だけで、値（パスワード等）は含まない。
HANG_LOG_FILE = STATE_DIR / "hang-traceback.log"
HANG_DUMP_SEC = 20
RESTART_DELAY_SEC = 5
CALIBRATE_DONE_DISPLAY_SEC = 5
FAILURE_DISPLAY_SEC = 15  # 失敗の理由は読めるよう長めに残す（全文はツールチップ）
VALUE_WIDTH_CHARS = 7  # 「未較正」「21% 13G」「3.5/24G」が収まる幅
MAX_WIDTH_CHARS = 5
ESTIMATE_PREFIX = "≈"  # 推定値の印。項目名に (推定) と書くと横幅が伸びるため値の頭に付ける
LEVEL_CSS_CLASSES = ("level-critical", "level-warning", "level-ok", "level-unknown", "level-plain")
FAN_OFFSET_SCALE_MIN_WIDTH = 60  # 窓が横に伸びないよう、スライダーは「現在」「最大」の 2 列に収まる幅にする
FAN_OFFSET_TOOLTIP = (
    "A30 ファンの自動カーブへの加減（%ポイント）。結果は 30〜100% に収まる。\n"
    "GPU 70℃ 以上では下げ側（マイナス）は無視される。GPU Watch を閉じると 0（自動）に戻る。\n"
    "実際の PWM はファンの行で確認する"
)

CSS = b"""
.level-critical { color: #ff4d4d; font-weight: bold; }
.level-warning  { color: #e6b800; font-weight: bold; }
.level-ok       { color: #2ecc71; }
.level-unknown  { color: #888888; }
.level-plain    { }
.gpu-watch-colhead { font-size: smaller; }
separator.gpu-watch-separator { background: #808080; min-height: 1px; }
.gpu-watch-status { font-size: smaller; }
button.gpu-watch-calibrate, button.gpu-watch-fan-reset { padding: 0 6px; min-height: 0; font-size: smaller; }
"""


def parse_args(argv: Optional[list] = None) -> argparse.Namespace:
    """-n・-w・-t・-T・-i・-F・-P を gpu-watch.sh と同じ既定値で受け取る。"""
    parser = argparse.ArgumentParser(description="gpu-watch.sh の値を見る小さな常駐ウィンドウ")
    parser.add_argument("-n", dest="interval", type=int, default=1, metavar="SEC",
                        help="サンプリング間隔（秒）。既定 1")
    parser.add_argument("-w", dest="window", type=int, default=60, metavar="SEC",
                        help="窓内最大の対象期間（秒）。既定 60")
    parser.add_argument("-t", dest="gpu_threshold", type=int, default=80, metavar="DEG",
                        help="GPU 温度アラートのしきい値（摂氏）。既定 80")
    parser.add_argument("-T", dest="cpu_threshold", type=int, default=90, metavar="DEG",
                        help="CPU 温度アラートのしきい値（摂氏）。既定 90")
    parser.add_argument("-i", dest="gpu_index", default="", metavar="IDX",
                        help="監視する GPU（nvidia-smi -i と同じ書式）。既定は全 GPU")
    parser.add_argument("-F", dest="fan_unit", default="a30-fan.service", metavar="UNIT",
                        help="ファン PWM を読む systemd ユニット。既定 a30-fan.service。off で無効")
    parser.add_argument("-P", dest="baseline_watt", default=None, metavar="WATT",
                        help="筐体ベースライン（W）。省略すると推定は出さない")
    parser.add_argument("--psu-efficiency", dest="psu_efficiency", type=float,
                        default=model.DEFAULT_PSU_EFFICIENCY, metavar="RATIO",
                        help="PC 全体(推定)で使う電源ユニット効率（0 より大きく 1 以下）。既定 0.90")
    parser.add_argument("--app-id", dest="app_id", default=APP_ID, metavar="ID",
                        help="GLib アプリケーション ID。既定時の二重起動防止を避け、"
                             "試験で別インスタンスを起動するためのもの")
    parser.add_argument("--fan-offset-file", dest="fan_offset_file", default=str(fan_offset.DEFAULT_PATH),
                        metavar="PATH",
                        help="ファン補正値を書くファイル。既定は fan_host.py が読む場所。"
                             "試験で別インスタンスが本番の補正を消さないよう分けるためのもの")
    parser.add_argument("--tapo", choices=("auto", "off"), default="auto",
                        help="PC 全体の実測に Tapo を使う。auto は起動時にログイン画面を出す。既定 auto")
    parser.add_argument("--tapo-host", dest="tapo_host", default="", metavar="IP",
                        help="Tapo プラグの IP。ログイン画面の初期値（省略時は前回の入力）")
    parser.add_argument("--tapo-python", dest="tapo_python", default=str(tapo_feed.DEFAULT_PYTHON), metavar="PATH",
                        help="tapo を入れた専用 venv の python。既定 ~/.local/share/tapo-meter/.venv/bin/python")
    parser.add_argument("--tapo-interval", dest="tapo_interval", type=float, default=tapo_feed.DEFAULT_INTERVAL_SEC,
                        metavar="SEC", help="Tapo の読み取り間隔（秒、2 以上）。既定 5")
    args = parser.parse_args(argv)
    if args.tapo_interval < 2:
        parser.error(f"--tapo-interval は 2 以上で指定する: {args.tapo_interval}")
    if not (0 < args.psu_efficiency <= 1):
        parser.error(f"--psu-efficiency は 0 より大きく 1 以下で指定する: {args.psu_efficiency}")
    return args


def build_feeder_argv(args: argparse.Namespace) -> list:
    """gpu-watch.sh -o raw の起動コマンドライン（受け取った引数をそのまま渡す）。"""
    argv = [
        str(GPU_WATCH_SH), "-o", "raw",
        "-n", str(args.interval), "-w", str(args.window),
        "-t", str(args.gpu_threshold), "-T", str(args.cpu_threshold),
        "-F", args.fan_unit,
    ]
    if args.gpu_index:
        argv += ["-i", args.gpu_index]
    if args.baseline_watt is not None:
        argv += ["-P", str(args.baseline_watt)]
    return argv


def notify_critical(summary: str, body: str) -> None:
    """失敗しても落ちない（デスクトップ通知は付加機能で、本体の表示を止めてはいけない）。"""
    try:
        subprocess.Popen(
            ["notify-send", "-u", "critical", summary, body],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except OSError:
        pass


def format_fan_offset(offset: int) -> str:
    return f"{offset:+d}" if offset else "0"


def format_scalar(value: Optional[float], unit: str, precision: int = 0, prefix: str = "") -> str:
    if value is None:
        return "n/a"
    return f"{prefix}{value:.{precision}f}{unit}"


def format_mem(used_mib: Optional[float], pct: Optional[float]) -> str:
    """「21% 13G」。詳しい値（小数・全体量）はツールチップに出す。"""
    if used_mib is None or pct is None:
        return "n/a"
    return f"{pct:.0f}% {used_mib / 1024:.0f}G"


def format_vram(used_mib: Optional[float], total_mib: Optional[float]) -> str:
    """「3.5/24G」。MiB の値はツールチップに出す。"""
    if used_mib is None or total_mib is None:
        return "n/a"
    return f"{used_mib / 1024:.1f}/{total_mib / 1024:.0f}G"


def format_gib(used_mib: Optional[float]) -> str:
    if used_mib is None:
        return "n/a"
    return f"{used_mib / 1024:.1f}G"


def stamp_time_part(stamp: Optional[str]) -> str:
    """gpu-watch.sh の "2026/10/02 10:40:25.135" から "10:40:25" だけを取り出す。"""
    if not stamp:
        return ""
    parts = stamp.split()
    clock = parts[1] if len(parts) >= 2 else parts[0]
    return clock.split(".")[0]


class MetricRow:
    """「項目名 / 現在値 / 60 秒最大」の 1 行。色は現在値セルに CSS クラスで付ける。"""

    def __init__(self, grid: Gtk.Grid, row: int, label_text: str):
        self.name_label = Gtk.Label(label=label_text, xalign=0)
        self.value_label = Gtk.Label(label="n/a", xalign=1)
        self.max_label = Gtk.Label(label="n/a", xalign=1)
        self.value_label.set_width_chars(VALUE_WIDTH_CHARS)
        self.max_label.set_width_chars(MAX_WIDTH_CHARS)
        grid.attach(self.name_label, 0, row, 1, 1)
        grid.attach(self.value_label, 1, row, 1, 1)
        grid.attach(self.max_label, 2, row, 1, 1)

    def set_values(self, current_text: str, max_text: str, level: str) -> None:
        self.value_label.set_label(current_text)
        self.max_label.set_label(max_text)
        for css_class in LEVEL_CSS_CLASSES:
            self.value_label.remove_css_class(css_class)
        self.value_label.add_css_class(f"level-{level}")

    def set_tooltip(self, text: str) -> None:
        self.value_label.set_tooltip_text(text or None)

    def set_name_tooltip(self, text: str) -> None:
        self.name_label.set_tooltip_text(text or None)

    def set_name(self, text: str) -> None:
        """実測/推定の切り替えなど、項目名を後から変える行のためのメソッド。"""
        self.name_label.set_label(text)


class LineFeeder:
    """子プロセスの stdout を 1 行ずつ読み、終了したら通知する。stdin には起動時に 1 回だけ書ける。

    gpu-watch.sh 用の既存の読み取り（GpuWatchWindow._start_feeder）とは別に、Tapo 用に切り出した部品。
    stop() の後は on_line / on_exit を呼ばない。起動に失敗したら GLib.Error をそのまま投げる。
    """

    def __init__(self, argv: list, on_line, on_exit, stdin_bytes: Optional[bytes] = None) -> None:
        self._on_line = on_line
        self._on_exit = on_exit
        self._stopped = False
        self.last_stderr = ""
        flags = Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE
        if stdin_bytes is not None:
            flags |= Gio.SubprocessFlags.STDIN_PIPE
        self._process = Gio.SubprocessLauncher.new(flags).spawnv(argv)
        if stdin_bytes is not None:
            try:
                stdin_pipe = self._process.get_stdin_pipe()
                stdin_pipe.write_all(stdin_bytes, None)
                stdin_pipe.close(None)
            except GLib.Error:
                pass  # 子が先に終了していた。理由は on_exit で報告される
        self._stdout = Gio.DataInputStream.new(self._process.get_stdout_pipe())
        self._stderr = Gio.DataInputStream.new(self._process.get_stderr_pipe())
        self._read(self._stdout, self._handle_stdout)
        self._read(self._stderr, self._handle_stderr)
        self._process.wait_async(None, self._handle_exit)

    @staticmethod
    def _read(stream: Gio.DataInputStream, callback) -> None:
        stream.read_line_async(GLib.PRIORITY_DEFAULT, None, callback)

    def _next_line(self, stream: Gio.DataInputStream, result: Gio.AsyncResult) -> Optional[str]:
        try:
            line_bytes, _length = stream.read_line_finish(result)
        except GLib.Error:
            return None
        if not line_bytes:
            return None
        return line_bytes.decode("utf-8", errors="replace")

    def _handle_stdout(self, stream: Gio.DataInputStream, result: Gio.AsyncResult) -> None:
        line = self._next_line(stream, result)
        if line is None or self._stopped:
            return
        self._on_line(line)
        self._read(stream, self._handle_stdout)

    def _handle_stderr(self, stream: Gio.DataInputStream, result: Gio.AsyncResult) -> None:
        line = self._next_line(stream, result)
        if line is None:
            return
        self.last_stderr = line.strip()
        self._read(stream, self._handle_stderr)

    def _handle_exit(self, process: Gio.Subprocess, result: Gio.AsyncResult) -> None:
        try:
            process.wait_finish(result)
        except GLib.Error:
            pass
        if self._stopped:
            return
        exit_code = process.get_exit_status() if process.get_if_exited() else -1
        self._on_exit(exit_code, self.last_stderr)

    def stop(self) -> None:
        self._stopped = True
        try:
            self._process.send_signal(15)  # SIGTERM
        except GLib.Error:
            pass


class TapoLoginWindow(Gtk.Window):
    """TP-Link ID・パスワード・プラグの IP を受け取る小さなログイン画面。

    パスワードは on_submit の引数として 1 回渡すだけで、ここには残さない（送った後に入力欄を空にする）。
    メールアドレスと IP は次回の初期値として呼び出し側が保存する。パスワードは保存しない。
    """

    def __init__(self, parent: Gtk.Window, host: str, username: str, message: str,
                 on_submit, on_cancel, on_closed) -> None:
        super().__init__(title="Tapo にログイン", transient_for=parent, modal=True)
        self.set_resizable(False)
        self._on_submit = on_submit
        self._on_cancel = on_cancel
        self._on_closed = on_closed  # 閉じるたびに必ず呼ぶ（参照を手放してもらう）
        self._submitted = False

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for setter in (box.set_margin_top, box.set_margin_bottom, box.set_margin_start, box.set_margin_end):
            setter(12)
        self.set_child(box)

        intro = Gtk.Label(
            label="PC 全体の電力を、Tapo プラグ（P110M）の実測で表示します。\n"
                  "パスワードは保存しません（この画面を閉じた後はメモリにだけ置きます）。",
            xalign=0,
        )
        box.append(intro)

        grid = Gtk.Grid(column_spacing=8, row_spacing=6)
        box.append(grid)
        self.host_entry = Gtk.Entry(text=host, hexpand=True, placeholder_text="例 192.168.8.128")
        self.username_entry = Gtk.Entry(text=username, hexpand=True, placeholder_text="TP-Link ID（メールアドレス）")
        self.password_entry = Gtk.PasswordEntry(show_peek_icon=True, hexpand=True)
        for row, (caption, entry) in enumerate((
            ("プラグの IP", self.host_entry),
            ("TP-Link ID", self.username_entry),
            ("パスワード", self.password_entry),
        )):
            grid.attach(Gtk.Label(label=caption, xalign=0), 0, row, 1, 1)
            grid.attach(entry, 1, row, 1, 1)
        for entry in (self.host_entry, self.username_entry, self.password_entry):
            entry.connect("activate", self._on_activate)

        self.message_label = Gtk.Label(label=message, xalign=0, wrap=True, max_width_chars=48)
        self.message_label.add_css_class("level-critical" if message else "level-plain")
        box.append(self.message_label)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, halign=Gtk.Align.END)
        later_button = Gtk.Button(label="あとで")
        later_button.connect("clicked", lambda _button: self.close())
        self.login_button = Gtk.Button(label="ログイン")
        self.login_button.add_css_class("suggested-action")
        self.login_button.connect("clicked", self._on_activate)
        buttons.append(later_button)
        buttons.append(self.login_button)
        box.append(buttons)
        self.set_default_widget(self.login_button)

        self.connect("close-request", self._on_close_request)
        # 入っていない欄から順にフォーカスを当てる（IP とメールは前回の値が入っている）
        for entry in (self.host_entry, self.username_entry, self.password_entry):
            if not entry.get_text():
                entry.grab_focus()
                break
        else:
            self.password_entry.grab_focus()

    def _show_problem(self, text: str) -> None:
        self.message_label.set_label(text)
        self.message_label.remove_css_class("level-plain")
        self.message_label.add_css_class("level-critical")

    def _on_activate(self, _widget: Gtk.Widget) -> None:
        valid, host_or_problem = tapo_feed.validate_host(self.host_entry.get_text())
        if not valid:
            self._show_problem(host_or_problem)
            return
        username = self.username_entry.get_text().strip()
        password = self.password_entry.get_text()
        if not username or not password:
            self._show_problem("TP-Link ID とパスワードを入力してください")
            return
        self._submitted = True
        self.password_entry.set_text("")
        self.close()
        self._on_submit(host_or_problem, username, password)

    def _on_close_request(self, _window: Gtk.Window) -> bool:
        self.password_entry.set_text("")
        if not self._submitted:
            self._on_cancel()
        self._on_closed()
        return False


class GpuWatchWindow(Gtk.ApplicationWindow):
    def __init__(self, application: Gtk.Application, args: argparse.Namespace):
        super().__init__(application=application, title="GPU Watch")
        # 中身の自然な大きさに合わせる。値の列は幅を固定しているので伸び縮みしない。
        self.set_resizable(False)

        self.args = args
        self.model = GpuWatchModel(
            gpu_threshold=float(args.gpu_threshold),
            cpu_threshold=float(args.cpu_threshold),
            window_sec=float(args.window),
            interval_sec=float(args.interval),
            psu_efficiency=float(args.psu_efficiency),
        )

        self._subprocess: Optional[Gio.Subprocess] = None
        self._stdout_stream: Optional[Gio.DataInputStream] = None
        self._stderr_stream: Optional[Gio.DataInputStream] = None
        self._last_stderr_line = ""
        self._restart_source_id: Optional[int] = None
        self._shutting_down = False
        self._gpu_rows: Dict[str, Dict[str, MetricRow]] = {}
        self._gpu_headings: Dict[str, Gtk.Label] = {}
        self._calibrate_process: Optional[Gio.Subprocess] = None
        # 一定時間だけ最下行に残すメッセージ（較正の完了・失敗）。毎秒の「更新」で消さないため。
        self._held_message: Optional[str] = None
        self._held_until = 0.0
        # ファン補正スライダー。-F off（ファン行なし）のときは作らない。
        self._fan_offset = 0
        self._fan_offset_path = Path(args.fan_offset_file)
        self._fan_offset_published = False  # 0 以外の値をファイルに書いている間だけ True
        self.fan_offset_scale: Optional[Gtk.Scale] = None
        self.fan_offset_name: Optional[Gtk.Label] = None
        # Tapo（PC 全体の実測）。資格情報はこのメモリにだけ置き、ファイルには保存しない。
        self._tapo_credentials: Optional[tuple] = None  # (host, username, password)
        self._tapo_feeder: Optional[LineFeeder] = None
        self._tapo_login_window: Optional[TapoLoginWindow] = None
        self._tapo_restart_source_id: Optional[int] = None
        self._tapo_note = ""  # 実測が出ていない理由。「PC 全体」のツールチップに添える

        self._build_ui()
        self.connect("close-request", self._on_close_request)

        self._load_calibration_at_startup()
        self._start_hang_watchdog()
        self._start_feeder()
        GLib.timeout_add_seconds(1, self._on_tick)
        if args.tapo == "auto":
            GLib.timeout_add_seconds(1, self._startup_tapo_login)

    # ---- UI 組み立て --------------------------------------------------

    def _build_ui(self) -> None:
        # 縦に詰めるため見出し行（HOST / FAN / 較正 / 最終更新）は置かない。
        # 表の下に「較正ボタン + 状態 1 行」を別の箱で置く。GPU の行は値が届いてから表に足すので、
        # 表と最下行を分けておかないと GPU の行が最下行より下に並んでしまう。
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        outer.set_margin_top(6)
        outer.set_margin_bottom(6)
        outer.set_margin_start(8)
        outer.set_margin_end(8)
        self.set_child(outer)

        self.grid = Gtk.Grid(column_spacing=8, row_spacing=0)
        outer.append(self.grid)

        self._next_row = 0
        for column, text_value in enumerate(("", "現在", f"最大{self.args.window}s")):
            head = Gtk.Label(label=text_value, xalign=1)
            head.add_css_class("gpu-watch-colhead")
            self.grid.attach(head, column, self._next_row, 1, 1)
        self._next_row += 1

        # 並びは「PC 全体 ─ CPU 系 ─ GPU 系」。区切り線で 3 つに分ける。
        self.row_system_w = self._metric_row("PC 全体")
        self._separator_row()

        self.row_cpu = self._metric_row("CPU")
        self.row_cpu_temp = self._metric_row("CPU 温度")
        self.row_mem = self._metric_row("メモリ")
        self.row_cpu_w = self._metric_row("CPU 電力")
        self.row_psys_w = self._metric_row("PSys")
        for row in (self.row_cpu_w, self.row_psys_w, self.row_system_w):
            row.set_name_tooltip(f"値の頭の {ESTIMATE_PREFIX} は推定値。付いていなければ実測")
        self.row_cpu.set_name_tooltip("CPU 使用率")
        self._separator_row()

        # GPU の行は値が届いてから、この位置（ファンの行の手前）に差し込む。
        # ファンは A30 を冷やすファン（a30-fan.service）なので GPU 系の最後に置く。
        self._gpu_insert_row = self._next_row
        if self.args.fan_unit != "off":
            self.row_fan = self._metric_row("ファン")
            self.row_fan.set_name_tooltip("A30 のファン。Pico H への PWM 指令値で、回転数の実測ではない")
            self._build_fan_offset_row()
        else:
            self.row_fan = None

        bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.calibrate_button = Gtk.Button(label="較正")
        self.calibrate_button.add_css_class("gpu-watch-calibrate")
        self.calibrate_button.set_tooltip_text("電力の推定式を作り直す（pkexec でパスワードを聞かれる。約 1 分）")
        self.calibrate_button.connect("clicked", self._on_calibrate_clicked)
        bottom.append(self.calibrate_button)
        if self.args.tapo == "auto":
            self.tapo_button = Gtk.Button(label="Tapo")
            self.tapo_button.add_css_class("gpu-watch-calibrate")
            self.tapo_button.set_tooltip_text("Tapo プラグにログインして、PC 全体を実測で表示する（パスワードは保存しない）")
            self.tapo_button.connect("clicked", lambda _button: self._open_tapo_login(""))
            bottom.append(self.tapo_button)

        self.status_label = Gtk.Label(label="起動中", xalign=0)
        self.status_label.add_css_class("gpu-watch-status")
        self.status_label.set_hexpand(True)
        # 長い理由でウィンドウが横に伸びないよう省略する。全文はツールチップ。
        self.status_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.status_label.set_max_width_chars(14)
        bottom.append(self.status_label)
        outer.append(bottom)

        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(), css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def _metric_row(self, label_text: str) -> MetricRow:
        row = MetricRow(self.grid, self._next_row, label_text)
        self._next_row += 1
        return row

    def _build_fan_offset_row(self) -> None:
        """ファンの行の下に、自動カーブへの補正スライダー（-50〜+50）を 1 行で置く。

        現在値は項目名に出し（「補正 +10」）、「現在」「最大」の 2 列にはスライダーと 0 に戻すボタンを入れる。
        """
        self.fan_offset_name = Gtk.Label(label="補正 0", xalign=0)
        self.fan_offset_name.set_tooltip_text(FAN_OFFSET_TOOLTIP)
        self.grid.attach(self.fan_offset_name, 0, self._next_row, 1, 1)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.fan_offset_scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, -fan_offset.OFFSET_LIMIT, fan_offset.OFFSET_LIMIT, 1,
        )
        self.fan_offset_scale.set_digits(0)
        self.fan_offset_scale.set_value(0)  # new_with_range の初期値は最小値（-50）。接続の前に 0 へ戻す
        self.fan_offset_scale.set_draw_value(False)
        self.fan_offset_scale.set_hexpand(True)
        self.fan_offset_scale.set_size_request(FAN_OFFSET_SCALE_MIN_WIDTH, -1)
        self.fan_offset_scale.set_tooltip_text(FAN_OFFSET_TOOLTIP)
        self.fan_offset_scale.connect("value-changed", self._on_fan_offset_changed)
        box.append(self.fan_offset_scale)

        reset_button = Gtk.Button(label="0")
        reset_button.add_css_class("gpu-watch-fan-reset")
        reset_button.set_tooltip_text("補正を 0（自動カーブそのまま）に戻す")
        reset_button.connect("clicked", lambda _button: self.fan_offset_scale.set_value(0))
        box.append(reset_button)

        self.grid.attach(box, 1, self._next_row, 2, 1)
        self._next_row += 1

    def _on_fan_offset_changed(self, scale: Gtk.Scale) -> None:
        self._fan_offset = fan_offset.clamp_offset(scale.get_value())
        self.fan_offset_name.set_label(f"補正 {format_fan_offset(self._fan_offset)}")
        self._publish_fan_offset()

    def _publish_fan_offset(self) -> None:
        """補正が 0 以外の間だけ、ファイルを書き直す（fan_host.py は約 10 秒古いと 0 に戻す）。

        0 に戻したらファイルを消す。0 のままなら何も書かない（常駐する窓が毎秒ディスクを書かないため）。
        """
        if self.fan_offset_scale is None:
            return
        try:
            if self._fan_offset != 0:
                fan_offset.write_offset(self._fan_offset_path, self._fan_offset, time.time())
                self._fan_offset_published = True
            elif self._fan_offset_published:
                fan_offset.clear_offset(self._fan_offset_path)
                self._fan_offset_published = False
        except OSError as error:
            self._hold_message(f"ファン補正を書けません: {error}", FAILURE_DISPLAY_SEC)

    def _withdraw_fan_offset(self) -> None:
        """窓を閉じる・終了要求を受けたとき、補正を取り下げて自動カーブに戻す。"""
        if self.fan_offset_scale is not None:
            fan_offset.clear_offset(self._fan_offset_path)
            self._fan_offset_published = False

    def _separator_row(self) -> None:
        separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        separator.add_css_class("gpu-watch-separator")
        separator.set_margin_top(3)
        separator.set_margin_bottom(3)
        self.grid.attach(separator, 0, self._next_row, 3, 1)
        self._next_row += 1

    def _gpu_metric_row(self, label_text: str) -> MetricRow:
        """GPU 系の行を、ファンの行より前に差し込む（Gtk.Grid.insert_row で下の行をずらす）。"""
        self.grid.insert_row(self._gpu_insert_row)
        row = MetricRow(self.grid, self._gpu_insert_row, label_text)
        self._gpu_insert_row += 1
        self._next_row += 1
        return row

    def _ensure_gpu_rows(self, index: str, name: str) -> Dict[str, MetricRow]:
        if index in self._gpu_rows:
            return self._gpu_rows[index]
        # 見出し行の代わりに項目名へ GPU 番号を入れ、GPU 名はツールチップに出す。
        prefix = f"GPU{index}"
        rows = {
            "util": self._gpu_metric_row(prefix),
            "temp": self._gpu_metric_row(f"{prefix} 温度"),
            "vram": self._gpu_metric_row("VRAM" if index == "0" else f"VRAM{index}"),
            "power": self._gpu_metric_row(f"{prefix} 電力"),
        }
        for row in rows.values():
            row.set_name_tooltip(f"GPU {index}: {name}")
        self._gpu_rows[index] = rows
        return rows

    # ---- 子プロセス（gpu-watch.sh -o raw）の起動・読み取り --------------

    def _start_feeder(self) -> None:
        argv = build_feeder_argv(self.args)
        launcher = Gio.SubprocessLauncher.new(
            Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE
        )
        try:
            self._subprocess = launcher.spawnv(argv)
        except GLib.Error as error:
            self._on_feeder_died(f"起動に失敗: {error.message}")
            return

        self._last_stderr_line = ""
        self._stdout_stream = Gio.DataInputStream.new(self._subprocess.get_stdout_pipe())
        self._stderr_stream = Gio.DataInputStream.new(self._subprocess.get_stderr_pipe())
        self._read_stdout_line()
        self._read_stderr_line()
        self._subprocess.wait_async(None, self._on_subprocess_exited)

    def _read_stdout_line(self) -> None:
        if self._stdout_stream is None:
            return
        self._stdout_stream.read_line_async(GLib.PRIORITY_DEFAULT, None, self._on_stdout_line)

    def _on_stdout_line(self, stream: Gio.DataInputStream, result: Gio.AsyncResult) -> None:
        try:
            line_bytes, _length = stream.read_line_finish(result)
        except GLib.Error:
            return  # ストリームが閉じた。_on_subprocess_exited 側で停止扱いにする
        if not line_bytes:
            return  # EOF。子プロセスの終了は wait_async 側で検知する
        line = line_bytes.decode("utf-8", errors="replace")
        event = self.model.feed_line(line)
        self._refresh_view()
        if event is not None:
            self._handle_alert(event)
        self._read_stdout_line()

    def _read_stderr_line(self) -> None:
        if self._stderr_stream is None:
            return
        self._stderr_stream.read_line_async(GLib.PRIORITY_DEFAULT, None, self._on_stderr_line)

    def _on_stderr_line(self, stream: Gio.DataInputStream, result: Gio.AsyncResult) -> None:
        try:
            line_bytes, _length = stream.read_line_finish(result)
        except GLib.Error:
            return
        if not line_bytes:
            return
        self._last_stderr_line = line_bytes.decode("utf-8", errors="replace").strip()
        self._read_stderr_line()

    def _on_subprocess_exited(self, subprocess_obj: Gio.Subprocess, result: Gio.AsyncResult) -> None:
        try:
            subprocess_obj.wait_finish(result)
        except GLib.Error:
            pass
        if self._shutting_down:
            return
        exit_status = subprocess_obj.get_exit_status()
        reason = f"gpu-watch.sh が終了しました（終了コード {exit_status}）"
        if self._last_stderr_line:
            reason += f": {self._last_stderr_line}"
        self._on_feeder_died(reason)

    def _on_feeder_died(self, reason: str) -> None:
        if self._shutting_down:
            return
        self._subprocess = None
        self._stdout_stream = None
        self._stderr_stream = None
        self._set_status(f"停止中（{RESTART_DELAY_SEC}秒後に再起動）", f"停止中: {reason}")
        if self._restart_source_id is None:
            self._restart_source_id = GLib.timeout_add_seconds(RESTART_DELAY_SEC, self._on_restart_timeout)

    def _on_restart_timeout(self) -> bool:
        self._restart_source_id = None
        if not self._shutting_down:
            self._start_feeder()
        return GLib.SOURCE_REMOVE

    def _stop_feeder(self) -> None:
        self._shutting_down = True
        self._stop_hang_watchdog()  # 終了処理の共通の入口なので、ここで外す
        self._stop_tapo()
        if self._restart_source_id is not None:
            GLib.source_remove(self._restart_source_id)
            self._restart_source_id = None
        if self._subprocess is not None:
            try:
                self._subprocess.send_signal(15)  # SIGTERM。gpu-watch.sh 自身の trap が feeder も止める
            except GLib.Error:
                pass
            self._subprocess = None

    def _on_close_request(self, window: Gtk.ApplicationWindow) -> bool:
        self._stop_feeder()
        self._withdraw_fan_offset()
        return False  # 既定の「閉じる」処理を継続させる

    # ---- ハングの診断（faulthandler）----------------------------------------

    def _start_hang_watchdog(self) -> None:
        """メインループが止まったときの診断を仕掛ける。使えなくても本体の動作には影響させない。

        - 1 秒ごとの tick で見張りを掛け直す。tick が HANG_DUMP_SEC 秒途切れる（メインスレッドが固まる）と、
          faulthandler の別スレッドが全スレッドのスタックを HANG_LOG_FILE に書く。
        - SIGUSR1 でも同じ書き出しをする（固まった窓を終了させる前に `kill -USR1 <pid>` で取れる）。
        """
        self._hang_log = None
        try:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            self._hang_log = open(HANG_LOG_FILE, "a", encoding="utf-8")
            self._hang_log.write(f"---- 起動 {time.strftime('%Y-%m-%d %H:%M:%S')} pid={os.getpid()}\n")
            self._hang_log.flush()
            faulthandler.register(signal.SIGUSR1, file=self._hang_log, all_threads=True)
        except (OSError, ValueError, RuntimeError):
            self._hang_log = None
            return
        self._arm_hang_watchdog()

    def _arm_hang_watchdog(self) -> None:
        if self._hang_log is not None:
            faulthandler.dump_traceback_later(HANG_DUMP_SEC, repeat=False, file=self._hang_log)

    def _stop_hang_watchdog(self) -> None:
        if self._hang_log is None:
            return
        faulthandler.cancel_dump_traceback_later()
        try:
            faulthandler.unregister(signal.SIGUSR1)
        except (ValueError, RuntimeError):
            pass
        self._hang_log.close()
        self._hang_log = None

    # ---- Tapo（PC 全体の実測）。ログイン画面 → 読み取り役の子プロセス -------------

    def _startup_tapo_login(self) -> bool:
        """起動の 1 秒後に、読み取り役が使える状態ならログイン画面を出す（使えなければ理由だけ残す）。"""
        available, reason = tapo_feed.reader_available(Path(self.args.tapo_python))
        if not available:
            self._tapo_note = reason
            return GLib.SOURCE_REMOVE
        self._tapo_note = "未ログイン（「Tapo」ボタンでログイン）"
        self._open_tapo_login("")
        return GLib.SOURCE_REMOVE

    def _open_tapo_login(self, message: str) -> None:
        if self._shutting_down:
            return
        if self._tapo_login_window is not None:
            self._tapo_login_window.present()
            return
        available, reason = tapo_feed.reader_available(Path(self.args.tapo_python))
        if not available:
            self._tapo_note = reason
            self._hold_message("Tapo を使えません（理由は PC 全体のツールチップ）", FAILURE_DISPLAY_SEC)
            return
        profile = tapo_feed.load_profile()
        host = self.args.tapo_host or profile["host"]
        self._tapo_login_window = TapoLoginWindow(
            self, host, profile["username"], message,
            self._on_tapo_submitted, self._on_tapo_login_closed, self._on_tapo_login_window_closed,
        )
        self._tapo_login_window.present()

    def _on_tapo_login_window_closed(self) -> None:
        """ログイン画面が閉じた（ログイン・あとで・閉じるボタンのどれでも）。
        閉じた窓は Python 側の参照が残る間は破棄されない（destroy も出ない）ので、ここで参照を手放す。"""
        self._tapo_login_window = None

    def _on_tapo_login_closed(self) -> None:
        """「あとで」・閉じるボタン。実測は出さず、推定のままにする。"""
        if self._tapo_credentials is None:
            self._tapo_note = "未ログイン（「Tapo」ボタンでログイン）"

    def _on_tapo_submitted(self, host: str, username: str, password: str) -> None:
        try:
            tapo_feed.save_profile(host, username)  # 保存するのは IP とメールアドレスだけ
        except OSError as error:
            self._hold_message(f"Tapo の設定を保存できません: {error}", FAILURE_DISPLAY_SEC)
        self._tapo_credentials = (host, username, password)
        self._start_tapo()

    def _start_tapo(self) -> None:
        self._stop_tapo_feeder()
        if self._tapo_credentials is None or self._shutting_down:
            return
        available, reason = tapo_feed.reader_available(Path(self.args.tapo_python))
        if not available:
            self._tapo_note = reason
            return
        argv = tapo_feed.build_argv(Path(self.args.tapo_python), self.args.tapo_interval)
        host, username, password = self._tapo_credentials
        try:
            self._tapo_feeder = LineFeeder(
                argv, self._on_tapo_line, self._on_tapo_exit,
                stdin_bytes=tapo_feed.encode_credentials(host, username, password),
            )
        except GLib.Error as error:
            self._tapo_note = f"Tapo の読み取りを起動できません: {error.message}"
            return
        self._tapo_note = f"Tapo ({host}) に接続中"

    def _on_tapo_line(self, line: str) -> None:
        self.model.feed_line(line)
        if self.model.measured_system_w() is not None:
            self._tapo_note = ""
        self._refresh_view()

    def _on_tapo_exit(self, exit_code: int, last_stderr: str) -> None:
        self._tapo_feeder = None
        if self._shutting_down:
            return
        if tapo_feed.classify_exit(exit_code) == "ask_again":
            # 資格情報の不備、または最初の 1 回が読めなかった。誤ったパスワードで繰り返し試すと機器側に
            # 制限されるおそれがあるので自動では再試行せず、パスワードをメモリから消してログイン画面を出し直す。
            self._tapo_credentials = None
            reason = last_stderr or f"終了コード {exit_code}"
            self._tapo_note = f"Tapo に接続できませんでした: {reason}"
            self._open_tapo_login(f"接続できませんでした。IP・ID・パスワードを確認してください。\n（{reason}）")
        else:
            self._tapo_note = f"Tapo の読み取りが止まりました（終了コード {exit_code}）。{RESTART_DELAY_SEC} 秒後に再開します"
            if self._tapo_restart_source_id is None:
                self._tapo_restart_source_id = GLib.timeout_add_seconds(RESTART_DELAY_SEC, self._on_tapo_restart_timeout)
        self._refresh_view()

    def _on_tapo_restart_timeout(self) -> bool:
        self._tapo_restart_source_id = None
        if not self._shutting_down and self._tapo_feeder is None:
            self._start_tapo()
        return GLib.SOURCE_REMOVE

    def _stop_tapo_feeder(self) -> None:
        if self._tapo_restart_source_id is not None:
            GLib.source_remove(self._tapo_restart_source_id)
            self._tapo_restart_source_id = None
        if self._tapo_feeder is not None:
            self._tapo_feeder.stop()
            self._tapo_feeder = None

    def _stop_tapo(self) -> None:
        """終了時。読み取りの子プロセスを止め、パスワードをメモリから消す。"""
        self._stop_tapo_feeder()
        self._tapo_credentials = None

    # ---- 表示の更新 -----------------------------------------------------

    def _on_tick(self) -> bool:
        self._arm_hang_watchdog()
        self._publish_fan_offset()
        self._refresh_status()
        return GLib.SOURCE_CONTINUE

    def _set_status(self, text: str, detail: str = "") -> None:
        self.status_label.set_label(text)
        self.status_label.set_tooltip_text(detail or text)

    def _hold_message(self, text: str, seconds: float) -> None:
        self._held_message = text
        self._held_until = time.monotonic() + seconds
        self._set_status(text)

    def _refresh_status(self) -> None:
        if self._calibrate_process is not None:
            self._set_status("較正中（約1分）")
            return
        if self._held_message is not None:
            if time.monotonic() < self._held_until:
                self._set_status(self._held_message)
                return
            self._held_message = None
        if self._subprocess is None:
            return  # 停止中の理由表示を上書きしない
        if self.model.last_update is None:
            self._set_status("値の受信待ち")
            return
        stamp = self.model.host.stamp or ""
        self._set_status(f"更新 {stamp_time_part(stamp)}", f"最終更新: {stamp}")

    def _refresh_view(self) -> None:
        host = self.model.host
        self.row_cpu.set_values(
            format_scalar(host.cpu_pct_now, "%"), format_scalar(host.cpu_pct.max(), "%"),
            load_level(host.cpu_pct_now),
        )
        self.row_cpu_temp.set_values(
            format_scalar(host.cpu_temp_now, "℃"), format_scalar(host.cpu_temp.max(), "℃"),
            temp_level(host.cpu_temp_now, self.model.cpu_threshold),
        )
        self.row_mem.set_values(
            format_mem(host.mem_used_mib, host.mem_pct_now),
            format_scalar(host.mem_pct.max(), "%"),
            load_level(host.mem_pct_now),
        )
        if host.mem_used_mib is not None and host.mem_total_mib is not None:
            self.row_mem.set_tooltip(f"{host.mem_used_mib / 1024:.1f} / {host.mem_total_mib / 1024:.1f} GiB")
        self._refresh_power_rows(host)

        for index in self.model.gpu_order:
            gpu = self.model.gpus[index]
            rows = self._ensure_gpu_rows(index, gpu.name)
            rows["temp"].set_values(
                format_scalar(gpu.temp_now, "℃"), format_scalar(gpu.temp.max(), "℃"),
                temp_level(gpu.temp_now, self.model.gpu_threshold),
            )
            rows["util"].set_values(
                format_scalar(gpu.util_now, "%"), format_scalar(gpu.util.max(), "%"),
                load_level(gpu.util_now),
            )
            rows["vram"].set_values(
                format_vram(gpu.mem_used_mib, gpu.mem_total_mib),
                format_gib(gpu.mem_used.max()),
                plain_level(gpu.mem_used_mib),
            )
            if gpu.mem_used_mib is not None and gpu.mem_total_mib is not None:
                rows["vram"].set_tooltip(f"{gpu.mem_used_mib:.0f} / {gpu.mem_total_mib:.0f} MiB")
            rows["power"].set_values(
                format_scalar(gpu.power_w_now, "W"), format_scalar(gpu.power_w.max(), "W"), plain_level(gpu.power_w_now),
            )

        if self.row_fan is not None:
            fan = self.model.fan
            if fan.state == "ok":
                # 普段は CONTROL しか出ない。それ以外のモードのときだけ値に添えて目立たせる。
                current_text = format_scalar(fan.pwm_now, "%")
                if fan.mode != "CONTROL":
                    current_text += f" {fan.mode}"
                self.row_fan.set_tooltip(f"モード {fan.mode}")
            elif fan.state == "stale":
                current_text = "n/a"
                self.row_fan.set_tooltip(f"最新の PWM ログが {fan.age_sec} 秒前（サービスが指令を出せていない可能性）")
            else:
                current_text = "n/a"
                self.row_fan.set_tooltip("journal に PWM ログが無い（サービス停止中か権限不足）")
            self.row_fan.set_values(
                current_text, format_scalar(fan.pwm.max(), "%"),
                plain_level(fan.pwm_now if fan.state == "ok" else None),
            )

        self._refresh_status()

    def _power_label_for_status(self, status: str) -> str:
        if status == "cpu_mismatch":
            return "要再較正"  # CPU が較正時と違う。理由はツールチップ
        return "未較正"

    def _calibration_tooltip_text(self) -> str:
        """「較正ボタンを押す」の案内と、推定式・仮定・較正日時をまとめたツールチップ文。"""
        calibration = self.model.calibration
        lines = ["較正ボタンを押すと、負荷段階ごとの実測から推定式を作ります。"]
        if self.model.rapl_note:
            lines.append(self.model.rapl_note)
        lines.append("CPU 電力(推定) = 較正点を CPU 使用率で区間線形補間し、PL1 で頭打ち")
        lines.append("PSys(推定) = 補間値 − (CPU が PL1 で抑えられた分)")
        lines.append(f"PC 全体(推定) = (PSys推定 + GPU実測の合計) ÷ PSU効率({self.model.psu_efficiency:.2f})")
        lines.append("仮定: PSU 効率は実測していない既定値。PSys に dGPU の電力は含まれない前提")
        lines.append(f"較正日時: {calibration.calibrated_at if calibration is not None else '未較正'}")
        return "\n".join(lines)

    def _refresh_power_rows(self, host) -> None:
        """CPU 電力・PSys・PC 全体の 3 行。RAPL の実測が取れる環境ではそちらを優先し、
        取れない環境・未較正なら推定（無ければ「未較正」）を出す。混同しないよう
        項目名に (実測)/(推定) を付け、較正ボタンの案内はツールチップにまとめる。"""
        status = self.model.calibration_status()
        tooltip = self._calibration_tooltip_text()
        label = self._power_label_for_status(status)

        if host.cpu_w_now is not None:
            self.row_cpu_w.set_values(
                format_scalar(host.cpu_w_now, "W"), format_scalar(host.cpu_w.max(), "W"), plain_level(host.cpu_w_now),
            )
            self.row_cpu_w.set_tooltip("")
        else:
            if status == "ok":
                self.row_cpu_w.set_values(
                    format_scalar(host.cpu_w_est_now, "W", prefix=ESTIMATE_PREFIX),
                    format_scalar(host.cpu_w_est.max(), "W", prefix=ESTIMATE_PREFIX),
                    plain_level(host.cpu_w_est_now),
                )
            else:
                self.row_cpu_w.set_values(label, label, "unknown")
            self.row_cpu_w.set_tooltip(tooltip)

        if host.psys_w_now is not None:
            self.row_psys_w.set_values(
                format_scalar(host.psys_w_now, "W"), format_scalar(host.psys_w.max(), "W"), plain_level(host.psys_w_now),
            )
            self.row_psys_w.set_tooltip("")
        else:
            if status == "ok":
                self.row_psys_w.set_values(
                    format_scalar(host.psys_w_est_now, "W", prefix=ESTIMATE_PREFIX),
                    format_scalar(host.psys_w_est.max(), "W", prefix=ESTIMATE_PREFIX),
                    plain_level(host.psys_w_est_now),
                )
            else:
                self.row_psys_w.set_values(label, label, "unknown")
            self.row_psys_w.set_tooltip(tooltip)

        # PC 全体は、Tapo の実測が新しければそれを出す（頭に ≈ を付けない）。
        # 実測が無い・古いときは、較正済みなら推定（≈ 付き）、未較正なら「未較正」に戻す。
        measured = self.model.measured_system_w()
        if measured is not None:
            self.row_system_w.set_values(
                format_scalar(measured, "W"), format_scalar(host.system_w.max(), "W"), plain_level(measured),
            )
            # 毎秒変わる数値（推定との差など）は入れない。ツールチップの表示中に文が変わり続けると、
            # 弾き出し窓（Wayland の popup）を作り直し続けることになり、ハングの疑いがあるため固定文にする。
            self.row_system_w.set_tooltip("Tapo の実測（コンセント側の電力。電源ユニットの損失を含む）")
            return
        if status == "ok":
            self.row_system_w.set_values(
                format_scalar(host.system_w_est_now, "W", prefix=ESTIMATE_PREFIX),
                format_scalar(host.system_w_est.max(), "W", prefix=ESTIMATE_PREFIX),
                plain_level(host.system_w_est_now),
            )
        else:
            self.row_system_w.set_values(label, label, "unknown")
        suffix = f"\nTapo: {self._tapo_note}" if self._tapo_note else ""
        self.row_system_w.set_tooltip(tooltip + suffix)

    # ---- 較正（root で calibrate-power.py を pkexec 経由で実行） -------------

    def _load_calibration_at_startup(self) -> None:
        if not CALIBRATION_FILE.exists():
            return
        calibration, _reason = model.load_calibration(CALIBRATION_FILE)
        if calibration is not None:
            self.model.set_calibration(calibration)

    def _on_calibrate_clicked(self, button: Gtk.Button) -> None:
        self._held_message = None
        self.calibrate_button.set_sensitive(False)
        argv = ["pkexec", "/usr/bin/python3", str(CALIBRATE_SCRIPT)]
        launcher = Gio.SubprocessLauncher.new(
            Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE
        )
        try:
            process = launcher.spawnv(argv)
        except GLib.Error as error:
            self._on_calibrate_failed(f"較正の起動に失敗しました: {error.message}")
            return
        self._calibrate_process = process
        self._refresh_status()
        process.communicate_utf8_async(None, None, self._on_calibrate_done)

    def _on_calibrate_done(self, process: Gio.Subprocess, result: Gio.AsyncResult) -> None:
        self._calibrate_process = None
        try:
            _success, stdout_text, stderr_text = process.communicate_utf8_finish(result)
        except GLib.Error as error:
            self._on_calibrate_failed(f"較正の実行中にエラーが起きました: {error.message}")
            return

        exit_status = process.get_exit_status()
        if exit_status == 126:
            self._on_calibrate_failed("較正をキャンセルしたか、認証に失敗しました")
            return
        if exit_status == 127:
            self._on_calibrate_failed(f"較正スクリプトが見つかりません: {CALIBRATE_SCRIPT}")
            return
        if exit_status != 0:
            stderr_lines = (stderr_text or "").strip().splitlines()
            reason = stderr_lines[-1] if stderr_lines else ""
            suffix = f": {reason}" if reason else ""
            self._on_calibrate_failed(f"較正に失敗しました（終了コード {exit_status}）{suffix}")
            return

        try:
            data = json.loads(stdout_text)
        except (TypeError, ValueError):
            self._on_calibrate_failed("較正結果の JSON が壊れています")
            return
        calibration, reason = model.validate_calibration(data)
        if calibration is None:
            self._on_calibrate_failed(f"較正結果が不正です: {reason}")
            return

        try:
            self._save_calibration(data)
        except OSError as error:
            self._on_calibrate_failed(f"較正結果の保存に失敗しました: {error}")
            return

        self.model.set_calibration(calibration)
        self.calibrate_button.set_sensitive(True)
        self._hold_message("較正が完了しました", CALIBRATE_DONE_DISPLAY_SEC)
        self._refresh_view()

    def _on_calibrate_failed(self, reason: str) -> None:
        self.calibrate_button.set_sensitive(True)
        self._hold_message(reason, FAILURE_DISPLAY_SEC)

    def _save_calibration(self, data: dict) -> None:
        """一時ファイル→rename で保存する（書き込み途中の壊れた JSON を読まないため）。"""
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp_path = STATE_DIR / "calibration.json.tmp"
        tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.rename(CALIBRATION_FILE)

    def _handle_alert(self, event: AlertEvent) -> None:
        if not event.entered:
            return
        if event.target == "CPU":
            summary = "CPU 温度が超過しました"
            body = f"{event.value:.1f} C > {event.threshold:.0f} C"
        else:
            summary = f"GPU {event.target} 温度が超過しました"
            body = f"{event.value:.0f} C > {event.threshold:.0f} C"
        notify_critical(summary, body)


class GpuWatchApplication(Gtk.Application):
    def __init__(self, args: argparse.Namespace):
        super().__init__(application_id=args.app_id)
        self.args = args
        self.window: Optional[GpuWatchWindow] = None

    def do_activate(self) -> None:  # noqa: N802 (GObject の仮想メソッド名)
        if self.window is None:
            self.window = GpuWatchWindow(self, self.args)
            # SIGTERM/SIGINT（systemd や timeout コマンドからの終了要求）でも
            # ウィンドウを閉じた時と同じく子プロセスを止めてから終了する。
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, self._on_term_signal)
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, self._on_term_signal)
        self.window.present()

    def _on_term_signal(self) -> bool:
        if self.window is not None:
            self.window._stop_feeder()
            self.window._withdraw_fan_offset()
        self.quit()
        return GLib.SOURCE_REMOVE


def main(argv: Optional[list] = None) -> int:
    args = parse_args(argv)
    if not GPU_WATCH_SH.exists():
        print(f"gpu-watch-gui: gpu-watch.sh が見つかりません: {GPU_WATCH_SH}")
        return 1
    application = GpuWatchApplication(args)
    return application.run([])


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
