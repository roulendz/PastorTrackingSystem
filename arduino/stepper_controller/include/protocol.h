// Pastor Tracking — Stepper firmware protocol constants.
// Single source of truth shared between firmware and host docs.

#pragma once

#include <stdint.h>

namespace stepper_protocol {

// Protocol version. Bumped on any wire-format change.
// Firmware emits "READY:v2" on boot. Host MUST verify match.
constexpr uint8_t PROTOCOL_VERSION_MAJOR = 2;

// Mechanics — DO NOT change without verifying physical hardware.
constexpr int32_t MOTOR_STEPS_PER_REVOLUTION = 200;
constexpr int32_t MOTOR_GEAR_RATIO = 180;
constexpr int32_t MOTOR_MICROSTEPS = 8;
constexpr int32_t TOTAL_STEPS_PER_REVOLUTION =
    MOTOR_STEPS_PER_REVOLUTION * MOTOR_GEAR_RATIO * MOTOR_MICROSTEPS;  // 288000

// Software angle limits (degrees from home).
// Configurable at runtime via settings, persisted to EEPROM.
constexpr float DEFAULT_ANGLE_MIN_DEGREES = -90.0f;
constexpr float DEFAULT_ANGLE_MAX_DEGREES = 90.0f;

// Settings clamp ranges.
constexpr float MIN_MAX_SPEED_STEPS_PER_SEC = 100.0f;
constexpr float MAX_MAX_SPEED_STEPS_PER_SEC = 50000.0f;
constexpr float MIN_MAX_ACCEL_STEPS_PER_SEC2 = 50.0f;
constexpr float MAX_MAX_ACCEL_STEPS_PER_SEC2 = 30000.0f;

// Watchdog: PC must send any command at least this often, else motor halts.
constexpr uint16_t HEARTBEAT_TIMEOUT_MILLIS = 1000;

// Feedback throttle (ms between unsolicited FB lines).
constexpr uint16_t FEEDBACK_INTERVAL_MILLIS = 20;

// Serial input buffer size. Static, no heap.
constexpr uint8_t INPUT_BUFFER_SIZE = 48;

// Pin assignments.
constexpr uint8_t PIN_DIRECTION = 2;
constexpr uint8_t PIN_STEP = 3;
constexpr uint8_t PIN_ENABLE = 4;
constexpr uint8_t PIN_LIMIT_SWITCH_MIN = 5;
constexpr uint8_t PIN_LIMIT_SWITCH_MAX = 6;
constexpr bool ENABLE_ACTIVE_HIGH = false;

// Acceleration phase reported in feedback line.
enum class AccelPhase : uint8_t {
    Stopped = 0,
    Accelerating = 1,
    Cruising = 2,
    Decelerating = 3,
};

// Motor finite-state machine.
enum class MotorState : uint8_t {
    Idle = 0,
    Moving = 1,
    Stopped = 2,
    Homing = 3,
    Faulted = 4,
};

// Error codes — surface to host for structured handling.
enum class ErrorCode : uint8_t {
    None = 0,
    EmptyCommand = 1,
    UnknownCommandType = 2,
    MoveMissingArgument = 3,
    DiagnosticMissingArgument = 4,
    SettingsMissingArgument = 5,
    DriverMissingArgument = 6,
    DriverInvalidArgument = 7,
    HomingFailed = 8,
    AngleOutOfBounds = 9,
    SettingsOutOfBounds = 10,
    HeartbeatTimeout = 11,
};

// EEPROM layout.
constexpr uint16_t EEPROM_ADDR_MAX_SPEED = 0;
constexpr uint16_t EEPROM_ADDR_MAX_ACCEL = sizeof(float);
constexpr uint16_t EEPROM_ADDR_PID_P = 2 * sizeof(float);
constexpr uint16_t EEPROM_ADDR_PID_I = 3 * sizeof(float);
constexpr uint16_t EEPROM_ADDR_PID_D = 4 * sizeof(float);
constexpr uint16_t EEPROM_ADDR_ANGLE_MIN = 5 * sizeof(float);
constexpr uint16_t EEPROM_ADDR_ANGLE_MAX = 6 * sizeof(float);
constexpr uint16_t EEPROM_MAGIC_ADDR = 7 * sizeof(float);
constexpr uint32_t EEPROM_MAGIC_VALUE = 0x50545332;  // 'PTS2' — settings v2

}  // namespace stepper_protocol
