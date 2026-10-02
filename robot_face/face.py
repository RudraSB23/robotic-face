from __future__ import annotations

from . import config
from . import log
from .config import FaceState
from .link import ESP32Link


class Face:
    def __init__(self, link: ESP32Link) -> None:
        self._link = link
        self._state = FaceState.IDLE

    @property
    def state(self) -> FaceState:
        return self._state

    def _set_state(self, state: FaceState) -> None:
        self._state = state
        self._link.send(f"STATE:{state.value}")

    def flap(self, duration_sec: float) -> None:
        milliseconds = max(
            int(round(config.MIN_FLAP_SEC * 1000)),
            int(round(duration_sec * 1000)),
        )
        self._link.send(f"FLAP:{milliseconds}")

    def cancel_motion(self) -> None:
        self._link.send("STOP")

    def blink(self) -> None:
        self._link.send("BLINK")

    def recenter(self) -> None:
        self._link.send("CENTER")

    def notice(self) -> None:
        log.log("[face] noticing visitor")
        self._set_state(FaceState.NOTICING)

    def greet(self, duration_sec: float) -> None:
        log.log("[face] greeting")
        self._set_state(FaceState.GREETING)
        self.flap(duration_sec)

    def listen(self) -> None:
        log.log("[face] listening")
        self.cancel_motion()
        self._set_state(FaceState.LISTENING)

    def think(self, duration_sec: float | None = None) -> None:
        log.log("[face] thinking")
        self._set_state(FaceState.THINKING)

        if duration_sec is not None:
            self.flap(duration_sec)

    def speak(self, duration_sec: float) -> None:
        log.log("[face] speaking")
        self._set_state(FaceState.SPEAKING)
        self.flap(duration_sec)

    def finish(self) -> None:
        log.log("[face] finishing")
        self._set_state(FaceState.FINISHING)

    def apply(self, state: FaceState) -> None:
        self._set_state(state)

    def park(self) -> None:
        self.recenter()
        self._set_state(FaceState.IDLE)
