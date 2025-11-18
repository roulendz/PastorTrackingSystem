"""
main.py - Application entry point for Pastor Tracking System

This is the main entry point that initializes all modules and runs
the tracking loop with visualization.

Usage:
    python main.py [--config path/to/config.json]
"""

import cv2
import argparse
import logging
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from interfaces.motor_interface import MotorInterface
from interfaces.camera_interface import CameraInterface
from tracking.pose_tracker import PoseTracker
from tracking.fov_estimator import FieldOfViewEstimator
from control.control_algorithm import ProportionalController, PIDController
from control.tracker_controller import TrackerController
from utilities.config_manager import ConfigurationManager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def setup_argument_parser() -> argparse.ArgumentParser:
    """Create argument parser."""
    obParser = argparse.ArgumentParser(
        description='Pastor Tracking System - Automated Person Tracking'
    )
    obParser.add_argument(
        '--config',
        type=str,
        default='config/default_config.json',
        help='Path to configuration file'
    )
    obParser.add_argument(
        '--debug',
        action='store_true',
        help='Enable debug logging'
    )
    return obParser


def initialize_system(obConfig):
    """
    Initialize all system components.
    
    Returns:
        Tuple of (motor, camera, pose_tracker, fov_estimator, controller, tracker)
    """
    logger.info("Initializing system components...")
    
    # 1. Motor Interface
    logger.info("Connecting to motor...")
    obMotorInterface = MotorInterface(
        obConfig.sMotorSerialPortName,
        obConfig.iMotorBaudRate
    )
    
    if not obMotorInterface.connect_to_motor_controller():
        logger.error("Failed to connect to motor!")
        return None
    
    # Configure motor speed/acceleration
    obMotorInterface.send_speed_and_acceleration_settings(
        obConfig.flMotorMaxSpeedStepsPerSecond,
        obConfig.flMotorMaxAccelerationStepsPerSecondSquared
    )
    
    # 2. Camera Interface
    logger.info("Opening camera...")
    obCameraInterface = CameraInterface(
        obConfig.iCameraDeviceIndex,
        obConfig.iCameraWidthPixels,
        obConfig.iCameraHeightPixels,
        obConfig.iCameraFramesPerSecond
    )
    
    if not obCameraInterface.open_camera_device():
        logger.error("Failed to open camera!")
        obMotorInterface.disconnect_from_motor_controller()
        return None
    
    # 3. Pose Tracker
    logger.info("Initializing pose tracker...")
    obPoseTracker = PoseTracker(
        obConfig.flPoseMinDetectionConfidence,
        obConfig.flPoseMinTrackingConfidence,
        obConfig.bPoseEnableSegmentation
    )
    
    # 4. FOV Estimator
    logger.info("Initializing FOV estimator...")
    
    # Use stored calibration if available
    if obConfig.bUseStoredCalibration and obConfig.flStoredAnglePerPixelDegrees > 0:
        flInitialAnglePerPixel = obConfig.flStoredAnglePerPixelDegrees
        logger.info(f"Using stored calibration: {flInitialAnglePerPixel:.6f} deg/px")
    else:
        flInitialAnglePerPixel = obConfig.flInitialAnglePerPixelDegrees
        logger.info(f"Using default calibration: {flInitialAnglePerPixel:.6f} deg/px")
    
    obFOVEstimator = FieldOfViewEstimator(
        flInitialAnglePerPixel,
        obConfig.flMinAngleChangeForLearningDegrees,
        obConfig.flMinPixelChangeForLearningPixels,
        obConfig.flLearningRateAlpha
    )
    
    # 5. Control Algorithm
    logger.info(f"Initializing {obConfig.sControlAlgorithmType} controller...")
    
    if obConfig.sControlAlgorithmType == "PID":
        obControlAlgorithm = PIDController(
            obConfig.flControlProportionalGain,
            obConfig.flControlIntegralGain,
            obConfig.flControlDerivativeGain
        )
    else:  # Default to P controller
        obControlAlgorithm = ProportionalController(
            obConfig.flControlProportionalGain
        )
    
    # 6. Tracker Controller
    logger.info("Initializing tracker controller...")
    obTrackerController = TrackerController(
        obMotorInterface,
        obCameraInterface,
        obPoseTracker,
        obFOVEstimator,
        obControlAlgorithm
    )
    
    logger.info("✓ All systems initialized successfully!")
    
    return (
        obMotorInterface,
        obCameraInterface,
        obPoseTracker,
        obFOVEstimator,
        obControlAlgorithm,
        obTrackerController
    )


def draw_visualization_overlay(obFrame, obSample, obStats, obConfig):
    """
    Draw visualization overlay on frame.
    
    Args:
        obFrame: Frame to draw on
        obSample: Current tracking sample
        obStats: System statistics
        obConfig: Configuration
    """
    iHeight, iWidth = obFrame.shape[:2]
    
    # Draw center line
    iCenterX = iWidth // 2
    cv2.line(obFrame, (iCenterX, 0), (iCenterX, iHeight), (0, 255, 255), 2)
    
    # Draw person center if detected
    if obSample and obSample.bPersonWasDetected:
        iPersonX = int(obSample.flPersonCenterXPixels)
        iPersonY = int(obSample.flPersonCenterYPixels)
        
        # Draw person marker
        cv2.circle(obFrame, (iPersonX, iPersonY), 15, (0, 0, 255), 3)
        cv2.line(obFrame, (iCenterX, iPersonY), (iPersonX, iPersonY), (0, 0, 255), 2)
        
        # Draw offset text
        flPixelOffset = obSample.get_pixel_offset_from_center(iWidth)
        sOffsetText = f"Offset: {flPixelOffset:.0f}px"
        cv2.putText(obFrame, sOffsetText, (iPersonX + 20, iPersonY - 20),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
    
    # Draw status info
    if obConfig.bShowDebugInfo:
        iYPos = 30
        iLineHeight = 30
        
        vInfoLines = [
            f"State: {obStats['state']}",
            f"FPS: {obStats['average_fps']:.1f}",
            f"Motor: {obStats['motor_angle']:.2f}deg",
            f"FOV: {obStats['fov_degrees']:.1f}deg",
            f"Calibration: {obStats['calibration_samples']} samples",
            f"Converged: {'YES' if obStats['fov_converged'] else 'NO'}"
        ]
        
        for sLine in vInfoLines:
            cv2.putText(obFrame, sLine, (10, iYPos),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
            iYPos += iLineHeight
        
        # Controls
        iYPos = iHeight - 90
        vControlLines = [
            "Controls:",
            "S - Start tracking",
            "P - Pause",
            "Q - Quit"
        ]
        for sLine in vControlLines:
            cv2.putText(obFrame, sLine, (10, iYPos),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
            iYPos += 20


def main():
    """Main application loop."""
    # Parse arguments
    obParser = setup_argument_parser()
    obArgs = obParser.parse_args()
    
    if obArgs.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    
    # Load configuration
    logger.info("Loading configuration...")
    obConfigManager = ConfigurationManager(obArgs.config)
    obConfigManager.load_configuration_from_file()
    obConfig = obConfigManager.get_system_configuration()
    
    # Initialize system
    obComponents = initialize_system(obConfig)
    if obComponents is None:
        logger.error("System initialization failed!")
        return 1
    
    (obMotorInterface, obCameraInterface, obPoseTracker,
     obFOVEstimator, obControlAlgorithm, obTrackerController) = obComponents
    
    # Main loop
    logger.info("=" * 60)
    logger.info("System ready! Press 'S' to start tracking, 'Q' to quit")
    logger.info("=" * 60)
    
    try:
        while True:
            # Execute one tracking loop iteration
            obSample = obTrackerController.execute_main_tracking_loop_tick()
            
            if obSample is None:
                continue
            
            # Get statistics
            dStats = obTrackerController.get_system_statistics()
            
            # Visualization
            if obConfig.bEnableVisualization and obSample.obFrameImage is not None:
                obVisFrame = obSample.obFrameImage.copy()
                
                # Draw pose if person detected
                if obSample.bPersonWasDetected:
                    obPoseResult = obPoseTracker.detect_person_in_frame(obSample.obFrameImage)
                    obVisFrame = obPoseTracker.draw_pose_on_frame(obVisFrame, obPoseResult)
                
                # Draw overlay
                draw_visualization_overlay(obVisFrame, obSample, dStats, obConfig)
                
                # Show frame
                cv2.imshow('Pastor Tracking System', obVisFrame)
            
            # Handle keyboard input
            iKey = cv2.waitKey(1) & 0xFF
            
            if iKey == ord('q') or iKey == ord('Q'):
                logger.info("Quit command received")
                break
            elif iKey == ord('s') or iKey == ord('S'):
                if not obTrackerController.is_currently_tracking():
                    obTrackerController.start_tracking_mode()
                    logger.info("Tracking STARTED")
            elif iKey == ord('p') or iKey == ord('P'):
                if obTrackerController.is_currently_tracking():
                    obTrackerController.stop_tracking_mode()
                    logger.info("Tracking PAUSED")
            elif iKey == ord('c') or iKey == ord('C'):
                obTrackerController.start_calibration_mode()
                logger.info("Calibration mode STARTED")
            elif iKey == ord('h') or iKey == ord('H'):
                obMotorInterface.send_home_command()
                logger.info("Homing motor...")
    
    except KeyboardInterrupt:
        logger.info("\nKeyboard interrupt received")
    
    finally:
        # Cleanup
        logger.info("Shutting down...")
        
        # Stop tracking
        if obTrackerController.is_currently_tracking():
            obTrackerController.stop_tracking_mode()
        
        # Save calibration
        flFinalAnglePerPixel = obFOVEstimator.get_estimated_angle_per_pixel_ratio()
        obConfigManager.update_stored_calibration(flFinalAnglePerPixel)
        obConfigManager.save_configuration_to_file()
        logger.info(f"Calibration saved: {flFinalAnglePerPixel:.6f} deg/px")
        
        # Home motor
        obMotorInterface.send_home_command()
        import time
        time.sleep(2)
        
        # Disable motor
        obMotorInterface.send_enable_driver_command(False)
        
        # Close resources
        obCameraInterface.close_camera_device()
        obMotorInterface.disconnect_from_motor_controller()
        obPoseTracker.close_pose_tracker()
        
        cv2.destroyAllWindows()
        
        logger.info("Shutdown complete")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
