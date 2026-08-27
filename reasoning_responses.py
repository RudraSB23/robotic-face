import os
from pathlib import Path
import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("ELEVENLABS_API_KEY")
VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID")
OUT_DIR = Path(__file__).parent / "reasoning_responses"
URL = f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}"

responses = [
    "Sure, let me check that for you. It may take a while.",
    "Of course. Give me just a moment.",
    "Sure, let me look that up for you.",
    "One moment, please. I'm checking the school information.",
    "Got it. Let me find that for you.",
    "Sure. I'll check my school information and get back to you.",
]

if not API_KEY or not VOICE_ID:
    raise RuntimeError("Set ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID in your .env file.")

OUT_DIR.mkdir(exist_ok=True)

for i, text in enumerate(responses, 1):
    path = OUT_DIR / f"response_{i}.mp3"
    print(f"[{i}/{len(responses)}] Generating: {text}")
    try:
        response = requests.post(
            URL,
            headers={"xi-api-key": API_KEY, "Content-Type": "application/json"},
            json={"text": text, "model_id": "eleven_multilingual_v2"},
            timeout=60,
        )
        response.raise_for_status()
        path.write_bytes(response.content)
        print(f"    -> {path.name}")
    except requests.RequestException as exc:
        print(f"    [error] {exc}")

print(f"\nDone. Files are in: {OUT_DIR}")
