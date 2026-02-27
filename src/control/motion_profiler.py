"""
motion_profiler.py - S-curve velocity profiler for smooth motor commands

Implements a velocity-domain S-curve profiler that rate-limits velocity
changes with exponential approach. This produces S-curve-like acceleration
and deceleration without a full trajectory planner.

Per research: "The velocity-domain approach is simpler and adequate for
camera panning."

Key behavior:
- Direction reversals are handled naturally -- the profiler decelerates
  through zero (using decel time constant) then accelerates in the new
  direction. No special-case code needed.
- Dead zone prevents oscillation around zero desired velocity.

Follows Hungarian notation per CLAUDE.md.
"""

import math


class MotionProfiler:
    """
    Velocity-domain S-curve profiler for smooth camera motion.

    Rate-limits velocity changes using exponential approach with separate
    acceleration and deceleration time constants. This produces smooth
    ease-in/ease-out behavior without a full trajectory planner.

    Parameters flAccelerationTimeSeconds and flDecelerationTimeSeconds are
    public attributes for runtime tuning via the settings panel.
    """

    def __init__(
        self,
        flMaxVelocityDegreesPerSecond: float = 30.0,
        flAccelerationTimeSeconds: float = 0.4,
        flDecelerationTimeSeconds: float = 0.5,
    ):
        """
        Initialize the motion profiler.

        Args:
            flMaxVelocityDegreesPerSecond: Maximum allowed velocity (deg/s)
            flAccelerationTimeSeconds: Time constant for acceleration (seconds)
            flDecelerationTimeSeconds: Time constant for deceleration (seconds)
        """
        self._flMaxVelocityDegreesPerSecond = flMaxVelocityDegreesPerSecond
        self.flAccelerationTimeSeconds = flAccelerationTimeSeconds
        self.flDecelerationTimeSeconds = flDecelerationTimeSeconds
        self._flCurrentVelocity: float = 0.0

    def compute_smoothed_velocity(
        self, flDesiredVelocity: float, flDeltaTimeSeconds: float
    ) -> float:
        """
        Apply S-curve smoothing to transition from current velocity toward desired.

        The method uses exponential approach with separate time constants for
        acceleration and deceleration. Direction reversals are handled naturally
        by treating the sign-change case as deceleration.

        Args:
            flDesiredVelocity: Target velocity in degrees/second
            flDeltaTimeSeconds: Time step in seconds

        Returns:
            Smoothed velocity in degrees/second
        """
        # Step 1: Compute delta
        flDelta = flDesiredVelocity - self._flCurrentVelocity

        # Dead zone to prevent oscillation around target
        if abs(flDelta) < 0.001:
            return self._flCurrentVelocity

        # Step 2: Determine if decelerating
        # Decelerating if: moving toward zero, or sign change (opposite signs)
        bDecelerating = False
        if abs(flDesiredVelocity) < abs(self._flCurrentVelocity):
            bDecelerating = True
        elif (
            self._flCurrentVelocity != 0.0
            and flDesiredVelocity != 0.0
            and math.copysign(1.0, flDesiredVelocity) != math.copysign(1.0, self._flCurrentVelocity)
        ):
            bDecelerating = True

        # Step 3: Select time constant
        flTimeConstant = (
            self.flDecelerationTimeSeconds if bDecelerating
            else self.flAccelerationTimeSeconds
        )

        # Step 4: Compute alpha using exponential approach
        # alpha = 1 - exp(-dt / (timeConstant * 0.2))
        # The 0.2 factor converts the "time to reach target" into a time constant
        flTau = flTimeConstant * 0.2
        if flTau <= 0.0:
            flTau = 0.001  # safety minimum
        flAlpha = 1.0 - math.exp(-flDeltaTimeSeconds / flTau)

        # Step 5: Update velocity
        self._flCurrentVelocity += flAlpha * flDelta

        return self._flCurrentVelocity

    def reset(self):
        """Reset current velocity to zero."""
        self._flCurrentVelocity = 0.0

    def get_current_velocity(self) -> float:
        """
        Get the current smoothed velocity.

        Returns:
            Current velocity in degrees/second
        """
        return self._flCurrentVelocity
