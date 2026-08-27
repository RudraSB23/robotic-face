from __future__ import annotations

import asyncio
import os
import queue
import tempfile
import threading
import time
import uuid
from pathlib import Path

import dotenv
dotenv.load_dotenv()

import edge_tts
import pygame
import requests
import serial
import serial.tools.list_ports
import speech_recognition as sr

SESSION_ID = f"robot-{uuid.uuid4().hex[:12]}"

N8N_WEBHOOK_URL = "https://n8n.duggu.space/webhook/cf13b86f-4a88-45a5-9a48-20c386159c02"
SERIAL_PORT = "COM6"
BAUD_RATE = 115200
ESP32_BOOT_DELAY_SEC = 2.0

STT_LANGUAGE = "en"
STT_TIMEOUT = 5
STT_PHRASE_TIME_LIMIT = 10

TTS_PROVIDER = os.getenv("ROBOT_TTS_PROVIDER", "elevenlabs").lower()
TTS_VOICE = "en-US-AriaNeural"
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "")

ELEVENLABS_STT_KEYTERMS = [
    "Him Academy", "Him Academy Public School", "HAPS", "Shimla",
    "Himachal Pradesh", "Class 9", "Class 10", "Class 11", "Class 12",
    "ninth", "tenth", "eleventh", "twelfth", "principal", "vice principal",
    "reception", "admission", "admissions", "library", "auditorium", "ATL",
    "Atal Tinkering Lab", "physics", "chemistry", "biology", "computer lab",
    "hostel", "boarding", "transport", "school office", "medical room",
    "laboratory", "laboratories",
]

HTTP_TIMEOUT_SEC = 30
MIN_FLAP_MS = 300

BASE_DIR = Path(__file__).resolve().parent
ACK_AUDIO_DIR = BASE_DIR / "reasoning_responses"
WAKE_AUDIO_DIR = BASE_DIR / "wake_responses"

ACK_AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg"}
WAKE_AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg"}

pygame_initialized = False
esp32: serial.Serial | None = None
test_mode = False

shutdown_event = threading.Event()

microphone_lock = threading.Lock()
query_lock = threading.Lock()
interaction_lock = threading.Lock()
audio_lock = threading.Lock()

speech_active = threading.Event()
interaction_active = threading.Event()
ack_stop_event = threading.Event()

serial_thread: threading.Thread | None = None


def log(message: str) -> None:
    print(message, flush=True)


def warn(message: str) -> None:
    print(f"[warn] {message}", flush=True)


def error(message: str) -> None:
    print(f"[error] {message}", flush=True)


def initialize_audio() -> bool:
    global pygame_initialized

    try:
        log("[audio] initializing pygame...")
        pygame.mixer.init()
        pygame_initialized = True
        log("[audio] pygame initialized.")
        return True
    except Exception as exc:
        error(f"pygame initialization failed: {exc}")
        return False


def shutdown_audio() -> None:
    global pygame_initialized

    if not pygame_initialized:
        return

    try:
        with audio_lock:
            pygame.mixer.music.stop()
            pygame.mixer.music.unload()

        pygame.mixer.quit()
    except Exception:
        pass

    pygame_initialized = False


def list_serial_ports() -> None:
    ports = list(serial.tools.list_ports.comports())

    if not ports:
        log("[serial] no serial ports detected.")
        return

    for port in ports:
        log(f"[serial] {port.device} - {port.description or 'Unknown device'}")


def connect_esp32() -> serial.Serial | None:
    global test_mode

    log(f"[serial] connecting to {SERIAL_PORT}")

    try:
        board = serial.Serial(
            SERIAL_PORT,
            BAUD_RATE,
            timeout=0.1,
        )

        time.sleep(ESP32_BOOT_DELAY_SEC)

        test_mode = False

        log(
            f"[serial] connected to "
            f"{SERIAL_PORT} @ {BAUD_RATE}"
        )

        return board

    except Exception as exc:
        test_mode = True
        log(f"[TEST MODE] ESP32 unavailable: {exc}")
        return None


def close_esp32() -> None:
    global esp32

    if esp32 is not None:
        try:
            esp32.close()
        except Exception:
            pass

    esp32 = None


def send_motion(command: str) -> None:
    command = command.strip()

    log(f"[motion] → {command}")

    if test_mode or esp32 is None:
        log("[TEST MODE] motion not sent")
        return

    try:
        esp32.write(
            (command + "\n").encode("utf-8")
        )

        esp32.flush()

    except Exception as exc:
        error(f"motion send failed: {exc}")


def set_listening_state(active: bool) -> None:
    send_motion(
        f"LISTENING:{1 if active else 0}"
    )


def generate_motion(duration_sec: float) -> None:
    duration_ms = max(
        MIN_FLAP_MS,
        round(duration_sec * 1000),
    )

    send_motion(
        f"FLAP:{duration_ms}"
    )


def cleanup_file(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        warn(
            f"could not delete {path.name}: {exc}"
        )


async def edge_tts_generate(
    text: str,
    path: Path,
) -> None:
    communicator = edge_tts.Communicate(
        text=text,
        voice=TTS_VOICE,
    )

    await communicator.save(str(path))


def elevenlabs_generate(
    text: str,
    path: Path,
) -> None:
    if (
        not ELEVENLABS_API_KEY
        or not ELEVENLABS_VOICE_ID
    ):
        raise RuntimeError(
            "ELEVENLABS_API_KEY and "
            "ELEVENLABS_VOICE_ID are required."
        )

    response = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/"
        f"{ELEVENLABS_VOICE_ID}",
        headers={
            "xi-api-key": ELEVENLABS_API_KEY,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        },
        json={
            "text": text,
            "model_id": "eleven_multilingual_v2",
        },
        timeout=HTTP_TIMEOUT_SEC,
    )

    response.raise_for_status()

    path.write_bytes(response.content)


def elevenlabs_transcribe(
    audio: sr.AudioData,
) -> str:
    if not ELEVENLABS_API_KEY:
        raise RuntimeError(
            "ELEVENLABS_API_KEY is required for STT."
        )

    fields = [
        ("model_id", "scribe_v2"),
    ]

    if STT_LANGUAGE:
        fields.append(
            ("language_code", STT_LANGUAGE)
        )

    fields.extend(
        ("keyterms", term)
        for term in ELEVENLABS_STT_KEYTERMS
    )

    response = requests.post(
        "https://api.elevenlabs.io/v1/speech-to-text",
        headers={
            "xi-api-key": ELEVENLABS_API_KEY,
        },
        data=fields,
        files={
            "file": (
                "microphone.wav",
                audio.get_wav_data(),
                "audio/wav",
            )
        },
        timeout=HTTP_TIMEOUT_SEC,
    )

    response.raise_for_status()

    data = response.json()

    text = (
        data.get("text")
        if isinstance(data, dict)
        else None
    )

    if not isinstance(text, str) or not text.strip():
        raise RuntimeError(
            "ElevenLabs STT returned no transcription."
        )

    return text.strip()


def get_audio_duration(path: Path) -> float:
    try:
        sound = pygame.mixer.Sound(str(path))
        duration = float(sound.get_length())
        del sound
        return duration
    except Exception as exc:
        warn(
            f"could not determine audio duration: {exc}"
        )

        return 0.5


def synthesize(
    text: str,
    path: Path,
) -> float:
    if TTS_PROVIDER == "elevenlabs":
        log("[tts] provider: ElevenLabs")
        elevenlabs_generate(text, path)

    elif TTS_PROVIDER == "edge":
        log(
            f"[tts] provider: Edge TTS "
            f"({TTS_VOICE})"
        )

        asyncio.run(
            edge_tts_generate(text, path)
        )

    else:
        raise RuntimeError(
            "ROBOT_TTS_PROVIDER must be "
            "'elevenlabs' or 'edge'."
        )

    return get_audio_duration(path)


def play_audio(
    path: Path,
    stop_event: threading.Event | None = None,
) -> bool:
    if not pygame_initialized:
        raise RuntimeError(
            "pygame is not initialized."
        )

    try:
        with audio_lock:
            pygame.mixer.music.load(str(path))
            pygame.mixer.music.play()

        log("[audio] playing...")

        while not shutdown_event.is_set():
            if (
                stop_event is not None
                and stop_event.is_set()
            ):
                break

            with audio_lock:
                busy = pygame.mixer.music.get_busy()

            if not busy:
                break

            time.sleep(0.03)

        return (
            not shutdown_event.is_set()
            and not (
                stop_event
                and stop_event.is_set()
            )
        )

    finally:
        with audio_lock:
            try:
                pygame.mixer.music.stop()
                pygame.mixer.music.unload()
            except Exception:
                pass


def stop_audio() -> None:
    if not pygame_initialized:
        return

    with audio_lock:
        try:
            pygame.mixer.music.stop()
            pygame.mixer.music.unload()
        except Exception:
            pass


def get_audio_files(
    directory: Path,
    extensions: set[str],
) -> list[Path]:
    if not directory.exists():
        return []

    return sorted(
        path
        for path in directory.iterdir()
        if (
            path.is_file()
            and path.suffix.lower() in extensions
        )
    )


def play_random_wake_response() -> bool:
    files = get_audio_files(
        WAKE_AUDIO_DIR,
        WAKE_AUDIO_EXTENSIONS,
    )

    if not files:
        warn(
            f"no wake responses found in "
            f"{WAKE_AUDIO_DIR}"
        )

        return False

    import random

    path = random.choice(files)

    log(
        f"[wake] {path.name}"
    )

    try:
        duration = get_audio_duration(path)

        log(
            f"[wake] duration: "
            f"{duration:.2f}s"
        )

        generate_motion(duration)

        return play_audio(path)

    except Exception as exc:
        error(
            f"wake response failed: {exc}"
        )

        return False


def get_acknowledgement_files() -> list[Path]:
    return get_audio_files(
        ACK_AUDIO_DIR,
        ACK_AUDIO_EXTENSIONS,
    )


def play_acknowledgement(
    stop_event: threading.Event,
) -> None:
    files = get_acknowledgement_files()

    if not files:
        log(
            f"[ack] no audio files found in "
            f"{ACK_AUDIO_DIR}"
        )

        return

    import random

    path = random.choice(files)

    log(
        f"[ack] {path.name}"
    )

    try:
        duration = get_audio_duration(path)

        log(
            f"[ack] duration: "
            f"{duration:.2f}s"
        )

        generate_motion(duration)

        play_audio(
            path,
            stop_event,
        )

    except Exception as exc:
        warn(
            f"acknowledgement playback failed: "
            f"{exc}"
        )


def speak(text: str) -> None:
    text = text.strip()

    if not text:
        return

    path: Path | None = None

    speech_active.set()

    try:
        log(f"[robot] {text}")

        with tempfile.NamedTemporaryFile(
            prefix="robot_speech_",
            suffix=".mp3",
            delete=False,
        ) as file:
            path = Path(file.name)

        duration = synthesize(
            text,
            path,
        )

        log(
            f"[tts] duration: "
            f"{duration:.2f}s"
        )

        generate_motion(duration)

        play_audio(path)

    except Exception as exc:
        error(
            f"speech pipeline failed: {exc}"
        )

    finally:
        speech_active.clear()

        if path:
            cleanup_file(path)


def listen_for_command(
    recognizer: sr.Recognizer,
    microphone: sr.Microphone,
) -> str | None:
    if not microphone_lock.acquire(
        blocking=False
    ):
        return None

    try:
        log("[stt] listening...")

        try:
            with microphone as source:
                audio = recognizer.listen(
                    source,
                    timeout=STT_TIMEOUT,
                    phrase_time_limit=STT_PHRASE_TIME_LIMIT,
                )

        except sr.WaitTimeoutError:
            warn("no speech detected.")
            return None

        except OSError as exc:
            error(
                f"microphone read failed: {exc}"
            )

            return None

    finally:
        microphone_lock.release()

    try:
        text = elevenlabs_transcribe(audio)

        log(
            f"[stt] {text}"
        )

        return text

    except requests.RequestException as exc:
        error(
            f"ElevenLabs STT request failed: "
            f"{exc}"
        )

    except (
        ValueError,
        RuntimeError,
    ) as exc:
        error(
            f"ElevenLabs STT failed: "
            f"{exc}"
        )

    return None


def ask_n8n(
    text: str,
) -> str | None:
    payload = {
        "source": "robot",
        "type": "query",
        "text": text,
        "sessionId": SESSION_ID,
    }

    try:
        response = requests.post(
            N8N_WEBHOOK_URL,
            json=payload,
            timeout=HTTP_TIMEOUT_SEC,
        )

        response.raise_for_status()

        data = response.json()

    except requests.RequestException as exc:
        error(
            f"n8n request failed: {exc}"
        )

        return None

    except ValueError as exc:
        error(
            f"n8n returned invalid JSON: "
            f"{exc}"
        )

        return None

    answer = (
        data.get("text")
        if isinstance(data, dict)
        else None
    )

    if (
        not isinstance(answer, str)
        or not answer.strip()
    ):
        error(
            f"n8n response missing text: "
            f"{data}"
        )

        return None

    log("[n8n] response received.")

    return answer.strip()


def process_query(text: str) -> None:
    text = text.strip()

    if not text:
        return

    with query_lock:
        ack_stop_event.clear()

        ack_thread = threading.Thread(
            target=play_acknowledgement,
            args=(ack_stop_event,),
            name="ack-worker",
            daemon=True,
        )

        ack_thread.start()

        answer = ask_n8n(text)

        ack_stop_event.set()
        stop_audio()
        ack_thread.join(timeout=1.0)

        if answer:
            speak(answer)


def touch_interaction(
    recognizer: sr.Recognizer | None,
    microphone: sr.Microphone | None,
) -> None:
    if recognizer is None or microphone is None:
        warn("touch interaction unavailable; microphone is not ready.")
        return

    if not interaction_lock.acquire(blocking=False):
        return

    interaction_active.set()

    try:
        log("[touch] visitor detected")
        set_listening_state(True)

        play_random_wake_response()

        if shutdown_event.is_set():
            return

        text = listen_for_command(
            recognizer,
            microphone,
        )

        if text:
            process_query(text)
        else:
            log("[touch] no query received")

    except Exception as exc:
        error(f"touch interaction failed: {exc}")

    finally:
        set_listening_state(False)
        interaction_active.clear()
        interaction_lock.release()
        log("[touch] interaction finished")


def handle_touch(
    recognizer: sr.Recognizer | None,
    microphone: sr.Microphone | None,
) -> None:
    if shutdown_event.is_set() or interaction_active.is_set():
        return

    threading.Thread(
        target=touch_interaction,
        args=(recognizer, microphone),
        name="touch-interaction",
        daemon=True,
    ).start()


def serial_reader(
    recognizer: sr.Recognizer | None,
    microphone: sr.Microphone | None,
) -> None:
    log("[serial] event listener started.")

    while not shutdown_event.is_set():
        if test_mode or esp32 is None:
            shutdown_event.wait(0.1)
            continue

        try:
            if esp32.in_waiting:
                message = (
                    esp32.readline()
                    .decode(
                        "utf-8",
                        errors="ignore",
                    )
                    .strip()
                )

                if not message:
                    continue

                log(
                    f"[esp32] {message}"
                )

                if message == "TOUCH":
                    handle_touch(
                        recognizer,
                        microphone,
                    )

            else:
                time.sleep(0.02)

        except (
            serial.SerialException,
            OSError,
        ) as exc:
            error(
                f"serial read failed: {exc}"
            )

            shutdown_event.wait(0.5)


def start_serial_reader(
    recognizer: sr.Recognizer | None,
    microphone: sr.Microphone | None,
) -> None:
    global serial_thread

    if test_mode or esp32 is None:
        return

    serial_thread = threading.Thread(
        target=serial_reader,
        args=(recognizer, microphone),
        name="serial-reader",
        daemon=True,
    )

    serial_thread.start()


def setup_microphone() -> (
    tuple[
        sr.Recognizer,
        sr.Microphone,
    ] | None
):
    recognizer = sr.Recognizer()

    recognizer.dynamic_energy_threshold = True
    recognizer.pause_threshold = 0.7

    try:
        microphone = sr.Microphone()

    except Exception as exc:
        error(
            f"microphone initialization "
            f"failed: {exc}"
        )

        return None

    log("[stt] calibrating microphone...")

    try:
        with microphone as source:
            recognizer.adjust_for_ambient_noise(
                source,
                duration=1,
            )

    except Exception as exc:
        error(
            f"microphone calibration "
            f"failed: {exc}"
        )

        return None

    log("[stt] microphone ready.")

    return recognizer, microphone


def print_help() -> None:
    print("""
Commands:
/help       Show help
/listen     Manually start a touch interaction
/ports      Show serial ports
/status     Show status
/quit       Exit

Normal text is sent directly to n8n.
""")


def print_status() -> None:
    log(
        f"[status] "
        f"ESP32={'TEST' if test_mode else 'CONNECTED'} | "
        f"STT=ElevenLabs | "
        f"TTS={TTS_PROVIDER} | "
        f"interaction="
        f"{'ACTIVE' if interaction_active.is_set() else 'IDLE'} | "
        f"wake_files="
        f"{len(get_audio_files(WAKE_AUDIO_DIR, WAKE_AUDIO_EXTENSIONS))} | "
        f"ack_files="
        f"{len(get_acknowledgement_files())}"
    )


def console_loop(
    recognizer: sr.Recognizer | None,
    microphone: sr.Microphone | None,
) -> None:
    print_help()

    while not shutdown_event.is_set():
        try:
            command = input(
                "robot> "
            ).strip()

        except (
            EOFError,
            KeyboardInterrupt,
        ):
            break

        if not command:
            continue

        lowered = command.lower()

        if lowered in {
            "/quit",
            "/exit",
        }:
            break

        if lowered == "/help":
            print_help()
            continue

        if lowered == "/ports":
            list_serial_ports()
            continue

        if lowered == "/status":
            print_status()
            continue

        if lowered == "/listen":
            handle_touch(
                recognizer,
                microphone,
            )

            continue

        process_query(command)


def main() -> None:
    global esp32

    log("========================================")
    log("ROBOT RECEPTION CONTROLLER")
    log("========================================")
    log(
        f"n8n: {N8N_WEBHOOK_URL}"
    )
    log(
        f"serial: "
        f"{SERIAL_PORT} @ {BAUD_RATE}"
    )
    log(
        "STT: ElevenLabs Scribe v2"
    )
    log(
        f"TTS: {TTS_PROVIDER}"
    )
    log(
        f"session: {SESSION_ID}"
    )
    log(
        f"wake audio: {WAKE_AUDIO_DIR}"
    )
    log(
        f"ack audio: {ACK_AUDIO_DIR}"
    )

    if not initialize_audio():
        return

    esp32 = connect_esp32()

    microphone = setup_microphone()

    recognizer = (
        microphone[0]
        if microphone
        else None
    )

    mic = (
        microphone[1]
        if microphone
        else None
    )

    if microphone is None:
        warn(
            "microphone unavailable; "
            "touch interaction is disabled, "
            "but console mode remains active."
        )

    start_serial_reader(
        recognizer,
        mic,
    )

    try:
        console_loop(
            recognizer,
            mic,
        )

    finally:
        shutdown_event.set()

        ack_stop_event.set()

        stop_audio()

        if serial_thread is not None:
            serial_thread.join(
                timeout=1
            )

        close_esp32()
        shutdown_audio()

        log("[robot] stopped.")


if __name__ == "__main__":
    main()
