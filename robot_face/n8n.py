from __future__ import annotations

import uuid

import requests

from . import config
from . import log


def new_session_id() -> str:
    return f"robot-{uuid.uuid4().hex[:12]}"


def build_payload(text: str, session_id: str) -> dict[str, str]:
    return {
        "source": "robot",
        "type": "query",
        "text": text,
        "sessionId": session_id,
    }


class Brain:
    def __init__(self, session_id: str | None = None) -> None:
        self.session_id = session_id or new_session_id()

    def rotate(self) -> str:
        self.session_id = new_session_id()
        log.debug(f"[n8n] new conversation: {self.session_id}")
        return self.session_id

    def ask(self, text: str) -> str | None:
        if not config.WEBHOOK_URL:
            log.error("[n8n] WEBHOOK_URL is not set.")
            return None

        try:
            response = requests.post(
                config.WEBHOOK_URL,
                json=build_payload(text, self.session_id),
                timeout=config.HTTP_TIMEOUT_SEC,
            )

            response.raise_for_status()
            data = response.json()
        except requests.RequestException as exc:
            log.error(f"[n8n] request failed: {exc}")
            return None
        except ValueError as exc:
            log.error(f"[n8n] invalid JSON: {exc}")
            return None

        answer = data.get("text") if isinstance(data, dict) else None

        if not isinstance(answer, str) or not answer.strip():
            log.error(f"[n8n] response carried no text: {data}")
            return None

        log.log("[n8n] response received.")
        return answer.strip()
