from __future__ import annotations

import argparse
from pathlib import Path

import requests

from robot_face import config

TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech"

WAKE_LINES = [
    "Hey! How can I help you today?",
    "Hi there! What can I help you with?",
    "Hello! What can I do for you?",
    "Hey! Need some help finding something?",
    "Hi! How can I assist you?",
    "Hello! What would you like to know?",
]

ACK_LINES = [
    "Sure, let me check that for you. It may take a while.",
    "Of course. Give me just a moment.",
    "Sure, let me look that up for you.",
    "One moment, please. I'm checking the school information.",
    "Got it. Let me find that for you.",
    "Sure. I'll check my school information and get back to you.",
]

SETS = {
    "wake": (config.WAKE_AUDIO_DIR, WAKE_LINES),
    "ack": (config.ACK_AUDIO_DIR, ACK_LINES),
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Regenerate robot audio clips.")
    parser.add_argument(
        "set_name",
        nargs="?",
        default="all",
        choices=["wake", "ack", "all"],
    )
    args = parser.parse_args()

    if not config.ELEVENLABS_API_KEY or not config.VOICE_ID:
        raise SystemExit(
            "Set ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID in your .env file."
        )

    if args.set_name == "all":
        selected = SETS.items()
    else:
        selected = [(args.set_name, SETS[args.set_name])]

    for label, (directory, lines) in selected:
        generate(label, directory, lines)


def generate(label: str, directory: Path, lines: list[str]) -> None:
    directory.mkdir(exist_ok=True)

    for index, text in enumerate(lines, 1):
        target = directory / f"response_{index}.mp3"
        print(f"[{label} {index}/{len(lines)}] {text}")

        try:
            response = requests.post(
                f"{TTS_URL}/{config.VOICE_ID}",
                headers={
                    "xi-api-key": config.ELEVENLABS_API_KEY,
                    "Content-Type": "application/json",
                },
                json={
                    "text": text,
                    "model_id": config.ELEVENLABS_TTS_MODEL,
                },
                timeout=config.HTTP_TIMEOUT_SEC,
            )

            response.raise_for_status()
            target.write_bytes(response.content)
            print(f"    -> {target.name}")
        except requests.RequestException as exc:
            print(f"    [error] {exc}")

    print(f"[{label}] written to {directory}")


if __name__ == "__main__":
    main()
