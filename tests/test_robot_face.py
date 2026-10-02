from __future__ import annotations

import re
import tempfile
import threading
import time
import unittest
from pathlib import Path

from robot_face import config
from robot_face.app import RobotApp, audio_files
from robot_face.audio import AudioOut
from robot_face.config import FaceState
from robot_face.face import Face
from robot_face.link import ESP32Link
from robot_face.listening import build_stt_fields
from robot_face.n8n import build_payload

FIRMWARE = (
    Path(__file__).resolve().parent.parent
    / "reception_robot"
    / "reception_robot.ino"
)

WIRE_VERBS = ("STATE:", "STOP", "FLAP:", "LISTENING:", "CENTER", "BLINK")


class FakeLink:
    def __init__(self) -> None:
        self.commands: list[str] = []
        self.connected = True

    def send(self, command: str) -> None:
        self.commands.append(command)

    def describe(self) -> str:
        return "FAKE"

    def connect(self) -> bool:
        return True

    def close(self) -> None:
        pass

    def states(self) -> list[str]:
        return [
            command.split(":", 1)[1]
            for command in self.commands
            if command.startswith("STATE:")
        ]

    def has(self, prefix: str) -> bool:
        return any(command.startswith(prefix) for command in self.commands)

    def index_of_state(self, state: str) -> int:
        return self.states().index(state)


class FakeAudio:
    def __init__(self, duration: float = 2.0) -> None:
        self.duration_value = duration
        self.played: list[Path] = []
        self.stop_calls = 0
        self.available = True
        self.silent = False

    def start(self) -> bool:
        return True

    def duration(self, path: Path) -> float:
        return self.duration_value

    def play(self, path: Path, stop_event: threading.Event | None = None) -> bool:
        self.played.append(path)
        return True

    def stop(self) -> None:
        self.stop_calls += 1

    def close(self) -> None:
        pass


class FakeListener:
    def __init__(self) -> None:
        self.ready = True
        self.captures = 0

    def setup(self) -> bool:
        return True

    def capture(self) -> object:
        self.captures += 1
        return object()


class FakeTranscriber:
    def __init__(self, transcripts: list[str]) -> None:
        self._transcripts = list(transcripts)

    def transcribe(self, audio: object) -> str | None:
        if not self._transcripts:
            return None
        return self._transcripts.pop(0)


class FakeBrain:
    def __init__(self, answer: str | None, on_ask=None) -> None:
        self.answer = answer
        self.on_ask = on_ask
        self.session_id = "robot-test"
        self.asked: list[str] = []

    def ask(self, text: str) -> str | None:
        self.asked.append(text)

        if self.on_ask is not None:
            self.on_ask()

        return self.answer


class FakeSpeaker:
    def __init__(self) -> None:
        self.provider = "fake"
        self.spoken: list[str] = []
        self._paths: list[Path] = []

    def render(self, text: str) -> Path:
        self.spoken.append(text)

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as handle:
            path = Path(handle.name)

        path.write_bytes(b"fake-audio")
        self._paths.append(path)
        return path

    def leftovers(self) -> list[Path]:
        return [path for path in self._paths if path.exists()]


def make_app(
    transcripts: list[str],
    answer: str | None = "The library is on the first floor.",
    on_ask=None,
) -> tuple[RobotApp, FakeLink, FakeAudio, FakeSpeaker]:
    link = FakeLink()
    audio = FakeAudio()
    speaker = FakeSpeaker()

    app = RobotApp(
        link=link,
        audio=audio,
        face=Face(link),
        listener=FakeListener(),
        transcriber=FakeTranscriber(transcripts),
        brain=FakeBrain(answer, on_ask=on_ask),
        speaker=speaker,
    )

    return app, link, audio, speaker


class ProtocolAgreementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.firmware = FIRMWARE.read_text(encoding="utf-8")

    def test_firmware_accepts_exactly_the_python_states(self) -> None:
        accepted = set(re.findall(r'name == "([A-Z]+)"', self.firmware))
        expected = {state.value for state in FaceState}
        self.assertEqual(accepted, expected)

    def test_every_state_has_a_reply_name_in_the_firmware(self) -> None:
        reported = set(re.findall(r'return "([A-Z_]+)";', self.firmware))
        expected = {state.value for state in FaceState}
        self.assertEqual(reported & expected, expected)

    def test_firmware_handles_every_verb_python_can_send(self) -> None:
        for verb in WIRE_VERBS:
            self.assertIn(f'"{verb}"', self.firmware)

    def test_firmware_answers_the_keepalive(self) -> None:
        self.assertIn('command == "PING"', self.firmware)
        self.assertIn("OK:PONG", self.firmware)

    def test_firmware_owns_the_mouth_only_through_one_writer(self) -> None:
        self.assertEqual(self.firmware.count("applyTargets()"), 2)
        for state in ("updateSpeaking", "updateGreeting"):
            self.assertIn(f"void {state}(unsigned long now)", self.firmware)

    def test_link_frames_commands_with_newlines(self) -> None:
        link = ESP32Link()
        link._connected = True
        link.send("STATE:LISTENING")
        self.assertEqual(link._outbox.get_nowait(), "STATE:LISTENING")


class FaceCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.link = FakeLink()
        self.face = Face(self.link)

    def test_speak_sets_speaking_then_flaps(self) -> None:
        self.face.speak(3.2)
        self.assertEqual(self.link.commands, ["STATE:SPEAKING", "FLAP:3200"])

    def test_listen_cancels_motion_before_listening(self) -> None:
        self.face.listen()
        self.assertEqual(self.link.commands, ["STOP", "STATE:LISTENING"])

    def test_greet_sets_greeting_then_flaps(self) -> None:
        self.face.greet(1.8)
        self.assertEqual(self.link.commands, ["STATE:GREETING", "FLAP:1800"])

    def test_flap_respects_the_firmware_minimum(self) -> None:
        self.face.flap(0.01)
        self.assertEqual(self.link.commands[-1], "FLAP:300")

    def test_thinking_with_a_duration_flaps_the_mouth(self) -> None:
        self.face.think(2.4)
        self.assertEqual(
            self.link.commands, ["STATE:THINKING", "FLAP:2400"]
        )

    def test_thinking_without_a_duration_is_silent(self) -> None:
        self.face.think()
        self.assertEqual(self.link.commands, ["STATE:THINKING"])


class InteractionFlowTests(unittest.TestCase):
    def test_touch_visits_states_in_conversational_order(self) -> None:
        app, link, _audio, speaker = make_app(["where is the library"])
        app.touch_interaction()

        states = link.states()

        self.assertEqual(states[0], "NOTICING")
        self.assertEqual(states[-1], "FINISHING")
        self.assertLess(
            link.index_of_state("GREETING"),
            link.index_of_state("LISTENING"),
        )
        self.assertLess(
            link.index_of_state("LISTENING"),
            link.index_of_state("THINKING"),
        )
        self.assertLess(
            link.index_of_state("THINKING"),
            link.index_of_state("SPEAKING"),
        )
        self.assertEqual(app.brain.asked, ["where is the library"])
        self.assertEqual(speaker.spoken, ["The library is on the first floor."])

    def test_speaking_flap_matches_the_clip_duration(self) -> None:
        app, link, _audio, _speaker = make_app(["hello"])
        app.touch_interaction()
        self.assertIn("FLAP:2000", link.commands)

    def test_greeting_flap_precedes_listening(self) -> None:
        app, link, _audio, _speaker = make_app(["hello"])
        app.touch_interaction()

        flap = link.commands.index("FLAP:2000")
        listening = link.commands.index("STATE:LISTENING")
        self.assertLess(flap, listening)

    def test_interrupt_during_answer_stops_motion_and_skips_speech(self) -> None:
        app, link, _audio, speaker = make_app(["first question"])

        def visitor_touches_again() -> None:
            app.interrupt.set()
            app.face.cancel_motion()
            app.audio.stop()

        app.brain.on_ask = visitor_touches_again
        app.touch_interaction()

        self.assertIn("STOP", link.commands)
        self.assertNotIn("SPEAKING", link.states())
        self.assertEqual(speaker.spoken, [])
        self.assertEqual(link.states()[-1], "FINISHING")

    def test_interrupt_after_answer_offers_a_second_listen(self) -> None:
        app, link, _audio, _speaker = make_app(
            ["first question", "second question"]
        )
        original_speak = app.speak

        def speak_then_interrupt(text: str) -> bool:
            result = original_speak(text)
            app.interrupt.set()
            return result

        app.speak = speak_then_interrupt
        app.touch_interaction()

        self.assertEqual(link.states().count("LISTENING"), 2)
        self.assertEqual(app.brain.asked, ["first question", "second question"])

    def test_silence_ends_without_calling_n8n(self) -> None:
        app, link, _audio, speaker = make_app([])
        app.touch_interaction()

        self.assertEqual(app.brain.asked, [])
        self.assertEqual(speaker.spoken, [])
        self.assertEqual(link.states()[-1], "FINISHING")
        self.assertNotIn("SPEAKING", link.states())

    def test_failed_lookup_speaks_the_fallback_line(self) -> None:
        app, link, _audio, speaker = make_app(["obscure"], answer=None)
        app.touch_interaction()

        self.assertEqual(speaker.spoken, [config.FALLBACK_REPLY])
        self.assertIn("SPEAKING", link.states())

    def test_speech_temp_files_are_deleted(self) -> None:
        app, _link, _audio, speaker = make_app(["hello"])
        app.touch_interaction()
        self.assertEqual(speaker.leftovers(), [])

    def test_second_touch_is_dropped_while_busy(self) -> None:
        app, link, _audio, _speaker = make_app(["hello"], answer="hi")
        started = threading.Event()
        asked: list[str] = []

        def slow_ask(text: str) -> str:
            asked.append(text)
            started.set()
            time.sleep(0.2)
            return "hi"

        app.brain.ask = slow_ask

        first = threading.Thread(target=app.touch_interaction)
        first.start()
        started.wait(1)

        app.touch_interaction()

        first.join(timeout=3)

        self.assertEqual(len(asked), 1)
        self.assertEqual(link.states().count("GREETING"), 1)

    def test_acknowledgement_flaps_while_it_waits(self) -> None:
        app, link, _audio, _speaker = make_app(["hello"])
        started = threading.Event()
        release = threading.Event()

        def blocking_ask(text: str) -> str:
            started.set()
            release.wait(2)
            return "answer"

        app.brain.ask = blocking_ask

        worker = threading.Thread(target=app.touch_interaction)
        worker.start()
        started.wait(1)

        for _ in range(200):
            if link.commands.count("FLAP:2000") >= 2:
                break
            time.sleep(0.01)

        release.set()
        worker.join(timeout=5)

        self.assertGreaterEqual(link.commands.count("FLAP:2000"), 2)

    def test_state_is_released_after_an_interaction(self) -> None:
        app, _link, _audio, _speaker = make_app(["hello"])
        app.touch_interaction()

        self.assertFalse(app.interaction_active.is_set())
        self.assertFalse(app.interrupt.is_set())
        self.assertFalse(app._interaction_lock.locked())

    def test_touch_without_a_microphone_is_ignored(self) -> None:
        app, link, _audio, _speaker = make_app(["hello"])
        app.listener.ready = False
        app.touch_interaction()
        self.assertEqual(link.commands, [])


class AudioSafetyTests(unittest.TestCase):
    def _clip(self) -> Path:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as handle:
            return Path(handle.name)

    def test_play_refuses_a_preset_stop_event(self) -> None:
        audio = AudioOut(threading.Event())
        stop = threading.Event()
        stop.set()
        path = self._clip()

        self.assertFalse(audio.play(path, stop))
        path.unlink(missing_ok=True)

    def test_play_without_an_output_device_reports_false(self) -> None:
        audio = AudioOut(threading.Event())
        path = self._clip()

        self.assertFalse(audio.play(path))
        path.unlink(missing_ok=True)

    def test_shutdown_stops_the_playback_loop(self) -> None:
        shutdown = threading.Event()
        shutdown.set()
        audio = AudioOut(shutdown)
        path = self._clip()

        self.assertFalse(audio.play(path))
        path.unlink(missing_ok=True)


class LinkDispatchTests(unittest.TestCase):
    def test_touch_ready_and_replies_are_dispatched(self) -> None:
        touches: list[bool] = []
        ready: list[bool] = []
        replies: list[str] = []

        link = ESP32Link(
            on_touch=lambda: touches.append(True),
            on_ready=lambda: ready.append(True),
            on_reply=replies.append,
        )

        link.feed("Robot Ready!")
        link.feed("TOUCH")
        link.feed("OK:STATE:LISTENING")
        link.feed("ERR:UNKNOWN")
        link.feed("   ")

        self.assertEqual(ready, [True])
        self.assertEqual(touches, [True])
        self.assertEqual(replies, ["OK:STATE:LISTENING", "ERR:UNKNOWN"])
        self.assertTrue(link.ready_seen)

    def test_pong_marks_the_link_healthy(self) -> None:
        link = ESP32Link()
        link._connected = True
        link._last_pong = 0.0

        self.assertFalse(link.link_healthy())
        link.feed("OK:PONG")
        self.assertTrue(link.link_healthy())

    def test_test_mode_never_queues_commands(self) -> None:
        link = ESP32Link()
        link._test_mode = True
        link.send("STATE:IDLE")

        self.assertTrue(link._outbox.empty())
        self.assertTrue(link.test_mode)


class PayloadTests(unittest.TestCase):
    def test_n8n_payload_shape(self) -> None:
        self.assertEqual(
            build_payload("hello", "robot-abc"),
            {
                "source": "robot",
                "type": "query",
                "text": "hello",
                "sessionId": "robot-abc",
            },
        )

    def test_stt_fields_include_every_keyterm_once(self) -> None:
        fields = build_stt_fields()
        self.assertIn(("model_id", config.ELEVENLABS_STT_MODEL), fields)

        keyterms = [value for name, value in fields if name == "keyterms"]
        self.assertEqual(len(keyterms), len(config.STT_KEYTERMS))
        self.assertEqual(len(set(keyterms)), len(keyterms))

    def test_audio_files_filters_by_extension(self) -> None:
        found = audio_files(config.WAKE_AUDIO_DIR)
        self.assertTrue(found)

        for path in found:
            self.assertEqual(path.suffix.lower(), ".mp3")

    def test_missing_audio_directory_is_not_fatal(self) -> None:
        self.assertEqual(audio_files(Path("does-not-exist")), [])


if __name__ == "__main__":
    unittest.main()
