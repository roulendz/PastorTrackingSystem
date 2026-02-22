"""
test_control_dt.py - Control algorithm determinism tests with explicit dt

Validates SYNC-05: control algorithms are deterministic when given explicit
delta-time. Same inputs always produce same outputs. No internal time calls.

Tests cover:
- ProportionalController: output independent of dt
- PIDController: integral accumulates with dt, derivative uses dt
- VelocityController: position change scales with dt
- Source inspection: no time.time() or time.perf_counter() in any controller
"""

import inspect
import pytest

from control.control_algorithm import (
    ProportionalController,
    PIDController,
    VelocityController,
)


class TestProportionalControllerDeterminism:
    """Verify P controller produces identical outputs for identical inputs."""

    def test_proportional_same_input_same_output(self):
        """Two P controllers with same gain should produce identical results."""
        obControllerA = ProportionalController(flProportionalGain=0.5)
        obControllerB = ProportionalController(flProportionalGain=0.5)

        flErrorDegrees = 3.0
        flDt = 0.033

        flResultA = obControllerA.calculate_correction_from_error(flErrorDegrees, flDt)
        flResultB = obControllerB.calculate_correction_from_error(flErrorDegrees, flDt)

        assert flResultA == flResultB, (
            f"P controllers diverged: {flResultA} vs {flResultB}"
        )

    def test_proportional_ignores_dt(self):
        """P controller output should be independent of dt (no time dependency)."""
        obController = ProportionalController(flProportionalGain=1.0)

        flError = 2.0
        flResultFastDt = obController.calculate_correction_from_error(flError, 0.010)
        obController.reset_controller()
        flResultSlowDt = obController.calculate_correction_from_error(flError, 0.100)

        assert flResultFastDt == flResultSlowDt, (
            f"P controller output depends on dt: {flResultFastDt} vs {flResultSlowDt}"
        )

    def test_proportional_output_equals_gain_times_error(self):
        """P controller should produce Kp * error exactly."""
        flKp = 0.75
        obController = ProportionalController(flProportionalGain=flKp)
        flError = 4.0
        flResult = obController.calculate_correction_from_error(flError, 0.033)
        assert flResult == pytest.approx(flKp * flError), (
            f"Expected {flKp * flError}, got {flResult}"
        )


class TestPIDControllerDeterminism:
    """Verify PID controller determinism with explicit dt."""

    def test_pid_same_sequence_same_output(self):
        """Two PID controllers fed identical (error, dt) sequences produce identical outputs."""
        obControllerA = PIDController(
            flProportionalGain=1.0,
            flIntegralGain=0.1,
            flDerivativeGain=0.05,
        )
        obControllerB = PIDController(
            flProportionalGain=1.0,
            flIntegralGain=0.1,
            flDerivativeGain=0.05,
        )

        # Feed identical sequence
        vErrors = [0.0, 1.0, 2.0, 1.5, 0.5, 0.0, -0.5]
        flDt = 0.033

        for flError in vErrors:
            flResultA = obControllerA.calculate_correction_from_error(flError, flDt)
            flResultB = obControllerB.calculate_correction_from_error(flError, flDt)
            assert flResultA == flResultB, (
                f"PID controllers diverged at error={flError}: "
                f"{flResultA} vs {flResultB}"
            )

    def test_pid_integral_accumulates_with_dt(self):
        """Integral term should be approximately error * dt * numFrames * Ki."""
        flKi = 0.5
        obController = PIDController(
            flProportionalGain=0.0,  # Zero out P and D to isolate I
            flIntegralGain=flKi,
            flDerivativeGain=0.0,
        )

        flError = 1.0
        flDt = 0.033
        iFrames = 10

        flLastResult = 0.0
        for _ in range(iFrames):
            flLastResult = obController.calculate_correction_from_error(flError, flDt)

        # Expected integral: error * dt * iFrames = 1.0 * 0.033 * 10 = 0.33
        # Output: Ki * integral = 0.5 * 0.33 = 0.165
        flExpectedIntegral = flError * flDt * iFrames
        flExpectedOutput = flKi * flExpectedIntegral
        assert flLastResult == pytest.approx(flExpectedOutput, abs=0.001), (
            f"Expected integral output ~{flExpectedOutput}, got {flLastResult}"
        )

    def test_pid_derivative_uses_dt(self):
        """Derivative term should be Kd * (error_change / dt)."""
        flKd = 0.1
        obController = PIDController(
            flProportionalGain=0.0,  # Zero out P and I to isolate D
            flIntegralGain=0.0,
            flDerivativeGain=flKd,
        )

        flDt = 0.033

        # First call: error=0 (establishes baseline)
        obController.calculate_correction_from_error(0.0, flDt)

        # Second call: error steps to 1.0
        flResult = obController.calculate_correction_from_error(1.0, flDt)

        # Expected: Kd * (1.0 - 0.0) / 0.033
        flExpected = flKd * (1.0 / flDt)
        assert flResult == pytest.approx(flExpected, rel=0.01), (
            f"Expected derivative output ~{flExpected}, got {flResult}"
        )

    def test_pid_no_time_calls(self):
        """PIDController source should not contain time.time() or time.perf_counter()."""
        sSource = inspect.getsource(PIDController)
        assert "time.time()" not in sSource, (
            "PIDController contains time.time() -- violates explicit dt contract"
        )
        assert "time.perf_counter()" not in sSource, (
            "PIDController contains time.perf_counter() -- violates explicit dt contract"
        )

    def test_pid_reset_clears_state(self):
        """After reset, PID should behave as if freshly constructed."""
        obController = PIDController(
            flProportionalGain=1.0,
            flIntegralGain=0.1,
            flDerivativeGain=0.05,
        )

        # Accumulate some state
        for _ in range(5):
            obController.calculate_correction_from_error(2.0, 0.033)

        obController.reset_controller()

        # Fresh controller for comparison
        obFresh = PIDController(
            flProportionalGain=1.0,
            flIntegralGain=0.1,
            flDerivativeGain=0.05,
        )

        flDt = 0.033
        flResult = obController.calculate_correction_from_error(1.0, flDt)
        flFreshResult = obFresh.calculate_correction_from_error(1.0, flDt)
        assert flResult == pytest.approx(flFreshResult), (
            f"Reset PID != fresh PID: {flResult} vs {flFreshResult}"
        )


class TestVelocityControllerDeterminism:
    """Verify VelocityController determinism with explicit dt."""

    def test_velocity_same_sequence_same_output(self):
        """Two VelocityControllers fed identical sequences produce identical outputs."""
        obControllerA = VelocityController(
            flVelocityGain=5.0,
            flMaximumVelocityDegreesPerSecond=30.0,
            flVelocitySmoothingAlpha=0.3,
        )
        obControllerB = VelocityController(
            flVelocityGain=5.0,
            flMaximumVelocityDegreesPerSecond=30.0,
            flVelocitySmoothingAlpha=0.3,
        )

        vErrors = [0.0, 1.0, 2.0, 3.0, 2.0, 1.0, 0.0]
        flDt = 0.033

        for flError in vErrors:
            flResultA = obControllerA.calculate_correction_from_error(flError, flDt)
            flResultB = obControllerB.calculate_correction_from_error(flError, flDt)
            assert flResultA == flResultB, (
                f"VelocityControllers diverged at error={flError}: "
                f"{flResultA} vs {flResultB}"
            )

    def test_velocity_position_change_scales_with_dt(self):
        """Same error but different dt should produce position change proportional to dt."""
        flDtShort = 0.010
        flDtLong = 0.100

        obControllerShort = VelocityController(
            flVelocityGain=5.0,
            flMaximumVelocityDegreesPerSecond=30.0,
            flVelocitySmoothingAlpha=1.0,  # No smoothing, so velocity is instant
        )
        obControllerLong = VelocityController(
            flVelocityGain=5.0,
            flMaximumVelocityDegreesPerSecond=30.0,
            flVelocitySmoothingAlpha=1.0,
        )

        flError = 2.0
        flResultShort = obControllerShort.calculate_correction_from_error(flError, flDtShort)
        flResultLong = obControllerLong.calculate_correction_from_error(flError, flDtLong)

        # Position change = velocity * dt, so ratio should be dt_long / dt_short
        flExpectedRatio = flDtLong / flDtShort
        flActualRatio = flResultLong / flResultShort if flResultShort != 0 else 0
        assert flActualRatio == pytest.approx(flExpectedRatio, rel=0.01), (
            f"Position change ratio {flActualRatio:.2f} != expected {flExpectedRatio:.2f}"
        )

    def test_velocity_no_time_calls(self):
        """VelocityController source should not contain time.time() or time.perf_counter()."""
        sSource = inspect.getsource(VelocityController)
        assert "time.time()" not in sSource, (
            "VelocityController contains time.time() -- violates explicit dt contract"
        )
        assert "time.perf_counter()" not in sSource, (
            "VelocityController contains time.perf_counter() -- violates explicit dt contract"
        )
