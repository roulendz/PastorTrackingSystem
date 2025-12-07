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

from interfaces.motor_interface import MotorInterface, NullMotorInterface
from interfaces.camera_interface import CameraInterface
from tracking.pose_tracker import PoseTracker, PoseResult
from tracking.fov_estimator import FieldOfViewEstimator
from control.control_algorithm import ProportionalController, PIDController
from control.tracker_controller import TrackerController
from utilities.config_manager import ConfigurationManager
from utilities.text_renderer import TextRenderer
from ui.live_settings_panel import start_live_settings_panel

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
    obParser.add_argument(
        '--edit-config',
        action='store_true',
        help='Open configuration editor GUI before starting'
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
        if obConfig.bAllowStartWithoutMotor:
            logger.warning("Motor not connected; starting in motor-less mode")
            obMotorInterface = NullMotorInterface()
        else:
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
    
    # 4. FOV (manual)
    logger.info("Initializing FOV...")
    flInitialAnglePerPixel = obConfig.flInitialAnglePerPixelDegrees
    obFOVEstimator = FieldOfViewEstimator(flInitialAnglePerPixel)
    
    # 5. Control Algorithm
    logger.info(f"Initializing {obConfig.sControlAlgorithmType} controller...")
    
    if obConfig.sControlAlgorithmType == "PID":
        obControlAlgorithm = PIDController(
            obConfig.flControlProportionalGain,
            obConfig.flControlIntegralGain,
            obConfig.flControlDerivativeGain
        )
    elif obConfig.sControlAlgorithmType == "Velocity":
        from control.control_algorithm import VelocityController
        obControlAlgorithm = VelocityController(
            obConfig.flVelocityGain,
            obConfig.flMaxVelocityDegreesPerSecond,
            obConfig.flVelocitySmoothingAlpha
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
    # Apply configuration to controller
    obTrackerController.apply_configuration(obConfig)
    
    logger.info("✓ All systems initialized successfully!")
    
    return (
        obMotorInterface,
        obCameraInterface,
        obPoseTracker,
        obFOVEstimator,
        obControlAlgorithm,
        obTrackerController
    )


class DeadzoneUIController:
    def __init__(self, obTrackerController: TrackerController, obConfig):
        self.obTrackerController = obTrackerController
        self.obConfig = obConfig
        self.iHomeLineX = 0
        self.flAnglePerPixel = 0.05
        self.iImageHeight = 0
        self.bDraggingLeft = False
        self.bDraggingRight = False
        self.bDraggingCenter = False
        self.iDragThresholdPixels = 8

    def update_mapping(self, iHomeLineX: int, flAnglePerPixel: float, iImageHeight: int):
        self.iHomeLineX = iHomeLineX
        self.flAnglePerPixel = max(1e-9, flAnglePerPixel)
        self.iImageHeight = iImageHeight

    def on_mouse(self, event, x, y, flags, param=None):
        flDeadbandDeg = self.obTrackerController.get_deadband_degrees()
        iDeadbandPx = int(flDeadbandDeg / self.flAnglePerPixel)
        iLeftX = self.iHomeLineX - iDeadbandPx
        iRightX = self.iHomeLineX + iDeadbandPx

        if event == cv2.EVENT_LBUTTONDOWN:
            if abs(x - iLeftX) <= self.iDragThresholdPixels:
                self.bDraggingLeft = True
            elif abs(x - iRightX) <= self.iDragThresholdPixels:
                self.bDraggingRight = True
            elif abs(x - self.iHomeLineX) <= self.iDragThresholdPixels:
                self.bDraggingCenter = True
        elif event == cv2.EVENT_MOUSEMOVE:
            if self.bDraggingLeft:
                iNewDeadbandPx = abs(self.iHomeLineX - x)
                flNewDeg = iNewDeadbandPx * self.flAnglePerPixel
                self.obTrackerController.set_deadband_degrees(flNewDeg)
            elif self.bDraggingRight:
                iNewDeadbandPx = abs(x - self.iHomeLineX)
                flNewDeg = iNewDeadbandPx * self.flAnglePerPixel
                self.obTrackerController.set_deadband_degrees(flNewDeg)
            elif self.bDraggingCenter:
                iCenterX = int(self.obConfig.iCameraWidthPixels // 2)
                flNewAngle = (iCenterX - x) * self.flAnglePerPixel
                self.obTrackerController.obMotorInterface.send_move_to_angle_command(flNewAngle)
                try:
                    from ui.live_settings_panel import update_center_angle_slider
                    update_center_angle_slider(flNewAngle)
                except Exception:
                    pass
        elif event == cv2.EVENT_LBUTTONUP:
            self.bDraggingLeft = False
            self.bDraggingRight = False
            self.bDraggingCenter = False

    def draw(self, obFrame):
        iHeight, iWidth = obFrame.shape[:2]
        flDeadbandDeg = self.obTrackerController.get_deadband_degrees()
        iDeadbandPx = int(flDeadbandDeg / self.flAnglePerPixel)
        iLeftX = max(0, self.iHomeLineX - iDeadbandPx)
        iRightX = min(iWidth - 1, self.iHomeLineX + iDeadbandPx)
        cv2.line(obFrame, (iLeftX, 0), (iLeftX, iHeight), (0, 255, 0), 1)
        cv2.line(obFrame, (iRightX, 0), (iRightX, iHeight), (0, 255, 0), 1)
        sText = f"Deadzone: {flDeadbandDeg:.2f}°"
        TextRenderer.draw_text(obFrame, sText, (iRightX + 10 if iRightX + 150 < iWidth else max(10, iLeftX - 150), 30), 0.6, (0, 255, 0), 2)


def draw_visualization_overlay(obFrame, obSample, obStats, obConfig, obDeadzoneUI: DeadzoneUIController):
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

    # Compute virtual home line based on current motor angle (locked to world plane)
    flFOVDegrees = obStats['fov_degrees'] if obStats and 'fov_degrees' in obStats else 0.0
    flAnglePerPixel = (flFOVDegrees / iWidth) if iWidth > 0 else 0.0
    flMotorAngle = obStats['motor_angle'] if obStats and 'motor_angle' in obStats else 0.0
    iHomeLineX = int(iCenterX - (flMotorAngle / (flAnglePerPixel if flAnglePerPixel != 0 else 1e-9)))
    iHomeLineX = max(0, min(iWidth - 1, iHomeLineX))
    cv2.line(obFrame, (iHomeLineX, 0), (iHomeLineX, iHeight), (255, 255, 0), 1)
    obDeadzoneUI.update_mapping(iHomeLineX, flAnglePerPixel if flAnglePerPixel != 0 else 1e-9, iHeight)
    obDeadzoneUI.draw(obFrame)
    
    # Draw person center if detected
    if obSample and obSample.bPersonWasDetected:
        iPersonX = int(obSample.flPersonCenterXPixels)
        iPersonY = int(obSample.flPersonCenterYPixels)
        
        iCircleRadiusPx = int(getattr(obConfig, 'iCenterDeadzoneRadiusPixels', 40))
        cv2.circle(obFrame, (iPersonX, iPersonY), max(1, iCircleRadiusPx), (0, 0, 255), 3)
        cv2.line(obFrame, (iCenterX, iPersonY), (iPersonX, iPersonY), (0, 0, 255), 2)
        
        flPixelOffset = obSample.get_pixel_offset_from_center(iWidth)
        sOffsetText = f"Offset: {flPixelOffset:.0f}px"
        TextRenderer.draw_text(obFrame, sOffsetText, (iPersonX + 20, iPersonY - 20), 0.6, (0, 0, 255), 2)
    
    # Draw status info
    if obConfig.bShowDebugInfo:
        iYPos = 30
        iLineHeight = 30
        
        vInfoLines = [
            f"State: {obStats['state']}",
            f"FPS: {obStats['average_fps']:.1f}",
            f"Motor: {obStats['motor_angle']:.2f}deg",
            f"FOV: {obStats['fov_degrees']:.1f}deg",
            f"Speed: {obStats['motor_speed']:.1f} steps/s",
        ]
        
        for sLine in vInfoLines:
            TextRenderer.draw_text(obFrame, sLine, (10, iYPos), 0.7, (0, 255, 0), 2)
            iYPos += iLineHeight
        
        # Controls
        iYPos = iHeight - 90
        vControlLines = [
            "Controls:",
            "S - Start tracking",
            "P - Pause",
            "Q - Quit",
            "H - Move to HOME (0°)",
            "R - Reset current position as HOME (0°)"
        ]
        for sLine in vControlLines:
            TextRenderer.draw_text(obFrame, sLine, (10, iYPos), 0.5, (255, 255, 255), 1)
            iYPos += 20

    # Background motion visualization removed


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
    obConfigManager.load_configuration_with_overrides(["config/user_config.json"])
    obConfig = obConfigManager.get_system_configuration()
    
    if obArgs.edit_config:
        try:
            from ui.config_editor import launch_config_editor
            bEdited = launch_config_editor(obArgs.config, "config/user_config.json")
            if bEdited:
                obConfigManager.load_configuration_with_overrides(["config/user_config.json"])
                obConfig = obConfigManager.get_system_configuration()
        except Exception as e:
            logger.error(f"Failed to open configuration editor: {e}")

    # Initialize system
    obComponents = initialize_system(obConfig)
    if obComponents is None:
        logger.error("System initialization failed!")
        return 1
    
    (obMotorInterface, obCameraInterface, obPoseTracker,
     obFOVEstimator, obControlAlgorithm, obTrackerController) = obComponents
    start_live_settings_panel(obConfigManager, obTrackerController, obMotorInterface, obCameraInterface, obFOVEstimator)
    
    # Prepare window
    cv2.namedWindow('Pastor Tracking System', cv2.WINDOW_NORMAL)
    obDeadzoneUI = DeadzoneUIController(obTrackerController, obConfig)
    cv2.setMouseCallback('Pastor Tracking System', obDeadzoneUI.on_mouse)
    
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
                
                if obSample.bPersonWasDetected and obSample.obPoseLandmarks is not None:
                    obPoseResult = PoseResult(
                        flPersonCenterXPixels=obSample.flPersonCenterXPixels,
                        flPersonCenterYPixels=obSample.flPersonCenterYPixels,
                        bPersonWasDetected=obSample.bPersonWasDetected,
                        flPersonConfidenceScore=obSample.flPersonConfidenceScore,
                        vLandmarks=obSample.obPoseLandmarks
                    )
                    obVisFrame = obPoseTracker.draw_pose_on_frame(obVisFrame, obPoseResult)

                # Draw overlay
                draw_visualization_overlay(obVisFrame, obSample, dStats, obConfig, obDeadzoneUI)
                
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
            
            elif iKey == ord('h') or iKey == ord('H'):
                obMotorInterface.send_home_command()
                logger.info("Homing motor...")
            elif iKey == ord('r') or iKey == ord('R'):
                obMotorInterface.send_reset_position_command()
                try:
                    from ui.live_settings_panel import update_center_angle_slider
                    update_center_angle_slider(0.0)
                except Exception:
                    pass
                logger.info("Home reset: current angle set as 0°")
    
    except KeyboardInterrupt:
        logger.info("\nKeyboard interrupt received")
    
    finally:
        # Cleanup
        logger.info("Shutting down...")
        
        # Stop tracking
        if obTrackerController.is_currently_tracking():
            obTrackerController.stop_tracking_mode()
        
        
        
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
