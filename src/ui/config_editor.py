from typing import Dict, Any, Tuple
import json
from pathlib import Path
from dataclasses import asdict
from utilities.config_manager import ConfigurationManager, SystemConfiguration

import dearpygui.dearpygui as dpg


def _get_default_slider_ranges() -> Dict[str, Tuple[float, float, float]]:
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


def _get_field_types() -> Dict[str, str]:
    return {
        "sMotorSerialPortName": "text",
        "iMotorBaudRate": "int",
        "flMotorMaxSpeedStepsPerSecond": "float",
        "flMotorMaxAccelerationStepsPerSecondSquared": "float",
        "flMotorMinAngleDegrees": "float",
        "flMotorMaxAngleDegrees": "float",
        "iCameraDeviceIndex": "int",
        "iCameraWidthPixels": "int",
        "iCameraHeightPixels": "int",
        "iCameraFramesPerSecond": "int",
        "flPoseMinDetectionConfidence": "float",
        "flPoseMinTrackingConfidence": "float",
        "bPoseEnableSegmentation": "bool",
        "flInitialAnglePerPixelDegrees": "float",
        "sControlAlgorithmType": "radio",
        "flControlProportionalGain": "float",
        "flControlIntegralGain": "float",
        "flControlDerivativeGain": "float",
        "flControlDeadbandDegrees": "float",
        "flDeadbandMinDegrees": "float",
        "flDeadbandMaxDegrees": "float",
        "flTrackingMinConfidenceForControl": "float",
        "bEnableVisualization": "bool",
        "bShowDebugInfo": "bool",
        "bAllowStartWithoutMotor": "bool",
        "bEnableDeadzoneOverlay": "bool",
        "iCenterDeadzoneRadiusPixels": "int",
        "flVelocityGain": "float",
        "flMaxVelocityDegreesPerSecond": "float",
        "flVelocitySmoothingAlpha": "float",
    }


def _get_field_groups() -> Dict[str, str]:
    return {
        "sMotorSerialPortName": "Motor",
        "iMotorBaudRate": "Motor",
        "flMotorMaxSpeedStepsPerSecond": "Motor",
        "flMotorMaxAccelerationStepsPerSecondSquared": "Motor",
        "flMotorMinAngleDegrees": "Motor",
        "flMotorMaxAngleDegrees": "Motor",
        "iCameraDeviceIndex": "Camera",
        "iCameraWidthPixels": "Camera",
        "iCameraHeightPixels": "Camera",
        "iCameraFramesPerSecond": "Camera",
        "flPoseMinDetectionConfidence": "Pose",
        "flPoseMinTrackingConfidence": "Pose",
        "bPoseEnableSegmentation": "Pose",
        "flInitialAnglePerPixelDegrees": "FOV",
        "sControlAlgorithmType": "Control",
        "flControlProportionalGain": "Control",
        "flControlIntegralGain": "Control",
        "flControlDerivativeGain": "Control",
        "flControlDeadbandDegrees": "Control",
        "flDeadbandMinDegrees": "Control",
        "flDeadbandMaxDegrees": "Control",
        "flTrackingMinConfidenceForControl": "Control",
        "flVelocityGain": "Control",
        "flMaxVelocityDegreesPerSecond": "Control",
        "flVelocitySmoothingAlpha": "Control",
        "bEnableVisualization": "Visualization",
        "bShowDebugInfo": "Visualization",
        "bAllowStartWithoutMotor": "Visualization",
        "bEnableDeadzoneOverlay": "Visualization",
        "iCenterDeadzoneRadiusPixels": "Visualization",
    }


def _create_numeric_control(sKey: str, value: Any, dRanges: Dict[str, Tuple[float, float, float]], dItems: Dict[str, int]):
    flMin, flMax, flStep = dRanges.get(sKey, (0.0, 100.0, 1.0))
    with dpg.group(horizontal=True):
        dpg.add_text("min")
        iMinInput = dpg.add_input_float(label="", default_value=flMin, width=90)
        dpg.add_text("max")
        iMaxInput = dpg.add_input_float(label="", default_value=flMax, width=90)
        iMaxInfo = dpg.add_button(label="?", width=18)
        with dpg.tooltip(iMaxInfo):
            dpg.add_text(f"{sKey}")
    def _update_slider_range():
        flNewMin = dpg.get_value(iMinInput)
        flNewMax = dpg.get_value(iMaxInput)
        if flNewMin > flNewMax:
            return
        dpg.configure_item(dItems[sKey], min_value=flNewMin, max_value=flNewMax)
    dpg.add_button(label="Apply range", callback=lambda: _update_slider_range())
    if isinstance(value, int):
        dItems[sKey] = dpg.add_slider_int(label="", default_value=int(value), min_value=int(flMin), max_value=int(flMax))
    else:
        dItems[sKey] = dpg.add_slider_float(label="", default_value=float(value), min_value=flMin, max_value=flMax)
    dpg.add_input_float(label="value", default_value=float(value), callback=lambda s, a, u: dpg.set_value(dItems[sKey], a))


def _create_bool_control(sKey: str, value: bool, dItems: Dict[str, int]):
    dItems[sKey] = dpg.add_checkbox(label=sKey, default_value=bool(value))


def _create_text_control(sKey: str, value: str, dItems: Dict[str, int]):
    dItems[sKey] = dpg.add_input_text(label=sKey, default_value=str(value))


def _create_radio_control(sKey: str, value: str, dItems: Dict[str, int]):
    vOptions = ["P", "PID", "Velocity"]
    dItems[sKey] = dpg.add_radio_button(items=vOptions, label=sKey, default_value=str(value), horizontal=True)


def _build_group_tab(sGroup: str, dInitial: Dict[str, Any], dTypes: Dict[str, str], dRanges: Dict[str, Tuple[float, float, float]], dGroups: Dict[str, str], dItems: Dict[str, int]):
    with dpg.tab(label=sGroup):
        for sKey, sFieldGroup in dGroups.items():
            if sFieldGroup != sGroup:
                continue
            sType = dTypes.get(sKey)
            value = dInitial.get(sKey)
            if sType in ("int", "float"):
                _create_numeric_control(sKey, value, dRanges, dItems)
            elif sType == "bool":
                _create_bool_control(sKey, value, dItems)
            elif sType == "text":
                _create_text_control(sKey, value, dItems)
            elif sType == "radio":
                _create_radio_control(sKey, value, dItems)


def _collect_values(dItems: Dict[str, int], dTypes: Dict[str, str]) -> Dict[str, Any]:
    dValues: Dict[str, Any] = {}
    for sKey, iItem in dItems.items():
        sType = dTypes.get(sKey)
        v = dpg.get_value(iItem)
        if sType == "int":
            dValues[sKey] = int(v)
        elif sType == "float":
            dValues[sKey] = float(v)
        elif sType == "bool":
            dValues[sKey] = bool(v)
        elif sType in ("text", "radio"):
            dValues[sKey] = str(v)
    return dValues


def launch_config_editor(sDefaultConfigPath: str, sUserConfigPath: str) -> bool:
    dTypes = _get_field_types()
    dRanges = _get_default_slider_ranges()
    dGroups = _get_field_groups()
    obManager = ConfigurationManager(sDefaultConfigPath)
    obManager.load_configuration_with_overrides([sUserConfigPath])
    obConfig = obManager.get_system_configuration()
    dInitial = asdict(obConfig)

    dpg.create_context()
    dpg.create_viewport(title="Pastor Tracking System - Configuration", width=1200, height=800)
    dpg.setup_dearpygui()

    dItems: Dict[str, int] = {}
    with dpg.window(label="Configuration", width=1180, height=760):
        with dpg.tab_bar():
            _build_group_tab("Motor", dInitial, dTypes, dRanges, dGroups, dItems)
            _build_group_tab("Camera", dInitial, dTypes, dRanges, dGroups, dItems)
            _build_group_tab("Pose", dInitial, dTypes, dRanges, dGroups, dItems)
            _build_group_tab("FOV", dInitial, dTypes, dRanges, dGroups, dItems)
            _build_group_tab("Control", dInitial, dTypes, dRanges, dGroups, dItems)
            _build_group_tab("Visualization", dInitial, dTypes, dRanges, dGroups, dItems)

        def _on_save():
            dValues = _collect_values(dItems, dTypes)
            try:
                obPath = Path(sUserConfigPath)
                obPath.parent.mkdir(parents=True, exist_ok=True)
                with open(obPath, "w") as f:
                    json.dump(dValues, f, indent=4)
            except Exception:
                pass

        def _on_close():
            dpg.stop_dearpygui()

        with dpg.group(horizontal=True):
            dpg.add_button(label="Save", callback=_on_save)
            dpg.add_button(label="Close", callback=_on_close)

    dpg.show_viewport()
    dpg.start_dearpygui()
    dpg.destroy_context()
    return True


if __name__ == "__main__":
    launch_config_editor("config/default_config.json", "config/user_config.json")
