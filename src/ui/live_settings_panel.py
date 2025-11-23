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
        "flMotorMaxSpeedStepsPerSecond": (50.0, 25000.0, 100.0),
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


def _add_slider_with_range(sKey: str, vDefault: float, dItems: Dict[str, int], fnOnChange=None, bInteger: bool = False, iWidth: int = 500):
    flMin, flMax, _ = _ranges().get(sKey, (0.0, 100.0, 1.0))
    with dpg.group():
        iMin = dpg.add_input_float(label=f"{sKey} min", default_value=flMin, width=120)
        iMax = dpg.add_input_float(label=f"{sKey} max", default_value=flMax, width=120)
        def _apply_range():
            dpg.configure_item(dItems[sKey], min_value=dpg.get_value(iMin), max_value=dpg.get_value(iMax))
        dpg.set_item_callback(iMin, lambda s, a, u: _apply_range())
        dpg.set_item_callback(iMax, lambda s, a, u: _apply_range())
    if bInteger:
        dItems[sKey] = dpg.add_slider_int(label=sKey, default_value=int(vDefault), min_value=int(flMin), max_value=int(flMax), width=iWidth, callback=lambda s, a, u: fnOnChange and fnOnChange(int(a)))
        dpg.add_input_int(label=f"{sKey} value", default_value=int(vDefault), callback=lambda s, a, u: dpg.set_value(dItems[sKey], int(a)))
    else:
        dItems[sKey] = dpg.add_slider_float(label=sKey, default_value=float(vDefault), min_value=flMin, max_value=flMax, width=iWidth, callback=lambda s, a, u: fnOnChange and fnOnChange(float(a)))
        dpg.add_input_float(label=f"{sKey} value", default_value=float(vDefault), callback=lambda s, a, u: dpg.set_value(dItems[sKey], float(a)))


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

def _bind_global_scale():
    try:
        dpg.set_global_font_scale(1.2)
    except Exception:
        pass


# Center angle external bridge
_g_iCenterAngleItem: int | None = None
_g_bProgrammaticUpdate: bool = False

def update_center_angle_slider(flAngleDegrees: float):
    global _g_iCenterAngleItem, _g_bProgrammaticUpdate
    if _g_iCenterAngleItem is None:
        return
    _g_bProgrammaticUpdate = True
    try:
        dpg.set_value(_g_iCenterAngleItem, float(flAngleDegrees))
    finally:
        _g_bProgrammaticUpdate = False


def start_live_settings_panel(
    obConfigManager: ConfigurationManager,
    obTracker: TrackerController,
    obMotor: MotorInterface,
    obCamera: CameraInterface,
    obFOV: FieldOfViewEstimator
):
    dpg.create_context()
    # Position the settings viewport to the right side of the primary monitor and full height
    try:
        import ctypes
        iScreenW = ctypes.windll.user32.GetSystemMetrics(0)
        iScreenH = ctypes.windll.user32.GetSystemMetrics(1)
    except Exception:
        iScreenW, iScreenH = 1600, 900
    iPanelW = 560
    dpg.create_viewport(title="Live Settings", width=iPanelW, height=iScreenH, x_pos=max(0, iScreenW - iPanelW), y_pos=0)
    dpg.setup_dearpygui()
    _bind_global_scale()

    obConfig = obConfigManager.get_system_configuration()
    dItems: Dict[str, int] = {}

    with dpg.window(label="Settings", width=iPanelW - 20, height=iScreenH - 40):
        dpg.add_text("Center")
        # Center angle slider at top; controls absolute motor angle
        flCurrentAngle = obTracker.get_current_motor_angle_degrees()
        dMin = obConfig.flMotorMinAngleDegrees
        dMax = obConfig.flMotorMaxAngleDegrees
        def _on_center_change(v):
            nonlocal obMotor
            if _g_bProgrammaticUpdate:
                return
            obMotor.send_move_to_angle_command(float(v))
        # Create min/max vertically and slider with 500px width
        with dpg.group():
            iMinCenter = dpg.add_input_float(label="flManualCenterAngleDegrees min", default_value=dMin, width=120)
            iMaxCenter = dpg.add_input_float(label="flManualCenterAngleDegrees max", default_value=dMax, width=120)
            _g_iCenterAngleItem = dpg.add_slider_float(label="flManualCenterAngleDegrees", default_value=float(flCurrentAngle), min_value=float(dMin), max_value=float(dMax), width=500, callback=lambda s, a, u: _on_center_change(float(a)))
            def _apply_center_range():
                dpg.configure_item(_g_iCenterAngleItem, min_value=dpg.get_value(iMinCenter), max_value=dpg.get_value(iMaxCenter))
            dpg.set_item_callback(iMinCenter, lambda s, a, u: _apply_center_range())
            dpg.set_item_callback(iMaxCenter, lambda s, a, u: _apply_center_range())
        dpg.add_text("Motor")
        _add_slider_with_range(
            "flMotorMaxSpeedStepsPerSecond",
            obConfig.flMotorMaxSpeedStepsPerSecond,
            dItems,
            fnOnChange=lambda v: (
                setattr(obConfig, 'flMotorMaxSpeedStepsPerSecond', float(v)),
                _apply_motor_settings(obMotor, obConfig)
            )
        , iWidth=500)
        _add_slider_with_range(
            "flMotorMaxAccelerationStepsPerSecondSquared",
            obConfig.flMotorMaxAccelerationStepsPerSecondSquared,
            dItems,
            fnOnChange=lambda v: (
                setattr(obConfig, 'flMotorMaxAccelerationStepsPerSecondSquared', float(v)),
                _apply_motor_settings(obMotor, obConfig)
            )
        , iWidth=500)

        dpg.add_separator()
        dpg.add_text("Angles")
        _add_slider_with_range(
            "flMotorMinAngleDegrees",
            obConfig.flMotorMinAngleDegrees,
            dItems,
            fnOnChange=lambda v: (
                setattr(obConfig, 'flMotorMinAngleDegrees', float(v)),
                obTracker.set_angle_limits(float(v), obConfig.flMotorMaxAngleDegrees)
            )
        , iWidth=500)
        _add_slider_with_range(
            "flMotorMaxAngleDegrees",
            obConfig.flMotorMaxAngleDegrees,
            dItems,
            fnOnChange=lambda v: (
                setattr(obConfig, 'flMotorMaxAngleDegrees', float(v)),
                obTracker.set_angle_limits(obConfig.flMotorMinAngleDegrees, float(v))
            )
        , iWidth=500)

        dpg.add_separator()
        dpg.add_text("Control")
        _add_slider_with_range(
            "flControlDeadbandDegrees",
            obConfig.flControlDeadbandDegrees,
            dItems,
            fnOnChange=lambda v: (
                setattr(obConfig, 'flControlDeadbandDegrees', float(v)),
                obTracker.set_deadband_degrees(float(v))
            )
        , iWidth=500)
        _add_slider_with_range(
            "flTrackingMinConfidenceForControl",
            obConfig.flTrackingMinConfidenceForControl,
            dItems,
            fnOnChange=lambda v: (
                setattr(obConfig, 'flTrackingMinConfidenceForControl', float(v)),
                obTracker.set_tracking_confidence_threshold(float(v))
            )
        , iWidth=500)

        dpg.add_text("Algorithm")
        iAlgo = dpg.add_radio_button(items=["P", "PID", "Velocity"], default_value=obConfig.sControlAlgorithmType, horizontal=True)
        dpg.set_item_callback(iAlgo, lambda s, a, u: (
            setattr(obConfig, 'sControlAlgorithmType', str(a)),
            _apply_control_algorithm(obTracker, obConfig)
        ))
        _add_slider_with_range(
            "flControlProportionalGain",
            obConfig.flControlProportionalGain,
            dItems,
            fnOnChange=lambda v: (
                setattr(obConfig, 'flControlProportionalGain', float(v)),
                isinstance(obTracker.obControlAlgorithm, ProportionalController) and obTracker.obControlAlgorithm.set_proportional_gain(float(v)),
                isinstance(obTracker.obControlAlgorithm, PIDController) and obTracker.obControlAlgorithm.set_gains(float(v), obConfig.flControlIntegralGain, obConfig.flControlDerivativeGain)
            )
        , iWidth=500)
        _add_slider_with_range(
            "flControlIntegralGain",
            obConfig.flControlIntegralGain,
            dItems,
            fnOnChange=lambda v: (
                setattr(obConfig, 'flControlIntegralGain', float(v)),
                isinstance(obTracker.obControlAlgorithm, PIDController) and obTracker.obControlAlgorithm.set_gains(obConfig.flControlProportionalGain, float(v), obConfig.flControlDerivativeGain)
            )
        , iWidth=500)
        _add_slider_with_range(
            "flControlDerivativeGain",
            obConfig.flControlDerivativeGain,
            dItems,
            fnOnChange=lambda v: (
                setattr(obConfig, 'flControlDerivativeGain', float(v)),
                isinstance(obTracker.obControlAlgorithm, PIDController) and obTracker.obControlAlgorithm.set_gains(obConfig.flControlProportionalGain, obConfig.flControlIntegralGain, float(v))
            )
        , iWidth=500)
        _add_slider_with_range(
            "flVelocityGain",
            obConfig.flVelocityGain,
            dItems,
            fnOnChange=lambda v: (
                setattr(obConfig, 'flVelocityGain', float(v)),
                isinstance(obTracker.obControlAlgorithm, VelocityController) and obTracker.obControlAlgorithm.set_parameters(float(v), obConfig.flMaxVelocityDegreesPerSecond, obConfig.flVelocitySmoothingAlpha)
            )
        , iWidth=500)
        _add_slider_with_range(
            "flMaxVelocityDegreesPerSecond",
            obConfig.flMaxVelocityDegreesPerSecond,
            dItems,
            fnOnChange=lambda v: (
                setattr(obConfig, 'flMaxVelocityDegreesPerSecond', float(v)),
                isinstance(obTracker.obControlAlgorithm, VelocityController) and obTracker.obControlAlgorithm.set_parameters(obConfig.flVelocityGain, float(v), obConfig.flVelocitySmoothingAlpha)
            )
        , iWidth=500)
        _add_slider_with_range(
            "flVelocitySmoothingAlpha",
            obConfig.flVelocitySmoothingAlpha,
            dItems,
            fnOnChange=lambda v: (
                setattr(obConfig, 'flVelocitySmoothingAlpha', float(v)),
                isinstance(obTracker.obControlAlgorithm, VelocityController) and obTracker.obControlAlgorithm.set_parameters(obConfig.flVelocityGain, obConfig.flMaxVelocityDegreesPerSecond, float(v))
            )
        , iWidth=500)

        dpg.add_separator()
        dpg.add_text("FOV")
        iWidth, _ = obCamera.get_frame_dimensions()
        flInitialFOV = obFOV.get_estimated_field_of_view_degrees(iWidth)
        # FOV degrees slider maps to angle-per-pixel internally
        def _on_fov_change(flFovDegrees: float):
            flAnglePerPixel = float(flFovDegrees) / float(max(1, iWidth))
            obConfig.flInitialAnglePerPixelDegrees = flAnglePerPixel
            obTracker.set_fov_angle_per_pixel(flAnglePerPixel)
        # Use a dedicated range for FOV degrees
        dItems["flFieldOfViewDegrees"] = None
        with dpg.group():
            iMinFov = dpg.add_input_float(label="flFieldOfViewDegrees min", default_value=10.0, width=120)
            iMaxFov = dpg.add_input_float(label="flFieldOfViewDegrees max", default_value=180.0, width=120)
            def _apply_fov_range():
                dpg.configure_item(dItems["flFieldOfViewDegrees"], min_value=dpg.get_value(iMinFov), max_value=dpg.get_value(iMaxFov))
            dpg.set_item_callback(iMinFov, lambda s, a, u: _apply_fov_range())
            dpg.set_item_callback(iMaxFov, lambda s, a, u: _apply_fov_range())
        dItems["flFieldOfViewDegrees"] = dpg.add_slider_float(label="flFieldOfViewDegrees", default_value=float(flInitialFOV), min_value=10.0, max_value=180.0, width=500, callback=lambda s, a, u: _on_fov_change(float(a)))
        dpg.add_input_float(label="flFieldOfViewDegrees value", default_value=float(flInitialFOV), callback=lambda s, a, u: dpg.set_value(dItems["flFieldOfViewDegrees"], float(a)))

        dpg.add_separator()
        dpg.add_text("Visualization")
        iShow = dpg.add_checkbox(label="bShowDebugInfo", default_value=obConfig.bShowDebugInfo)
        dpg.set_item_callback(iShow, lambda s, a, u: setattr(obConfig, 'bShowDebugInfo', bool(a)))
        iEnableVis = dpg.add_checkbox(label="bEnableVisualization", default_value=obConfig.bEnableVisualization)
        dpg.set_item_callback(iEnableVis, lambda s, a, u: setattr(obConfig, 'bEnableVisualization', bool(a)))
        iDeadzoneOverlay = dpg.add_checkbox(label="bEnableDeadzoneOverlay", default_value=obConfig.bEnableDeadzoneOverlay)
        dpg.set_item_callback(iDeadzoneOverlay, lambda s, a, u: setattr(obConfig, 'bEnableDeadzoneOverlay', bool(a)))
        _add_slider_with_range(
            "iCenterDeadzoneRadiusPixels",
            obConfig.iCenterDeadzoneRadiusPixels,
            dItems,
            fnOnChange=lambda v: setattr(obConfig, 'iCenterDeadzoneRadiusPixels', int(v)),
            bInteger=True,
            iWidth=500
        )

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