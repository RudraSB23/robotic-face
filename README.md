# Reception Robot

An ESP32 servo face driven by a Python controller. A visitor touches the sensor,
the robot greets them, listens, sends the question to an n8n workflow, and answers
out loud — while the face expresses what it is doing at every step.

The voice pipeline is `touch → ElevenLabs STT → n8n → TTS → speakers`. The face is
animated on the ESP32 itself, so movement never depends on Python timing.

---

> ## ⚠️ Read this first
>
> **The current version of this project was written by vibe coding.** Large parts
> of it — especially the ESP32 firmware — were generated in one pass and have never
> been run on real hardware or compiled by a toolchain. It very likely does not work
> properly yet.
>
> **If the robot misbehaves, does not move, or will not boot: use the previous
> version.** It is the last version known to work on a physical robot, and it is
> preserved in git at commit [`9067c0b`](https://github.com/RudraSB23/robotic-face/commit/9067c0b38055410cc9e6e2423974fba905942a8c).
>
> ```powershell
> # work on the old version alongside the new one
> git switch -c known-good 9067c0b
>
> # or just look at what changed
> git diff 9067c0b HEAD
> ```
>
> Treat the current rewrite as a prototype to flash, poke at, and debug — not as
> something to put in front of visitors.

---

## Hardware

| Part | GPIO | Notes |
| --- | --- | --- |
| Mouth servo | 12 | travel 40–110° |
| Eye vertical servo | 27 | travel 62–109° |
| Eye horizontal servo | 14 | travel 65–140° |
| Neck servo | 25 | centre 75° |
| Touch sensor | 33 | input only, active HIGH |

The servo travel limits are mechanical calibration, not arbitrary. They are
defined once at the top of `reception_robot/reception_robot.ino` and enforced in
three places: the state animation, the manual `MOUTH:` / `EYES:` / `NECK:`
commands, and the motion stepper itself. Do not widen any of them without
checking the linkage first.

## Flashing the firmware

1. Open `reception_robot/reception_robot.ino` in the Arduino IDE.
2. Install the **ESP32Servo** library.
3. Select your ESP32 board and the port the robot is connected on.
4. Upload, then **close the serial monitor** — the controller needs that port.

## Configuration

Everything lives in `.env`. Only three keys are required.

| Variable | Default | Meaning |
| --- | --- | --- |
| `WEBHOOK_URL` | – | n8n webhook; must answer with `{"text": "..."}` |
| `ELEVENLABS_API_KEY` | – | used for both STT and TTS |
| `ELEVENLABS_VOICE_ID` | – | TTS voice |
| `ROBOT_SERIAL_PORT` | `COM6` | ESP32 port |
| `ROBOT_TTS_PROVIDER` | `elevenlabs` | or `edge` for Edge TTS |
| `ROBOT_TTS_VOICE` | `en-US-AriaNeural` | Edge TTS voice |
| `ROBOT_STT_LANGUAGE` | `en` | sent to the transcriber |
| `ROBOT_STT_TIMEOUT_SEC` | `5` | silence before the mic gives up |
| `ROBOT_STT_PHRASE_LIMIT_SEC` | `10` | longest single utterance |
| `ROBOT_LINK_PING_SEC` | `5` | keepalive interval |
| `ROBOT_LINK_TIMEOUT_SEC` | `15` | silence after which the link is "unresponsive" |
| `ROBOT_FALLBACK_REPLY` | see `config.py` | spoken when the workflow returns nothing |
| `ROBOT_DEBUG` | off | logs every motion command |
| `ROBOT_LOG_TIMESTAMPS` | off | prefixes logs with an elapsed timer |

## Running

```powershell
uv sync
uv run python main.py
```

Or with an already-provisioned virtualenv:

```powershell
.venv\Scripts\python.exe main.py
```

**Without an ESP32** the controller starts in test mode: it logs every motion
command instead of sending it, `/status` shows `link=TEST MODE`, and everything
else — STT, n8n, TTS, playback, the console — works normally. This is the
easiest way to work on the conversation without the robot in front of you.

### Console

```
/help              show help
/listen            start a full touch interaction by hand
/face              show the current face state
/face GREETING     force a state (IDLE, NOTICING, GREETING,
                   LISTENING, THINKING, SPEAKING, FINISHING)
/ports             list serial ports
/status            show link, state, and audio health
/quit              exit
```

Anything that is not a slash command is sent straight to n8n and answered out
loud, which is a quick way to test the voice path without the robot.

## Tests

```powershell
uv run python -m unittest discover -s tests
```

The suite needs no hardware and no network. It covers the conversational state
sequence, interruption handling, playback cancellation, serial framing, and it
parses the firmware source to assert that the set of states Python can send is
exactly the set the firmware accepts.

Note what this does **not** prove: the Python side is unit tested, but the
firmware is not compiled or run anywhere in CI. The firmware has only been
checked statically.

## How the face works

### States

The conversation has seven states. Python sends one `STATE:<name>` command per
transition; the ESP32 owns what each state looks like.

| State | Behaviour |
| --- | --- |
| `IDLE` | eyes half-lidded, slow breathing sway, random blinks, occasional glances around the room |
| `NOTICING` | sharp look-up and a small lean-in, holds ~0.4 s |
| `GREETING` | warm attentive gaze, mouth flaps for the wake clip |
| `LISTENING` | holds eye contact, blinks, glances aside, occasional "hmm" mouth pulse |
| `THINKING` | eyes drift up and to one side as if recalling, slower blinks, pursed mouth |
| `SPEAKING` | mouth flaps for the spoken clip, gentle sway and blinks |
| `FINISHING` | a small nod, soft eyes, then returns to `IDLE` by itself |

### Why animations do not fight each other

This is the part worth understanding before changing anything.

There is exactly **one writer per servo**. Animation states do not move servos —
they only produce a *target angle*. A single stepper eases each axis toward its
target at a per-axis speed and calls `Servo.write()` only when the rounded value
actually changes.

Three layers of intent resolve in `applyTargets()`, in priority order:

1. **Manual overrides** — `MOUTH:`, `EYES:`, `NECK:` win for 4 seconds, then the
   current state takes the face back.
2. **Blink** — forces the eye-vertical target fully closed. It modifies a target
   rather than writing directly, so a blink during a head turn or a sentence
   cannot conflict with anything.
3. **Mouth flap vs. state animation** — while a flap is running the state
   animation has no say over the mouth; otherwise the state's own mouth pose is
   used.

Point 3 was the original firmware's central bug: `updateFlap()` and
`updateListening()` both wrote `mouthServo` directly and both ran every loop, so
the jaw fought itself for the whole conversation. Ownership is now explicit and
mutually exclusive.

Because targets move gradually and writes are change-detected, the head, eyes and
neck glide rather than snapping — the previous firmware jumped instantly between
angles, which made the servos buzz.

### Idle life

`IDLE` is not a static pose. A shared sway oscillator drives small neck and eye
drifts, a shared glance scheduler occasionally looks aside and back, and blinks
are scheduled per state (faster while attentive, slower while thinking). This is
what makes the face read as alive between visitors, and it needs no Python
involvement at all.

## Serial protocol

Newline-delimited ASCII at 115200 baud. Every command is answered with `OK:`,
`ERR:` or `OK:PONG`. Nothing blocks waiting for a reply.

Python → ESP32:

| Command | Effect |
| --- | --- |
| `STATE:<NAME>` | switch state, replies `OK:STATE:<NAME>` |
| `STOP` | cancel any flap and manual override immediately |
| `FLAP:<ms>` | flap the mouth for that long (min 300 ms) |
| `LISTENING:<0\|1>` | legacy alias for `STATE:LISTENING` / `STATE:IDLE` |
| `MOUTH:<deg>` | manual mouth, 4 s hold |
| `EYES:<hor>,<ver>` | manual eyes, 4 s hold |
| `NECK:<deg>` | manual neck, 4 s hold |
| `CENTER` | drop overrides and return to `IDLE` |
| `BLINK` | blink once |
| `PING` | liveness probe, replies `OK:PONG` |

ESP32 → Python:

| Message | Meaning |
| --- | --- |
| `TOUCH` | visitor touched the sensor (1 s cooldown, must release first) |
| `Robot Ready!` | boot banner, also used as a handshake |
| `OK:*` / `ERR:*` / `WARN:*` | command results |

### Failure handling

- **ESP32 missing** → test mode, everything else still runs.
- **ESP32 unplugged mid-run** → writes start failing, the link is marked down,
  and after 10 failures the controller reports it once and stops retrying.
- **ESP32 silent but port open** → the `PING` keepalive notices and warns.
- **Python dies mid-interaction** → the firmware watchdog returns the face to
  `IDLE` 30 s after the last command.
- **No sound card** → the controller logs it and keeps animating.
- **n8n returns nothing** → the fallback line is spoken instead of silence.

## Concurrency

- The serial reader only dispatches; it never performs network or audio work, so
  a touch is always answered immediately.
- All serial writes go through a single writer thread and a bounded queue, so
  commands from the touch thread, the acknowledgement thread and the console can
  never interleave mid-line on the wire.
- One touch interaction runs at a time (`interaction_lock`, non-blocking).
- Queries are serialised by `query_lock`. Console queries use a non-blocking
  acquire, so typing at the console can never stall a visitor.
- The microphone is captured under its own lock, released before transcription,
  so recording and HTTP never overlap.
- A second touch during an interaction is an interrupt: it cancels the flap on
  the ESP32, stops playback, and re-opens the microphone for a follow-up.

## Layout

```
main.py                     entry point
generate_audio.py           regenerates the wake and acknowledgement clips
robot_face/
  config.py                 every constant and env var, plus the state list
  log.py                    logging helpers
  link.py                   ESP32 serial: writer queue, reader, keepalive
  face.py                   conversation state <-> wire commands
  audio.py                  pygame playback and TTS
  listening.py              microphone capture and ElevenLabs STT
  n8n.py                    n8n client
  app.py                    interaction lifecycle and console
reception_robot/
  reception_robot.ino       ESP32 firmware
wake_responses/             "How can I help you?" clips
reasoning_responses/        "let me check that" clips
tests/                      unittest suite
```

To re-record the clips in the current voice:

```powershell
uv run python generate_audio.py          # both sets
uv run python generate_audio.py wake     # one set
```

## Known gaps

Being explicit about what is unfinished:

- The firmware has never been compiled by a real toolchain. The Arduino IDE is
  the first place it will meet a compiler — expect to fix things there.
- `backup.py`, `backup/` and `src/` are stale local leftovers. They are
  gitignored and no longer part of the system.
- The `keyterms` list for STT is sent as repeated multipart fields. The ElevenLabs
  docs describe `keyterms` as a list of strings; that this encoding is parsed as
  a list rather than only the last value being read is assumed, not verified.
- No CI, and the tests do not cover the firmware.

### Security note

The previous version (`9067c0b`) hardcoded a live n8n webhook URL directly in
`main.py`. That URL is therefore public in this repository's git history, and
anyone can send requests to it. The current version reads the webhook from
`.env` and redacts it in the startup banner, but history cannot be cleaned
without destroying the known-good fallback commit.

**Rotate that webhook in n8n.** It should be treated as compromised.

## Hardware cautions

- Four servos need an external 5 V supply. Brownouts mid-flap reset the board and
  flood the controller with `Robot Ready!`.
- GPIO 33 has no internal pull-up. If the touch sensor is unplugged the pin floats
  and the ESP32 reports touches continuously.
- The controller holds the serial port while running. Close the Arduino monitor.
- `ESP32Servo` uses the ESP32's LEDC peripherals; adding a fifth servo on these
  pins is not a software change.