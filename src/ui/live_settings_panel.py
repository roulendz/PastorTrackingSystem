import threading
import json
from dataclasses import asdict
from typing import Dict, Any, Tuple
import dearpygui.dearpygui as dpg

from utilities.config_manager import ConfigurationManager
from control.tracker_controller import TrackerController
from interfaces.motor_interface import MotorInterface
from interfaces.camera_interface import CameraInterface
from tracking.pose_tracker import PoseTracker
from tracking.fov_estimator import FieldOfViewEstimator
from control.control_algorithm import ProportionalController, PIDController, VelocityController


def _ranges() -> Dict[str, Tuple[float, float, float]]:
    return {
        "flMotorMaxSpeedStepsPerSecond": (1000.0, 50000.0, 100.0),
        "flMotorMaxAccelerationStepsPerSecondSquared": (1000.0, 50000.0, 100.0),
        "flMotorMinAngleDegrees": (-180.0, 0.0, 0.1),
        "flMotorMaxAngleDegrees": (0.0, 180.0, 0.1),
        "iCameraDeviceIndex": (0.0, 8.0, 1.0),
        "iCameraWidthPixels": (320.0, 1920.0, 10.0),
        "iCameraHeightPixels": (240.0, 1080.0, 10.0),
        "iCameraFramesPerSecond": (1.0, 120.0, 1.0),
        "flPoseMinDetectionConfidence": (0.0, 1.0, 0.01),
        "flPoseMinTrackingConfidence": (0.0, 1.0, 0.01),
        "flInitialAnglePerPixelDegrees": (0.001, 1.0, 0.001),
        "flControlProportionalGain": (0.001, 10.0, 0.001),
        "flControlIntegralGain": (0.0, 10.0, 0.001),
        "flControlDerivativeGain": (0.0, 10.0, 0.001),
        "flControlDeadbandDegrees": (0.0, 20.0, 0.01),
        "flDeadbandMinDegrees": (0.0, 20.0, 0.01),
        "flDeadbandMaxDegrees": (0.0, 20.0, 0.01),
        "flTrackingMinConfidenceForControl": (0.0, 1.0, 0.01),
        "iCenterDeadzoneRadiusPixels": (0.0, 300.0, 1.0),
        "flVelocityGain": (0.0, 20.0, 0.01),
        "flMaxVelocityDegreesPerSecond": (0.0, 180.0, 0.1),
        "flVelocitySmoothingAlpha": (0.0, 1.0, 0.01),
    }


def _add_slider_with_range(sKey: str, vDefault: float, dItems: Dict[str, int]):
    flMin, flMax, _ = _ranges().get(sKey, (0.0, 100.0, 1.0))
    with dpg.group(horizontal=True):
        iMin = dpg.add_input_float(label=f"{sKey} min", default_value=flMin, width=100)
        iMax = dpg.add_input_float(label=f"{sKey} max", default_value=flMax, width=100)
        dpg.add_button(label="Apply", callback=lambda: dpg.configure_item(dItems[sKey], min_value=dpg.get_value(iMin), max_value=dpg.get_value(iMax)))
    dItems[sKey] = dpg.add_slider_float(label=sKey, default_value=float(vDefault), min_value=flMin, max_value=flMax)
    dpg.add_input_float(label=f"{sKey} value", default_value=float(vDefault), callback=lambda s, a, u: dpg.set_value(dItems[sKey], a))


def _apply_motor_settings(obMotor: MotorInterface, obConfig):
    obMotor.send_speed_and_acceleration_settings(
        obConfig.flMotorMaxSpeedStepsPerSecond,
        obConfig.flMotorMaxAccelerationStepsPerSecondSquared
    )


def _apply_camera_settings(obCamera: CameraInterface, obConfig):
    obCamera.close_camera_device()
    obCamera.iCameraDeviceIndex = obConfig.iCameraDeviceIndex
    obCamera.iCameraWidthPixels = obConfig.iCameraWidthPixels
    obCamera.iCameraHeightPixels = obConfig.iCameraHeightPixels
    obCamera.iCameraFramesPerSecond = obConfig.iCameraFramesPerSecond
    obCamera.open_camera_device()


def _apply_pose_settings(obTracker: TrackerController, obConfig):
    obOld = obTracker.obPoseTracker
    try:
        obNew = PoseTracker(
            obConfig.flPoseMinDetectionConfidence,
            obConfig.flPoseMinTrackingConfidence,
            obConfig.bPoseEnableSegmentation
        )
        obTracker.obPoseTracker = obNew
    finally:
        try:
            obOld.close_pose_tracker()
        except Exception:
            pass


def _apply_control_algorithm(obTracker: TrackerController, obConfig):
    if obConfig.sControlAlgorithmType == "PID":
        obAlgo = PIDController(
            obConfig.flControlProportionalGain,
            obConfig.flControlIntegralGain,
            obConfig.flControlDerivativeGain
        )
    elif obConfig.sControlAlgorithmType == "Velocity":
        obAlgo = VelocityController(
            obConfig.flVelocityGain,
            obConfig.flMaxVelocityDegreesPerSecond,
            obConfig.flVelocitySmoothingAlpha
        )
    else:
        obAlgo = ProportionalController(obConfig.flControlProportionalGain)
    obTracker.set_control_algorithm(obAlgo)


def start_live_settings_panel(
    obConfigManager: ConfigurationManager,
    obTracker: TrackerController,
    obMotor: MotorInterface,
    obCamera: CameraInterface,
    obFOV: FieldOfViewEstimator
):
    dpg.create_context()
    dpg.create_viewport(title="Live Settings", width=600, height=800)
    dpg.setup_dearpygui()

    obConfig = obConfigManager.get_system_configuration()
    dItems: Dict[str, int] = {}

    with dpg.window(label="Settings", width=580, height=760):
        dpg.add_text("Motor")
        _add_slider_with_range("flMotorMaxSpeedStepsPerSecond", obConfig.flMotorMaxSpeedStepsPerSecond, dItems)
        _add_slider_with_range("flMotorMaxAccelerationStepsPerSecondSquared", obConfig.flMotorMaxAccelerationStepsPerSecondSquared, dItems)

        def _on_apply_motor():
            obConfig.flMotorMaxSpeedStepsPerSecond = float(dpg.get_value(dItems["flMotorMaxSpeedStepsPerSecond"]))
            obConfig.flMotorMaxAccelerationStepsPerSecondSquared = float(dpg.get_value(dItems["flMotorMaxAccelerationStepsPerSecondSquared"]))
            _apply_motor_settings(obMotor, obConfig)
        dpg.add_button(label="Apply Motor", callback=_on_apply_motor)

        dpg.add_separator()
        dpg.add_text("Angles")
        _add_slider_with_range("flMotorMinAngleDegrees", obConfig.flMotorMinAngleDegrees, dItems)
        _add_slider_with_range("flMotorMaxAngleDegrees", obConfig.flMotorMaxAngleDegrees, dItems)
        def _on_apply_angles():
            obConfig.flMotorMinAngleDegrees = float(dpg.get_value(dItems["flMotorMinAngleDegrees"]))
            obConfig.flMotorMaxAngleDegrees = float(dpg.get_value(dItems["flMotorMaxAngleDegrees"]))
            obTracker.set_angle_limits(obConfig.flMotorMinAngleDegrees, obConfig.flMotorMaxAngleDegrees)
        dpg.add_button(label="Apply Angle Limits", callback=_on_apply_angles)

        dpg.add_separator()
        dpg.add_text("Control")
        _add_slider_with_range("flControlDeadbandDegrees", obConfig.flControlDeadbandDegrees, dItems)
        _add_slider_with_range("flTrackingMinConfidenceForControl", obConfig.flTrackingMinConfidenceForControl, dItems)
        def _on_apply_control():
            obConfig.flControlDeadbandDegrees = float(dpg.get_value(dItems["flControlDeadbandDegrees"]))
            obTracker.set_deadband_degrees(obConfig.flControlDeadbandDegrees)
            obConfig.flTrackingMinConfidenceForControl = float(dpg.get_value(dItems["flTrackingMinConfidenceForControl"]))
            obTracker.set_tracking_confidence_threshold(obConfig.flTrackingMinConfidenceForControl)
        dpg.add_button(label="Apply Control", callback=_on_apply_control)

        dpg.add_text("Algorithm")
        iAlgo = dpg.add_radio_button(items=["P", "PID", "Velocity"], default_value=obConfig.sControlAlgorithmType, horizontal=True)
        _add_slider_with_range("flControlProportionalGain", obConfig.flControlProportionalGain, dItems)
        _add_slider_with_range("flControlIntegralGain", obConfig.flControlIntegralGain, dItems)
        _add_slider_with_range("flControlDerivativeGain", obConfig.flControlDerivativeGain, dItems)
        _add_slider_with_range("flVelocityGain", obConfig.flVelocityGain, dItems)
        _add_slider_with_range("flMaxVelocityDegreesPerSecond", obConfig.flMaxVelocityDegreesPerSecond, dItems)
        _add_slider_with_range("flVelocitySmoothingAlpha", obConfig.flVelocitySmoothingAlpha, dItems)
        def _on_apply_algo():
            obConfig.sControlAlgorithmType = str(dpg.get_value(iAlgo))
            obConfig.flControlProportionalGain = float(dpg.get_value(dItems["flControlProportionalGain"]))
            obConfig.flControlIntegralGain = float(dpg.get_value(dItems["flControlIntegralGain"]))
            obConfig.flControlDerivativeGain = float(dpg.get_value(dItems["flControlDerivativeGain"]))
            obConfig.flVelocityGain = float(dpg.get_value(dItems["flVelocityGain"]))
            obConfig.flMaxVelocityDegreesPerSecond = float(dpg.get_value(dItems["flMaxVelocityDegreesPerSecond"]))
            obConfig.flVelocitySmoothingAlpha = float(dpg.get_value(dItems["flVelocitySmoothingAlpha"]))
            _apply_control_algorithm(obTracker, obConfig)
        dpg.add_button(label="Apply Algorithm", callback=_on_apply_algo)

        dpg.add_separator()
        dpg.add_text("FOV")
        _add_slider_with_range("flInitialAnglePerPixelDegrees", obConfig.flInitialAnglePerPixelDegrees, dItems)
        def _on_apply_fov():
            obConfig.flInitialAnglePerPixelDegrees = float(dpg.get_value(dItems["flInitialAnglePerPixelDegrees"]))
            obTracker.set_fov_angle_per_pixel(obConfig.flInitialAnglePerPixelDegrees)
        dpg.add_button(label="Apply FOV", callback=_on_apply_fov)

        dpg.add_separator()
        dpg.add_text("Visualization")
        iShow = dpg.add_checkbox(label="bShowDebugInfo", default_value=obConfig.bShowDebugInfo)
        iEnableVis = dpg.add_checkbox(label="bEnableVisualization", default_value=obConfig.bEnableVisualization)
        iDeadzoneOverlay = dpg.add_checkbox(label="bEnableDeadzoneOverlay", default_value=obConfig.bEnableDeadzoneOverlay)
        _add_slider_with_range("iCenterDeadzoneRadiusPixels", obConfig.iCenterDeadzoneRadiusPixels, dItems)
        def _on_apply_vis():
            obConfig.bShowDebugInfo = bool(dpg.get_value(iShow))
            obConfig.bEnableVisualization = bool(dpg.get_value(iEnableVis))
            obConfig.bEnableDeadzoneOverlay = bool(dpg.get_value(iDeadzoneOverlay))
            obConfig.iCenterDeadzoneRadiusPixels = int(dpg.get_value(dItems["iCenterDeadzoneRadiusPixels"]))
        dpg.add_button(label="Apply Visualization", callback=_on_apply_vis)

        dpg.add_separator()
        def _on_save():
            obConfigManager.save_configuration_to_file("config/user_config.json")
        dpg.add_button(label="Save Config", callback=_on_save)

    def _run():
        dpg.show_viewport()
        dpg.start_dearpygui()
        dpg.destroy_context()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t