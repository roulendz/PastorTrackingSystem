"""
home_return_controller.py - Home return state machine with S-curve motion

Manages the camera's return-to-home behavior when the pastor enters the
safe zone. Uses a MotionProfiler for smooth S-curve return motion.

State machine:
  TRACKING -> SAFE_ZONE_DELAY -> RETURNING_HOME -> AT_HOME
  (with cancellation paths back to TRACKING at each stage)

Key behaviors per CONTEXT.md:
- Short delay before starting return (confirms pastor is staying)
- S-curve return motion with zero overshoot at home
- Immediate cancellation on safe zone exit (preserves profiler velocity)
- Faster re-engagement when leaving AT_HOME state

Follows Hungarian notation per CLAUDE.md.
"""

import math
from enum import Enum

from control.motion_profiler import MotionProfiler


class HomeReturnState(Enum):
    """States of the home return state machine."""
    TRACKING = "TRACKING"
    SAFE_ZONE_DELAY = "SAFE_ZONE_DELAY"
    RETURNING_HOME = "RETURNING_HOME"
    AT_HOME = "AT_HOME"


class HomeReturnController:
    """
    Home return state machine with S-curve return motion.

    Transitions:
    - TRACKING -> SAFE_ZONE_DELAY: Person enters safe zone
    - SAFE_ZONE_DELAY -> RETURNING_HOME: Delay timer expires
    - SAFE_ZONE_DELAY -> TRACKING: Person leaves safe zone (cancel)
    - RETURNING_HOME -> TRACKING: Person leaves safe zone (cancel, keep velocity)
    - RETURNING_HOME -> AT_HOME: Motor reaches home (angle ~0, velocity ~0)
    - AT_HOME -> TRACKING: Person leaves safe zone (fast re-engagement)

    The delay `flDelaySeconds` is a public attribute for runtime tuning.
    """

    def __init__(
        self,
        flDelaySeconds: float = 1.5,
        flReturnMaxVelocity: float = 10.0,
        flReturnAccelTime: float = 0.5,
        flReturnDecelTime: float = 0.5,
        flReengagementAccelTime: float = 0.3,
    ):
        """
        Initialize the home return controller.

        Args:
            flDelaySeconds: Delay before starting return (seconds)
            flReturnMaxVelocity: Max velocity during return (deg/s)
            flReturnAccelTime: Acceleration time for return profiler (seconds)
            flReturnDecelTime: Deceleration time for return profiler (seconds)
            flReengagementAccelTime: Faster accel time when leaving AT_HOME
        """
        self.flDelaySeconds = flDelaySeconds
        self._flReturnMaxVelocity = flReturnMaxVelocity
        self._flNormalAccelTime = flReturnAccelTime
        self._flNormalDecelTime = flReturnDecelTime
        self._flReengagementAccelTime = flReengagementAccelTime

        self._eState = HomeReturnState.TRACKING
        self._flDelayTimer: float = 0.0
        self._obReturnProfiler = MotionProfiler(
            flMaxVelocityDegreesPerSecond=flReturnMaxVelocity,
            flAccelerationTimeSeconds=flReturnAccelTime,
            flDecelerationTimeSeconds=flReturnDecelTime,
        )
        self._flReturnTargetAngle: float = 0.0
        self._bJustLeftHome: bool = False

    def update(
        self,
        flPersonAngleRelativeToHome: float,
        flSafeZoneThresholdDegrees: float,
        flDeltaTimeSeconds: float,
        flCurrentMotorAngleDegrees: float,
    ) -> HomeReturnState:
        """
        Update the home return state machine.

        Args:
            flPersonAngleRelativeToHome: Person's angle relative to home (degrees)
            flSafeZoneThresholdDegrees: Safe zone threshold (degrees)
            flDeltaTimeSeconds: Time step (seconds)
            flCurrentMotorAngleDegrees: Current motor angle (degrees)

        Returns:
            Current state after update
        """
        bInSafeZone = abs(flPersonAngleRelativeToHome) <= flSafeZoneThresholdDegrees

        if self._eState == HomeReturnState.TRACKING:
            # Handle post-AT_HOME re-engagement: restore normal accel time
            if self._bJustLeftHome:
                self._obReturnProfiler.flAccelerationTimeSeconds = self._flNormalAccelTime
                self._bJustLeftHome = False

            if bInSafeZone:
                self._eState = HomeReturnState.SAFE_ZONE_DELAY
                self._flDelayTimer = 0.0

        elif self._eState == HomeReturnState.SAFE_ZONE_DELAY:
            if not bInSafeZone:
                # Person left safe zone -- cancel delay
                self._eState = HomeReturnState.TRACKING
            else:
                self._flDelayTimer += flDeltaTimeSeconds
                if self._flDelayTimer >= self.flDelaySeconds:
                    # Timer expired -- start returning
                    self._eState = HomeReturnState.RETURNING_HOME
                    self._flReturnTargetAngle = flCurrentMotorAngleDegrees
                    self._obReturnProfiler.reset()

        elif self._eState == HomeReturnState.RETURNING_HOME:
            if not bInSafeZone:
                # Person left safe zone -- cancel return
                # CRITICAL: Do NOT reset profiler velocity (Pitfall 3)
                self._eState = HomeReturnState.TRACKING
            else:
                # Compute desired velocity toward home
                flDistanceToHome = 0.0 - self._flReturnTargetAngle

                if abs(flDistanceToHome) > 0.001:
                    flDirection = math.copysign(1.0, flDistanceToHome)
                    flDesiredVelocity = flDirection * self._flReturnMaxVelocity
                else:
                    flDesiredVelocity = 0.0

                # Pass through profiler for S-curve shaping
                flSmoothedVelocity = self._obReturnProfiler.compute_smoothed_velocity(
                    flDesiredVelocity, flDeltaTimeSeconds
                )

                # Remember sign before integration for overshoot detection
                flPreviousTarget = self._flReturnTargetAngle

                # Integrate position
                self._flReturnTargetAngle += flSmoothedVelocity * flDeltaTimeSeconds

                # Clamp to prevent overshoot past home (zero overshoot per CONTEXT.md)
                # If the sign of the target flipped across zero, we overshot
                bOvershot = False
                if flPreviousTarget > 0.0 and self._flReturnTargetAngle < 0.0:
                    bOvershot = True
                elif flPreviousTarget < 0.0 and self._flReturnTargetAngle > 0.0:
                    bOvershot = True

                if bOvershot:
                    self._flReturnTargetAngle = 0.0
                    # Reset profiler velocity to prevent oscillation around zero
                    self._obReturnProfiler.reset()

                # Check if we've arrived at home
                flProfilerVelocity = self._obReturnProfiler.get_current_velocity()
                if (
                    abs(self._flReturnTargetAngle) < 0.01
                    and abs(flProfilerVelocity) < 0.01
                ):
                    self._eState = HomeReturnState.AT_HOME
                    self._flReturnTargetAngle = 0.0

        elif self._eState == HomeReturnState.AT_HOME:
            if not bInSafeZone:
                # Person left safe zone -- switch to tracking with faster re-engagement
                self._eState = HomeReturnState.TRACKING
                self._obReturnProfiler.flAccelerationTimeSeconds = self._flReengagementAccelTime
                self._bJustLeftHome = True

        return self._eState

    def get_target_angle(self) -> float:
        """
        Get the current target angle for the motor.

        When RETURNING_HOME, returns the S-curve-profiled angle gradually
        approaching 0.0. When AT_HOME, returns 0.0.

        Returns:
            Target angle in degrees
        """
        if self._eState == HomeReturnState.AT_HOME:
            return 0.0
        if self._eState == HomeReturnState.RETURNING_HOME:
            return self._flReturnTargetAngle
        return self._flReturnTargetAngle

    def get_state(self) -> HomeReturnState:
        """
        Get the current state of the state machine.

        Returns:
            Current HomeReturnState
        """
        return self._eState

    def reset(self):
        """Reset to TRACKING state, clear timer and profiler."""
        self._eState = HomeReturnState.TRACKING
        self._flDelayTimer = 0.0
        self._obReturnProfiler.reset()
        self._flReturnTargetAngle = 0.0
        self._bJustLeftHome = False
