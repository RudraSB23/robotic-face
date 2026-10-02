#include <ESP32Servo.h>

Servo mouthServo;
Servo eyeVerServo;
Servo eyeHorServo;
Servo neckServo;

#define MOUTH_PIN 12
#define EYE_VER_PIN 27
#define EYE_HOR_PIN 14
#define NECK_PIN 25
#define TOUCH_PIN 33

const int MOUTH_MIN = 40;
const int MOUTH_CLOSED = 40;
const int MOUTH_SLIGHT = 54;
const int MOUTH_HALF = 70;
const int MOUTH_MAX = 110;
const int MOUTH_FLAP_MIN = 65;
const int MOUTH_FLAP_MAX = 100;

const int EYE_H_LEFT = 65;
const int EYE_H_CENTER = 90;
const int EYE_H_RIGHT = 140;

const int EYE_V_UP = 62;
const int EYE_V_MID = 90;
const int EYE_V_SOFT = 97;
const int EYE_V_DOWN = 109;

const int NECK_MIN = 0;
const int NECK_MAX = 180;
const int NECK_CENTER = 75;
const int NECK_TILT = 10;
const int NECK_NOD = 12;

const int CH_MOUTH = 1;
const int CH_EYE_HOR = 2;
const int CH_EYE_VER = 4;
const int CH_NECK = 8;

const float MOUTH_SPEED = 700.0;
const float EYE_VER_SPEED = 260.0;
const float EYE_HOR_SPEED = 420.0;
const float NECK_SPEED = 150.0;

const unsigned long STEP_MAX_MS = 100;
const unsigned long MANUAL_HOLD_MS = 4000;
const unsigned long WATCHDOG_MS = 30000;
const unsigned long TOUCH_COOLDOWN_MS = 1000;
const unsigned long NOTICING_MS = 420;
const unsigned long FINISHING_MS = 1100;
const unsigned long FLAP_MIN_MS = 300;
const unsigned long FLAP_SUSTAIN_MS = 620;
const unsigned long BLINK_MIN_MS = 80;
const unsigned long BLINK_MAX_MS = 140;

enum RobotState {
  ST_IDLE,
  ST_NOTICING,
  ST_GREETING,
  ST_LISTENING,
  ST_THINKING,
  ST_SPEAKING,
  ST_FINISHING
};

struct Axis {
  Servo *servo;
  float position;
  float target;
  float speed;
  int minimum;
  int maximum;
  int sent;
};

Axis mouthAxis = {nullptr, MOUTH_CLOSED, MOUTH_CLOSED, MOUTH_SPEED, MOUTH_MIN, MOUTH_MAX, -1};
Axis eyeVerAxis = {nullptr, EYE_V_DOWN, EYE_V_DOWN, EYE_VER_SPEED, EYE_V_UP, EYE_V_DOWN, -1};
Axis eyeHorAxis = {nullptr, EYE_H_CENTER, EYE_H_CENTER, EYE_HOR_SPEED, EYE_H_LEFT, EYE_H_RIGHT, -1};
Axis neckAxis = {nullptr, NECK_CENTER, NECK_CENTER, NECK_SPEED, NECK_MIN, NECK_MAX, -1};

RobotState state = ST_IDLE;
unsigned long stateSince = 0;
unsigned long lastCommandAt = 0;
unsigned long lastStepAt = 0;

int animMouth = MOUTH_CLOSED;
int animEyeHor = EYE_H_CENTER;
int animEyeVer = EYE_V_DOWN;
int animNeck = NECK_CENTER;

int manualValue[4] = {MOUTH_CLOSED, EYE_V_DOWN, EYE_H_CENTER, NECK_CENTER};
int manualMask = 0;
unsigned long manualUntil = 0;

bool flapActive = false;
bool flapOpen = false;
int flapOpenAngle = MOUTH_HALF;
unsigned long flapEnd = 0;
unsigned long nextFlapStep = 0;

bool blinkActive = false;
unsigned long blinkEnd = 0;
unsigned long nextBlinkAt = 0;

unsigned long nextSwayAt = 0;
int swaySign = 1;
int sway = 0;

unsigned long glanceEnd = 0;
int glanceHor = EYE_H_CENTER;
int glanceVer = EYE_V_MID;
unsigned long nextGlanceAt = 0;

unsigned long nextStepAt = 0;
int animStep = 0;

bool touchState = false;
unsigned long touchCooldownEnd = 0;

String serialBuffer;

int clampInt(int value, int low, int high) {
  if (value < low) return low;
  if (value > high) return high;
  return value;
}

bool glanceActive(unsigned long now) {
  return glanceEnd != 0 && (long)(now - glanceEnd) < 0;
}

void setTarget(Axis &axis, float value) {
  if (value < (float)axis.minimum) value = (float)axis.minimum;
  if (value > (float)axis.maximum) value = (float)axis.maximum;
  axis.target = value;
}

void stepAxis(Axis &axis, unsigned long deltaMs) {
  float step = axis.speed * ((float)deltaMs / 1000.0);
  float difference = axis.target - axis.position;

  if (difference > step) {
    axis.position += step;
  } else if (difference < -step) {
    axis.position -= step;
  } else {
    axis.position = axis.target;
  }

  int value = (int)(axis.position + 0.5f);

  if (value != axis.sent) {
    axis.servo->write(value);
    axis.sent = value;
  }
}

void applyTargets() {
  int mouth = flapActive
                  ? (flapOpen ? flapOpenAngle : MOUTH_CLOSED)
                  : animMouth;

  int eyeVer = animEyeVer;
  int eyeHor = animEyeHor;
  int neck = animNeck;

  if (blinkActive) eyeVer = EYE_V_DOWN;

  if (manualMask & CH_MOUTH) mouth = manualValue[0];
  if (manualMask & CH_EYE_VER) eyeVer = manualValue[1];
  if (manualMask & CH_EYE_HOR) eyeHor = manualValue[2];
  if (manualMask & CH_NECK) neck = manualValue[3];

  setTarget(mouthAxis, (float)mouth);
  setTarget(eyeVerAxis, (float)eyeVer);
  setTarget(eyeHorAxis, (float)eyeHor);
  setTarget(neckAxis, (float)neck);
}

void startFlap(unsigned long durationMs) {
  if (durationMs < FLAP_MIN_MS) durationMs = FLAP_MIN_MS;

  flapActive = true;
  flapOpen = false;
  flapOpenAngle = random(MOUTH_FLAP_MIN, MOUTH_FLAP_MAX + 1);
  flapEnd = millis() + durationMs;
  nextFlapStep = millis();
}

void updateFlap() {
  if (!flapActive) return;

  unsigned long now = millis();

  if ((long)(now - flapEnd) >= 0) {
    flapActive = false;
    flapOpen = false;
    return;
  }

  if ((long)(now - nextFlapStep) < 0) return;

  flapOpen = !flapOpen;

  if (flapOpen) {
    flapOpenAngle = random(MOUTH_FLAP_MIN, MOUTH_FLAP_MAX + 1);
    nextFlapStep = now + random(170, 310);
  } else {
    nextFlapStep = now + random(130, 280);
  }
}

void cancelFlap() {
  flapActive = false;
  flapOpen = false;
}

void startBlink() {
  blinkActive = true;
  blinkEnd = millis() + random(BLINK_MIN_MS, BLINK_MAX_MS + 1);
}

void updateBlink() {
  if (!blinkActive) return;

  if ((long)(millis() - blinkEnd) >= 0) blinkActive = false;
}

void blinkDue(unsigned long now, unsigned long lowMs, unsigned long highMs) {
  if ((long)(now - nextBlinkAt) < 0) return;

  nextBlinkAt = now + random(lowMs, highMs + 1);

  if (state == ST_NOTICING || state == ST_FINISHING) return;

  startBlink();
}

void updateSway(unsigned long now) {
  if ((long)(now - nextSwayAt) < 0) return;

  swaySign = -swaySign;
  sway = swaySign * random(1, 5);
  nextSwayAt = now + random(700, 1800);
}

void updateGlance(unsigned long now) {
  if (glanceActive(now)) return;

  glanceEnd = 0;

  if ((long)(now - nextGlanceAt) < 0) return;

  nextGlanceAt = now + random(2600, 6400);

  if (random(0, 10) < 4) return;

  int side = random(0, 2) ? 1 : -1;

  glanceHor = EYE_H_CENTER + side * random(10, 31);
  glanceVer = random(0, 10) < 6 ? EYE_V_MID : EYE_V_MID + random(-5, 7);
  glanceEnd = now + random(300, 950);
}

void setState(RobotState next) {
  if (next == state) return;

  state = next;
  stateSince = millis();
  animStep = 0;
  nextStepAt = millis();
  nextGlanceAt = millis() + random(2000, 5200);
  glanceEnd = 0;
  nextSwayAt = millis() + random(600, 1400);

  if (next != ST_SPEAKING && next != ST_GREETING) cancelFlap();
}

void updateIdle(unsigned long now) {
  updateSway(now);
  updateGlance(now);
  blinkDue(now, 2600, 6400);

  animMouth = MOUTH_CLOSED;

  if (glanceActive(now)) {
    animEyeHor = glanceHor;
    animEyeVer = glanceVer;
  } else {
    animEyeHor = EYE_H_CENTER;
    animEyeVer = EYE_V_DOWN - sway;
  }

  animNeck = NECK_CENTER + sway;
}

void updateNoticing(unsigned long now) {
  animMouth = MOUTH_CLOSED;
  animEyeHor = EYE_H_CENTER;
  animEyeVer = EYE_V_UP;
  animNeck = NECK_CENTER + NECK_TILT / 2;

  if ((long)(now - stateSince) >= (long)NOTICING_MS) setState(ST_GREETING);
}

void updateGreeting(unsigned long now) {
  updateSway(now);
  updateGlance(now);
  blinkDue(now, 3200, 6000);

  if (!flapActive) startFlap(FLAP_SUSTAIN_MS);

  animMouth = MOUTH_CLOSED;

  if (glanceActive(now)) {
    animEyeHor = glanceHor;
    animEyeVer = glanceVer;
  } else {
    animEyeHor = EYE_H_CENTER + sway;
    animEyeVer = EYE_V_MID - sway;
  }

  animNeck = NECK_CENTER + NECK_TILT / 2 + sway / 2;
}

void updateListening(unsigned long now) {
  updateSway(now);
  updateGlance(now);
  blinkDue(now, 2800, 6000);

  if ((long)(now - nextStepAt) >= 0) {
    if (animStep == 0) {
      animMouth = MOUTH_CLOSED;
      nextStepAt = now + random(2400, 5400);
      animStep = 1;
    } else {
      animMouth = MOUTH_HALF;
      nextStepAt = now + 240;
      animStep = 0;
    }
  }

  if (glanceActive(now)) {
    animEyeHor = glanceHor;
    animEyeVer = glanceVer;
  } else {
    animEyeHor = EYE_H_CENTER + sway / 2;
    animEyeVer = EYE_V_MID - sway;
  }

  animNeck = NECK_CENTER + sway;
}

void updateThinking(unsigned long now) {
  updateSway(now);
  updateGlance(now);
  blinkDue(now, 3400, 7000);

  if ((long)(now - nextStepAt) >= 0) {
    if (animStep == 0) {
      animMouth = MOUTH_SLIGHT;
      nextStepAt = now + 320;
      animStep = 1;
    } else {
      animMouth = MOUTH_CLOSED;
      nextStepAt = now + random(1800, 3800);
      animStep = 0;
    }
  }

  if (glanceActive(now)) {
    animEyeHor = glanceHor;
    animEyeVer = glanceVer;
  } else {
    animEyeHor = EYE_H_CENTER - 10 + sway * 2;
    animEyeVer = EYE_V_UP + 6 - sway;
  }

  animNeck = NECK_CENTER - 4 + sway;
}

void updateSpeaking(unsigned long now) {
  updateSway(now);
  updateGlance(now);
  blinkDue(now, 3000, 6200);

  if (!flapActive) startFlap(FLAP_SUSTAIN_MS);

  animMouth = MOUTH_CLOSED;

  if (glanceActive(now)) {
    animEyeHor = glanceHor;
    animEyeVer = glanceVer;
  } else {
    animEyeHor = EYE_H_CENTER + sway;
    animEyeVer = EYE_V_MID - sway;
  }

  animNeck = NECK_CENTER + sway;
}

void updateFinishing(unsigned long now) {
  long elapsed = (long)(now - stateSince);

  animEyeHor = EYE_H_CENTER;
  animEyeVer = EYE_V_SOFT;

  if (elapsed < 240) {
    animNeck = NECK_CENTER + NECK_NOD;
    animMouth = MOUTH_CLOSED;
  } else if (elapsed < 640) {
    animNeck = NECK_CENTER - NECK_NOD / 2;
    animMouth = MOUTH_SLIGHT;
  } else if (elapsed < FINISHING_MS) {
    animNeck = NECK_CENTER;
    animMouth = MOUTH_CLOSED;
  } else {
    setState(ST_IDLE);
  }
}

void updateState(unsigned long now) {
  switch (state) {
    case ST_NOTICING:
      updateNoticing(now);
      break;
    case ST_GREETING:
      updateGreeting(now);
      break;
    case ST_LISTENING:
      updateListening(now);
      break;
    case ST_THINKING:
      updateThinking(now);
      break;
    case ST_SPEAKING:
      updateSpeaking(now);
      break;
    case ST_FINISHING:
      updateFinishing(now);
      break;
    default:
      updateIdle(now);
      break;
  }
}

void updateManual() {
  if (!manualMask) return;

  if ((long)(millis() - manualUntil) >= 0) {
    manualMask = 0;
    Serial.println("OK:MANUAL-END");
  }
}

void clearManual() {
  manualMask = 0;
}

void applyManual(int channel, int value) {
  manualMask |= channel;

  if (channel == CH_MOUTH) manualValue[0] = value;
  if (channel == CH_EYE_VER) manualValue[1] = value;
  if (channel == CH_EYE_HOR) manualValue[2] = value;
  if (channel == CH_NECK) manualValue[3] = value;

  manualUntil = millis() + MANUAL_HOLD_MS;
}

void centerRobot() {
  clearManual();
  cancelFlap();
  glanceEnd = 0;
  animEyeHor = EYE_H_CENTER;
  animEyeVer = EYE_V_DOWN;
  animNeck = NECK_CENTER;
  animMouth = MOUTH_CLOSED;
}

bool parseState(const String &name, RobotState &out) {
  if (name == "IDLE") {
    out = ST_IDLE;
    return true;
  }

  if (name == "NOTICING") {
    out = ST_NOTICING;
    return true;
  }

  if (name == "GREETING") {
    out = ST_GREETING;
    return true;
  }

  if (name == "LISTENING") {
    out = ST_LISTENING;
    return true;
  }

  if (name == "THINKING") {
    out = ST_THINKING;
    return true;
  }

  if (name == "SPEAKING") {
    out = ST_SPEAKING;
    return true;
  }

  if (name == "FINISHING") {
    out = ST_FINISHING;
    return true;
  }

  return false;
}

const char *stateName(RobotState value) {
  switch (value) {
    case ST_NOTICING:
      return "NOTICING";
    case ST_GREETING:
      return "GREETING";
    case ST_LISTENING:
      return "LISTENING";
    case ST_THINKING:
      return "THINKING";
    case ST_SPEAKING:
      return "SPEAKING";
    case ST_FINISHING:
      return "FINISHING";
    default:
      return "IDLE";
  }
}

void handleCommand(String command) {
  command.trim();

  if (!command.length()) return;

  lastCommandAt = millis();

  if (command.startsWith("STATE:")) {
    RobotState next = ST_IDLE;

    if (!parseState(command.substring(6), next)) {
      Serial.println("ERR:STATE");
      return;
    }

    setState(next);

    Serial.print("OK:STATE:");
    Serial.println(stateName(state));
    return;
  }

  if (command == "STOP") {
    cancelFlap();
    clearManual();

    Serial.println("OK:STOP");
    return;
  }

  if (command.startsWith("FLAP:")) {
    startFlap(command.substring(5).toInt());

    Serial.println("OK:FLAP");
    return;
  }

  if (command.startsWith("MOUTH:")) {
    applyManual(CH_MOUTH, clampInt(command.substring(6).toInt(), MOUTH_MIN, MOUTH_MAX));

    Serial.println("OK:MOUTH");
    return;
  }

  if (command.startsWith("EYES:")) {
    int comma = command.indexOf(',', 5);

    if (comma < 0) {
      Serial.println("ERR:EYES");
      return;
    }

    int horizontal = clampInt(command.substring(5, comma).toInt(), EYE_H_LEFT, EYE_H_RIGHT);
    int vertical = clampInt(command.substring(comma + 1).toInt(), EYE_V_UP, EYE_V_DOWN);

    applyManual(CH_EYE_HOR, horizontal);
    applyManual(CH_EYE_VER, vertical);

    Serial.println("OK:EYES");
    return;
  }

  if (command.startsWith("NECK:")) {
    applyManual(CH_NECK, clampInt(command.substring(5).toInt(), NECK_MIN, NECK_MAX));

    Serial.println("OK:NECK");
    return;
  }

  if (command.startsWith("LISTENING:")) {
    setState(command.substring(10).toInt() ? ST_LISTENING : ST_IDLE);

    Serial.println("OK:LISTENING");
    return;
  }

  if (command == "CENTER") {
    centerRobot();
    setState(ST_IDLE);

    Serial.println("OK:CENTER");
    return;
  }

  if (command == "BLINK") {
    startBlink();

    Serial.println("OK:BLINK");
    return;
  }

  if (command == "PING") {
    Serial.println("OK:PONG");
    return;
  }

  Serial.println("ERR:UNKNOWN");
}

void readSerial() {
  while (Serial.available()) {
    char c = (char)Serial.read();

    if (c == '\n' || c == '\r') {
      if (serialBuffer.length()) {
        handleCommand(serialBuffer);
        serialBuffer = "";
      }
    } else if (serialBuffer.length() < 100) {
      serialBuffer += c;
    } else {
      serialBuffer = "";
      Serial.println("ERR:BUFFER");
    }
  }
}

void serviceTouch() {
  bool touched = digitalRead(TOUCH_PIN) == HIGH;

  if (touched && !touchState && (long)(millis() - touchCooldownEnd) >= 0) {
    touchState = true;
    touchCooldownEnd = millis() + TOUCH_COOLDOWN_MS;

    if (state == ST_IDLE) setState(ST_NOTICING);

    Serial.println("TOUCH");
  }

  if (!touched) touchState = false;
}

void serviceWatchdog() {
  if (state == ST_IDLE) return;

  if ((long)(millis() - lastCommandAt) < (long)WATCHDOG_MS) return;

  Serial.println("WARN:WATCHDOG");

  centerRobot();
  setState(ST_IDLE);
}

void setup() {
  Serial.begin(115200);

  pinMode(TOUCH_PIN, INPUT);

  randomSeed(analogRead(34));

  mouthServo.attach(MOUTH_PIN);
  eyeVerServo.attach(EYE_VER_PIN);
  eyeHorServo.attach(EYE_HOR_PIN);
  neckServo.attach(NECK_PIN);

  mouthAxis.servo = &mouthServo;
  eyeVerAxis.servo = &eyeVerServo;
  eyeHorAxis.servo = &eyeHorServo;
  neckAxis.servo = &neckServo;

  mouthServo.write(MOUTH_CLOSED);
  eyeVerServo.write(EYE_V_DOWN);
  eyeHorServo.write(EYE_H_CENTER);
  neckServo.write(NECK_CENTER);

  mouthAxis.position = MOUTH_CLOSED;
  mouthAxis.sent = MOUTH_CLOSED;
  eyeVerAxis.position = EYE_V_DOWN;
  eyeVerAxis.sent = EYE_V_DOWN;
  eyeHorAxis.position = EYE_H_CENTER;
  eyeHorAxis.sent = EYE_H_CENTER;
  neckAxis.position = NECK_CENTER;
  neckAxis.sent = NECK_CENTER;

  unsigned long startedAt = millis();

  nextBlinkAt = startedAt + random(1800, 4200);
  nextGlanceAt = startedAt + random(1500, 4000);
  nextSwayAt = startedAt + random(800, 1600);
  stateSince = startedAt;
  lastStepAt = startedAt;
  lastCommandAt = startedAt;

  delay(1000);

  Serial.println("Robot Ready!");
}

void loop() {
  readSerial();

  serviceWatchdog();
  updateManual();
  updateBlink();

  unsigned long now = millis();

  updateFlap();
  updateState(now);
  applyTargets();

  unsigned long delta = now - lastStepAt;
  if (delta > STEP_MAX_MS) delta = STEP_MAX_MS;
  lastStepAt = now;

  stepAxis(mouthAxis, delta);
  stepAxis(eyeVerAxis, delta);
  stepAxis(eyeHorAxis, delta);
  stepAxis(neckAxis, delta);

  serviceTouch();
}
