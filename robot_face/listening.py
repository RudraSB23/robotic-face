from __future__ import annotations

import threading

import requests
import speech_recognition as sr

from . import config
from . import log


class MicrophoneListener:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._recognizer: sr.Recognizer | None = None
        self._microphone: sr.Microphone | None = None

    @property
    def ready(self) -> bool:
        return self._recognizer is not None and self._microphone is not None

    def setup(self) -> bool:
        recognizer = sr.Recognizer()
        recognizer.dynamic_energy_threshold = True
        recognizer.pause_threshold = 0.7

        try:
            microphone = sr.Microphone()
        except Exception as exc:
            log.error(f"[stt] microphone initialization failed: {exc}")
            return False

        log.log("[stt] calibrating microphone...")

        try:
            with microphone as source:
                recognizer.adjust_for_ambient_noise(source, duration=1)
        except Exception as exc:
            log.error(f"[stt] microphone calibration failed: {exc}")
            return False

        self._recognizer = recognizer
        self._microphone = microphone

        log.log("[stt] microphone ready.")
        return True

    def capture(self) -> sr.AudioData | None:
        recognizer = self._recognizer
        microphone = self._microphone

        if recognizer is None or microphone is None:
            return None

        if not self._lock.acquire(blocking=False):
            log.debug("[stt] microphone busy, capture skipped")
            return None

        try:
            log.log("[stt] listening...")

            try:
                with microphone as source:
                    return recognizer.listen(
                        source,
                        timeout=config.STT_TIMEOUT_SEC,
                        phrase_time_limit=config.STT_PHRASE_TIME_LIMIT_SEC,
                    )
            except sr.WaitTimeoutError:
                log.warn("[stt] no speech detected.")
            except OSError as exc:
                log.error(f"[stt] microphone read failed: {exc}")
        finally:
            self._lock.release()

        return None


def build_stt_fields() -> list[tuple[str, str]]:
    fields: list[tuple[str, str]] = [
        ("model_id", config.ELEVENLABS_STT_MODEL),
    ]

    if config.STT_LANGUAGE:
        fields.append(("language_code", config.STT_LANGUAGE))

    fields.extend(("keyterms", term) for term in config.STT_KEYTERMS)
    return fields


class Transcriber:
    def transcribe(self, audio: sr.AudioData) -> str | None:
        if not config.ELEVENLABS_API_KEY:
            log.error("[stt] ELEVENLABS_API_KEY is not set.")
            return None

        try:
            response = requests.post(
                "https://api.elevenlabs.io/v1/speech-to-text",
                headers={"xi-api-key": config.ELEVENLABS_API_KEY},
                data=build_stt_fields(),
                files={
                    "file": (
                        "microphone.wav",
                        audio.get_wav_data(),
                        "audio/wav",
                    )
                },
                timeout=config.HTTP_TIMEOUT_SEC,
            )

            response.raise_for_status()
            data = response.json()
        except requests.RequestException as exc:
            log.error(f"[stt] ElevenLabs request failed: {exc}")
            return None
        except ValueError as exc:
            log.error(f"[stt] invalid response: {exc}")
            return None

        text = data.get("text") if isinstance(data, dict) else None

        if not isinstance(text, str) or not text.strip():
            log.error("[stt] transcription was empty.")
            return None

        return text.strip()
