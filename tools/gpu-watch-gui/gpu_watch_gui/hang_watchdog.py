"""メインループのハングの診断と、自動での立ち直り。GTK には依存しない純 Python。

GUI のメインスレッドが固まると、窓は応答しなくなり、ファンの補正ファイルも更新されなくなる。
2026-11-03 の実例では、メインスレッドが GTK の描画（gsk_renderer_render）から Mesa のドライバの中で
待ち続けていた。Python のスタックは `Gio.Application.run` だけで、原因は C のスタックにしか出なかった。

そこで GUI は 1 秒ごとの tick で `beat()` を呼び、別スレッドで見張る。
  - `beat()` が HANG_EXIT_SEC 秒途切れたら、Python のスタックと（gdb があれば）C のスタックを
    ログに書いて、終了コード EXIT_HANG（75）で終わる。再起動は run-gpu-watch-gui が行う。
  - 見張りのスレッド自身が GIL を取れずに動けないときのために、faulthandler のタイマーも掛ける
    （LAST_RESORT_SEC 秒 beat が無ければ、Python のスタックを書いてプロセスを終了する。終了コード 1）。
  - SIGUSR1 でも Python のスタックを書き出す（手動の診断用。終了はしない）。

書くのは関数名と行番号、アドレスだけで、変数の値（パスワード等）は含まない。
gdb は `bt`（引数と局所変数は出さない）だけを実行し、`bt full` やコアの書き出しはしない。
"""
from __future__ import annotations

import ctypes
import faulthandler
import os
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, List, Optional, TextIO

EXIT_HANG = 75  # sysexits の EX_TEMPFAIL（一時的な失敗。あとで試し直せば直るかもしれない）
HANG_EXIT_SEC = 30.0
LAST_RESORT_SEC = 120.0  # HANG_EXIT_SEC + gdb の待ち時間（GDB_TIMEOUT_SEC）より長くする
CHECK_INTERVAL_SEC = 1.0
GDB_TIMEOUT_SEC = 60.0
PR_SET_PTRACER = 0x59616D61  # prctl の操作番号（"Yama"）


def build_gdb_argv(gdb_path: str, process_id: int) -> List[str]:
    """C のスタックだけを採る gdb のコマンドライン。
    -batch: 終わったら抜ける。-nx: 初期化ファイルを読まない。debuginfod は切る（ネットワークを待たない）。
    `thread apply all bt` は引数も局所変数も出さない。"""
    return [
        gdb_path, "-p", str(process_id), "-batch", "-nx",
        "-ex", "set debuginfod enabled off",
        "-ex", "thread apply all bt",
    ]


def allow_tracer(tracer_pid: int) -> Optional[str]:
    """prctl(PR_SET_PTRACER, tracer_pid): その pid のプロセスだけに、自分への ptrace を許す。
    0 を渡すと許可を取り下げる。失敗したら理由を返し、成功なら None。

    Yama の ptrace_scope=1 は「親が子に付ける」向きしか許さない。子の gdb が親（このプロセス）に付くのは
    許されないので、この許可が要る。pid を絞るので、同じ利用者の他のプロセスには開けない。"""
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        result = libc.prctl(PR_SET_PTRACER, tracer_pid, 0, 0, 0)
    except (OSError, AttributeError) as error:
        return f"{type(error).__name__}: {error}"
    if result != 0:
        return os.strerror(ctypes.get_errno())
    return None


def collect_native_stack(process_id: int, log_file: TextIO, timeout_sec: float = GDB_TIMEOUT_SEC) -> str:
    """自分自身に gdb を付けて C のスタックをログへ書く。結果の一言（成功・失敗の理由）を返す。

    gdb が付いている間はこのプロセスの全スレッドが止まる。出力をパイプで受けるとこのスレッドも止まっていて
    読めず、gdb が詰まる。そのため出力はログファイルへ直接書かせる。
    gdb が付く前に許可（allow_tracer）を済ませるため、/bin/sh で起動して 1 行を受け取るまで待たせ、
    pid を許可してから 1 行を送って gdb に差し替える（exec なので pid は変わらない）。"""
    gdb_path = shutil.which("gdb")
    if gdb_path is None:
        return "gdb が無いので C スタックは採らない"
    log_file.flush()
    launcher_argv = ["/bin/sh", "-c", 'read -r _go; exec "$@"', "sh", *build_gdb_argv(gdb_path, process_id)]
    try:
        process = subprocess.Popen(
            launcher_argv, stdin=subprocess.PIPE, stdout=log_file, stderr=subprocess.STDOUT,
        )
    except OSError as error:
        return f"gdb を起動できなかった: {error}"
    problem = allow_tracer(process.pid)
    try:
        process.stdin.write(b"go\n")
        process.stdin.close()
    except OSError:
        pass  # 起動側が先に終わっていた。終了コードで分かる
    try:
        process.wait(timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        return f"gdb が {timeout_sec:g} 秒で終わらなかったので打ち切った"
    finally:
        allow_tracer(0)
    note = f"gdb の終了コード {process.returncode}"
    if problem is not None:
        note += f"（ptrace の許可に失敗: {problem}）"
    return note


class HangWatchdog:
    def __init__(
        self,
        log_path: Path,
        hang_exit_sec: float = HANG_EXIT_SEC,
        last_resort_sec: float = LAST_RESORT_SEC,
        check_interval_sec: float = CHECK_INTERVAL_SEC,
        clock: Callable[[], float] = time.monotonic,
        exit_function: Callable[[int], None] = os._exit,
        native_collector: Callable[[int, TextIO], str] = collect_native_stack,
        use_faulthandler: bool = True,
    ) -> None:
        self._log_path = log_path
        self._hang_exit_sec = hang_exit_sec
        self._last_resort_sec = last_resort_sec
        self._check_interval_sec = check_interval_sec
        self._clock = clock
        self._exit_function = exit_function
        self._native_collector = native_collector
        self._use_faulthandler = use_faulthandler  # False は試験用。プロセス全体の設定（SIGUSR1・タイマー）に触らない
        self._log: Optional[TextIO] = None
        self._last_beat = 0.0
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ---- 開始・終了 ----------------------------------------------------

    def start(self) -> bool:
        """見張りを始める。ログを開けないなど使えないときは False（本体の動作には影響させない）。"""
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            self._log = open(self._log_path, "a", encoding="utf-8")
            self._write(f"---- 起動 {time.strftime('%Y-%m-%d %H:%M:%S')} pid={os.getpid()}\n")
            if self._use_faulthandler:
                faulthandler.register(signal.SIGUSR1, file=self._log, all_threads=True)
        except (OSError, ValueError, RuntimeError):
            self._close_log()
            return False
        self._last_beat = self._clock()
        self._thread = threading.Thread(target=self._run, name="hang-watchdog", daemon=True)
        self._thread.start()
        self._arm_last_resort()
        return True

    def stop(self) -> None:
        if self._log is None:
            return
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=self._check_interval_sec + 2.0)
        if self._use_faulthandler:
            faulthandler.cancel_dump_traceback_later()
            try:
                faulthandler.unregister(signal.SIGUSR1)
            except (ValueError, RuntimeError):
                pass
        self._close_log()

    def beat(self) -> None:
        """メインループが動いている印。1 秒ごとの tick から呼ぶ。"""
        if self._log is None:
            return
        self._last_beat = self._clock()
        self._arm_last_resort()

    # ---- 判定と書き出し ------------------------------------------------

    def stalled_seconds(self) -> float:
        return self._clock() - self._last_beat

    def is_stalled(self) -> bool:
        return self.stalled_seconds() >= self._hang_exit_sec

    def _run(self) -> None:
        while not self._stop_event.wait(self._check_interval_sec):
            if self.is_stalled():
                self.handle_hang()
                return

    def handle_hang(self) -> None:
        """ハングと判断したとき。スタックを書いて、終了コード EXIT_HANG で終わる。"""
        log = self._log
        if log is None:
            return
        stalled = self.stalled_seconds()
        self._write(
            f"---- ハング検出 {time.strftime('%Y-%m-%d %H:%M:%S')} pid={os.getpid()} "
            f"（tick が {stalled:.0f} 秒途切れた）\n"
        )
        self._write("-- Python のスタック\n")
        faulthandler.dump_traceback(file=log, all_threads=True)
        log.flush()
        self._write("-- C のスタック\n")
        note = self._native_collector(os.getpid(), log)
        self._write(f"-- {note}\n")
        self._write(f"---- 終了コード {EXIT_HANG} で終了する（run-gpu-watch-gui が起動し直す）\n")
        self._exit_function(EXIT_HANG)

    def _arm_last_resort(self) -> None:
        if self._use_faulthandler and self._log is not None:
            faulthandler.dump_traceback_later(self._last_resort_sec, repeat=False, file=self._log, exit=True)

    def _write(self, text: str) -> None:
        if self._log is not None:
            self._log.write(text)
            self._log.flush()

    def _close_log(self) -> None:
        if self._log is not None:
            try:
                self._log.close()
            except OSError:
                pass
            self._log = None
