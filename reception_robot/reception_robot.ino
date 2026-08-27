#include <ESP32Servo.h>

Servo mouthServo;
Servo eyeVerServo;
Servo eyeHorServo;
Servo neckServo;

#define MOUTH 12
#define EYEV 27
#define EYEH 14
#define NECK 25
#define TOUCH_PIN 33

const int mouthClose = 40;
const int mouthOpen = 110;

const int eyeLeft = 65;
const int eyeCenter = 90;
const int eyeRight = 140;

const int eyeUp = 62;
const int eyeMiddle = 90;
const int eyeDown = 109;

const int neckCenter = 75;

String serialBuffer;

bool flapActive = false;
unsigned long flapEnd = 0;
unsigned long nextFlapStep = 0;
bool flapOpen = false;

bool listeningActive = false;
int listeningStep = 0;
unsigned long nextListeningStep = 0;

bool blinkActive = false;
unsigned long blinkEnd = 0;

bool touchState = false;
unsigned long touchCooldownEnd = 0;

void startFlap(unsigned long durationMs) {
  if (durationMs < 300) durationMs = 300;

  flapActive = true;
  flapEnd = millis() + durationMs;
  nextFlapStep = millis();
  flapOpen = false;

  mouthServo.write(mouthClose);
}

void updateFlap() {
  if (!flapActive) return;

  unsigned long now = millis();

  if ((long)(now - flapEnd) >= 0) {
    flapActive = false;
    flapOpen = false;
    mouthServo.write(mouthClose);
    return;
  }

  if ((long)(now - nextFlapStep) < 0) return;

  flapOpen = !flapOpen;

  if (flapOpen) {
    int opening = random(65, 101);
    mouthServo.write(opening);

    nextFlapStep = now + random(180, 320);
  } else {
    mouthServo.write(mouthClose);

    nextFlapStep = now + random(140, 300);
  }
}

void startListening() {
  listeningActive = true;
  listeningStep = 0;
  nextListeningStep = millis();

  blinkActive = false;
  flapActive = false;

  eyeHorServo.write(eyeCenter);
  eyeVerServo.write(eyeDown);
  mouthServo.write(mouthClose);
}

void stopListening() {
  listeningActive = false;
  listeningStep = 0;

  blinkActive = false;
  flapActive = false;

  mouthServo.write(mouthClose);
  eyeHorServo.write(eyeCenter);
  eyeVerServo.write(eyeDown);
  neckServo.write(neckCenter);
}

void updateListening() {
  if (!listeningActive) return;

  unsigned long now = millis();

  if ((long)(now - nextListeningStep) < 0) return;

  switch (listeningStep) {
    case 0:
      eyeVerServo.write(eyeUp);
      nextListeningStep = now + 450;
      listeningStep = 1;
      break;

    case 1:
      eyeHorServo.write(eyeLeft);
      nextListeningStep = now + 400;
      listeningStep = 2;
      break;

    case 2:
      eyeHorServo.write(eyeRight);
      nextListeningStep = now + 500;
      listeningStep = 3;
      break;

    case 3:
      eyeHorServo.write(eyeCenter);
      nextListeningStep = now + 350;
      listeningStep = 4;
      break;

    case 4:
      mouthServo.write(75);
      nextListeningStep = now + 300;
      listeningStep = 5;
      break;

    case 5:
      mouthServo.write(mouthClose);
      nextListeningStep = now + 600;
      listeningStep = 6;
      break;

    case 6: {
      int movement = random(0, 6);

      switch (movement) {
        case 0:
          eyeHorServo.write(random(78, 86));
          break;

        case 1:
          eyeHorServo.write(random(94, 103));
          break;

        case 2:
          eyeVerServo.write(random(82, 88));
          break;

        case 3:
          eyeVerServo.write(random(92, 98));
          break;

        case 4:
          eyeHorServo.write(eyeCenter);
          break;

        case 5:
          eyeVerServo.write(eyeMiddle);
          break;
      }

      nextListeningStep = now + random(700, 1800);
      break;
    }
  }
}

void startBlink() {
  blinkActive = true;
  blinkEnd = millis() + 100;

  eyeVerServo.write(eyeDown);
}

void updateBlink() {
  if (!blinkActive) return;

  if ((long)(millis() - blinkEnd) >= 0) {
    blinkActive = false;
    eyeVerServo.write(
      listeningActive ? eyeMiddle : eyeDown
    );
  }
}

void centerRobot() {
  flapActive = false;
  blinkActive = false;
  listeningActive = false;
  listeningStep = 0;

  mouthServo.write(mouthClose);
  eyeHorServo.write(eyeCenter);
  eyeVerServo.write(eyeDown);
  neckServo.write(neckCenter);
}

void handleCommand(String command) {
  command.trim();

  if (!command.length()) return;

  if (command.startsWith("FLAP:")) {
    unsigned long duration = command.substring(5).toInt();

    startFlap(duration);

    Serial.println("OK:FLAP");
    return;
  }

  if (command.startsWith("MOUTH:")) {
    int angle = command.substring(6).toInt();

    angle = constrain(
      angle,
      mouthClose,
      mouthOpen
    );

    flapActive = false;
    listeningActive = false;

    mouthServo.write(angle);

    Serial.println("OK:MOUTH");
    return;
  }

  if (command.startsWith("EYES:")) {
    int comma = command.indexOf(',', 5);

    if (comma < 0) {
      Serial.println("ERR:EYES");
      return;
    }

    int horizontal =
      command.substring(5, comma).toInt();

    int vertical =
      command.substring(comma + 1).toInt();

    horizontal = constrain(
      horizontal,
      eyeLeft,
      eyeRight
    );

    vertical = constrain(
      vertical,
      eyeUp,
      eyeDown
    );

    eyeHorServo.write(horizontal);
    eyeVerServo.write(vertical);

    Serial.println("OK:EYES");
    return;
  }

  if (command.startsWith("NECK:")) {
    int angle = command.substring(5).toInt();

    neckServo.write(
      constrain(angle, 0, 180)
    );

    Serial.println("OK:NECK");
    return;
  }

  if (command.startsWith("LISTENING:")) {
    int active =
      command.substring(10).toInt();

    if (active) {
      startListening();
    } else {
      stopListening();
    }

    Serial.println("OK:LISTENING");
    return;
  }

  if (command == "CENTER") {
    centerRobot();

    Serial.println("OK:CENTER");
    return;
  }

  if (command == "BLINK") {
    startBlink();

    Serial.println("OK:BLINK");
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

void setup() {
  Serial.begin(115200);

  pinMode(TOUCH_PIN, INPUT);

  randomSeed(analogRead(34));

  mouthServo.attach(MOUTH);
  eyeVerServo.attach(EYEV);
  eyeHorServo.attach(EYEH);
  neckServo.attach(NECK);

  mouthServo.write(mouthClose);
  eyeHorServo.write(eyeCenter);
  eyeVerServo.write(eyeDown);
  neckServo.write(neckCenter);

  delay(1000);

  Serial.println("Robot Ready!");
}

void loop() {
  readSerial();
  updateFlap();
  updateListening();
  updateBlink();

  bool touched = digitalRead(TOUCH_PIN) == HIGH;

  if (
    touched &&
    !touchState &&
    (long)(millis() - touchCooldownEnd) >= 0
  ) {
    touchState = true;
    touchCooldownEnd = millis() + 1000;

    Serial.println("TOUCH");
  }

  if (!touched) {
    touchState = false;
  }
}