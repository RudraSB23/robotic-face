from __future__ import annotations

import random
import threading
from pathlib import Path

from . import config
from . import log
from .audio import AudioOut, Speaker, discard
from .config import FaceState
from .face import Face
from .link import ESP32Link
from .listening import MicrophoneListener, Transcriber
from .n8n import Brain


def audio_files(directory: Path) -> list[Path]:
    if not directory.exists():
        return []

    return sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() in config.AUDIO_EXTENSIONS
    )


def pick_audio(directory: Path) -> Path | None:
    files = audio_files(directory)

    if not files:
        return None

    return random.choice(files)


class RobotApp:
    def __init__(
        self,
        link: ESP32Link | None = None,
        audio: AudioOut | None = None,
        face: Face | None = None,
        listener: MicrophoneListener | None = None,
        transcriber: Transcriber | None = None,
        brain: Brain | None = None,
        speaker: Speaker | None = None,
    ) -> None:
        self.shutdown = threading.Event()
        self.interrupt = threading.Event()
        self.interaction_active = threading.Event()

        self._ack_stop = threading.Event()
        self._query_lock = threading.Lock()
        self._interaction_lock = threading.Lock()

        self.link = (
            link
            if link is not None
            else ESP32Link(
                on_touch=self.handle_touch,
                on_ready=self.on_board_ready,
                on_reply=self.on_board_reply,
            )
        )
        self.audio = audio if audio is not None else AudioOut(self.shutdown)
        self.face = face if face is not None else Face(self.link)
        self.listener = (
            listener if listener is not None else MicrophoneListener()
        )
        self.transcriber = (
            transcriber if transcriber is not None else Transcriber()
        )
        self.brain = brain if brain is not None else Brain()
        self.speaker = speaker if speaker is not None else Speaker()

    def start(self) -> None:
        log.log("=" * 40)
        log.log("ROBOT RECEPTION CONTROLLER")
        log.log("=" * 40)
        log.log(f"n8n: {_mask(config.WEBHOOK_URL)}")
        log.log(f"serial: {config.SERIAL_PORT} @ {config.BAUD_RATE}")
        log.log(f"stt: ElevenLabs {config.ELEVENLABS_STT_MODEL}")
        log.log(f"tts: {self.speaker.provider}")
        log.log(f"session: {self.brain.session_id}")
        log.log(f"wake audio: {config.WAKE_AUDIO_DIR}")
        log.log(f"ack audio: {config.ACK_AUDIO_DIR}")

        self.audio.start()
        self.link.connect()
        self.listener.setup()

        if not self.listener.ready:
            log.warn(
                "microphone unavailable; touch interaction is disabled, "
                "console mode remains active"
            )

        self.face.park()

    def shutdown_app(self) -> None:
        log.log("[robot] shutting down...")
        self.shutdown.set()
        self.interrupt.set()
        self._ack_stop.set()

        if self.link.connected:
            self.face.park()

        self.audio.stop()
        self.link.close()
        self.audio.close()

        log.log("[robot] stopped.")

    def on_board_ready(self) -> None:
        log.log("[serial] ESP32 reported ready")

    def on_board_reply(self, line: str) -> None:
        if line.startswith("ERR:"):
            log.warn(f"[serial] board rejected a command: {line}")

    def handle_touch(self) -> None:
        if self.shutdown.is_set():
            return

        if self.interaction_active.is_set():
            log.log("[touch] interrupt requested")
            self.interrupt.set()
            self.face.cancel_motion()
            self.audio.stop()
            return

        threading.Thread(
            target=self.touch_interaction,
            name="touch-interaction",
            daemon=True,
        ).start()

    def touch_interaction(self) -> None:
        if not self.listener.ready:
            log.warn("[touch] no microphone, ignoring touch")
            return

        if not self._interaction_lock.acquire(blocking=False):
            log.debug("[touch] already busy, ignoring touch")
            return

        self.interaction_active.set()
        self.interrupt.clear()

        try:
            log.log("[touch] visitor detected")
            self.face.notice()

            self._greet()

            if self.interrupt.is_set():
                log.log("[touch] interrupted during greeting")
                return

            self.face.listen()

            text = self.capture_query()

            if text:
                self.process_query(text)

            if self.interrupt.is_set():
                self.interrupt.clear()
                log.log("[touch] interrupted, listening again")
                self.face.listen()

                text = self.capture_query()

                if text:
                    self.process_query(text)
        except Exception as exc:
            log.error(f"[touch] interaction failed: {exc}")
        finally:
            self.interrupt.clear()
            self.audio.stop()
            self.face.finish()
            self.interaction_active.clear()
            self._interaction_lock.release()
            log.log("[touch] interaction finished")

    def capture_query(self) -> str | None:
        audio = self.listener.capture()

        if audio is None:
            return None

        try:
            text = self.transcriber.transcribe(audio)
        except Exception as exc:
            log.error(f"[stt] failed: {exc}")
            return None

        if text:
            log.log(f"[stt] {text}")

        return text

    def _greet(self) -> None:
        clip = pick_audio(config.WAKE_AUDIO_DIR)

        if clip is None:
            log.warn(f"[wake] no clips in {config.WAKE_AUDIO_DIR}")
            self.face.greet(config.FALLBACK_CLIP_SEC)
            return

        log.log(f"[wake] {clip.name}")
        self.play_clip(clip, self.face.greet, self.interrupt)

    def play_clip(
        self,
        clip: Path,
        announce,
        stop_event: threading.Event | None = None,
    ) -> bool:
        if stop_event is not None and stop_event.is_set():
            return False

        duration = self.audio.duration(clip)
        log.log(f"[audio] duration: {duration:.2f}s")

        if stop_event is not None and stop_event.is_set():
            return False

        announce(duration)

        return self.audio.play(clip, stop_event)

    def _play_acknowledgement(self, clip: Path) -> None:
        try:
            self.play_clip(clip, self.face.think, self._ack_stop)
        except Exception as exc:
            log.warn(f"[ack] playback failed: {exc}")

    def _stop_acknowledgement(self, worker: threading.Thread | None) -> None:
        self._ack_stop.set()
        self.audio.stop()

        if worker is not None:
            worker.join(timeout=config.ACK_JOIN_TIMEOUT_SEC)

    def process_query(self, text: str, blocking: bool = True) -> bool:
        text = text.strip()

        if not text:
            return False

        if blocking:
            self._query_lock.acquire()
        elif not self._query_lock.acquire(blocking=False):
            log.debug("[query] another query is still running")
            return False

        try:
            self._ack_stop.clear()
            self.face.think()

            worker = self._start_acknowledgement()

            try:
                answer = self.brain.ask(text)
            finally:
                self._stop_acknowledgement(worker)

            if self.interrupt.is_set():
                log.log("[query] cancelled before speaking")
                return False

            if not answer:
                answer = config.FALLBACK_REPLY

            return self.speak(answer)
        except Exception as exc:
            log.error(f"[query] failed: {exc}")
            return False
        finally:
            self._query_lock.release()

    def _start_acknowledgement(self) -> threading.Thread | None:
        clip = pick_audio(config.ACK_AUDIO_DIR)

        if clip is None:
            log.warn(f"[ack] no clips in {config.ACK_AUDIO_DIR}")
            return None

        log.log(f"[ack] {clip.name}")

        worker = threading.Thread(
            target=self._play_acknowledgement,
            args=(clip,),
            name="ack-worker",
            daemon=True,
        )
        worker.start()
        return worker

    def speak(self, text: str) -> bool:
        text = text.strip()

        if not text:
            return False

        log.log(f"[robot] {text}")
        path: Path | None = None

        try:
            path = self.speaker.render(text)
            duration = self.audio.duration(path)
            log.log(f"[tts] duration: {duration:.2f}s")

            if self.interrupt.is_set() or self.shutdown.is_set():
                return False

            self.face.speak(duration)
            return self.audio.play(path, self.interrupt)
        except Exception as exc:
            log.error(f"[speech] failed: {exc}")
            return False
        finally:
            if path is not None:
                discard(path)

    def status(self) -> None:
        log.log(
            f"[status] link={self.link.describe()} | "
            f"face={self.face.state.value} | "
            f"tts={self.speaker.provider} | "
            f"stt={config.ELEVENLABS_STT_MODEL} | "
            f"mic={'ready' if self.listener.ready else 'unavailable'} | "
            f"sound={'silent' if self.audio.silent else 'on'} | "
            f"interaction="
            f"{'ACTIVE' if self.interaction_active.is_set() else 'IDLE'} | "
            f"wake={len(audio_files(config.WAKE_AUDIO_DIR))} | "
            f"ack={len(audio_files(config.ACK_AUDIO_DIR))}"
        )

    def print_help(self) -> None:
        print(
            """
Commands:
/help              Show help
/listen            Start a touch interaction by hand
/face [STATE]      Show or set the face state
/ports             List serial ports
/status            Show status
/quit              Exit

Anything else is sent straight to n8n.
"""
        )

    def console_loop(self) -> None:
        self.print_help()

        while not self.shutdown.is_set():
            try:
                line = input("robot> ").strip()
            except (EOFError, KeyboardInterrupt):
                break

            if not line:
                continue

            command, _, argument = line.partition(" ")
            command = command.lower()

            if command in {"/quit", "/exit"}:
                break

            if command == "/help":
                self.print_help()
            elif command == "/ports":
                for port in self.link.list_ports():
                    log.log(f"[serial] {port}")
            elif command == "/status":
                self.status()
            elif command == "/listen":
                self.handle_touch()
            elif command == "/face":
                self._face_command(argument.strip())
            else:
                self.process_query(line, blocking=False)

    def _face_command(self, argument: str) -> None:
        if not argument:
            log.log(f"[face] state: {self.face.state.value}")
            return

        try:
            state = FaceState(argument.upper())
        except ValueError:
            choices = ", ".join(item.value for item in FaceState)
            log.warn(f"[face] unknown state '{argument}'. try: {choices}")
            return

        if state is FaceState.IDLE:
            self.face.park()
        else:
            self.face.apply(state)

        suffix = "" if self.link.connected else " (no ESP32, not sent)"
        log.log(f"[face] state -> {state.value}{suffix}")


def _mask(url: str) -> str:
    if "://" not in url:
        return url or "<not set>"

    scheme, _, rest = url.partition("://")
    host, _, _ = rest.partition("/")
    return f"{scheme}://{host}/<redacted>"


def run() -> None:
    app = RobotApp()

    try:
        app.start()
        app.console_loop()
    except KeyboardInterrupt:
        log.log("[robot] interrupted.")
    finally:
        app.shutdown_app()
