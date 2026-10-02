from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable

import serial
import serial.tools.list_ports

from . import config
from . import log

READY_MESSAGE = "Robot Ready!"
TOUCH_MESSAGE = "TOUCH"
PONG_REPLY = "OK:PONG"


class ESP32Link:
    def __init__(
        self,
        on_touch: Callable[[], None] | None = None,
        on_ready: Callable[[], None] | None = None,
        on_reply: Callable[[str], None] | None = None,
    ) -> None:
        self._on_touch = on_touch
        self._on_ready = on_ready
        self._on_reply = on_reply

        self._board: serial.Serial | None = None
        self._outbox: queue.Queue[str] = queue.Queue(
            maxsize=config.SERIAL_WRITE_QUEUE_SIZE
        )
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._connected = False
        self._test_mode = False
        self._ready = False
        self._last_pong = 0.0
        self._errors = 0
        self._reported_link_loss = False

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def test_mode(self) -> bool:
        return self._test_mode

    @property
    def ready_seen(self) -> bool:
        return self._ready

    def link_healthy(self) -> bool:
        if not self._connected:
            return False

        return (time.monotonic() - self._last_pong) <= config.LINK_TIMEOUT_SEC

    def describe(self) -> str:
        if self._test_mode:
            return "TEST MODE"

        if not self._connected:
            return "DISCONNECTED"

        return "LIVE" if self.link_healthy() else "UNRESPONSIVE"

    def list_ports(self) -> list[str]:
        ports = serial.tools.list_ports.comports()

        if not ports:
            log.log("[serial] no serial ports detected.")
            return []

        return [
            f"{port.device} - {port.description or 'Unknown device'}"
            for port in ports
        ]

    def connect(self) -> bool:
        log.log(
            f"[serial] opening {config.SERIAL_PORT} @ {config.BAUD_RATE}"
        )

        try:
            board = serial.Serial(
                config.SERIAL_PORT,
                config.BAUD_RATE,
                timeout=0.1,
            )
        except Exception as exc:
            self._test_mode = True
            log.log(f"[TEST MODE] ESP32 unavailable: {exc}")
            return False

        time.sleep(config.ESP32_BOOT_DELAY_SEC)

        self._board = board
        self._connected = True
        self._test_mode = False
        self._last_pong = time.monotonic()

        self._spawn("serial-writer", self._writer_loop)
        self._spawn("serial-reader", self._reader_loop)
        self._spawn("serial-keepalive", self._keepalive_loop)

        log.log(
            f"[serial] connected to {config.SERIAL_PORT} @ {config.BAUD_RATE}"
        )
        return True

    def _spawn(self, name: str, target: Callable[[], None]) -> None:
        thread = threading.Thread(
            target=target,
            name=name,
            daemon=True,
        )
        thread.start()
        self._threads.append(thread)

    def send(self, command: str) -> None:
        command = command.strip()

        if not command:
            return

        log.motion(command)

        if self._test_mode:
            log.debug("[TEST MODE] motion not sent")
            return

        if not self._connected:
            log.debug("[serial] link down, command dropped")
            return

        try:
            self._outbox.put_nowait(command)
        except queue.Full:
            log.warn("[serial] write queue full, command dropped")

    def close(self) -> None:
        self._stop.set()

        for thread in self._threads:
            thread.join(timeout=1.0)

        self._threads.clear()

        if self._board is not None:
            try:
                self._board.close()
            except Exception:
                pass

        self._board = None
        self._connected = False
        self._ready = False

    def _writer_loop(self) -> None:
        while not self._stop.is_set():
            try:
                command = self._outbox.get(timeout=0.2)
            except queue.Empty:
                continue

            if not self._write(command):
                self._drain()

    def _write(self, command: str) -> bool:
        board = self._board

        if board is None:
            return False

        try:
            board.write(f"{command}\n".encode("utf-8"))
            board.flush()
        except (serial.SerialException, OSError) as exc:
            self._report_failure(f"[serial] write failed: {exc}")
            return False

        self._errors = 0
        return True

    def _drain(self) -> None:
        while True:
            try:
                self._outbox.get_nowait()
            except queue.Empty:
                return

    def _reader_loop(self) -> None:
        buffer = ""

        while not self._stop.is_set():
            board = self._board

            if board is None:
                break

            try:
                chunk = board.read(config.SERIAL_READ_SIZE)
            except (serial.SerialException, OSError) as exc:
                self._report_failure(f"[serial] read failed: {exc}")
                self._stop.wait(0.5)
                continue

            if not chunk:
                continue

            buffer += chunk.decode("utf-8", errors="ignore")

            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                self.feed(line.strip())

    def feed(self, line: str) -> None:
        if not line:
            return

        log.debug(f"[esp32] {line}")

        if line == TOUCH_MESSAGE:
            if self._on_touch is not None:
                self._on_touch()
            return

        if line == READY_MESSAGE:
            self._ready = True

            if self._on_ready is not None:
                self._on_ready()
            return

        if line.startswith("OK:") or line.startswith("ERR:"):
            if line == PONG_REPLY:
                self._last_pong = time.monotonic()
                self._reported_link_loss = False

            if self._on_reply is not None:
                self._on_reply(line)
            return

        if line.startswith("WARN:"):
            log.warn(f"[esp32] {line}")
            return

        log.log(f"[esp32] {line}")

    def _keepalive_loop(self) -> None:
        while not self._stop.wait(config.LINK_PING_SEC):
            if not self._connected:
                continue

            self.send("PING")

            if self.link_healthy() or self._reported_link_loss:
                continue

            self._reported_link_loss = True
            log.warn(
                f"[serial] no reply from ESP32 for "
                f"{config.LINK_TIMEOUT_SEC:.0f}s "
                f"(check USB cable and port {config.SERIAL_PORT})"
            )

    def _report_failure(self, message: str) -> None:
        self._errors += 1
        self._connected = False

        if self._errors == 1 or self._errors % config.SERIAL_ERROR_LOG_EVERY == 0:
            log.error(f"{message} (failure #{self._errors})")

        if self._errors >= config.SERIAL_GIVE_UP_ERRORS:
            log.error(
                "[serial] link lost permanently; "
                "restart the controller to reconnect"
            )
            self._stop.set()
