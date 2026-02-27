"""
tracker_controller.py - Main orchestration for tracking system

This module ties everything together: camera, motor, pose detection,
FOV configuration, control, and motion smoothing.

Follows:
- SRP: Orchestrates but doesn't implement low-level logic
- All business logic delegated to specialized modules

Phase 3 additions:
- OneEuroFilter on pose input for jitter suppression (MOTN-03)
- Confidence-based gain scaling on control output (MOTN-04)
- S-curve velocity profiling on motor commands (MOTN-01)
- HomeReturnController state machine for safe zone behavior (MOTN-02)
"""

from enum import Enum
from typing import Optional
import logging

from core.tracking_sample import TrackingSample
from interfaces.motor_interface import MotorInterface
from interfaces.camera_interface import CameraInterface
from tracking.pose_tracker import PoseTracker
from control.control_algorithm import ControlAlgorithm
from utilities.clock import Clock, RealClock

# Phase 3: Motion smoothing imports
from tracking.pose_filter import OneEuroFilter, compute_confidence_scale_factor
from control.motion_profiler import MotionProfiler
from control.home_return_controller import HomeReturnController, HomeReturnState

logger = logging.getLogger(__name__)


class TrackerState(Enum):
    """States for the tracking system."""
    IDLE = "idle"
    TRACKING = "tracking"
    ERROR = "error"


class TrackerController:
    """
    Main controller that orchestrates all tracking components.

    Responsibilities:
    - Run main tracking loop at camera frame rate (e.g., 30 FPS).
    - Build TrackingSamples from camera + motor
    - Calculate and send control corrections
    - Manage system state
    - Apply motion smoothing pipeline (Phase 3):
      OneEuroFilter -> Control Algorithm -> Confidence Scaling -> S-curve Profiling
    """

    def __init__(
        self,
        obMotorInterface: MotorInterface,
        obCameraInterface: CameraInterface,
        obPoseTracker: PoseTracker,
        obControlAlgorithm: ControlAlgorithm,
        obClock: Optional[Clock] = None
    ):
        """
        Initialize tracker controller.

        Args:
            obMotorInterface: Motor communication interface
            obCameraInterface: Camera capture interface
            obPoseTracker: Pose detection module
            obControlAlgorithm: Control algorithm (P/PID)
            obClock: Injected clock for timestamps (defaults to RealClock)
        """
        # Store references to injected dependencies
        self.obMotorInterface = obMotorInterface
        self.obCameraInterface = obCameraInterface
        self.obPoseTracker = obPoseTracker
        self.obControlAlgorithm = obControlAlgorithm
        self._obClock = obClock or RealClock()
        self.obConfig = None

        # State
        self.eCurrentState = TrackerState.IDLE
        self.iNextSampleSequenceNumber = 0

        # Configuration
        self.flDeadbandDegrees = 0.3
        self.flDeadbandMinDegrees = 0.01
        self.flDeadbandMaxDegrees = 20.0
        self.flMinimumMotorAngleDegrees = -90.0
        self.flMaximumMotorAngleDegrees = 90.0
        self.flMinimumTrackingConfidenceForControl = 0.5

        # Timing: dt clamp bounds (overridden by config via apply_configuration)
        self.flDtMaxSeconds = 0.066
        self.flDtMinSeconds = 0.001

        # Statistics
        self.iFramesProcessedCount = 0
        self.dStartTime = self._obClock.get_time_seconds()
        self._flLastCommandedAngle = None
        self.flCommandMinDeltaDegrees = 0.05
        self._dPreviousControlTimestampSeconds = None

        # Motion smoothing modules (Phase 3)
        self._obPoseFilter: Optional[OneEuroFilter] = None  # Initialized on first frame
        self._obMotionProfiler = MotionProfiler()
        self._obHomeReturnController = HomeReturnController()

        # Confidence tracking (Phase 3)
        self.flConfidenceHoldThreshold = 0.3
        self.flConfidenceFullThreshold = 0.7
        self.flConfidenceLowTimeoutSeconds = 5.0
        self._flLowConfidenceTimer = 0.0

        logger.info("TrackerController initialized")

    def execute_main_tracking_loop_tick(self) -> Optional[TrackingSample]:
        """
        Execute one iteration of the main tracking loop.

        This runs at camera frame rate (e.g., 30 FPS).

        Returns:
            The TrackingSample created this tick, or None if failed
        """
        try:
            # STEP 1: Capture camera frame with timestamp
            obFrameImage, dFrameTimestamp = self.obCameraInterface.capture_frame_with_timestamp()

            if obFrameImage is None:
                logger.warning("Failed to capture frame")
                return None

            # STEP 2: Get latest motor state (snapshot at this instant)
            obMotorState = self.obMotorInterface.get_latest_motor_state()
            try:
                flEstimatedAngle = float(self.obMotorInterface.get_estimated_motor_angle_degrees(dFrameTimestamp))
            except (ValueError, TypeError, AttributeError) as e:
                logger.debug(f"Motor angle estimation fallback: {e}")
                flEstimatedAngle = obMotorState.flMotorAngleDegrees

            # STEP 3: Run pose detection
            obPoseResult = self.obPoseTracker.detect_person_in_frame(obFrameImage)

            # STEP 4: Build TrackingSample (atomic measurement)
            obNewSample = TrackingSample(
                dSampleTimestampSeconds=dFrameTimestamp,
                flMotorAngleDegrees=flEstimatedAngle,
                flPersonCenterXPixels=obPoseResult.flPersonCenterXPixels,
                flPersonCenterYPixels=obPoseResult.flPersonCenterYPixels,
                bPersonWasDetected=obPoseResult.bPersonWasDetected,
                iSampleSequenceNumber=self.iNextSampleSequenceNumber,
                flPersonConfidenceScore=obPoseResult.flPersonConfidenceScore,
                flMinimumConfidenceRequired=self.flMinimumTrackingConfidenceForControl,
                obFrameImage=obFrameImage,
                obPoseLandmarks=obPoseResult.vLandmarks
            )
            self.iNextSampleSequenceNumber += 1

            # STEP 5: Execute control (if tracking)
            if self.eCurrentState == TrackerState.TRACKING:
                self._execute_centering_control_algorithm(obNewSample)

            # Update statistics
            self.iFramesProcessedCount += 1

            return obNewSample

        except Exception as e:
            logger.error(f"Error in main loop: {e}", exc_info=True)
            self.eCurrentState = TrackerState.ERROR
            return None

    def start_tracking_mode(self):
        """Enable tracking mode."""
        self.eCurrentState = TrackerState.TRACKING
        self.obControlAlgorithm.reset_controller()
        # Reset Phase 3 module state
        self._obMotionProfiler.reset()
        self._obHomeReturnController.reset()
        self._obPoseFilter = None  # Re-initialize on first frame
        self._flLowConfidenceTimer = 0.0
        logger.info("Tracking mode STARTED")

    def stop_tracking_mode(self):
        """Disable tracking mode."""
        self.eCurrentState = TrackerState.IDLE
        flAngle = self.get_current_motor_angle_degrees()
        self.obMotorInterface.send_move_to_angle_command(flAngle)
        logger.info("Tracking mode STOPPED")

    def is_currently_tracking(self) -> bool:
        """Check if actively tracking."""
        return self.eCurrentState == TrackerState.TRACKING

    def get_current_field_of_view_degrees(self) -> float:
        """Get current configured FOV (degrees)."""
        iWidth, _ = self.obCameraInterface.get_frame_dimensions()
        flFovDegrees = float(getattr(self.obConfig, 'flFieldOfViewDegrees', 0.0)) if self.obConfig is not None else 0.0
        if flFovDegrees > 0.0:
            return flFovDegrees
        return self._get_angle_per_pixel_degrees(iWidth) * float(iWidth)

    def get_current_motor_angle_degrees(self) -> float:
        """Get latest motor angle."""
        obState = self.obMotorInterface.get_latest_motor_state()
        return obState.flMotorAngleDegrees

    def get_current_motor_speed_steps_per_second(self) -> float:
        """Get latest motor speed (steps/s)."""
        obState = self.obMotorInterface.get_latest_motor_state()
        return obState.flMotorSpeedStepsPerSecond

    def get_angle_per_pixel_degrees(self, iImageWidthPixels: int) -> float:
        return self._get_angle_per_pixel_degrees(iImageWidthPixels)


    def get_system_statistics(self) -> dict:
        """
        Get comprehensive system statistics.

        Returns:
            Dictionary with statistics
        """
        dElapsedTime = self._obClock.get_time_seconds() - self.dStartTime
        flAverageFPS = self.iFramesProcessedCount / dElapsedTime if dElapsedTime > 0 else 0.0

        return {
            'state': self.eCurrentState.value,
            'frames_processed': self.iFramesProcessedCount,
            'average_fps': flAverageFPS,
            'motor_angle': self.get_current_motor_angle_degrees(),
            'fov_degrees': self.get_current_field_of_view_degrees(),
            'motor_speed': self.get_current_motor_speed_steps_per_second()
        }

    # Phase 3 public properties for DearPyGui access

    @property
    def obPoseFilter(self) -> Optional[OneEuroFilter]:
        """Get the pose filter instance (may be None before first frame)."""
        return self._obPoseFilter

    @property
    def obMotionProfiler(self) -> MotionProfiler:
        """Get the motion profiler instance."""
        return self._obMotionProfiler

    @property
    def obHomeReturnController(self) -> HomeReturnController:
        """Get the home return controller instance."""
        return self._obHomeReturnController

    # Private methods

    def _execute_centering_control_algorithm(self, obCurrentSample: TrackingSample):
        """
        Execute control algorithm to center person with full motion smoothing pipeline.

        Pipeline order (Phase 3):
        1. Skip if no person detected
        2. Compute dt (preserved from Phase 2)
        3. Confidence gate with low-confidence timer (MOTN-04)
        4. Filter pose input with OneEuroFilter (MOTN-03)
        5. Convert to angle error
        6. Home return state machine (MOTN-02)
        7. Control algorithm + confidence scaling
        8. S-curve velocity profiling (MOTN-01)
        9. Clamp and send motor command

        Args:
            obCurrentSample: Current tracking sample
        """
        # STEP 1: Skip if no person detected
        # Phase 4 will add hold logic here
        if not obCurrentSample.bPersonWasDetected:
            return

        # STEP 2: Compute delta time from absolute timestamps
        # (preserved verbatim from Phase 2 -- dt computed at pipeline boundary)
        dNow = float(obCurrentSample.dSampleTimestampSeconds)
        if self._dPreviousControlTimestampSeconds is None:
            # First frame: use nominal frame interval, do not skip
            flDeltaTimeSeconds = 1.0 / 30.0
            self._dPreviousControlTimestampSeconds = dNow
        else:
            flDeltaTimeSeconds = dNow - float(self._dPreviousControlTimestampSeconds)

        # dt clamping at pipeline boundary (per locked decisions)
        if flDeltaTimeSeconds > self.flDtMaxSeconds:
            # Dropped frame: skip control update entirely
            # Per locked decision: motor telemetry still recorded in history buffer,
            # but control update skipped for that cycle.
            # Update timestamp so next frame gets a fresh dt (prevent cascade skip)
            logger.debug(
                f"Control update skipped: dt={flDeltaTimeSeconds:.4f}s exceeds "
                f"max={self.flDtMaxSeconds:.4f}s (dropped frame)"
            )
            self._dPreviousControlTimestampSeconds = dNow
            return
        if flDeltaTimeSeconds < self.flDtMinSeconds:
            # Near-zero dt: clamp up to minimum to avoid division issues
            flDeltaTimeSeconds = self.flDtMinSeconds

        self._dPreviousControlTimestampSeconds = dNow

        # STEP 3: Confidence gate (MOTN-04)
        flConfidenceScale = compute_confidence_scale_factor(
            obCurrentSample.flPersonConfidenceScore,
            self.flConfidenceHoldThreshold,
            self.flConfidenceFullThreshold
        )
        if flConfidenceScale <= 0.0:
            # Below hold threshold: freeze camera at current position
            # Accumulate low-confidence timer -- sustained low confidence triggers home return
            # (Per CONTEXT.md: "treat it like soft detection loss -- hold for timeout, then S-curve home")
            self._flLowConfidenceTimer += flDeltaTimeSeconds
            if self._flLowConfidenceTimer >= self.flConfidenceLowTimeoutSeconds:
                # Sustained low confidence exceeded timeout -- trigger home return
                # Feed the HomeReturnController as if person is in safe zone (angle 0.0)
                # so it transitions through SAFE_ZONE_DELAY -> RETURNING_HOME -> AT_HOME
                eHomeState = self._obHomeReturnController.update(
                    0.0,  # Treat as if person is at home (in safe zone)
                    self.flDeadbandDegrees,
                    flDeltaTimeSeconds,
                    obCurrentSample.flMotorAngleDegrees
                )
                if eHomeState == HomeReturnState.RETURNING_HOME:
                    flReturnTarget = self._obHomeReturnController.get_target_angle()
                    self._send_profiled_motor_command(
                        flReturnTarget,
                        obCurrentSample.flMotorAngleDegrees,
                        flDeltaTimeSeconds
                    )
                elif eHomeState == HomeReturnState.AT_HOME:
                    pass  # Camera locked at home -- output nothing
            return
        # Person detected with usable confidence -- reset low-confidence timer
        self._flLowConfidenceTimer = 0.0

        # STEP 4: Filter pose input (MOTN-03)
        iImageWidth, _ = self.obCameraInterface.get_frame_dimensions()

        # Lazy-initialize OneEuroFilter on first valid detection
        if self._obPoseFilter is None:
            self._obPoseFilter = OneEuroFilter(
                dInitialTimestamp=obCurrentSample.dSampleTimestampSeconds,
                flInitialValue=obCurrentSample.flPersonCenterXPixels,
                flMinCutoffHz=float(getattr(self.obConfig, 'flPoseFilterMinCutoffHz', 0.01)) if self.obConfig else 0.01,
                flBeta=float(getattr(self.obConfig, 'flPoseFilterBeta', 0.007)) if self.obConfig else 0.007,
                flDerivativeCutoffHz=float(getattr(self.obConfig, 'flPoseFilterDerivativeCutoffHz', 0.1)) if self.obConfig else 0.1,
            )

        flFilteredX = self._obPoseFilter.filter_value(
            obCurrentSample.dSampleTimestampSeconds,
            obCurrentSample.flPersonCenterXPixels
        )
        # Use filtered X instead of raw for pixel offset calculation
        flPixelOffset = flFilteredX - (iImageWidth / 2.0)

        # STEP 5: Convert to angle error relative to camera center
        flAnglePerPixel = self._get_angle_per_pixel_degrees(iImageWidth)
        flAngleError = flPixelOffset * flAnglePerPixel

        flPersonAngleRelativeToHome = obCurrentSample.flMotorAngleDegrees + flAngleError

        # STEP 6: Home return state machine (MOTN-02)
        eHomeState = self._obHomeReturnController.update(
            flPersonAngleRelativeToHome,
            self.flDeadbandDegrees,
            flDeltaTimeSeconds,
            obCurrentSample.flMotorAngleDegrees
        )
        if eHomeState == HomeReturnState.AT_HOME:
            # Camera locked at home -- output nothing
            return
        if eHomeState == HomeReturnState.RETURNING_HOME:
            flNewTargetAngle = self._obHomeReturnController.get_target_angle()
        else:
            # STEP 7: Normal tracking: control algorithm + confidence scaling
            flCorrection = self.obControlAlgorithm.calculate_correction_from_error(flAngleError, flDeltaTimeSeconds)
            flCorrection *= flConfidenceScale  # MOTN-04: Scale by confidence
            flNewTargetAngle = obCurrentSample.flMotorAngleDegrees + flCorrection

        # STEP 8-9: S-curve velocity profiling and send command
        self._send_profiled_motor_command(
            flNewTargetAngle,
            obCurrentSample.flMotorAngleDegrees,
            flDeltaTimeSeconds
        )

    def _send_profiled_motor_command(
        self,
        flTargetAngle: float,
        flCurrentAngle: float,
        flDeltaTimeSeconds: float
    ):
        """
        Apply S-curve velocity profiling, clamp to safety limits, and send motor command.

        Shared by both normal tracking path and low-confidence home return path
        to avoid logic duplication.

        Args:
            flTargetAngle: Desired target angle (degrees)
            flCurrentAngle: Current motor angle (degrees)
            flDeltaTimeSeconds: Time step (seconds)
        """
        # S-curve velocity profiling (MOTN-01)
        # Convert target angle to desired velocity for profiler
        flDesiredVelocity = (flTargetAngle - flCurrentAngle) / flDeltaTimeSeconds
        flSmoothedVelocity = self._obMotionProfiler.compute_smoothed_velocity(
            flDesiredVelocity, flDeltaTimeSeconds
        )
        flSmoothedTarget = flCurrentAngle + flSmoothedVelocity * flDeltaTimeSeconds

        # Clamp to safety limits
        flSmoothedTarget = max(
            self.flMinimumMotorAngleDegrees,
            min(self.flMaximumMotorAngleDegrees, flSmoothedTarget)
        )
        flCommandAngle = flSmoothedTarget

        # Check minimum command delta to avoid flooding motor with tiny commands
        if (self._flLastCommandedAngle is not None
                and abs(flCommandAngle - self._flLastCommandedAngle) < self.flCommandMinDeltaDegrees):
            return
        bSuccess = self.obMotorInterface.send_move_to_angle_command(flCommandAngle)

        if not bSuccess:
            logger.error("Failed to send motor command")
        else:
            self._flLastCommandedAngle = flCommandAngle

    def _get_angle_per_pixel_degrees(self, iImageWidthPixels: int) -> float:
        if iImageWidthPixels <= 0:
            return 0.0
        if self.obConfig is not None:
            flFovDegrees = float(getattr(self.obConfig, 'flFieldOfViewDegrees', 0.0))
            if flFovDegrees > 0.0:
                return flFovDegrees / float(iImageWidthPixels)
            flApx = float(getattr(self.obConfig, 'flInitialAnglePerPixelDegrees', 0.0))
            if flApx > 0.0:
                return flApx
        return 0.0

    # Public API for UI/config integration
    def set_deadband_degrees(self, flDegrees: float):
        self.flDeadbandDegrees = max(self.flDeadbandMinDegrees, min(self.flDeadbandMaxDegrees, float(flDegrees)))

    def get_deadband_degrees(self) -> float:
        return self.flDeadbandDegrees

    def set_angle_limits(self, flMinDegrees: float, flMaxDegrees: float):
        self.flMinimumMotorAngleDegrees = float(flMinDegrees)
        self.flMaximumMotorAngleDegrees = float(flMaxDegrees)

    def set_tracking_confidence_threshold(self, flThreshold: float):
        self.flMinimumTrackingConfidenceForControl = float(flThreshold)

    def set_control_algorithm(self, obAlgorithm: ControlAlgorithm):
        self.obControlAlgorithm = obAlgorithm

    def apply_configuration(self, obConfig):
        self.obConfig = obConfig
        self.flDeadbandDegrees = float(getattr(obConfig, 'flControlDeadbandDegrees', self.flDeadbandDegrees))
        self.flDeadbandMinDegrees = float(getattr(obConfig, 'flDeadbandMinDegrees', self.flDeadbandMinDegrees))
        self.flDeadbandMaxDegrees = float(getattr(obConfig, 'flDeadbandMaxDegrees', self.flDeadbandMaxDegrees))
        self.flMinimumMotorAngleDegrees = float(getattr(obConfig, 'flMotorMinAngleDegrees', self.flMinimumMotorAngleDegrees))
        self.flMaximumMotorAngleDegrees = float(getattr(obConfig, 'flMotorMaxAngleDegrees', self.flMaximumMotorAngleDegrees))
        self.flMinimumTrackingConfidenceForControl = float(getattr(obConfig, 'flTrackingMinConfidenceForControl', self.flMinimumTrackingConfidenceForControl))
        self.iCenterDeadzoneRadiusPixels = int(getattr(obConfig, 'iCenterDeadzoneRadiusPixels', getattr(self, 'iCenterDeadzoneRadiusPixels', 0)))
        self.flDtMaxSeconds = float(getattr(obConfig, 'flDtMaxSeconds', self.flDtMaxSeconds))
        self.flDtMinSeconds = float(getattr(obConfig, 'flDtMinSeconds', self.flDtMinSeconds))

        # Motion profiler config (Phase 3)
        self._obMotionProfiler.flAccelerationTimeSeconds = float(getattr(obConfig, 'flMotionAccelerationTimeSeconds', 0.4))
        self._obMotionProfiler.flDecelerationTimeSeconds = float(getattr(obConfig, 'flMotionDecelerationTimeSeconds', 0.5))

        # Home return config (Phase 3)
        self._obHomeReturnController.flDelaySeconds = float(getattr(obConfig, 'flHomeReturnDelaySeconds', 1.5))

        # Confidence config (Phase 3)
        self.flConfidenceHoldThreshold = float(getattr(obConfig, 'flConfidenceHoldThreshold', 0.3))
        self.flConfidenceFullThreshold = float(getattr(obConfig, 'flConfidenceFullThreshold', 0.7))
        self.flConfidenceLowTimeoutSeconds = float(getattr(obConfig, 'flConfidenceLowTimeoutSeconds', 5.0))
