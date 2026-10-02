from __future__ import annotations

import os
from enum import StrEnum
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

TRUE_VALUES = {"1", "true", "yes", "on"}


def env_text(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def env_flag(name: str, default: bool = False) -> bool:
    return env_text(name).lower() in TRUE_VALUES


def env_number(name: str, default: float) -> float:
    raw = env_text(name)

    if not raw:
        return default

    try:
        return float(raw)
    except ValueError:
        return default


class FaceState(StrEnum):
    IDLE = "IDLE"
    NOTICING = "NOTICING"
    GREETING = "GREETING"
    LISTENING = "LISTENING"
    THINKING = "THINKING"
    SPEAKING = "SPEAKING"
    FINISHING = "FINISHING"


ELEVENLABS_API_KEY = env_text("ELEVENLABS_API_KEY")
WEBHOOK_URL = env_text("WEBHOOK_URL")
VOICE_ID = env_text("ELEVENLABS_VOICE_ID")

ELEVENLABS_TTS_MODEL = env_text(
    "ROBOT_TTS_MODEL", "eleven_multilingual_v2"
)
ELEVENLABS_STT_MODEL = env_text("ROBOT_STT_MODEL", "scribe_v2")
TTS_PROVIDER = env_text("ROBOT_TTS_PROVIDER", "elevenlabs").lower()
TTS_VOICE = env_text("ROBOT_TTS_VOICE", "en-US-AriaNeural")

SERIAL_PORT = env_text("ROBOT_SERIAL_PORT", "COM6")
BAUD_RATE = int(env_number("ROBOT_BAUD_RATE", 115200))
ESP32_BOOT_DELAY_SEC = env_number("ROBOT_BOOT_DELAY_SEC", 2.0)
SERIAL_READ_SIZE = 64
SERIAL_WRITE_QUEUE_SIZE = 64
SERIAL_ERROR_LOG_EVERY = 25
SERIAL_GIVE_UP_ERRORS = 10

LINK_PING_SEC = env_number("ROBOT_LINK_PING_SEC", 5.0)
LINK_TIMEOUT_SEC = env_number("ROBOT_LINK_TIMEOUT_SEC", 15.0)

STT_LANGUAGE = env_text("ROBOT_STT_LANGUAGE", "en")
STT_TIMEOUT_SEC = env_number("ROBOT_STT_TIMEOUT_SEC", 5)
STT_PHRASE_TIME_LIMIT_SEC = env_number("ROBOT_STT_PHRASE_LIMIT_SEC", 10)
STT_KEYTERMS = (
    "Him Academy",
    "Him Academy Public School",
    "HAPS",
    "Shimla",
    "Himachal Pradesh",
    "Class 9",
    "Class 10",
    "Class 11",
    "Class 12",
    "ninth",
    "tenth",
    "eleventh",
    "twelfth",
    "principal",
    "vice principal",
    "reception",
    "admission",
    "admissions",
    "library",
    "auditorium",
    "ATL",
    "Atal Tinkering Lab",
    "physics",
    "chemistry",
    "biology",
    "computer lab",
    "hostel",
    "boarding",
    "transport",
    "school office",
    "medical room",
    "laboratory",
    "laboratories",
)

HTTP_TIMEOUT_SEC = env_number("ROBOT_HTTP_TIMEOUT_SEC", 30)
FALLBACK_REPLY = env_text(
    "ROBOT_FALLBACK_REPLY",
    "Sorry, I could not find that. Please try asking again.",
)

WAKE_AUDIO_DIR = BASE_DIR / "wake_responses"
ACK_AUDIO_DIR = BASE_DIR / "reasoning_responses"
AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg"}

MIN_FLAP_SEC = 0.3
FALLBACK_CLIP_SEC = 0.5
ESTIMATED_MP3_BYTES_PER_SEC = 16000

ACK_JOIN_TIMEOUT_SEC = 1.5

DEBUG = env_flag("ROBOT_DEBUG")
TIMESTAMP_LOGS = env_flag("ROBOT_LOG_TIMESTAMPS")
