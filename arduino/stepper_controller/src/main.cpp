// Pastor Tracking — Stepper Controller Firmware (v2).
//
// Hardware: Arduino Uno R3, DM542 stepper driver, 200-step motor with 180:1
// gearbox, 8 microsteps. See include/protocol.h for pin map and constants.
//
// Wire protocol (v2): newline-terminated ASCII at 115200 baud.
//   Boot:        READY:v2
//   Command set: M:<deg>  S:<spd,acc,p,i,d>  L:<min,max>  R  Q  E  H  X:<0|1>
//   Feedback:    FB:<curAng>,<tgtAng>,<speed>,<isRun>,<microsTs>,<seq>,<accelState>
//   Errors:      ERROR:<code> - <message>
//
// Tier 1 safety: heartbeat watchdog, software angle limits, settings clamp.
// Tier 2 robustness: static buffers, enum class, cstdint, version handshake.

#include <AccelStepper.h>
#include <Arduino.h>
#include <EEPROM.h>
#include <avr/wdt.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "../include/protocol.h"

using namespace stepper_protocol;

namespace {

AccelStepper g_stepper(AccelStepper::DRIVER, PIN_STEP, PIN_DIRECTION);

float g_max_speed_steps_per_sec = 25000.0f;
float g_max_accel_steps_per_sec2 = 12500.0f;
float g_pid_p = 1.0f;
float g_pid_i = 0.0f;
float g_pid_d = 0.1f;
float g_angle_min_degrees = DEFAULT_ANGLE_MIN_DEGREES;
float g_angle_max_degrees = DEFAULT_ANGLE_MAX_DEGREES;

int32_t g_target_position_steps = 0;
uint32_t g_last_feedback_millis = 0;
uint32_t g_last_command_millis = 0;
uint32_t g_feedback_sequence_number = 0;

char g_input_buffer[INPUT_BUFFER_SIZE];
uint8_t g_input_buffer_index = 0;

MotorState g_motor_state = MotorState::Idle;
ErrorCode g_last_error = ErrorCode::None;

// ---------- helpers ----------

void enable_driver() {
    digitalWrite(PIN_ENABLE, ENABLE_ACTIVE_HIGH ? HIGH : LOW);
}

void disable_driver() {
    digitalWrite(PIN_ENABLE, ENABLE_ACTIVE_HIGH ? LOW : HIGH);
}

void report_error(ErrorCode code, const char* message) {
    g_last_error = code;
    g_motor_state = MotorState::Faulted;
    Serial.print(F("ERROR:"));
    Serial.print(static_cast<uint8_t>(code));
    Serial.print(F(" - "));
    Serial.println(message);
}

float clampf(float value, float low, float high) {
    if (value < low) return low;
    if (value > high) return high;
    return value;
}

int32_t angle_to_steps(float angle_degrees) {
    return static_cast<int32_t>(angle_degrees * TOTAL_STEPS_PER_REVOLUTION / 360.0f);
}

float steps_to_angle(int32_t steps) {
    return static_cast<float>(steps) * 360.0f / TOTAL_STEPS_PER_REVOLUTION;
}

bool angle_within_limits(float angle_degrees) {
    return angle_degrees >= g_angle_min_degrees && angle_degrees <= g_angle_max_degrees;
}

void apply_motion_settings() {
    g_stepper.setMaxSpeed(g_max_speed_steps_per_sec);
    g_stepper.setAcceleration(g_max_accel_steps_per_sec2);
}

// ---------- EEPROM ----------

void load_settings_from_eeprom() {
    uint32_t magic = 0;
    EEPROM.get(EEPROM_MAGIC_ADDR, magic);
    if (magic != EEPROM_MAGIC_VALUE) {
        Serial.println(F("SETTINGS: defaults (no valid EEPROM)"));
        apply_motion_settings();
        return;
    }
    EEPROM.get(EEPROM_ADDR_MAX_SPEED, g_max_speed_steps_per_sec);
    EEPROM.get(EEPROM_ADDR_MAX_ACCEL, g_max_accel_steps_per_sec2);
    EEPROM.get(EEPROM_ADDR_PID_P, g_pid_p);
    EEPROM.get(EEPROM_ADDR_PID_I, g_pid_i);
    EEPROM.get(EEPROM_ADDR_PID_D, g_pid_d);
    EEPROM.get(EEPROM_ADDR_ANGLE_MIN, g_angle_min_degrees);
    EEPROM.get(EEPROM_ADDR_ANGLE_MAX, g_angle_max_degrees);
    apply_motion_settings();
    Serial.println(F("SETTINGS: loaded from EEPROM"));
}

void save_settings_to_eeprom() {
    EEPROM.put(EEPROM_ADDR_MAX_SPEED, g_max_speed_steps_per_sec);
    EEPROM.put(EEPROM_ADDR_MAX_ACCEL, g_max_accel_steps_per_sec2);
    EEPROM.put(EEPROM_ADDR_PID_P, g_pid_p);
    EEPROM.put(EEPROM_ADDR_PID_I, g_pid_i);
    EEPROM.put(EEPROM_ADDR_PID_D, g_pid_d);
    EEPROM.put(EEPROM_ADDR_ANGLE_MIN, g_angle_min_degrees);
    EEPROM.put(EEPROM_ADDR_ANGLE_MAX, g_angle_max_degrees);
    constexpr uint32_t magic = EEPROM_MAGIC_VALUE;
    EEPROM.put(EEPROM_MAGIC_ADDR, magic);
    Serial.println(F("SETTINGS: saved to EEPROM"));
}

// ---------- feedback ----------

AccelPhase compute_acceleration_phase() {
    if (!g_stepper.isRunning()) return AccelPhase::Stopped;

    const float current_speed_abs = fabsf(g_stepper.speed());
    const float cruise_threshold = g_max_speed_steps_per_sec * 0.95f;
    if (current_speed_abs >= cruise_threshold) return AccelPhase::Cruising;

    const int32_t distance_to_go = labs(g_stepper.distanceToGo());
    const int32_t steps_to_decel = static_cast<int32_t>(
        (current_speed_abs * current_speed_abs) / (2.0f * g_max_accel_steps_per_sec2));
    return distance_to_go > steps_to_decel ? AccelPhase::Accelerating
                                           : AccelPhase::Decelerating;
}

void emit_feedback_line() {
    const int32_t current_position_steps = g_stepper.currentPosition();
    const float current_angle = steps_to_angle(current_position_steps);
    const float target_angle = steps_to_angle(g_target_position_steps);
    const uint32_t timestamp_micros = micros();
    const AccelPhase phase = compute_acceleration_phase();

    Serial.print(F("FB:"));
    Serial.print(current_angle, 2);
    Serial.print(',');
    Serial.print(target_angle, 2);
    Serial.print(',');
    Serial.print(g_stepper.speed());
    Serial.print(',');
    Serial.print(g_stepper.isRunning() ? 1 : 0);
    Serial.print(',');
    Serial.print(timestamp_micros);
    Serial.print(',');
    Serial.print(g_feedback_sequence_number++);
    Serial.print(',');
    Serial.println(static_cast<uint8_t>(phase));
}

void emit_feedback_if_due() {
    const uint32_t now = millis();
    if (now - g_last_feedback_millis < FEEDBACK_INTERVAL_MILLIS) return;
    emit_feedback_line();
    g_last_feedback_millis = now;
}

// ---------- command handlers ----------

void handle_move(const char* args) {
    if (args == nullptr || *args == '\0') {
        report_error(ErrorCode::MoveMissingArgument, "M missing angle");
        return;
    }
    const float requested_angle = atof(args);
    if (!angle_within_limits(requested_angle)) {
        report_error(ErrorCode::AngleOutOfBounds, "M angle outside software limits");
        return;
    }
    g_target_position_steps = angle_to_steps(requested_angle);
    g_stepper.moveTo(g_target_position_steps);
    g_motor_state = MotorState::Moving;
}

void handle_diagnostic_move(const char* args) {
    if (args == nullptr || *args == '\0') {
        report_error(ErrorCode::DiagnosticMissingArgument, "D missing steps");
        return;
    }
    const int32_t diagnostic_steps = atol(args);
    g_stepper.move(diagnostic_steps);
    Serial.print(F("DIAG: moving "));
    Serial.print(diagnostic_steps);
    Serial.println(F(" steps"));
}

void handle_settings(char* args) {
    if (args == nullptr || *args == '\0') {
        report_error(ErrorCode::SettingsMissingArgument, "S missing arguments");
        return;
    }
    char* save_ptr = nullptr;
    char* token = strtok_r(args, ",", &save_ptr);

    float new_max_speed = g_max_speed_steps_per_sec;
    float new_max_accel = g_max_accel_steps_per_sec2;
    float new_pid_p = g_pid_p;
    float new_pid_i = g_pid_i;
    float new_pid_d = g_pid_d;

    if (token) new_max_speed = atof(token);
    token = strtok_r(nullptr, ",", &save_ptr);
    if (token) new_max_accel = atof(token);
    token = strtok_r(nullptr, ",", &save_ptr);
    if (token) new_pid_p = atof(token);
    token = strtok_r(nullptr, ",", &save_ptr);
    if (token) new_pid_i = atof(token);
    token = strtok_r(nullptr, ",", &save_ptr);
    if (token) new_pid_d = atof(token);

    new_max_speed = clampf(new_max_speed, MIN_MAX_SPEED_STEPS_PER_SEC, MAX_MAX_SPEED_STEPS_PER_SEC);
    new_max_accel = clampf(new_max_accel, MIN_MAX_ACCEL_STEPS_PER_SEC2, MAX_MAX_ACCEL_STEPS_PER_SEC2);

    g_max_speed_steps_per_sec = new_max_speed;
    g_max_accel_steps_per_sec2 = new_max_accel;
    g_pid_p = new_pid_p;
    g_pid_i = new_pid_i;
    g_pid_d = new_pid_d;
    apply_motion_settings();

    Serial.print(F("SETTINGS:"));
    Serial.print(g_max_speed_steps_per_sec);
    Serial.print(',');
    Serial.print(g_max_accel_steps_per_sec2);
    Serial.print(',');
    Serial.print(g_pid_p);
    Serial.print(',');
    Serial.print(g_pid_i);
    Serial.print(',');
    Serial.println(g_pid_d);

    save_settings_to_eeprom();
}

void handle_limits(char* args) {
    if (args == nullptr || *args == '\0') {
        report_error(ErrorCode::SettingsMissingArgument, "L missing min,max");
        return;
    }
    char* save_ptr = nullptr;
    char* token = strtok_r(args, ",", &save_ptr);
    if (!token) {
        report_error(ErrorCode::SettingsMissingArgument, "L missing min");
        return;
    }
    const float new_min = atof(token);
    token = strtok_r(nullptr, ",", &save_ptr);
    if (!token) {
        report_error(ErrorCode::SettingsMissingArgument, "L missing max");
        return;
    }
    const float new_max = atof(token);
    if (new_min >= new_max) {
        report_error(ErrorCode::SettingsOutOfBounds, "L min >= max");
        return;
    }
    g_angle_min_degrees = new_min;
    g_angle_max_degrees = new_max;
    Serial.print(F("LIMITS:"));
    Serial.print(g_angle_min_degrees);
    Serial.print(',');
    Serial.println(g_angle_max_degrees);
    save_settings_to_eeprom();
}

void handle_reset() {
    g_stepper.setCurrentPosition(0);
    g_target_position_steps = 0;
    g_motor_state = MotorState::Idle;
    Serial.println(F("RESET:OK"));
}

void handle_query() {
    emit_feedback_line();
}

void handle_emergency_stop() {
    g_stepper.stop();
    g_stepper.setCurrentPosition(g_stepper.currentPosition());
    disable_driver();
    Serial.println(F("STOP:OK"));
    enable_driver();
    g_motor_state = MotorState::Stopped;
}

void handle_home() {
    if (!angle_within_limits(0.0f)) {
        report_error(ErrorCode::AngleOutOfBounds, "H zero outside limits");
        return;
    }
    g_target_position_steps = 0;
    g_stepper.moveTo(0);
    g_motor_state = MotorState::Homing;
}

void handle_driver(const char* args) {
    if (args == nullptr || *args == '\0') {
        report_error(ErrorCode::DriverMissingArgument, "X missing 0|1");
        return;
    }
    const int enable_flag = atoi(args);
    if (enable_flag == 1) {
        enable_driver();
        Serial.println(F("DRIVER:ENABLED"));
        return;
    }
    if (enable_flag == 0) {
        disable_driver();
        Serial.println(F("DRIVER:DISABLED"));
        return;
    }
    report_error(ErrorCode::DriverInvalidArgument, "X must be 0 or 1");
}

// ---------- dispatch ----------

void dispatch_command(char* command_string) {
    g_last_command_millis = millis();

    if (command_string == nullptr || command_string[0] == '\0') {
        report_error(ErrorCode::EmptyCommand, "empty command");
        return;
    }
    const char command_type = command_string[0];
    char* args = (strlen(command_string) > 2) ? command_string + 2 : nullptr;

    switch (command_type) {
        case 'M': handle_move(args); break;
        case 'D': handle_diagnostic_move(args); break;
        case 'S': handle_settings(args); break;
        case 'L': handle_limits(args); break;
        case 'R': handle_reset(); break;
        case 'Q': handle_query(); break;
        case 'E': handle_emergency_stop(); break;
        case 'H': handle_home(); break;
        case 'X': handle_driver(args); break;
        default:  report_error(ErrorCode::UnknownCommandType, "unknown command type"); break;
    }
}

void read_serial_input() {
    while (Serial.available()) {
        const char incoming = Serial.read();
        if (incoming == '\n' || incoming == '\r') {
            if (g_input_buffer_index == 0) continue;
            g_input_buffer[g_input_buffer_index] = '\0';
            dispatch_command(g_input_buffer);
            g_input_buffer_index = 0;
            continue;
        }
        if (g_input_buffer_index < INPUT_BUFFER_SIZE - 1) {
            g_input_buffer[g_input_buffer_index++] = incoming;
        }
    }
}

void check_heartbeat_watchdog() {
    if (g_motor_state != MotorState::Moving && g_motor_state != MotorState::Homing) return;
    const uint32_t now = millis();
    if (now - g_last_command_millis < HEARTBEAT_TIMEOUT_MILLIS) return;
    g_stepper.stop();
    g_motor_state = MotorState::Stopped;
    report_error(ErrorCode::HeartbeatTimeout, "PC heartbeat lost");
}

void run_motor_state_machine() {
    switch (g_motor_state) {
        case MotorState::Idle:
            g_stepper.run();
            break;
        case MotorState::Moving:
            g_stepper.run();
            if (!g_stepper.isRunning()) g_motor_state = MotorState::Idle;
            break;
        case MotorState::Homing:
            g_stepper.run();
            if (!g_stepper.isRunning()) g_motor_state = MotorState::Idle;
            break;
        case MotorState::Stopped:
            break;
        case MotorState::Faulted:
            disable_driver();
            break;
    }
}

}  // namespace

// ---------- arduino entry points ----------

void setup() {
    Serial.begin(115200);
    Serial.setTimeout(10);

    pinMode(PIN_ENABLE, OUTPUT);
    pinMode(PIN_LIMIT_SWITCH_MIN, INPUT_PULLUP);
    pinMode(PIN_LIMIT_SWITCH_MAX, INPUT_PULLUP);
    enable_driver();

    g_stepper.setCurrentPosition(0);
    apply_motion_settings();
    load_settings_from_eeprom();
    g_last_command_millis = millis();

    Serial.println(F("FB_HEADER:currentAngle,targetAngle,speed,isRunning,timestampMicros,sequence,accelState"));
    Serial.print(F("READY:v"));
    Serial.println(PROTOCOL_VERSION_MAJOR);

    wdt_enable(WDTO_500MS);
}

void loop() {
    wdt_reset();
    read_serial_input();
    check_heartbeat_watchdog();
    run_motor_state_machine();
    emit_feedback_if_due();
}
