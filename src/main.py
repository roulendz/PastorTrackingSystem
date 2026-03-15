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
import time
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from interfaces.motor_interface import MotorInterface, NullMotorInterface, SimulatedMotorInterface
from interfaces.camera_interface import CameraInterface
from utilities.clock import RealClock
from tracking.pose_tracker import PoseTracker, PoseResult
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
    obParser.add_argument(
        '--video',
        type=str,
        default='',
        help='Path to video file for synthetic testing (replaces camera)'
    )
    return obParser


def initialize_system(obConfig, sVideoFilePath: str = ""):
    """
    Initialize all system components.

    Args:
        obConfig: System configuration
        sVideoFilePath: Optional video file path for synthetic mode (no hardware)

    Returns:
        Tuple of (motor, camera, pose_tracker, controller, tracker)
    """
    logger.info("Initializing system components...")

    # Shared clock for all components
    obClock = RealClock()

    # 1. Motor Interface
    if sVideoFilePath:
        # Synthetic mode: use simulated motor instead of real hardware
        logger.info("Synthetic mode: using SimulatedMotorInterface")
        obMotorInterface = SimulatedMotorInterface(obClock=obClock)
        obMotorInterface.connect_to_motor_controller()
    else:
        logger.info("Connecting to motor...")
        obMotorInterface = MotorInterface(
            obConfig.sMotorSerialPortName,
            obConfig.iMotorBaudRate,
            obClock=obClock
        )

        if not obMotorInterface.connect_to_motor_controller():
            if obConfig.bAllowStartWithoutMotor:
                logger.warning("Motor not connected; starting in motor-less mode")
                obMotorInterface = SimulatedMotorInterface(obClock=obClock)
                obMotorInterface.connect_to_motor_controller()
            else:
                logger.error("Failed to connect to motor!")
                return None

    # Configure motor speed/acceleration
    obMotorInterface.send_speed_and_acceleration_settings(
        obConfig.flMotorMaxSpeedStepsPerSecond,
        obConfig.flMotorMaxAccelerationStepsPerSecondSquared
    )

    # Start background simulation for SimulatedMotorInterface
    if isinstance(obMotorInterface, SimulatedMotorInterface):
        obMotorInterface.start_background_simulation()

    # 2. Camera Interface
    logger.info("Opening camera...")
    obCameraInterface = CameraInterface(
        obConfig.iCameraDeviceIndex,
        obConfig.iCameraWidthPixels,
        obConfig.iCameraHeightPixels,
        obConfig.iCameraFramesPerSecond,
        sVideoFilePath=sVideoFilePath,
        obClock=obClock
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
    
    # 4. Control Algorithm
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
    
    # 5. Tracker Controller
    logger.info("Initializing tracker controller...")
    obTrackerController = TrackerController(
        obMotorInterface,
        obCameraInterface,
        obPoseTracker,
        obControlAlgorithm,
        obClock=obClock
    )
    # Apply configuration to controller
    obTrackerController.apply_configuration(obConfig)
    
    logger.info("✓ All systems initialized successfully!")
    
    return (
        obMotorInterface,
        obCameraInterface,
        obPoseTracker,
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
        self.iImageWidth = 0
        self.bDraggingLeft = False
        self.bDraggingRight = False
        self.bDraggingCenter = False
        self.iDragThresholdPixels = 8
        self.iHomeUpdateMinDeltaPixels = 2

    def update_mapping(self, iHomeLineX: int, flAnglePerPixel: float, iImageWidth: int, iImageHeight: int):
        self.iHomeLineX = int(iHomeLineX)
        self.flAnglePerPixel = max(1e-9, flAnglePerPixel)
        self.iImageWidth = int(iImageWidth)
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
                iCenterX = int(self.iImageWidth // 2) if self.iImageWidth > 0 else int(self.obConfig.iCameraWidthPixels // 2)
                flNewAngle = (iCenterX - x) * self.flAnglePerPixel
                self.obTrackerController.obMotorInterface.send_move_to_angle_command(flNewAngle)
                try:
                    from ui.live_settings_panel import update_center_angle_slider
                    update_center_angle_slider(flNewAngle)
                except Exception as e:
                    logger.debug(f"UI slider update skipped: {e}")
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


def draw_visualization_overlay(obFrame, obSample, obStats, obConfig, obTrackerController: TrackerController, obDeadzoneUI: DeadzoneUIController):
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

    flAnglePerPixel = obTrackerController.get_angle_per_pixel_degrees(iWidth)
    flFOVDegrees = float(getattr(obConfig, 'flFieldOfViewDegrees', 0.0))
    if not (flFOVDegrees > 0.0):
        flFOVDegrees = flAnglePerPixel * float(iWidth) if iWidth > 0 else 0.0
    flMotorAngle = obSample.flMotorAngleDegrees if obSample is not None else (obStats['motor_angle'] if obStats and 'motor_angle' in obStats else 0.0)
    flCameraMotorOffsetDegrees = float(getattr(obConfig, 'flCameraMotorOffsetDegrees', 0.0))
    if flAnglePerPixel != 0 and iWidth > 0:
        iHomeLineX = int(iCenterX - ((flMotorAngle - flCameraMotorOffsetDegrees) / flAnglePerPixel))
        iHomeLineX = max(0, min(iWidth - 1, iHomeLineX))
    else:
        iHomeLineX = iCenterX
    cv2.line(obFrame, (iHomeLineX, 0), (iHomeLineX, iHeight), (255, 255, 0), 1)

    # V-marker lectern indicator at home position (Phase 5: TEST-03)
    if obConfig.bShowDebugInfo:
        iVMarkerTipY = iHeight - 20  # 20px from bottom
        iVMarkerSize = 20  # Half-width and height of V
        cv2.line(obFrame, (iHomeLineX - iVMarkerSize, iVMarkerTipY - iVMarkerSize),
                 (iHomeLineX, iVMarkerTipY), (0, 255, 255), 2)  # Left arm
        cv2.line(obFrame, (iHomeLineX + iVMarkerSize, iVMarkerTipY - iVMarkerSize),
                 (iHomeLineX, iVMarkerTipY), (0, 255, 255), 2)  # Right arm

    obDeadzoneUI.update_mapping(iHomeLineX, flAnglePerPixel if flAnglePerPixel != 0 else 1e-9, iWidth, iHeight)
    if bool(getattr(obConfig, 'bEnableDeadzoneOverlay', True)):
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
            f"FOV: {flFOVDegrees:.1f}deg",
            f"Speed: {obStats['motor_speed']:.1f} steps/s",
        ]

        # Detection state (Phase 4) -- debug overlay only
        sDetectionState = "TRACKING"
        if hasattr(obTrackerController, '_eDetectionState'):
            sDetectionState = obTrackerController._eDetectionState.value
        vInfoLines.append(f"Detection: {sDetectionState}")
        
        for sLine in vInfoLines:
            TextRenderer.draw_text(obFrame, sLine, (10, iYPos), 0.7, (0, 255, 0), 2)
            iYPos += iLineHeight
        
        # Controls
        iControlLineHeight = 20
        iBottomPadding = 10
        vControlLines = [
            "Controls:",
            "S - Start tracking",
            "P - Pause",
            "Q - Quit",
            "C - Calibrate FOV (press twice)",
            "H - Move to HOME (0°)",
            "R - Reset current position as HOME (0°)"
        ]
        iYPos = iHeight - ((len(vControlLines) - 1) * iControlLineHeight) - iBottomPadding
        for sLine in vControlLines:
            TextRenderer.draw_text(obFrame, sLine, (10, iYPos), 0.5, (255, 255, 255), 1)
            iYPos += iControlLineHeight

    # Timing debug overlay (Phase 2 locked decision: gated behind debug flag)
    if obConfig.bShowDebugInfo and obSample is not None:
        flRawMotorAngle = obStats.get('motor_angle', 0.0)
        flInterpolatedAngle = obSample.flMotorAngleDegrees  # Already interpolated in tracking loop
        flAngleDelta = flInterpolatedAngle - flRawMotorAngle

        # Compute pixel error (signed): how far is virtual center from where it should be
        flPixelError = 0.0
        if flAnglePerPixel > 0:
            flPixelError = flAngleDelta / flAnglePerPixel

        vTimingLines = [
            f"Raw Motor: {flRawMotorAngle:.3f} deg",
            f"Interp Motor: {flInterpolatedAngle:.3f} deg",
            f"Delta: {flAngleDelta:.4f} deg",
            f"Pixel Err: {flPixelError:.1f} px",
        ]

        iTimingY = 180  # Below existing debug info
        for sLine in vTimingLines:
            TextRenderer.draw_text(obFrame, sLine, (10, iTimingY), 0.6, (255, 200, 0), 2)
            iTimingY += 25


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
    obComponents = initialize_system(obConfig, sVideoFilePath=obArgs.video)
    if obComponents is None:
        logger.error("System initialization failed!")
        return 1
    
    (obMotorInterface, obCameraInterface, obPoseTracker,
     obControlAlgorithm, obTrackerController) = obComponents
    start_live_settings_panel(obConfigManager, obTrackerController, obMotorInterface, obCameraInterface)
    
    # Prepare window
    cv2.namedWindow('Pastor Tracking System', cv2.WINDOW_NORMAL)
    obDeadzoneUI = DeadzoneUIController(obTrackerController, obConfig)
    cv2.setMouseCallback('Pastor Tracking System', obDeadzoneUI.on_mouse)
    dFovCalibration = None
    
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
                draw_visualization_overlay(obVisFrame, obSample, dStats, obConfig, obTrackerController, obDeadzoneUI)
                
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
                try:
                    if obSample is None or not bool(getattr(obSample, 'bPersonWasDetected', False)):
                        logger.info("FOV calibrate: no person detected")
                    else:
                        iWidth = int(obSample.obFrameImage.shape[1]) if obSample.obFrameImage is not None else int(obConfig.iCameraWidthPixels)
                        flAngle = float(obSample.flMotorAngleDegrees)
                        flX = float(obSample.flPersonCenterXPixels)
                        if dFovCalibration is None:
                            dFovCalibration = {"angle": flAngle, "x": flX, "width": iWidth}
                            logger.info("FOV calibrate: captured first point")
                        else:
                            flDeltaAngle = float(flAngle - float(dFovCalibration["angle"]))
                            flDeltaPx = float(flX - float(dFovCalibration["x"]))
                            if abs(flDeltaPx) < 5.0 or abs(flDeltaAngle) < 0.05:
                                logger.info("FOV calibrate: move more before second capture")
                            else:
                                flFov = abs(flDeltaAngle) * float(iWidth) / abs(flDeltaPx)
                                obConfig.flFieldOfViewDegrees = float(flFov)
                                try:
                                    obConfig.flInitialAnglePerPixelDegrees = float(flFov) / float(iWidth) if iWidth > 0 else obConfig.flInitialAnglePerPixelDegrees
                                except Exception as e:
                                    logger.debug(f"UI slider update skipped: {e}")
                                try:
                                    from ui.live_settings_panel import update_fov_degrees_slider
                                    update_fov_degrees_slider(float(flFov))
                                except Exception as e:
                                    logger.debug(f"UI slider update skipped: {e}")
                                logger.info(f"FOV calibrate: set flFieldOfViewDegrees={flFov:.3f}")
                                # Auto-save calibrated FOV to user config
                                try:
                                    from ui.live_settings_panel import _save_full_config
                                    _save_full_config(obConfigManager)
                                    logger.info("FOV calibrate: saved to user_config.json")
                                except Exception as e:
                                    logger.debug(f"FOV auto-save skipped: {e}")
                                dFovCalibration = None
                except (ValueError, TypeError, AttributeError, ZeroDivisionError) as e:
                    logger.error(f"FOV calibration error: {e}")
                    dFovCalibration = None
            
            elif iKey == ord('h') or iKey == ord('H'):
                obMotorInterface.send_home_command()
                logger.info("Homing motor...")
            elif iKey == ord('r') or iKey == ord('R'):
                obMotorInterface.send_reset_position_command()
                try:
                    from ui.live_settings_panel import update_center_angle_slider
                    update_center_angle_slider(0.0)
                except Exception as e:
                    logger.debug(f"UI slider update skipped: {e}")
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
