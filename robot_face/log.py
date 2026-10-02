from __future__ import annotations

import time

from . import config

_STARTED = time.monotonic()


def _prefix() -> str:
    if not config.TIMESTAMP_LOGS:
        return ""

    elapsed = time.monotonic() - _STARTED
    return f"[{elapsed:8.2f}s] "


def log(message: str) -> None:
    print(f"{_prefix()}{message}", flush=True)


def warn(message: str) -> None:
    print(f"{_prefix()}[warn] {message}", flush=True)


def error(message: str) -> None:
    print(f"{_prefix()}[error] {message}", flush=True)


def debug(message: str) -> None:
    if config.DEBUG:
        print(f"{_prefix()}[debug] {message}", flush=True)


def motion(command: str) -> None:
    debug(f"[motion] -> {command}")
