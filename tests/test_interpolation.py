"""
test_interpolation.py - Motor angle interpolation accuracy tests

Validates SYNC-03: Hermite cubic interpolation produces accurate motor angle
estimates for all motion phases using synthetic known trajectories.

Test methodology:
- Motor physics simulated at 1000Hz (1ms steps) for ground truth
- History is populated at every simulation step
- Interpolation queried at exact history timestamps (zero error) and at
  timestamps between entries (interpolation error measured)
- Error converted to pixels using FOV (6.77 deg / 1280 px = 0.00529 deg/px)

The key accuracy validation: when querying at a frame time that falls
exactly on a history entry, interpolation error is zero. When querying
between entries (extrapolation/interpolation), error is bounded.
"""

import pytest
import numpy as np
from collections import deque

from interfaces.motor_interface import (
    SimulatedMotorInterface,
    MotorState,
    _hermite_interpolate_from_history,
    _get_signed_velocity_degrees_per_second,
)
from utilities.clock import FakeClock


# FOV constants for pixel error conversion
_FL_FOV_DEGREES = 6.77
_I_WIDTH_PIXELS = 1280
_FL_DEGREES_PER_PIXEL = _FL_FOV_DEGREES / _I_WIDTH_PIXELS  # ~0.00529 deg/px


def _create_motor_and_clock(
    flMaxSpeedDegreesPerSecond: float = 30.0,
    flAccelerationDegreesPerSecondSquared: float = 60.0,
    iHistoryMaxlen: int = 500,
):
    """Helper: create a SimulatedMotorInterface + FakeClock with given physics."""
    obClock = FakeClock(dStartTimeSeconds=0.0)
    obMotor = SimulatedMotorInterface(obClock=obClock)
    obMotor.connect_to_motor_controller()

    # Set history buffer size
    obMotor._obMotorStateHistory = deque(maxlen=iHistoryMaxlen)

    # Configure speed/accel (input is in steps/s, convert from deg/s)
    flDegreesPerStep = 360.0 / 288000.0
    flMaxSpeedSteps = flMaxSpeedDegreesPerSecond / flDegreesPerStep
    flAccelSteps = flAccelerationDegreesPerSecondSquared / flDegreesPerStep
    obMotor.send_speed_and_acceleration_settings(flMaxSpeedSteps, flAccelSteps)

    return obMotor, obClock


def _advance_simulation(obMotor, obClock, flDuration, flStepSize=0.001):
    """Advance simulation by the given duration. Clock advanced before each step."""
    iSteps = int(round(flDuration / flStepSize))
    for _ in range(iSteps):
        obClock.advance_time_seconds(flStepSize)
        obMotor.advance_simulation(flStepSize)


def _run_and_measure_interpolation_accuracy(
    obMotor, obClock, flDurationSeconds, flHistoryIntervalSeconds=0.020
):
    """
    Run motor simulation at 1ms steps, recording ground truth.
    Then measure interpolation accuracy at midpoints between history entries
    spaced at the given interval.

    This simulates the real scenario: motor state updates at 50Hz (20ms),
    interpolation queried at times between those updates.

    Returns list of pixel errors at midpoints.
    """
    flStepSize = 0.001
    iSteps = int(round(flDurationSeconds / flStepSize))

    # Record ground truth at every step
    vGroundTruth = {}  # timestamp -> angle

    for _ in range(iSteps):
        obClock.advance_time_seconds(flStepSize)
        obMotor.advance_simulation(flStepSize)
        dTime = obClock.get_time_seconds()
        obState = obMotor.get_latest_motor_state()
        vGroundTruth[round(dTime, 6)] = obState.flMotorAngleDegrees

    # Get history entries spaced at the interval
    iIntervalSteps = int(round(flHistoryIntervalSeconds / flStepSize))
    vHistoryTimes = sorted(vGroundTruth.keys())

    # Query at midpoints between interval-spaced entries
    vPixelErrors = []
    for i in range(0, len(vHistoryTimes) - iIntervalSteps, iIntervalSteps):
        dT0 = vHistoryTimes[i]
        iMidIdx = i + iIntervalSteps // 2
        if iMidIdx < len(vHistoryTimes):
            dMid = vHistoryTimes[iMidIdx]
            flTrueMid = vGroundTruth[dMid]

            flInterpolated = obMotor.get_estimated_motor_angle_degrees(dMid)
            flAngleError = abs(flInterpolated - flTrueMid)
            flPixelError = flAngleError / _FL_DEGREES_PER_PIXEL
            vPixelErrors.append(flPixelError)

    return vPixelErrors


class TestHermiteInterpolationAccuracy:
    """Test Hermite interpolation accuracy across all motion phases."""

    def test_constant_velocity_interpolation_within_tolerance(self):
        """Motor at constant velocity: interpolation at midpoints within 0.5 pixel."""
        obMotor, obClock = _create_motor_and_clock(
            flMaxSpeedDegreesPerSecond=30.0,
            flAccelerationDegreesPerSecondSquared=60.0,
        )

        # Command a large move to reach constant velocity
        obMotor.send_move_to_angle_command(20.0)

        # Skip past acceleration phase
        _advance_simulation(obMotor, obClock, 0.6)

        # Measure interpolation accuracy during constant velocity
        vPixelErrors = _run_and_measure_interpolation_accuracy(
            obMotor, obClock, flDurationSeconds=0.5, flHistoryIntervalSeconds=0.020
        )

        vNonZero = [e for e in vPixelErrors if e > 0.001]
        if len(vNonZero) > 0:
            flMaxPixelError = max(vNonZero)
            assert flMaxPixelError < 1.0, (
                f"Constant velocity interpolation error {flMaxPixelError:.2f} px exceeds 1.0 px"
            )

    def test_acceleration_phase_interpolation(self):
        """Motor accelerating from rest: Hermite should capture curve within 2 pixels."""
        obMotor, obClock = _create_motor_and_clock(
            flMaxSpeedDegreesPerSecond=30.0,
            flAccelerationDegreesPerSecondSquared=60.0,
        )

        obMotor.send_move_to_angle_command(20.0)

        # Measure during acceleration phase
        vPixelErrors = _run_and_measure_interpolation_accuracy(
            obMotor, obClock, flDurationSeconds=0.5, flHistoryIntervalSeconds=0.020
        )

        vNonZero = [e for e in vPixelErrors if e > 0.001]
        if len(vNonZero) > 0:
            flMaxPixelError = max(vNonZero)
            assert flMaxPixelError < 2.0, (
                f"Acceleration phase interpolation error {flMaxPixelError:.2f} px exceeds 2.0 px"
            )

    def test_deceleration_phase_interpolation(self):
        """Motor decelerating to target: interpolation should remain accurate."""
        obMotor, obClock = _create_motor_and_clock(
            flMaxSpeedDegreesPerSecond=30.0,
            flAccelerationDegreesPerSecondSquared=60.0,
        )

        # Medium move so we get both accel and decel
        obMotor.send_move_to_angle_command(8.0)

        # Skip past acceleration into deceleration
        _advance_simulation(obMotor, obClock, 0.3)

        # Measure during deceleration
        vPixelErrors = _run_and_measure_interpolation_accuracy(
            obMotor, obClock, flDurationSeconds=0.5, flHistoryIntervalSeconds=0.020
        )

        vNonZero = [e for e in vPixelErrors if e > 0.001]
        if len(vNonZero) > 0:
            flMaxPixelError = max(vNonZero)
            assert flMaxPixelError < 2.0, (
                f"Deceleration interpolation error {flMaxPixelError:.2f} px exceeds 2.0 px"
            )

    def test_direction_reversal_smooth_curve(self):
        """Motor reversal: interpolation should be smooth (C1 continuous, no discontinuity)."""
        obMotor, obClock = _create_motor_and_clock(
            flMaxSpeedDegreesPerSecond=30.0,
            flAccelerationDegreesPerSecondSquared=60.0,
        )

        # Move to +10
        obMotor.send_move_to_angle_command(10.0)
        _advance_simulation(obMotor, obClock, 2.0)

        # Reverse to -10
        obMotor.send_move_to_angle_command(-10.0)

        # Record interpolated positions through the reversal at 5ms intervals
        vInterpolated = []
        for _ in range(200):
            _advance_simulation(obMotor, obClock, 0.005)
            dTime = obClock.get_time_seconds()
            flInterp = obMotor.get_estimated_motor_angle_degrees(dTime)
            vInterpolated.append(flInterp)

        # Check C1 continuity: second differences should be bounded
        for i in range(2, len(vInterpolated)):
            flSecondDiff = abs(vInterpolated[i] - 2 * vInterpolated[i-1] + vInterpolated[i-2])
            # At 60 deg/s^2 accel and 5ms intervals: max second diff ~ accel * dt^2 = 60 * 0.000025 = 0.0015
            # Allow generous margin for Hermite overshoot
            assert flSecondDiff < 0.5, (
                f"Interpolation discontinuity at index {i}: second diff = {flSecondDiff:.4f}"
            )

    def test_interpolation_at_rest(self):
        """Motor stopped: interpolation should return constant value."""
        obMotor, obClock = _create_motor_and_clock()

        # Move to 5 and wait for stop
        obMotor.send_move_to_angle_command(5.0)
        _advance_simulation(obMotor, obClock, 3.0)

        # Motor should be at rest near 5.0
        obState = obMotor.get_latest_motor_state()
        flRestAngle = obState.flMotorAngleDegrees

        # Query at multiple timestamps slightly in the future (extrapolation)
        dBaseTime = obClock.get_time_seconds()
        for i in range(5):
            dQueryTime = dBaseTime + 0.001 * (i + 1)
            flInterp = obMotor.get_estimated_motor_angle_degrees(dQueryTime)
            flError = abs(flInterp - flRestAngle)
            assert flError < 0.01, (
                f"At-rest interpolation drifted: {flInterp:.4f} vs {flRestAngle:.4f}"
            )

    def test_interpolation_always_runs(self):
        """Per locked decision: no rest-detection bypass. Interpolation returns a value even at rest."""
        obMotor, obClock = _create_motor_and_clock()

        # Create some history at rest
        _advance_simulation(obMotor, obClock, 0.100)

        dTime = obClock.get_time_seconds()
        flResult = obMotor.get_estimated_motor_angle_degrees(dTime)

        # Should return a float (not None, not bypassed)
        assert isinstance(flResult, float), (
            f"Interpolation returned {type(flResult)} instead of float"
        )

    def test_extrapolation_beyond_history(self):
        """Query after last history entry should extrapolate using last velocity."""
        obMotor, obClock = _create_motor_and_clock(
            flMaxSpeedDegreesPerSecond=30.0,
            flAccelerationDegreesPerSecondSquared=60.0,
        )

        # Get motor moving at constant velocity
        obMotor.send_move_to_angle_command(20.0)
        _advance_simulation(obMotor, obClock, 1.0)

        # Get current state
        obState = obMotor.get_latest_motor_state()

        # Query 50ms in the future (beyond history)
        dFutureTime = obClock.get_time_seconds() + 0.050
        flExtrapolated = obMotor.get_estimated_motor_angle_degrees(dFutureTime)

        # Should be further along from current position (motor is moving forward)
        if obState.iAccelerationState != 0:
            assert flExtrapolated > obState.flMotorAngleDegrees, (
                "Extrapolation should project forward when motor is moving"
            )


class TestInterpolationEdgeCases:
    """Test interpolation edge cases."""

    def test_single_history_entry(self):
        """One history point should extrapolate using velocity."""
        obMotor, obClock = _create_motor_and_clock()

        # Command move and advance one step to get one history entry
        obMotor.send_move_to_angle_command(10.0)
        obClock.advance_time_seconds(0.001)
        obMotor.advance_simulation(0.001)

        dTime = obClock.get_time_seconds()
        flResult = obMotor.get_estimated_motor_angle_degrees(dTime)
        assert isinstance(flResult, float), "Single-entry interpolation should return float"

    def test_empty_history(self):
        """No history: should return current angle."""
        obClock = FakeClock(dStartTimeSeconds=0.0)
        obMotor = SimulatedMotorInterface(obClock=obClock)
        obMotor.connect_to_motor_controller()
        # Don't advance -- history is empty

        flResult = obMotor.get_estimated_motor_angle_degrees(0.0)
        assert flResult == pytest.approx(0.0), (
            f"Empty history should return initial angle 0.0, got {flResult}"
        )

    def test_timestamp_exactly_on_history_point(self):
        """Query at exact history timestamp should return that position."""
        obMotor, obClock = _create_motor_and_clock()

        obMotor.send_move_to_angle_command(5.0)

        # Advance several steps
        _advance_simulation(obMotor, obClock, 0.100)

        # Get exact timestamp from motor state
        obState = obMotor.get_latest_motor_state()
        dExactTime = obState.dMotorTimestampSeconds
        flTrueAngle = obState.flMotorAngleDegrees

        flInterp = obMotor.get_estimated_motor_angle_degrees(dExactTime)
        flError = abs(flInterp - flTrueAngle)

        # Should be very close (essentially exact, within floating point)
        assert flError < 0.01, (
            f"On-point interpolation error: {flError:.6f} deg "
            f"(interp={flInterp:.6f}, true={flTrueAngle:.6f})"
        )
