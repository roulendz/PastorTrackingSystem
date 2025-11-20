"""
tracker_controller.py - Main orchestration for tracking system

This module ties everything together: camera, motor, pose detection,
FOV learning, and control.

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
from tracking.fov_estimator import FieldOfViewEstimator
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
    - Run main tracking loop at camera frame rate
    - Build TrackingSamples from camera + motor
    - Update FOV estimator
    - Calculate and send control corrections
    - Manage system state
    """
    
    def __init__(
        self,
        obMotorInterface: MotorInterface,
        obCameraInterface: CameraInterface,
        obPoseTracker: PoseTracker,
        obFieldOfViewEstimator: FieldOfViewEstimator,
        obControlAlgorithm: ControlAlgorithm
    ):
        """
        Initialize tracker controller.
        
        Args:
            obMotorInterface: Motor communication interface
            obCameraInterface: Camera capture interface
            obPoseTracker: Pose detection module
            obFieldOfViewEstimator: FOV learning module
            obControlAlgorithm: Control algorithm (P/PID)
        """
        # Store references to injected dependencies
        self.obMotorInterface = obMotorInterface
        self.obCameraInterface = obCameraInterface
        self.obPoseTracker = obPoseTracker
        self.obFieldOfViewEstimator = obFieldOfViewEstimator
        self.obControlAlgorithm = obControlAlgorithm
        
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
            
            # STEP 3: Run pose detection
            obPoseResult = self.obPoseTracker.detect_person_in_frame(obFrameImage)
            
            # STEP 4: Build TrackingSample (atomic measurement)
            obNewSample = TrackingSample(
                dSampleTimestampSeconds=dFrameTimestamp,
                flMotorAngleDegrees=obMotorState.flMotorAngleDegrees,
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
        # Optionally send stop command
        self.obMotorInterface.send_emergency_stop_command()
        logger.info("Tracking mode STOPPED")
    
    def is_currently_tracking(self) -> bool:
        """Check if actively tracking."""
        return self.eCurrentState == TrackerState.TRACKING
    
    def get_current_field_of_view_degrees(self) -> float:
        """Get current FOV estimate."""
        iWidth, _ = self.obCameraInterface.get_frame_dimensions()
        return self.obFieldOfViewEstimator.get_estimated_field_of_view_degrees(iWidth)
    
    def get_current_motor_angle_degrees(self) -> float:
        """Get latest motor angle."""
        obState = self.obMotorInterface.get_latest_motor_state()
        return obState.flMotorAngleDegrees
    
    
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
            'fov_degrees': self.get_current_field_of_view_degrees()
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
        
        # Get current FOV calibration
        flAnglePerPixel = self.obFieldOfViewEstimator.get_estimated_angle_per_pixel_ratio()
        
        # Calculate pixel offset from center
        iImageWidth, _ = self.obCameraInterface.get_frame_dimensions()
        flPixelOffset = obCurrentSample.get_pixel_offset_from_center(iImageWidth)
        
        # Convert to angle error relative to camera center
        flAngleError = flPixelOffset * flAnglePerPixel

        # Absolute person angle relative to home (0°)
        flPersonAngleRelativeToHome = obCurrentSample.flMotorAngleDegrees + flAngleError

        # Home-biased deadzone: if person within ±deadband of home line, move to 0°
        if abs(flPersonAngleRelativeToHome) <= self.flDeadbandDegrees:
            flNewTargetAngle = 0.0
        else:
            # Calculate correction using control algorithm (track person)
            flCorrection = self.obControlAlgorithm.calculate_correction_from_error(
                flAngleError
            )
            # Compute new target angle
            flNewTargetAngle = obCurrentSample.flMotorAngleDegrees + flCorrection
        
        # Clamp to safety limits
        flNewTargetAngle = max(
            self.flMinimumMotorAngleDegrees,
            min(self.flMaximumMotorAngleDegrees, flNewTargetAngle)
        )
        
        # Send command to motor
        bSuccess = self.obMotorInterface.send_move_to_angle_command(flNewTargetAngle)
        
        if not bSuccess:
            logger.error("Failed to send motor command")
        else:
            logger.debug(
                f"Control: error={flAngleError:.2f}°, "
                f"target={flNewTargetAngle:.2f}°"
            )

    # Public API for UI/config integration
    def set_deadband_degrees(self, flDegrees: float):
        self.flDeadbandDegrees = max(self.flDeadbandMinDegrees, min(self.flDeadbandMaxDegrees, float(flDegrees)))

    def get_deadband_degrees(self) -> float:
        return self.flDeadbandDegrees

    def apply_configuration(self, obConfig):
        self.flDeadbandDegrees = float(getattr(obConfig, 'flControlDeadbandDegrees', self.flDeadbandDegrees))
        self.flDeadbandMinDegrees = float(getattr(obConfig, 'flDeadbandMinDegrees', self.flDeadbandMinDegrees))
        self.flDeadbandMaxDegrees = float(getattr(obConfig, 'flDeadbandMaxDegrees', self.flDeadbandMaxDegrees))
        self.flMinimumMotorAngleDegrees = float(getattr(obConfig, 'flMotorMinAngleDegrees', self.flMinimumMotorAngleDegrees))
        self.flMaximumMotorAngleDegrees = float(getattr(obConfig, 'flMotorMaxAngleDegrees', self.flMaximumMotorAngleDegrees))
        self.flMinimumTrackingConfidenceForControl = float(getattr(obConfig, 'flTrackingMinConfidenceForControl', self.flMinimumTrackingConfidenceForControl))
