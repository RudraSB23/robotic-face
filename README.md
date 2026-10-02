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

## Setup

Do these in order. Steps 1–5 get the software running without the robot;
steps 6–8 are what you need for it to move and answer.

### 1. Install uv

uv is the package manager this project uses. It creates the virtualenv and
installs dependencies in one command.

**Windows (PowerShell)**

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

or, if you prefer a package manager:

```powershell
winget install --id=astral-sh.uv -e
```

**macOS / Linux**

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Verify it worked:

```powershell
uv --version
```

### 2. Get the code

```powershell
git clone https://github.com/RudraSB23/robotic-face.git
cd robotic-face
```

### 3. Install dependencies

```powershell
uv sync
```

This creates `.venv` and installs everything from `uv.lock`. Python 3.13 is
required; uv will fetch it for you if you do not have it.

### 4. Create `.env`

Copy the three required values into a file called `.env` in the project root.
It is gitignored — never commit it.

```dotenv
# n8n — step 7 tells you what to paste here
WEBHOOK_URL=

# ElevenLabs — used for both speech-to-text and text-to-speech
ELEVENLABS_API_KEY=
ELEVENLABS_VOICE_ID=
```

`ELEVENLABS_VOICE_ID` is the last item in the voice browser on your ElevenLabs
dashboard. A voice that matches the existing clips is ideal, otherwise the
robot will change personality when it starts speaking.

Everything else is optional:

| Variable | Default | Meaning |
| --- | --- | --- |
| `ROBOT_SERIAL_PORT` | `COM6` | ESP32 port; run `/ports` to find yours |
| `ROBOT_TTS_PROVIDER` | `elevenlabs` | or `edge` to use Edge TTS instead |
| `ROBOT_TTS_VOICE` | `en-US-AriaNeural` | Edge TTS voice |
| `ROBOT_TTS_MODEL` | `eleven_multilingual_v2` | ElevenLabs TTS model |
| `ROBOT_STT_MODEL` | `scribe_v2` | ElevenLabs speech-to-text model |
| `ROBOT_STT_LANGUAGE` | `en` | sent to the transcriber |
| `ROBOT_STT_TIMEOUT_SEC` | `5` | silence before the mic gives up |
| `ROBOT_STT_PHRASE_LIMIT_SEC` | `10` | longest single utterance |
| `ROBOT_HTTP_TIMEOUT_SEC` | `30` | how long to wait for the n8n answer |
| `ROBOT_BAUD_RATE` | `115200` | serial speed; only change if you reflash at another rate |
| `ROBOT_BOOT_DELAY_SEC` | `2.0` | wait after opening the port, while the ESP32 boots |
| `ROBOT_LINK_PING_SEC` | `5` | keepalive interval |
| `ROBOT_LINK_TIMEOUT_SEC` | `15` | silence after which the link is "unresponsive" |
| `ROBOT_FALLBACK_REPLY` | see `config.py` | spoken when the workflow returns nothing |
| `ROBOT_DEBUG` | off | set to `1` to log every motion command |
| `ROBOT_LOG_TIMESTAMPS` | off | set to `1` to prefix logs with a timer |

### 5. First run without hardware

```powershell
uv run python main.py
```

With no ESP32 connected it starts in test mode and logs every motion command
instead of sending it. Check that the console comes up:

```
robot> /status
[status] link=TEST MODE | face=IDLE | tts=elevenlabs | stt=scribe_v2 | mic=ready | sound=on | interaction=IDLE | wake=6 | ack=6
```

Then run the tests:

```powershell
uv run python -m unittest discover -s tests
```

You can stop here until you have the robot assembled.

### 6. Wire up and flash the ESP32

**Servo and sensor pins**

See the table in *Hardware* above. Briefly: mouth 12, eye vertical 27, eye
horizontal 14, neck 25, touch 33.

All servo grounds must be tied together and to the ESP32 ground. Power the
servos from a **separate 5 V supply** — four servos on the board's 3V3 rail will
brown out and reboot the ESP32 mid-movement.

**Flashing**

1. Install the [Arduino IDE](https://www.arduino.cc/en/software).
2. In *Tools → Boards*, install **esp32 by Espressif Systems** (this is a large
   package; expect a few minutes).
3. *Tools → Manage Libraries*, search for **ESP32Servo** by Kevin Harrington and
   install it.
4. Open `reception_robot/reception_robot.ino`.
5. Select your board in *Tools → Board*, and the port in *Tools → Port*.
6. Upload. The board prints `Robot Ready!` once it boots.
7. **Close the serial monitor.** The controller needs that port, and the two
   cannot share it.

### 7. Set up the n8n workflow

The repository ships the workflow as `Talking Robot - HAPS RAG.json`. It is a RAG
agent for Him Academy Public School:

```
Webhook → AI Agent (knowledge base + memory) → Set field → Respond to Webhook
```

**a. Import it.** In n8n, *Workflows → Import from File*, and select
`Talking Robot - HAPS RAG.json`. If you are on n8n Cloud, you can also paste the
file contents into a new workflow.

**b. Add credentials.** The agent needs two, both named `main` in the export:

| Credential | Used by | Notes |
| --- | --- | --- |
| **OpenAI** | `Embeddings OpenAI1` | required — this creates the vector embeddings |
| **OpenRouter** | `OpenRouter Chat Model1` | required — this is the model that answers |

Because the file was exported as a template, n8n will prompt for these on
import. Create them under *Credentials → New credential*.

> The agent node also has a second chat model (`OpenAI Chat Model`) wired into the
> same input. Only one model can be active on that port and which one n8n picks
> is undefined. Open the agent, disconnect the model you do not want, and save.

**c. Activate it.** Toggle the workflow to *Active*. n8n only serves production
webhook URLs for active workflows.

**d. Copy the webhook URL.** Open the **Webhook1** node and copy its
**Production URL**. It looks like:

```
https://your-n8n-host/webhook/76d40ee1-0d27-47fe-88f2-acd7bf3fca01
```

Paste that into `WEBHOOK_URL` in your `.env`. The long UUID at the end *is* the
credential — anyone holding it can query your knowledge base.

**e. Load the knowledge base.** This step is easy to miss, and the robot will
appear to work while answering nothing useful if you skip it.

The workflow stores documents in n8n's **in-memory** vector store under the key
`school-robot`, and that store starts empty on a fresh import. To fill it:

1. Open the **Upload your file here1** node and copy its **Production URL**.
2. Open that URL in a browser — it is a form titled "Upload your data to test RAG".
3. Upload your school documents (`.pdf` or `.csv`) and submit.
4. Watch the n8n executions panel: you should see the upload flow run through the
   data loader and into the vector store.

Only after this will the agent be able to answer school-specific questions.

> Because the store is in memory, **restarting n8n empties it** and you must
> re-upload. See *Known gaps*.

**f. Sanity-check it.** With the workflow active, in a new terminal:

```powershell
curl.exe -X POST "https://your-n8n-host/webhook/76d40ee1-..." `
  -H "Content-Type: application/json" `
  -d "{\"text\":\"Where is the library?\",\"sessionId\":\"test-1\"}"
```

You should get `{"text":"..."}` back. If you get an error instead, check that the
workflow is active and that the credentials are saved.

### 8. Re-record the greeting clips (optional)

The repository already contains six wake clips and six acknowledgement clips. You
only need this if you change the ElevenLabs voice:

```powershell
uv run python generate_audio.py          # both sets
uv run python generate_audio.py wake     # wake clips only
```

### 9. Run it

```powershell
uv run python main.py
```

Or with an already-provisioned virtualenv:

```powershell
.venv\Scripts\python.exe main.py
```

When the ESP32 is connected you should see the link come up and the face animate.
`/status` is the fastest way to confirm.

### 10. Verify

| Check | How |
| --- | --- |
| Microphone and speakers work | `/listen`, then say something |
| n8n round trip | type a question at the console instead of a slash command |
| ESP32 link | `/status` should say `link=LIVE`, not `TEST MODE` |
| Face animation | `/face THINKING`, then `/face IDLE` |

## Running

Start it with `uv run python main.py` (see *Setup* step 9).

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

## The n8n workflow

The controller talks to an n8n webhook. The bundled workflow is a RAG agent:
`Webhook → AI Agent (RAG + memory) → Set field → Respond to Webhook`.

The contract the controller depends on:

| Direction | Shape |
| --- | --- |
| robot → n8n | `{"source": "robot", "type": "query", "text": "...", "sessionId": "robot-..."}` |
| n8n → robot | `{"text": "..."}` |

The workflow reads only `body.text` (the question) and `body.sessionId` (the
conversation memory key). `source` and `type` are sent for readability and
ignored by the workflow.

**`sessionId` is per visitor, not per process.** The controller rotates it at the
start of every touch interaction, so each visitor gets a fresh conversation and
the next one does not inherit the previous visitor's context. Follow-up
questions inside the same interaction keep the same session.

The workflow export lives in the repository as `Talking Robot - HAPS RAG.json`;
*Setup* step 7 walks through importing and configuring it.

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
Talking Robot - HAPS RAG.json   n8n workflow (see Setup step 7)
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
- The workflow's vector store is n8n's **in-memory** store (`school-robot`). It
  is not persisted, so restarting n8n empties the knowledge base and the school
  documents have to be re-uploaded through the workflow's upload form before the
  robot can answer anything school-specific. Move it to a persistent store (Qdrant
  or pgvector) if this is going anywhere near a real deployment.
- The AI Agent node has **two** chat models wired into its single language-model
  input (OpenRouter on port 0, OpenAI on port 1). Only one will actually be used
  and which is undefined — disconnect the one you do not want.
- The controller allows 30 s for a webhook call. A cold RAG agent can exceed
  that; raise `ROBOT_HTTP_TIMEOUT_SEC` if you see the fallback line fire on slow
  questions.
- No CI, and the tests do not cover the firmware.

## Hardware cautions

- Four servos need an external 5 V supply. Brownouts mid-flap reset the board and
  flood the controller with `Robot Ready!`.
- GPIO 33 has no internal pull-up. If the touch sensor is unplugged the pin floats
  and the ESP32 reports touches continuously.
- The controller holds the serial port while running. Close the Arduino monitor.
- `ESP32Servo` uses the ESP32's LEDC peripherals; adding a fifth servo on these
  pins is not a software change.