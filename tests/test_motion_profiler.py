"""
test_motion_profiler.py - Tests for MotionProfiler S-curve velocity smoothing

Verifies:
- Velocity ramps smoothly from zero (no instant jump)
- Velocity decelerates smoothly to zero
- Direction reversal passes through zero velocity
- Reset zeros velocity
- Dead zone prevents oscillation
"""

import pytest

from control.motion_profiler import MotionProfiler


class TestMotionProfiler:
    """Tests for the MotionProfiler velocity smoothing."""

    def test_velocity_ramps_smoothly_from_zero(self):
        """Start at 0, desire 20 deg/s, step at 1/30s for 60 frames.
        First frame velocity < desired (not instant jump).
        Final velocity approaches desired (within 5%)."""
        obProfiler = MotionProfiler(
            flMaxVelocityDegreesPerSecond=30.0,
            flAccelerationTimeSeconds=0.4,
            flDecelerationTimeSeconds=0.5,
        )

        flDt = 1.0 / 30.0
        flDesired = 20.0

        flFirstFrame = obProfiler.compute_smoothed_velocity(flDesired, flDt)
        assert flFirstFrame < flDesired, (
            f"First frame velocity {flFirstFrame} should be less than desired {flDesired}"
        )
        assert flFirstFrame > 0.0, (
            f"First frame velocity should be positive, got {flFirstFrame}"
        )

        # Run remaining frames
        flVelocity = flFirstFrame
        for _ in range(59):
            flVelocity = obProfiler.compute_smoothed_velocity(flDesired, flDt)

        # Final should be within 5% of desired
        flError = abs(flVelocity - flDesired) / flDesired
        assert flError < 0.05, (
            f"Final velocity {flVelocity:.4f} is {flError*100:.1f}% away from "
            f"desired {flDesired} (exceeds 5%)"
        )

    def test_velocity_decelerates_smoothly_to_zero(self):
        """Ramp up to 20 deg/s first (60 frames), then desire 0 for 60 frames.
        Velocity should decrease monotonically. Final velocity < 1.0 deg/s."""
        obProfiler = MotionProfiler(
            flMaxVelocityDegreesPerSecond=30.0,
            flAccelerationTimeSeconds=0.4,
            flDecelerationTimeSeconds=0.5,
        )

        flDt = 1.0 / 30.0

        # Ramp up
        for _ in range(60):
            obProfiler.compute_smoothed_velocity(20.0, flDt)

        # Decelerate
        vDecelVelocities = []
        for _ in range(60):
            flVelocity = obProfiler.compute_smoothed_velocity(0.0, flDt)
            vDecelVelocities.append(flVelocity)

        # Verify monotonic decrease
        for i in range(1, len(vDecelVelocities)):
            assert vDecelVelocities[i] <= vDecelVelocities[i - 1] + 0.001, (
                f"Velocity increased at frame {i}: {vDecelVelocities[i-1]:.4f} -> "
                f"{vDecelVelocities[i]:.4f}"
            )

        # Final velocity should be very small
        assert vDecelVelocities[-1] < 1.0, (
            f"Final velocity {vDecelVelocities[-1]:.4f} should be < 1.0 deg/s"
        )

    def test_direction_reversal_decelerates_through_zero(self):
        """Ramp to +20 deg/s, then desire -20 deg/s.
        Velocity should pass through zero (sign change).
        Max frame-to-frame change should be bounded."""
        obProfiler = MotionProfiler(
            flMaxVelocityDegreesPerSecond=30.0,
            flAccelerationTimeSeconds=0.4,
            flDecelerationTimeSeconds=0.5,
        )

        flDt = 1.0 / 30.0

        # Ramp up to +20
        for _ in range(60):
            obProfiler.compute_smoothed_velocity(20.0, flDt)

        # Now reverse to -20 and track velocities
        vReversalVelocities = []
        for _ in range(120):
            flVelocity = obProfiler.compute_smoothed_velocity(-20.0, flDt)
            vReversalVelocities.append(flVelocity)

        # Verify velocity passes through zero at some point
        bPassedThroughZero = False
        for i in range(1, len(vReversalVelocities)):
            if vReversalVelocities[i - 1] > 0 and vReversalVelocities[i] <= 0:
                bPassedThroughZero = True
                break
        assert bPassedThroughZero, "Velocity did not pass through zero during reversal"

        # Verify no instant jumps (bounded frame-to-frame change)
        flMaxChange = 0.0
        for i in range(1, len(vReversalVelocities)):
            flChange = abs(vReversalVelocities[i] - vReversalVelocities[i - 1])
            flMaxChange = max(flMaxChange, flChange)

        # Max change should be bounded -- with exponential approach, max change
        # in one frame should be << desired velocity
        assert flMaxChange < 10.0, (
            f"Max frame-to-frame velocity change {flMaxChange:.4f} is too large"
        )

    def test_reset_zeros_velocity(self):
        """Ramp up, call reset(), assert get_current_velocity() == 0.0."""
        obProfiler = MotionProfiler(
            flMaxVelocityDegreesPerSecond=30.0,
            flAccelerationTimeSeconds=0.4,
            flDecelerationTimeSeconds=0.5,
        )

        flDt = 1.0 / 30.0

        # Ramp up
        for _ in range(30):
            obProfiler.compute_smoothed_velocity(20.0, flDt)

        assert obProfiler.get_current_velocity() > 0.0, "Should have non-zero velocity"

        obProfiler.reset()
        assert obProfiler.get_current_velocity() == 0.0, (
            f"After reset, velocity should be 0.0, got {obProfiler.get_current_velocity()}"
        )

    def test_dead_zone_prevents_oscillation(self):
        """Desire 0.0005 deg/s (below 0.001 threshold).
        Should return current velocity unchanged."""
        obProfiler = MotionProfiler(
            flMaxVelocityDegreesPerSecond=30.0,
            flAccelerationTimeSeconds=0.4,
            flDecelerationTimeSeconds=0.5,
        )

        flDt = 1.0 / 30.0
        flCurrentBefore = obProfiler.get_current_velocity()  # 0.0

        flResult = obProfiler.compute_smoothed_velocity(0.0005, flDt)
        assert flResult == flCurrentBefore, (
            f"Dead zone should return current velocity {flCurrentBefore}, got {flResult}"
        )
