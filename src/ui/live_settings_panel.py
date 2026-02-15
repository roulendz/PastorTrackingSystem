import threading
import json
import logging
from dataclasses import asdict
from typing import Dict, Any, Tuple
import dearpygui.dearpygui as dpg

logger = logging.getLogger(__name__)

from utilities.config_manager import ConfigurationManager
from control.tracker_controller import TrackerController
from interfaces.motor_interface import MotorInterface
from interfaces.camera_interface import CameraInterface
from tracking.pose_tracker import PoseTracker
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
        "flInitialAnglePerPixelDegrees": (0.00001, 1.0, 0.00005),
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


def _attach_double_click_reset(iItem: int, fnReset):
    with dpg.item_handler_registry() as obReg:
        dpg.add_item_double_clicked_handler(callback=lambda s, a, u: fnReset())
    dpg.bind_item_handler_registry(iItem, obReg)


def _add_section_header_with_tooltip(sLabel: str, sTooltip: str):
    with dpg.group(horizontal=True):
        dpg.add_text(sLabel)
        iInfo = dpg.add_button(label="?", width=22)
        with dpg.tooltip(iInfo):
            dpg.add_text(sTooltip)


def _get_section_tooltips() -> Dict[str, str]:
    return {
        "Center": (
            "flManualCenterAngleDegrees: Absolute motor angle in degrees.\n"
            "Changing this sends an immediate move command to the motor.\n"
            "Range is limited by flMotorMinAngleDegrees and flMotorMaxAngleDegrees.\n"
            "Dragging the center line in the video updates this value.\n"
            "'Set current angle as HOME' sets current position as 0° on the controller."
        ),
        "Motor": (
            "flMotorMaxSpeedStepsPerSecond: Maximum step rate (steps/s) applied to the driver.\n"
            "flMotorMaxAccelerationStepsPerSecondSquared: Maximum acceleration (steps/s²).\n"
            "Both parameters are sent to the motor immediately and affect all moves."
        ),
        "Angles": (
            "flMotorMinAngleDegrees & flMotorMaxAngleDegrees: Allowed mechanical angle window.\n"
            "The controller clamps every commanded target into this range.\n"
            "Also defines the range of the Center slider and must satisfy min < max."
        ),
        "Control": (
            "flControlDeadbandDegrees: Angular tolerance around the virtual home line;\n"
            "errors within this are ignored and position is held.\n"
            "flTrackingMinConfidenceForControl: Minimum detection confidence required\n"
            "before any correction is applied."
        ),
        "Algorithm": (
            "P: Outputs Kp * error (fast, simple).\n"
            "PID: Adds integral (Ki) with anti-windup and derivative (Kd) damping.\n"
            "Velocity: Converts error to velocity, clamps to max velocity, smooths with alpha,\n"
            "and integrates to position change."
        ),
        "FOV": (
            "flFieldOfViewDegrees: Visual field of view of the camera.\n"
            "Used to convert pixel error to angle error (angle-per-pixel = FOV / image width).\n"
            "This value is treated as fixed during runtime."
        ),
        "Visualization": (
            "bEnableVisualization: Toggles drawing of the overlay on the video.\n"
            "bShowDebugInfo: Shows status text (FPS, motor angle, controls).\n"
            "bEnableDeadzoneOverlay: Shows draggable deadzone lines around the home line.\n"
            "iCenterDeadzoneRadiusPixels: Pixel radius around center where small errors are ignored\n"
            "and a circle is drawn around the detected person."
        ),
    }


def _build_algorithm_tabs(obConfig, obTracker: TrackerController, dItems: Dict[str, int]):
    def _on_tab_change(sender, app_data, user_data):
        try:
            sLabel = dpg.get_item_label(app_data)
            setattr(obConfig, 'sControlAlgorithmType', str(sLabel))
            _apply_control_algorithm(obTracker, obConfig)
        except Exception:
            logger.debug("DearPyGui operation skipped")
    with dpg.tab_bar(callback=_on_tab_change) as iAlgoBar:
        with dpg.tab(label="P") as iTabP:
            _add_slider_with_range(
                "flControlProportionalGain",
                obConfig.flControlProportionalGain,
                dItems,
                fnOnChange=lambda v: (
                    setattr(obConfig, 'flControlProportionalGain', float(v)),
                    isinstance(obTracker.obControlAlgorithm, ProportionalController) and obTracker.obControlAlgorithm.set_proportional_gain(float(v)),
                    isinstance(obTracker.obControlAlgorithm, PIDController) and obTracker.obControlAlgorithm.set_gains(float(v), obConfig.flControlIntegralGain, obConfig.flControlDerivativeGain)
                ),
                iWidth=500
            )
        with dpg.tab(label="PID") as iTabPID:
            _add_slider_with_range(
                "flControlProportionalGain",
                obConfig.flControlProportionalGain,
                dItems,
                fnOnChange=lambda v: (
                    setattr(obConfig, 'flControlProportionalGain', float(v)),
                    isinstance(obTracker.obControlAlgorithm, PIDController) and obTracker.obControlAlgorithm.set_gains(float(v), obConfig.flControlIntegralGain, obConfig.flControlDerivativeGain)
                ),
                iWidth=500
            )
            _add_slider_with_range(
                "flControlIntegralGain",
                obConfig.flControlIntegralGain,
                dItems,
                fnOnChange=lambda v: (
                    setattr(obConfig, 'flControlIntegralGain', float(v)),
                    isinstance(obTracker.obControlAlgorithm, PIDController) and obTracker.obControlAlgorithm.set_gains(obConfig.flControlProportionalGain, float(v), obConfig.flControlDerivativeGain)
                ),
                iWidth=500
            )
            _add_slider_with_range(
                "flControlDerivativeGain",
                obConfig.flControlDerivativeGain,
                dItems,
                fnOnChange=lambda v: (
                    setattr(obConfig, 'flControlDerivativeGain', float(v)),
                    isinstance(obTracker.obControlAlgorithm, PIDController) and obTracker.obControlAlgorithm.set_gains(obConfig.flControlProportionalGain, obConfig.flControlIntegralGain, float(v))
                ),
                iWidth=500
            )
        with dpg.tab(label="Velocity") as iTabVel:
            _add_slider_with_range(
                "flVelocityGain",
                obConfig.flVelocityGain,
                dItems,
                fnOnChange=lambda v: (
                    setattr(obConfig, 'flVelocityGain', float(v)),
                    isinstance(obTracker.obControlAlgorithm, VelocityController) and obTracker.obControlAlgorithm.set_parameters(float(v), obConfig.flMaxVelocityDegreesPerSecond, obConfig.flVelocitySmoothingAlpha)
                ),
                iWidth=500
            )
            _add_slider_with_range(
                "flMaxVelocityDegreesPerSecond",
                obConfig.flMaxVelocityDegreesPerSecond,
                dItems,
                fnOnChange=lambda v: (
                    setattr(obConfig, 'flMaxVelocityDegreesPerSecond', float(v)),
                    isinstance(obTracker.obControlAlgorithm, VelocityController) and obTracker.obControlAlgorithm.set_parameters(obConfig.flVelocityGain, float(v), obConfig.flVelocitySmoothingAlpha)
                ),
                iWidth=500
            )
            _add_slider_with_range(
                "flVelocitySmoothingAlpha",
                obConfig.flVelocitySmoothingAlpha,
                dItems,
                fnOnChange=lambda v: (
                    setattr(obConfig, 'flVelocitySmoothingAlpha', float(v)),
                    isinstance(obTracker.obControlAlgorithm, VelocityController) and obTracker.obControlAlgorithm.set_parameters(obConfig.flVelocityGain, obConfig.flMaxVelocityDegreesPerSecond, float(v))
                ),
                iWidth=500
            )
    try:
        if obConfig.sControlAlgorithmType == "PID":
            dpg.set_value(iAlgoBar, iTabPID)
        elif obConfig.sControlAlgorithmType == "Velocity":
            dpg.set_value(iAlgoBar, iTabVel)
        else:
            dpg.set_value(iAlgoBar, iTabP)
    except Exception:
        logger.debug("DearPyGui operation skipped")

def _add_slider_with_range(sKey: str, vDefault: float, dItems: Dict[str, int], fnOnChange=None, bInteger: bool = False, iWidth: int = 500):
    flMin, flMax, flStep = _ranges().get(sKey, (0.0, 100.0, 0.0))
    try:
        dSaved = _g_dSavedUI.get(sKey, {})
        flMin = float(dSaved.get("min", flMin))
        flMax = float(dSaved.get("max", flMax))
    except Exception:
        logger.debug("DearPyGui operation skipped")
    with dpg.group(horizontal=True):
        iInputWidth = min(120, max(60, int((iWidth - (1 * 32) - 60) // 2)))
        dpg.add_text("min")
        iMin = dpg.add_input_float(label="", default_value=flMin, width=iInputWidth)
        dpg.add_text("max")
        iMax = dpg.add_input_float(label="", default_value=flMax, width=iInputWidth)
        iMaxInfo = dpg.add_button(label="?", width=22)
        with dpg.tooltip(iMaxInfo):
            dpg.add_text(f"{sKey}")
        def _apply_range():
            dpg.configure_item(dItems[sKey], min_value=dpg.get_value(iMin), max_value=dpg.get_value(iMax))
        dpg.set_item_callback(iMin, lambda s, a, u: _apply_range())
        dpg.set_item_callback(iMax, lambda s, a, u: _apply_range())
    if bInteger:
        dItems[sKey] = dpg.add_slider_int(label="", default_value=int(vDefault), min_value=int(flMin), max_value=int(flMax), width=iWidth)
        iValue = dpg.add_input_int(label="value", default_value=int(vDefault), width=iWidth)
        bProgrammatic = False
        def _on_slider_change_int(a: int):
            nonlocal bProgrammatic
            bProgrammatic = True
            dpg.set_value(iValue, int(a))
            if fnOnChange:
                fnOnChange(int(a))
            bProgrammatic = False
        def _on_input_change_int(a: int):
            nonlocal bProgrammatic
            if bProgrammatic:
                return
            bProgrammatic = True
            dpg.set_value(dItems[sKey], int(a))
            if fnOnChange:
                fnOnChange(int(a))
            bProgrammatic = False
        dpg.set_item_callback(dItems[sKey], lambda s, a, u: _on_slider_change_int(int(a)))
        dpg.set_item_callback(iValue, lambda s, a, u: _on_input_change_int(int(a)))
        def _reset_int():
            dpg.set_value(dItems[sKey], int(vDefault))
            dpg.set_value(iValue, int(vDefault))
            if fnOnChange:
                fnOnChange(int(vDefault))
        _attach_double_click_reset(dItems[sKey], _reset_int)
        _attach_double_click_reset(iValue, _reset_int)
        _g_dItemsMeta[sKey] = {"min": iMin, "max": iMax, "slider": dItems[sKey], "input": iValue}
    else:
        sFormat = "%.8f" if sKey == "flInitialAnglePerPixelDegrees" else "%.3f"
        dItems[sKey] = dpg.add_slider_float(label="", default_value=float(vDefault), min_value=flMin, max_value=flMax, width=iWidth, format=sFormat)
        iValue = dpg.add_input_float(label="value", default_value=float(vDefault), width=iWidth)
        bProgrammatic = False
        def _on_slider_change_float(a: float):
            nonlocal bProgrammatic
            bProgrammatic = True
            try:
                if float(flStep) > 0.0:
                    a = round(float(a) / float(flStep)) * float(flStep)
            except Exception:
                logger.debug("DearPyGui operation skipped")
            dpg.set_value(iValue, float(a))
            if fnOnChange:
                fnOnChange(float(a))
            bProgrammatic = False
        def _on_input_change_float(a: float):
            nonlocal bProgrammatic
            if bProgrammatic:
                return
            bProgrammatic = True
            try:
                if float(flStep) > 0.0:
                    a = round(float(a) / float(flStep)) * float(flStep)
            except Exception:
                logger.debug("DearPyGui operation skipped")
            dpg.set_value(dItems[sKey], float(a))
            if fnOnChange:
                fnOnChange(float(a))
            bProgrammatic = False
        dpg.set_item_callback(dItems[sKey], lambda s, a, u: _on_slider_change_float(float(a)))
        dpg.set_item_callback(iValue, lambda s, a, u: _on_input_change_float(float(a)))
        def _reset_float():
            dpg.set_value(dItems[sKey], float(vDefault))
            dpg.set_value(iValue, float(vDefault))
            if fnOnChange:
                fnOnChange(float(vDefault))
        _attach_double_click_reset(dItems[sKey], _reset_float)
        _attach_double_click_reset(iValue, _reset_float)
        _g_dItemsMeta[sKey] = {"min": iMin, "max": iMax, "slider": dItems[sKey], "input": iValue}


_g_dItemsMeta: Dict[str, Dict[str, int]] = {}
_g_dSavedUI: Dict[str, Any] = {}

def _load_saved_ui_meta():
    global _g_dSavedUI
    try:
        with open("config/user_config.json", "r") as f:
            d = json.load(f)
        _g_dSavedUI = d.get("ui", {}).get("controls", {})
    except Exception:
        logger.debug("DearPyGui operation skipped")
        _g_dSavedUI = {}

def _save_full_config(obConfigManager: ConfigurationManager):
    try:
        obConfig = obConfigManager.get_system_configuration()
        dConfig = asdict(obConfig)
        dUI: Dict[str, Any] = {"controls": {}}
        for sKey, dMeta in _g_dItemsMeta.items():
            dEntry: Dict[str, Any] = {}
            if "min" in dMeta:
                dEntry["min"] = dpg.get_value(dMeta["min"])
            if "max" in dMeta:
                dEntry["max"] = dpg.get_value(dMeta["max"])
            if "slider" in dMeta:
                dEntry["slider_value"] = dpg.get_value(dMeta["slider"])
            if "input" in dMeta:
                dEntry["input_value"] = dpg.get_value(dMeta["input"])
            dUI["controls"][sKey] = dEntry
        dConfig["ui"] = dUI
        with open("config/user_config.json", "w") as f:
            json.dump(dConfig, f, indent=4)
    except (IOError, OSError) as e:
        logger.error(f"Failed to save config from settings panel: {e}")

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
            logger.debug("DearPyGui operation skipped")


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
    except Exception as e:
        logger.debug(f"Screen metrics fallback: {e}")


# Center angle external bridge
_g_iCenterAngleItem: int | None = None
_g_iFovDegreesItem: int | None = None
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

def update_fov_degrees_slider(flFovDegrees: float):
    global _g_iFovDegreesItem, _g_bProgrammaticUpdate
    if _g_iFovDegreesItem is None:
        return
    _g_bProgrammaticUpdate = True
    try:
        dpg.set_value(_g_iFovDegreesItem, float(flFovDegrees))
    finally:
        _g_bProgrammaticUpdate = False


def start_live_settings_panel(
    obConfigManager: ConfigurationManager,
    obTracker: TrackerController,
    obMotor: MotorInterface,
    obCamera: CameraInterface
):
    dpg.create_context()
    # Position the settings viewport to the right side of the primary monitor and full height
    try:
        import ctypes
        iScreenW = ctypes.windll.user32.GetSystemMetrics(0)
        iScreenH = ctypes.windll.user32.GetSystemMetrics(1)
    except Exception as e:
        logger.debug(f"Screen metrics fallback: {e}")
        iScreenW, iScreenH = 1600, 900
    iPanelW = 560
    dpg.create_viewport(title="Live Settings", width=iPanelW, height=iScreenH, x_pos=max(0, iScreenW - iPanelW), y_pos=0)
    dpg.setup_dearpygui()
    _bind_global_scale()

    obConfig = obConfigManager.get_system_configuration()
    dItems: Dict[str, int] = {}

    with dpg.window(label="Settings", width=iPanelW - 20, height=iScreenH - 40):
        _load_saved_ui_meta()
        with dpg.handler_registry():
            def _on_key_s():
                try:
                    if dpg.is_key_down(dpg.mvKey_Control):
                        _save_full_config(obConfigManager)
                except Exception:
                    logger.debug("DearPyGui operation skipped")
            dpg.add_key_press_handler(key=dpg.mvKey_S, callback=_on_key_s)
            def _on_key_r():
                try:
                    obMotor.send_reset_position_command()
                    update_center_angle_slider(0.0)
                except Exception:
                    logger.debug("DearPyGui operation skipped")
            dpg.add_key_press_handler(key=dpg.mvKey_R, callback=_on_key_r)
        dTips = _get_section_tooltips()
        _add_section_header_with_tooltip("Center", dTips.get("Center", ""))
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
        with dpg.group(horizontal=True):
            iCenterWidth = 140
            dpg.add_text("min")
            iMinCenter = dpg.add_input_float(label="", default_value=dMin, width=iCenterWidth)
            dpg.add_text("max")
            iMaxCenter = dpg.add_input_float(label="", default_value=dMax, width=iCenterWidth)
            iMaxCenterInfo = dpg.add_button(label="?", width=22)
            with dpg.tooltip(iMaxCenterInfo):
                dpg.add_text("flManualCenterAngleDegrees")
        _g_iCenterAngleItem = dpg.add_slider_float(label="", default_value=float(flCurrentAngle), min_value=float(dMin), max_value=float(dMax), width=500, callback=lambda s, a, u: _on_center_change(float(a)))
        def _apply_center_range():
            dpg.configure_item(_g_iCenterAngleItem, min_value=dpg.get_value(iMinCenter), max_value=dpg.get_value(iMaxCenter))
        dpg.set_item_callback(iMinCenter, lambda s, a, u: _apply_center_range())
        dpg.set_item_callback(iMaxCenter, lambda s, a, u: _apply_center_range())
        _g_dItemsMeta["flManualCenterAngleDegrees"] = {"min": iMinCenter, "max": iMaxCenter, "slider": _g_iCenterAngleItem}
        def _on_set_current_home():
            obMotor.send_reset_position_command()
            update_center_angle_slider(0.0)
        dpg.add_button(label="Set current angle as HOME (0°)", callback=_on_set_current_home)
        _add_section_header_with_tooltip("Motor", dTips.get("Motor", ""))
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
        _add_section_header_with_tooltip("Angles", dTips.get("Angles", ""))
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
        _add_section_header_with_tooltip("Control", dTips.get("Control", ""))
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

        _add_section_header_with_tooltip("Algorithm", dTips.get("Algorithm", ""))
        _build_algorithm_tabs(obConfig, obTracker, dItems)

        dpg.add_separator()
        _add_section_header_with_tooltip("FOV", dTips.get("FOV", ""))
        iWidth, _ = obCamera.get_frame_dimensions()
        flConfigFov = float(getattr(obConfig, 'flFieldOfViewDegrees', 0.0))
        if flConfigFov > 0.0:
            flInitialFOV = flConfigFov
        else:
            flApx = float(getattr(obConfig, 'flInitialAnglePerPixelDegrees', 0.0))
            flInitialFOV = flApx * float(iWidth) if iWidth > 0 else 0.0
        try:
            dSavedFov = _g_dSavedUI.get("flFieldOfViewDegrees", {})
            flSavedMin = float(dSavedFov.get("min", 10.0))
            flSavedMax = float(dSavedFov.get("max", 180.0))
            flSavedSliderValue = float(dSavedFov.get("slider_value", flInitialFOV))
        except Exception:
            logger.debug("DearPyGui operation skipped")
            flSavedMin, flSavedMax, flSavedSliderValue = 10.0, 180.0, flInitialFOV
        def _on_fov_change(flFovDegrees: float):
            try:
                obConfig.flFieldOfViewDegrees = float(flFovDegrees)
            except Exception:
                logger.debug("DearPyGui operation skipped")
        # Use a dedicated range for FOV degrees
        dItems["flFieldOfViewDegrees"] = None
        with dpg.group(horizontal=True):
            dpg.add_text("min")
            iMinFov = dpg.add_input_float(label="", default_value=float(flSavedMin), width=180)
            dpg.add_text("max")
            iMaxFov = dpg.add_input_float(label="", default_value=float(flSavedMax), width=180)
            iMaxFovInfo = dpg.add_button(label="?", width=22)
            with dpg.tooltip(iMaxFovInfo):
                dpg.add_text("flFieldOfViewDegrees")
            def _apply_fov_range():
                dpg.configure_item(dItems["flFieldOfViewDegrees"], min_value=dpg.get_value(iMinFov), max_value=dpg.get_value(iMaxFov))
            dpg.set_item_callback(iMinFov, lambda s, a, u: _apply_fov_range())
            dpg.set_item_callback(iMaxFov, lambda s, a, u: _apply_fov_range())
        dItems["flFieldOfViewDegrees"] = dpg.add_slider_float(label="", default_value=float(flSavedSliderValue), min_value=float(flSavedMin), max_value=float(flSavedMax), width=500, callback=lambda s, a, u: _on_fov_change(float(a)))
        global _g_iFovDegreesItem
        _g_iFovDegreesItem = dItems["flFieldOfViewDegrees"]
        iFovValue = dpg.add_input_float(label="value", default_value=float(flSavedSliderValue), width=500, callback=lambda s, a, u: (_on_fov_change(float(a)), dpg.set_value(dItems["flFieldOfViewDegrees"], float(a))))
        def _reset_fov():
            dpg.set_value(dItems["flFieldOfViewDegrees"], float(flInitialFOV))
            dpg.set_value(iFovValue, float(flInitialFOV))
            _on_fov_change(float(flInitialFOV))
        _attach_double_click_reset(dItems["flFieldOfViewDegrees"], _reset_fov)
        _attach_double_click_reset(iFovValue, _reset_fov)
        _g_dItemsMeta["flFieldOfViewDegrees"] = {"min": iMinFov, "max": iMaxFov, "slider": dItems["flFieldOfViewDegrees"], "input": iFovValue}

        # Angle-per-pixel override slider (direct)
        def _on_apx_change(flAnglePerPixel: float):
            obConfig.flInitialAnglePerPixelDegrees = float(flAnglePerPixel)
        _add_slider_with_range(
            "flInitialAnglePerPixelDegrees",
            obConfig.flInitialAnglePerPixelDegrees,
            dItems,
            fnOnChange=_on_apx_change,
            iWidth=500
        )
        try:
            dMeta = _g_dItemsMeta.get("flInitialAnglePerPixelDegrees")
            if dMeta:
                _g_dItemsMeta["flInitialAnglePerPixelDegrees"] = dMeta
        except Exception:
            logger.debug("DearPyGui operation skipped")

        dpg.add_separator()
        _add_section_header_with_tooltip("Visualization", dTips.get("Visualization", ""))
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
            _save_full_config(obConfigManager)
        dpg.add_button(label="Save Config", callback=_on_save)

    def _run():
        dpg.show_viewport()
        dpg.start_dearpygui()
        dpg.destroy_context()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t
