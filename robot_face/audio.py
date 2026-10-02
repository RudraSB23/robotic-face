from __future__ import annotations

import asyncio
import tempfile
import threading
import time
from pathlib import Path

import edge_tts
import pygame
import requests

from . import config
from . import log


class AudioOut:
    def __init__(self, shutdown: threading.Event) -> None:
        self._shutdown = shutdown
        self._lock = threading.Lock()
        self._ready = False
        self._silent = False

    @property
    def available(self) -> bool:
        return self._ready

    @property
    def silent(self) -> bool:
        return self._silent

    def start(self) -> bool:
        log.log("[audio] initializing pygame...")

        try:
            pygame.mixer.init()
        except Exception as exc:
            self._silent = True
            log.warn(
                f"[audio] no output device ({exc}); "
                "continuing without sound, movement only"
            )
            return False

        self._ready = True
        log.log("[audio] pygame initialized.")
        return True

    def duration(self, path: Path) -> float:
        if self._ready:
            try:
                sound = pygame.mixer.Sound(str(path))
                length = float(sound.get_length())
                del sound
                return length
            except Exception as exc:
                log.warn(f"[audio] duration unavailable: {exc}")

        return self._estimate_duration(path)

    def _estimate_duration(self, path: Path) -> float:
        try:
            size = path.stat().st_size
        except OSError:
            return config.FALLBACK_CLIP_SEC

        return max(config.MIN_FLAP_SEC, size / config.ESTIMATED_MP3_BYTES_PER_SEC)

    def _interrupted(self, stop_event: threading.Event | None) -> bool:
        if self._shutdown.is_set():
            return True

        return stop_event is not None and stop_event.is_set()

    def play(
        self,
        path: Path,
        stop_event: threading.Event | None = None,
    ) -> bool:
        if self._interrupted(stop_event):
            return False

        if not self._ready:
            return False

        try:
            with self._lock:
                pygame.mixer.music.load(str(path))
                pygame.mixer.music.play()
        except Exception as exc:
            log.error(f"[audio] playback failed: {exc}")
            return False

        log.log(f"[audio] playing {path.name}")

        try:
            while not self._interrupted(stop_event):
                with self._lock:
                    busy = pygame.mixer.music.get_busy()

                if not busy:
                    break

                time.sleep(0.03)

            return not self._interrupted(stop_event)
        finally:
            self.stop()

    def stop(self) -> None:
        if not self._ready:
            return

        with self._lock:
            try:
                pygame.mixer.music.stop()
                pygame.mixer.music.unload()
            except Exception:
                pass

    def close(self) -> None:
        self.stop()

        if not self._ready:
            return

        try:
            pygame.mixer.quit()
        except Exception:
            pass

        self._ready = False


class Speaker:
    def __init__(self) -> None:
        self.provider = config.TTS_PROVIDER

    def render(self, text: str) -> Path:
        text = text.strip()

        if not text:
            raise ValueError("nothing to speak")

        with tempfile.NamedTemporaryFile(
            prefix="robot_speech_",
            suffix=".mp3",
            delete=False,
        ) as handle:
            path = Path(handle.name)

        try:
            self._synthesize(text, path)
        except Exception:
            discard(path)
            raise

        return path

    def _synthesize(self, text: str, path: Path) -> None:
        if self.provider == "elevenlabs":
            log.log("[tts] provider: ElevenLabs")
            self._elevenlabs(text, path)
        elif self.provider == "edge":
            log.log(f"[tts] provider: Edge TTS ({config.TTS_VOICE})")
            asyncio.run(self._edge(text, path))
        else:
            raise RuntimeError(
                "ROBOT_TTS_PROVIDER must be 'elevenlabs' or 'edge'."
            )

    async def _edge(self, text: str, path: Path) -> None:
        communicator = edge_tts.Communicate(
            text=text,
            voice=config.TTS_VOICE,
        )
        await communicator.save(str(path))

    def _elevenlabs(self, text: str, path: Path) -> None:
        if not config.ELEVENLABS_API_KEY or not config.VOICE_ID:
            raise RuntimeError(
                "ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID are required."
            )

        response = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{config.VOICE_ID}",
            headers={
                "xi-api-key": config.ELEVENLABS_API_KEY,
                "Content-Type": "application/json",
                "Accept": "audio/mpeg",
            },
            json={
                "text": text,
                "model_id": config.ELEVENLABS_TTS_MODEL,
            },
            timeout=config.HTTP_TIMEOUT_SEC,
        )

        response.raise_for_status()
        path.write_bytes(response.content)


def discard(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        log.warn(f"[audio] could not delete {path.name}: {exc}")
