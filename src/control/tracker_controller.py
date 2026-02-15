"""
tracker_controller.py - Main orchestration for tracking system

This module ties everything together: camera, motor, pose detection,
FOV configuration, and control.

Follows:
- SRP: Orchestrates but doesn't implement low-level logic
- All business logic delegated to specialized modules
"""

import time
from enum import Enum
from typing import Optional
import logging

from core.tracking_sample import TrackingSample
from interfaces.motor_interface import MotorInterface
from interfaces.camera_interface import CameraInterface
from tracking.pose_tracker import PoseTracker
from control.control_algorithm import ControlAlgorithm

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
    """
    
    def __init__(
        self,
        obMotorInterface: MotorInterface,
        obCameraInterface: CameraInterface,
        obPoseTracker: PoseTracker,
        obControlAlgorithm: ControlAlgorithm
    ):
        """
        Initialize tracker controller.
        
        Args:
            obMotorInterface: Motor communication interface
            obCameraInterface: Camera capture interface
            obPoseTracker: Pose detection module
            obControlAlgorithm: Control algorithm (P/PID)
        """
        # Store references to injected dependencies
        self.obMotorInterface = obMotorInterface
        self.obCameraInterface = obCameraInterface
        self.obPoseTracker = obPoseTracker
        self.obControlAlgorithm = obControlAlgorithm
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
        
        # Statistics
        self.iFramesProcessedCount = 0
        self.dStartTime = time.time()
        self._flLastCommandedAngle = None
        self.flCommandMinDeltaDegrees = 0.05
        self._dPreviousControlTimestampSeconds = None
        
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
            except Exception:
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
        dElapsedTime = time.time() - self.dStartTime
        flAverageFPS = self.iFramesProcessedCount / dElapsedTime if dElapsedTime > 0 else 0.0
        
        return {
            'state': self.eCurrentState.value,
            'frames_processed': self.iFramesProcessedCount,
            'average_fps': flAverageFPS,
            'motor_angle': self.get_current_motor_angle_degrees(),
            'fov_degrees': self.get_current_field_of_view_degrees(),
            'motor_speed': self.get_current_motor_speed_steps_per_second()
        }
    
    # Private methods
    
    def _execute_centering_control_algorithm(self, obCurrentSample: TrackingSample):
        """
        Execute control algorithm to center person.
        
        Args:
            obCurrentSample: Current tracking sample
        """
        # Skip if no person detected
        if not obCurrentSample.is_valid_for_tracking():
            # Could implement search pattern here
            return
        
        # Calculate pixel offset from center
        iImageWidth, _ = self.obCameraInterface.get_frame_dimensions()
        flPixelOffset = obCurrentSample.get_pixel_offset_from_center(iImageWidth)
        
        # Convert to angle error relative to camera center
        flAnglePerPixel = self._get_angle_per_pixel_degrees(iImageWidth)
        flAngleError = flPixelOffset * flAnglePerPixel

        flPersonAngleRelativeToHome = obCurrentSample.flMotorAngleDegrees + flAngleError
        dNow = float(obCurrentSample.dSampleTimestampSeconds)
        if self._dPreviousControlTimestampSeconds is None:
            dDeltaTime = 0.0
        else:
            dDeltaTime = dNow - float(self._dPreviousControlTimestampSeconds)
        self._dPreviousControlTimestampSeconds = dNow
        if dDeltaTime <= 0.0 or dDeltaTime > 0.5:
            dDeltaTime = 1.0 / 30.0

        if abs(flPersonAngleRelativeToHome) <= self.flDeadbandDegrees:
            flNewTargetAngle = self._compute_home_return_target_angle(
                obCurrentSample.flMotorAngleDegrees,
                dDeltaTime
            )
        else:
            flCorrection = self.obControlAlgorithm.calculate_correction_from_error(flAngleError)
            flNewTargetAngle = obCurrentSample.flMotorAngleDegrees + flCorrection
        
        # Clamp to safety limits
        flNewTargetAngle = max(
            self.flMinimumMotorAngleDegrees,
            min(self.flMaximumMotorAngleDegrees, flNewTargetAngle)
        )
        flCommandAngle = flNewTargetAngle

        if self._flLastCommandedAngle is not None and abs(flCommandAngle - self._flLastCommandedAngle) < self.flCommandMinDeltaDegrees:
            return
        bSuccess = self.obMotorInterface.send_move_to_angle_command(flCommandAngle)
        
        if not bSuccess:
            logger.error("Failed to send motor command")
        else:
            self._flLastCommandedAngle = flCommandAngle

    def _compute_home_return_target_angle(self, flCurrentMotorAngleDegrees: float, dDeltaTimeSeconds: float) -> float:
        flMaxVel = 10.0
        if self.obConfig is not None:
            try:
                flMaxVel = float(getattr(self.obConfig, 'flHomeReturnMaxVelocityDegreesPerSecond', flMaxVel))
            except Exception:
                pass
            try:
                if hasattr(self.obConfig, 'flMaxVelocityDegreesPerSecond'):
                    flMaxVel = min(flMaxVel, float(getattr(self.obConfig, 'flMaxVelocityDegreesPerSecond')))
            except Exception:
                pass

        flMaxDelta = abs(flMaxVel) * max(0.0, float(dDeltaTimeSeconds))
        flDesiredDelta = 0.0 - float(flCurrentMotorAngleDegrees)
        if flDesiredDelta > flMaxDelta:
            flDesiredDelta = flMaxDelta
        elif flDesiredDelta < -flMaxDelta:
            flDesiredDelta = -flMaxDelta
        return float(flCurrentMotorAngleDegrees) + flDesiredDelta

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
