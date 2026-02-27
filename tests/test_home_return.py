"""
test_home_return.py - Tests for HomeReturnController state machine

Verifies:
- State transitions: TRACKING -> SAFE_ZONE_DELAY -> RETURNING_HOME -> AT_HOME
- Delay cancellation when person leaves safe zone
- S-curve return to home with zero overshoot
- Mid-return cancellation preserves profiler velocity
- AT_HOME locks output at 0.0
"""

import pytest

from control.home_return_controller import HomeReturnController, HomeReturnState


class TestHomeReturnController:
    """Tests for the HomeReturnController state machine."""

    def test_tracking_to_safe_zone_delay_transition(self):
        """Person enters safe zone -> state transitions to SAFE_ZONE_DELAY."""
        obController = HomeReturnController(
            flDelaySeconds=1.5,
            flReturnMaxVelocity=10.0,
        )

        assert obController.get_state() == HomeReturnState.TRACKING

        # Person is within safe zone (angle 2.0, threshold 5.0)
        eState = obController.update(
            flPersonAngleRelativeToHome=2.0,
            flSafeZoneThresholdDegrees=5.0,
            flDeltaTimeSeconds=1.0 / 30.0,
            flCurrentMotorAngleDegrees=2.0,
        )

        assert eState == HomeReturnState.SAFE_ZONE_DELAY, (
            f"Expected SAFE_ZONE_DELAY, got {eState}"
        )

    def test_safe_zone_delay_to_returning_home(self):
        """Person stays in safe zone for > delay seconds -> transitions through RETURNING_HOME."""
        obController = HomeReturnController(
            flDelaySeconds=1.5,
            flReturnMaxVelocity=10.0,
        )

        flDt = 1.0 / 30.0

        # Enter safe zone
        obController.update(
            flPersonAngleRelativeToHome=2.0,
            flSafeZoneThresholdDegrees=5.0,
            flDeltaTimeSeconds=flDt,
            flCurrentMotorAngleDegrees=2.0,
        )

        # Stay in safe zone and track states we pass through
        bReachedReturningHome = False
        for _ in range(60):  # 2 seconds at 30 FPS
            eState = obController.update(
                flPersonAngleRelativeToHome=2.0,
                flSafeZoneThresholdDegrees=5.0,
                flDeltaTimeSeconds=flDt,
                flCurrentMotorAngleDegrees=2.0,
            )
            if eState == HomeReturnState.RETURNING_HOME:
                bReachedReturningHome = True

        assert bReachedReturningHome, (
            "Expected to transition through RETURNING_HOME after delay"
        )
        # Final state may be RETURNING_HOME or AT_HOME (return can complete quickly)
        assert eState in (HomeReturnState.RETURNING_HOME, HomeReturnState.AT_HOME), (
            f"Expected RETURNING_HOME or AT_HOME after delay, got {eState}"
        )

    def test_safe_zone_delay_cancelled_on_exit(self):
        """Person leaves safe zone during delay -> state returns to TRACKING."""
        obController = HomeReturnController(
            flDelaySeconds=1.5,
            flReturnMaxVelocity=10.0,
        )

        flDt = 1.0 / 30.0

        # Enter safe zone
        obController.update(
            flPersonAngleRelativeToHome=2.0,
            flSafeZoneThresholdDegrees=5.0,
            flDeltaTimeSeconds=flDt,
            flCurrentMotorAngleDegrees=2.0,
        )
        assert obController.get_state() == HomeReturnState.SAFE_ZONE_DELAY

        # Wait partial delay (0.5 seconds)
        for _ in range(15):
            obController.update(
                flPersonAngleRelativeToHome=2.0,
                flSafeZoneThresholdDegrees=5.0,
                flDeltaTimeSeconds=flDt,
                flCurrentMotorAngleDegrees=2.0,
            )

        # Person leaves safe zone
        eState = obController.update(
            flPersonAngleRelativeToHome=10.0,
            flSafeZoneThresholdDegrees=5.0,
            flDeltaTimeSeconds=flDt,
            flCurrentMotorAngleDegrees=10.0,
        )

        assert eState == HomeReturnState.TRACKING, (
            f"Expected TRACKING after leaving safe zone, got {eState}"
        )

    def test_returning_home_reaches_zero(self):
        """Step RETURNING_HOME for enough frames starting at 5.0 deg.
        get_target_angle() should approach 0.0 within 0.05 deg."""
        obController = HomeReturnController(
            flDelaySeconds=0.1,  # short delay for test
            flReturnMaxVelocity=10.0,
            flReturnAccelTime=0.3,
            flReturnDecelTime=0.3,
        )

        flDt = 1.0 / 30.0
        flMotorAngle = 5.0

        # Enter safe zone and pass through delay
        for _ in range(10):
            obController.update(
                flPersonAngleRelativeToHome=1.0,
                flSafeZoneThresholdDegrees=5.0,
                flDeltaTimeSeconds=flDt,
                flCurrentMotorAngleDegrees=flMotorAngle,
            )

        assert obController.get_state() == HomeReturnState.RETURNING_HOME, (
            f"Expected RETURNING_HOME, got {obController.get_state()}"
        )

        # Run return for several seconds
        for _ in range(300):  # 10 seconds
            obController.update(
                flPersonAngleRelativeToHome=1.0,
                flSafeZoneThresholdDegrees=5.0,
                flDeltaTimeSeconds=flDt,
                flCurrentMotorAngleDegrees=obController.get_target_angle(),
            )

        flFinalAngle = obController.get_target_angle()
        assert abs(flFinalAngle) < 0.05, (
            f"Target angle {flFinalAngle:.4f} did not reach home (within 0.05 deg)"
        )

    def test_returning_home_cancelled_on_exit(self):
        """Person leaves safe zone during return. State goes to TRACKING.
        Profiler velocity should NOT be reset (preserved for smooth transition)."""
        obController = HomeReturnController(
            flDelaySeconds=0.1,  # short delay for test
            flReturnMaxVelocity=10.0,
            flReturnAccelTime=0.3,
            flReturnDecelTime=0.3,
        )

        flDt = 1.0 / 30.0
        flMotorAngle = 20.0  # Large angle so return takes many frames

        # Enter safe zone and pass through delay to RETURNING_HOME
        for _ in range(10):
            obController.update(
                flPersonAngleRelativeToHome=1.0,
                flSafeZoneThresholdDegrees=25.0,
                flDeltaTimeSeconds=flDt,
                flCurrentMotorAngleDegrees=flMotorAngle,
            )

        assert obController.get_state() == HomeReturnState.RETURNING_HOME

        # Run just a few return frames (still far from home at 20 deg)
        for _ in range(5):
            obController.update(
                flPersonAngleRelativeToHome=1.0,
                flSafeZoneThresholdDegrees=25.0,
                flDeltaTimeSeconds=flDt,
                flCurrentMotorAngleDegrees=flMotorAngle,
            )

        # Verify still returning (not yet at home from 20 deg away)
        assert obController.get_state() == HomeReturnState.RETURNING_HOME, (
            f"Should still be RETURNING_HOME, got {obController.get_state()}"
        )

        # Person leaves safe zone
        eState = obController.update(
            flPersonAngleRelativeToHome=30.0,
            flSafeZoneThresholdDegrees=25.0,
            flDeltaTimeSeconds=flDt,
            flCurrentMotorAngleDegrees=flMotorAngle,
        )

        assert eState == HomeReturnState.TRACKING, (
            f"Expected TRACKING after exit, got {eState}"
        )

        # Profiler velocity should NOT be zero (preserved for smooth transition)
        flProfilerVelocity = obController._obReturnProfiler.get_current_velocity()
        assert flProfilerVelocity != 0.0, (
            "Profiler velocity should not be reset on cancel (Pitfall 3)"
        )

    def test_at_home_locks_output(self):
        """In AT_HOME state, get_target_angle() == 0.0."""
        obController = HomeReturnController(
            flDelaySeconds=0.1,
            flReturnMaxVelocity=10.0,
            flReturnAccelTime=0.3,
            flReturnDecelTime=0.3,
        )

        flDt = 1.0 / 30.0

        # Drive to AT_HOME: enter safe zone -> delay -> return -> arrive
        for _ in range(10):
            obController.update(
                flPersonAngleRelativeToHome=1.0,
                flSafeZoneThresholdDegrees=5.0,
                flDeltaTimeSeconds=flDt,
                flCurrentMotorAngleDegrees=5.0,
            )

        # Run return until AT_HOME
        for _ in range(300):
            eState = obController.update(
                flPersonAngleRelativeToHome=1.0,
                flSafeZoneThresholdDegrees=5.0,
                flDeltaTimeSeconds=flDt,
                flCurrentMotorAngleDegrees=obController.get_target_angle(),
            )
            if eState == HomeReturnState.AT_HOME:
                break

        assert obController.get_state() == HomeReturnState.AT_HOME, (
            f"Expected AT_HOME, got {obController.get_state()}"
        )

        # Regardless of jitter in person angle, target stays at 0.0
        for _ in range(10):
            obController.update(
                flPersonAngleRelativeToHome=0.5,  # slight jitter
                flSafeZoneThresholdDegrees=5.0,
                flDeltaTimeSeconds=flDt,
                flCurrentMotorAngleDegrees=0.0,
            )

        assert obController.get_target_angle() == 0.0, (
            f"AT_HOME target should be 0.0, got {obController.get_target_angle()}"
        )

    def test_at_home_to_tracking_on_exit(self):
        """Person leaves safe zone from AT_HOME -> state goes to TRACKING."""
        obController = HomeReturnController(
            flDelaySeconds=0.1,
            flReturnMaxVelocity=10.0,
            flReturnAccelTime=0.3,
            flReturnDecelTime=0.3,
        )

        flDt = 1.0 / 30.0

        # Drive to AT_HOME
        for _ in range(10):
            obController.update(
                flPersonAngleRelativeToHome=1.0,
                flSafeZoneThresholdDegrees=5.0,
                flDeltaTimeSeconds=flDt,
                flCurrentMotorAngleDegrees=5.0,
            )

        for _ in range(300):
            eState = obController.update(
                flPersonAngleRelativeToHome=1.0,
                flSafeZoneThresholdDegrees=5.0,
                flDeltaTimeSeconds=flDt,
                flCurrentMotorAngleDegrees=obController.get_target_angle(),
            )
            if eState == HomeReturnState.AT_HOME:
                break

        assert obController.get_state() == HomeReturnState.AT_HOME

        # Person leaves safe zone
        eState = obController.update(
            flPersonAngleRelativeToHome=10.0,
            flSafeZoneThresholdDegrees=5.0,
            flDeltaTimeSeconds=flDt,
            flCurrentMotorAngleDegrees=0.0,
        )

        assert eState == HomeReturnState.TRACKING, (
            f"Expected TRACKING after leaving safe zone from AT_HOME, got {eState}"
        )

    def test_no_overshoot_past_zero(self):
        """RETURNING_HOME from positive angle.
        get_target_angle() should never go negative (no overshoot past home)."""
        obController = HomeReturnController(
            flDelaySeconds=0.1,
            flReturnMaxVelocity=10.0,
            flReturnAccelTime=0.3,
            flReturnDecelTime=0.3,
        )

        flDt = 1.0 / 30.0
        flMotorAngle = 5.0

        # Enter safe zone and pass through delay
        for _ in range(10):
            obController.update(
                flPersonAngleRelativeToHome=1.0,
                flSafeZoneThresholdDegrees=5.0,
                flDeltaTimeSeconds=flDt,
                flCurrentMotorAngleDegrees=flMotorAngle,
            )

        assert obController.get_state() == HomeReturnState.RETURNING_HOME

        # Track all target angles during return
        vTargetAngles = []
        for _ in range(300):
            flCurrentAngle = obController.get_target_angle()
            obController.update(
                flPersonAngleRelativeToHome=1.0,
                flSafeZoneThresholdDegrees=5.0,
                flDeltaTimeSeconds=flDt,
                flCurrentMotorAngleDegrees=flCurrentAngle,
            )
            vTargetAngles.append(obController.get_target_angle())

        # Starting from positive angle, target should never go negative
        for i, flAngle in enumerate(vTargetAngles):
            assert flAngle >= -0.001, (
                f"Target angle went negative at step {i}: {flAngle:.6f} (overshoot)"
            )
