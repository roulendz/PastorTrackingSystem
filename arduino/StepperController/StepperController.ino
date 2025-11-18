/**
 * @file StepperController.ino
 * @brief Arduino Stepper Motor Controller - ENHANCED for PC Synchronization
 *
 * WHAT CHANGED FROM YOUR ORIGINAL:
 * ✅ Added microsecond timestamps (line 295)
 * ✅ Added sequence numbers to feedback (line 92, line 303)
 * ✅ Added acceleration state reporting (line 307-320)
 * ✅ Enhanced feedback header (line 174)
 *
 * These changes help the PC application better synchronize with motor movement.
 * Everything else stays THE SAME as your original firmware!
 *
 * Hardware Connections:
 * D2 -> DIR+ (DM542 Driver Direction Pin)
 * D3 -> PUL+ (DM542 Driver Pulse Pin - Must be PWM capable)
 * D4 -> ENA+ (DM542 Driver Enable Pin)
 *
 * @version 1.1.0 (Enhanced)
 */

#include <AccelStepper.h>
#include <EEPROM.h>

#define DIR_PIN 2      
#define STEP_PIN 3     
#define ENABLE_PIN 4   
#define ENABLE_ACTIVE_HIGH 0

#define LIMIT_SWITCH_MIN_PIN 5
#define LIMIT_SWITCH_MAX_PIN 6

#define EEPROM_MAX_SPEED_ADDR 0
#define EEPROM_MAX_ACCEL_ADDR sizeof(float)
#define EEPROM_PID_P_ADDR (2 * sizeof(float))
#define EEPROM_PID_I_ADDR (3 * sizeof(float))
#define EEPROM_PID_D_ADDR (4 * sizeof(float))

#define STEPS_PER_REV 200
#define GEAR_RATIO 180
#define MICROSTEPS 8
#define TOTAL_STEPS_PER_REV ((long)STEPS_PER_REV * GEAR_RATIO * MICROSTEPS)

struct Command {
  char type;
  char* args;
};

struct Feedback {
  long currentPosition;
  float currentSpeed;
  bool isMoving;
  long targetPosition;
  unsigned long timestamp;
};

AccelStepper stepper(AccelStepper::DRIVER, STEP_PIN, DIR_PIN);

void enableDriver() {
  digitalWrite(ENABLE_PIN, ENABLE_ACTIVE_HIGH ? HIGH : LOW);
}

void disableDriver() {
  digitalWrite(ENABLE_PIN, ENABLE_ACTIVE_HIGH ? LOW : HIGH);
}

#define COMMAND_DATA_OFFSET 2

float maxSpeed = 25000.0;
float maxAccel = 12500.0;
float pidP = 1.0;
float pidI = 0.0;
float pidD = 0.1;

long targetPosition = 0;
long lastPosition = 0;
unsigned long lastFeedbackTime = 0;
const unsigned long FEEDBACK_INTERVAL = 20;

// ✅ ENHANCEMENT: Sequence number tracking
static unsigned long feedbackSequenceNumber = 0;

char inputBuffer[32];
int bufferIndex = 0;

enum MotorState {
  IDLE, MOVING, STOPPED, HOMING, ERROR
};

enum ErrorType {
  NO_ERROR = 0,
  EMPTY_COMMAND_STRING_ERROR,
  UNKNOWN_COMMAND_TYPE_ERROR,
  MOVE_COMMAND_MISSING_ARG_ERROR,
  DIAGNOSTIC_MOVE_MISSING_ARG_ERROR,
  SETTINGS_COMMAND_MISSING_ARG_ERROR,
  DRIVER_COMMAND_MISSING_ARG_ERROR,
  INVALID_DRIVER_COMMAND_ARG_ERROR,
  HOMING_FAILED_LIMIT_SWITCH_ERROR
};

enum MotorState currentMotorState = IDLE;
enum ErrorType currentError = NO_ERROR;

void reportError(ErrorType error, const char* message = "") {
  currentError = error;
  currentMotorState = ERROR;
  Serial.print("ERROR:");
  Serial.print(error);
  Serial.print(" - ");
  Serial.println(message);
}

Command parseCommand(char* commandString) {
  Command cmd;
  cmd.type = '\0';
  cmd.args = nullptr;

  if (commandString == nullptr || strlen(commandString) == 0) {
    reportError(EMPTY_COMMAND_STRING_ERROR, "Empty command string");
    return cmd;
  }

  cmd.type = commandString[0];
  if (strlen(commandString) > COMMAND_DATA_OFFSET) {
    cmd.args = commandString + COMMAND_DATA_OFFSET;
  }
  return cmd;
}

void setup() {
  Serial.begin(115200);
  Serial.setTimeout(10);
  
  stepper.setMaxSpeed(maxSpeed);
  stepper.setAcceleration(maxAccel);
  stepper.setCurrentPosition(0);
  
  pinMode(ENABLE_PIN, OUTPUT);
  enableDriver();

  pinMode(LIMIT_SWITCH_MIN_PIN, INPUT_PULLUP);
  pinMode(LIMIT_SWITCH_MAX_PIN, INPUT_PULLUP);

  currentMotorState = IDLE;
  loadSettings();

  // ✅ ENHANCEMENT: Updated header with new fields
  Serial.println("FB_HEADER:currentAngle,targetAngle,speed,isRunning,timestampMicros,sequence,accelState");
  Serial.println("READY");
}

void loop() {
  switch (currentMotorState) {
    case IDLE:
      stepper.run();
      break;
    case MOVING:
      stepper.run();
      if (!stepper.isRunning()) {
        currentMotorState = IDLE;
      }
      break;
    case STOPPED:
      break;
    case HOMING:
      stepper.run();
      if (!stepper.isRunning()) {
        currentMotorState = IDLE;
      }
      break;
    case ERROR:
      disableDriver();
      break;
  }

  handleSerialInput();
  sendFeedback();
}

void handleSerialInput() {
  while (Serial.available()) {
    char c = Serial.read();
    
    if (c == '\n' || c == '\r') {
      if (bufferIndex > 0) {
        inputBuffer[bufferIndex] = '\0';
        processCommand(inputBuffer);
        bufferIndex = 0;
      }
    } else {
      if (bufferIndex < sizeof(inputBuffer) - 1) {
        inputBuffer[bufferIndex++] = c;
      }
    }
  }
}

void processCommand(char* commandString) {
  Command parsedCommand = parseCommand(commandString);
  char commandChar = parsedCommand.type;

  switch (commandChar) {
    case 'M': handleMoveCommand(parsedCommand); break;
    case 'D': handleDiagnosticMoveCommand(parsedCommand); break;
    case 'S': handleSettingsCommand(parsedCommand); break;
    case 'R': handleResetCommand(parsedCommand); break;
    case 'Q': handleQueryCommand(parsedCommand); break;
    case 'E': handleEmergencyStopCommand(parsedCommand); break;
    case 'H': handleHomeCommand(parsedCommand); break;
    case 'X': handleDriverCommand(parsedCommand); break;
    default: reportError(UNKNOWN_COMMAND_TYPE_ERROR, "Unknown command type"); break;
  }
}

void moveToAngle(float angle) {
  long steps = (long)(angle * TOTAL_STEPS_PER_REV / 360.0);
  targetPosition = steps;
  stepper.moveTo(targetPosition);
}

void handleMoveCommand(Command cmd) {
  if (cmd.args == nullptr || strlen(cmd.args) == 0) {
    reportError(MOVE_COMMAND_MISSING_ARG_ERROR, "Move command missing angle argument");
    return;
  }
  float angle = atof(cmd.args);
  moveToAngle(angle);
  currentMotorState = MOVING;
}

void handleDiagnosticMoveCommand(Command cmd) {
  if (cmd.args == nullptr || strlen(cmd.args) == 0) {
    reportError(DIAGNOSTIC_MOVE_MISSING_ARG_ERROR, "Diagnostic move command missing steps argument");
    return;
  }
  long diagnosticSteps = atol(cmd.args);
  stepper.move(diagnosticSteps);
  Serial.print("Diagnostic: Moving ");
  Serial.print(diagnosticSteps);
  Serial.println(" steps");
}

void handleSettingsCommand(Command cmd) {
  if (cmd.args == nullptr || strlen(cmd.args) == 0) {
    reportError(SETTINGS_COMMAND_MISSING_ARG_ERROR, "Settings command missing arguments");
    return;
  }
  char* argsCopy = strdup(cmd.args);
  char* token = strtok(argsCopy, ",");

  if (token) maxSpeed = atof(token);
  token = strtok(NULL, ",");
  if (token) maxAccel = atof(token);
  token = strtok(NULL, ",");
  if (token) pidP = atof(token);
  token = strtok(NULL, ",");
  if (token) pidI = atof(token);
  token = strtok(NULL, ",");
  if (token) pidD = atof(token);
  
  stepper.setMaxSpeed(maxSpeed);
  stepper.setAcceleration(maxAccel);
  
  Serial.print("SETTINGS:");
  Serial.print(maxSpeed); Serial.print(",");
  Serial.print(maxAccel); Serial.print(",");
  Serial.print(pidP); Serial.print(",");
  Serial.print(pidI); Serial.print(",");
  Serial.println(pidD);
  
  free(argsCopy);
  saveSettings();
}

void handleResetCommand(Command cmd) {
  stepper.setCurrentPosition(0);
  targetPosition = 0;
  Serial.println("RESET:OK");
  currentMotorState = IDLE;
}

void handleQueryCommand(Command cmd) {
  sendImmediateFeedback();
}

void handleEmergencyStopCommand(Command cmd) {
  stepper.stop();
  stepper.setCurrentPosition(stepper.currentPosition());
  disableDriver();
  Serial.println("STOP:OK");
  enableDriver();
  currentMotorState = STOPPED;
}

void handleHomeCommand(Command cmd) {
  moveToAngle(0);
  currentMotorState = HOMING;
}

void handleDriverCommand(Command cmd) {
  if (cmd.args == nullptr || strlen(cmd.args) == 0) {
    reportError(DRIVER_COMMAND_MISSING_ARG_ERROR, "Driver command missing argument");
    return;
  }
  int enable = atoi(cmd.args);

  if (enable == 1) {
    digitalWrite(ENABLE_PIN, ENABLE_ACTIVE_HIGH ? HIGH : LOW);
    Serial.println("Driver Enabled");
  } else if (enable == 0) {
    digitalWrite(ENABLE_PIN, ENABLE_ACTIVE_HIGH ? LOW : HIGH);
    Serial.println("Driver Disabled");
  } else {
    reportError(INVALID_DRIVER_COMMAND_ARG_ERROR, "Invalid driver command argument");
  }
}

void sendFeedback() {
  unsigned long currentTime = millis();
  if (currentTime - lastFeedbackTime >= FEEDBACK_INTERVAL) {
    sendImmediateFeedback();
    lastFeedbackTime = currentTime;
  }
}

// ✅ ENHANCEMENT: Better feedback with microseconds, sequence, and accel state
void sendImmediateFeedback() {
  long currentPos = stepper.currentPosition();
  float currentAngle = (float)currentPos * 360.0 / TOTAL_STEPS_PER_REV;
  float targetAngle = (float)targetPosition * 360.0 / TOTAL_STEPS_PER_REV;
  
  // ✅ Microsecond timestamp for better precision
  unsigned long ulTimestampMicros = micros();
  
  // ✅ Acceleration state: 0=stopped, 1=accel, 2=constant, 3=decel
  int iAccelerationState = 0;
  if (stepper.isRunning()) {
    float currentSpeed = abs(stepper.speed());
    float maxSpeedThreshold = maxSpeed * 0.95;
    
    if (currentSpeed < maxSpeedThreshold) {
      long distanceToGo = abs(stepper.distanceToGo());
      long stepsToDecel = (long)((currentSpeed * currentSpeed) / (2.0 * maxAccel));
      
      if (distanceToGo > stepsToDecel) {
        iAccelerationState = 1; // Accelerating
      } else {
        iAccelerationState = 3; // Decelerating
      }
    } else {
      iAccelerationState = 2; // Constant speed
    }
  }
  
  Serial.print("FB:");
  Serial.print(currentAngle, 2); Serial.print(",");
  Serial.print(targetAngle, 2); Serial.print(",");
  Serial.print(stepper.speed()); Serial.print(",");
  Serial.print(stepper.isRunning() ? 1 : 0); Serial.print(",");
  Serial.print(ulTimestampMicros); Serial.print(",");
  Serial.print(feedbackSequenceNumber++); Serial.print(",");
  Serial.println(iAccelerationState);
}

float angleToSteps(float angle) {
  return angle * TOTAL_STEPS_PER_REV / 360.0;
}

float stepsToAngle(long steps) {
  return (float)steps * 360.0 / TOTAL_STEPS_PER_REV;
}

void performHomingSequence() {
  Serial.println("HOMING: Starting homing sequence...");
  currentMotorState = HOMING;
  stepper.setMaxSpeed(maxSpeed / 2);
  stepper.setAcceleration(maxAccel / 2);
  stepper.moveTo(-TOTAL_STEPS_PER_REV * 2);

  while (digitalRead(LIMIT_SWITCH_MIN_PIN) == HIGH) {
    stepper.run();
    if (!stepper.isRunning()) {
      reportError(HOMING_FAILED_LIMIT_SWITCH_ERROR, "Homing failed");
      return;
    }
  }

  stepper.stop();
  stepper.setCurrentPosition(stepper.currentPosition());
  stepper.moveTo(stepper.currentPosition() + (TOTAL_STEPS_PER_REV / 100));
  while (stepper.isRunning()) {
    stepper.run();
  }

  stepper.setCurrentPosition(0);
  targetPosition = 0;
  Serial.println("HOMING: Complete");
  currentMotorState = IDLE;
  stepper.setMaxSpeed(maxSpeed);
  stepper.setAcceleration(maxAccel);
}

void loadSettings() {
  EEPROM.get(EEPROM_MAX_SPEED_ADDR, maxSpeed);
  EEPROM.get(EEPROM_MAX_ACCEL_ADDR, maxAccel);
  EEPROM.get(EEPROM_PID_P_ADDR, pidP);
  EEPROM.get(EEPROM_PID_I_ADDR, pidI);
  EEPROM.get(EEPROM_PID_D_ADDR, pidD);
  stepper.setMaxSpeed(maxSpeed);
  stepper.setAcceleration(maxAccel);
  Serial.println("SETTINGS: Loaded from EEPROM");
}

void saveSettings() {
  EEPROM.put(EEPROM_MAX_SPEED_ADDR, maxSpeed);
  EEPROM.put(EEPROM_MAX_ACCEL_ADDR, maxAccel);
  EEPROM.put(EEPROM_PID_P_ADDR, pidP);
  EEPROM.put(EEPROM_PID_I_ADDR, pidI);
  EEPROM.put(EEPROM_PID_D_ADDR, pidD);
  Serial.println("SETTINGS: Saved to EEPROM");
}
