"""
test_motor_simulation.py - Tests for SimulatedMotorInterface physics

Verifies that the simulated motor:
- Does not teleport (takes time to reach target)
- Respects velocity limits
- Decelerates to stop at target
- Reports moving/stopped state correctly
- Responds to home command
"""

import pytest
from interfaces.motor_interface import SimulatedMotorInterface


class TestSimulatedMotorPhysics:
    """Tests for SimulatedMotorInterface trapezoidal velocity profile physics."""

    def test_simulated_motor_does_not_teleport(self, obSimulatedMotor):
        """Verify motor does not instantly jump to target angle."""
        # Command a 10-degree move
        obSimulatedMotor.send_move_to_angle_command(10.0)

        # Advance only 1ms -- motor should barely have moved
        obSimulatedMotor.advance_simulation(0.001)
        obState = obSimulatedMotor.get_latest_motor_state()
        flAngleAfter1ms = obState.flMotorAngleDegrees
        assert flAngleAfter1ms < 1.0, (
            f"Motor teleported to {flAngleAfter1ms} degrees after only 1ms"
        )

        # Advance 2000 x 1ms (2 seconds total) -- motor should reach target
        for _ in range(2000):
            obSimulatedMotor.advance_simulation(0.001)

        obState = obSimulatedMotor.get_latest_motor_state()
        flFinalAngle = obState.flMotorAngleDegrees
        assert abs(flFinalAngle - 10.0) < 0.1, (
            f"Motor did not reach target: expected ~10.0, got {flFinalAngle}"
        )

    def test_simulated_motor_respects_velocity_limit(self, obSimulatedMotor):
        """Verify motor does not exceed configured max velocity."""
        # Set max speed to 20.0 deg/s, acceleration to 100.0 deg/s^2
        # send_speed_and_acceleration_settings takes steps/s, need to convert
        # _FL_DEGREES_PER_STEP = 360.0 / 288000.0 = 0.00125 deg/step
        flDegreesPerStep = 360.0 / 288000.0
        flMaxSpeedStepsPerSecond = 20.0 / flDegreesPerStep
        flMaxAccelStepsPerSecondSquared = 100.0 / flDegreesPerStep
        obSimulatedMotor.send_speed_and_acceleration_settings(
            flMaxSpeedStepsPerSecond, flMaxAccelStepsPerSecondSquared
        )

        # Command a 90-degree move (long enough to reach max speed)
        obSimulatedMotor.send_move_to_angle_command(90.0)

        flMaxObservedVelocity = 0.0
        flPreviousAngle = 0.0

        # Advance 5000 x 1ms, track maximum observed velocity
        for _ in range(5000):
            obSimulatedMotor.advance_simulation(0.001)
            obState = obSimulatedMotor.get_latest_motor_state()
            flCurrentAngle = obState.flMotorAngleDegrees

            # Calculate observed velocity from position change
            flObservedVelocity = abs(flCurrentAngle - flPreviousAngle) / 0.001
            if flObservedVelocity > flMaxObservedVelocity:
                flMaxObservedVelocity = flObservedVelocity

            flPreviousAngle = flCurrentAngle

        # Allow 1% tolerance for floating point
        assert flMaxObservedVelocity <= 20.0 * 1.01, (
            f"Motor exceeded velocity limit: max observed = {flMaxObservedVelocity:.2f} deg/s, "
            f"limit = 20.0 deg/s"
        )

    def test_simulated_motor_decelerates_to_stop(self, obSimulatedMotor):
        """Verify motor decelerates smoothly and stops at target position."""
        # Command a 5-degree move
        obSimulatedMotor.send_move_to_angle_command(5.0)

        # Advance until motor reaches target (within 0.05 degrees)
        iMaxIterations = 5000
        for _ in range(iMaxIterations):
            obSimulatedMotor.advance_simulation(0.001)
            obState = obSimulatedMotor.get_latest_motor_state()
            if abs(obState.flMotorAngleDegrees - 5.0) < 0.05:
                break

        # Advance a few more steps to let it fully settle
        for _ in range(100):
            obSimulatedMotor.advance_simulation(0.001)

        obState = obSimulatedMotor.get_latest_motor_state()

        # Check velocity is near zero when at target
        flDegreesPerStep = 360.0 / 288000.0
        flVelocityDegreesPerSecond = obState.flMotorSpeedStepsPerSecond * flDegreesPerStep
        assert flVelocityDegreesPerSecond < 0.1, (
            f"Motor still moving at {flVelocityDegreesPerSecond:.4f} deg/s after reaching target"
        )

    def test_simulated_motor_state_reports_moving(self, obSimulatedMotor):
        """Verify motor state correctly reports moving/stopped status."""
        # Command a move
        obSimulatedMotor.send_move_to_angle_command(10.0)

        # Advance a few steps -- motor should report as moving
        for _ in range(10):
            obSimulatedMotor.advance_simulation(0.001)

        obState = obSimulatedMotor.get_latest_motor_state()
        assert obState.bMotorIsMoving is True, (
            "Motor should report as moving shortly after receiving a move command"
        )

        # Advance to completion (2 seconds)
        for _ in range(3000):
            obSimulatedMotor.advance_simulation(0.001)

        obState = obSimulatedMotor.get_latest_motor_state()
        assert obState.bMotorIsMoving is False, (
            "Motor should report as stopped after reaching target"
        )

    def test_simulated_motor_home_command(self, obSimulatedMotor):
        """Verify home command returns motor to 0 degrees."""
        # Move to 10 degrees (advance to completion)
        obSimulatedMotor.send_move_to_angle_command(10.0)
        for _ in range(3000):
            obSimulatedMotor.advance_simulation(0.001)

        obState = obSimulatedMotor.get_latest_motor_state()
        assert abs(obState.flMotorAngleDegrees - 10.0) < 0.1, (
            f"Motor did not reach 10 degrees before home: {obState.flMotorAngleDegrees}"
        )

        # Send home command
        obSimulatedMotor.send_home_command()

        # Advance to completion
        for _ in range(3000):
            obSimulatedMotor.advance_simulation(0.001)

        obState = obSimulatedMotor.get_latest_motor_state()
        assert abs(obState.flMotorAngleDegrees) < 0.1, (
            f"Motor did not return home: angle = {obState.flMotorAngleDegrees}"
        )
