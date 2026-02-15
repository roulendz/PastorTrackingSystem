"""
config_manager.py - Configuration management

Handles loading and saving system configuration from JSON files.

Follows:
- SRP: Only handles configuration, nothing else
- Validation and defaults
"""

import json
import logging
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional, List

logger = logging.getLogger(__name__)


@dataclass
class SystemConfiguration:
    """Complete system configuration."""
    
    # Motor settings
    sMotorSerialPortName: str = "/dev/ttyUSB0"
    iMotorBaudRate: int = 115200
    flMotorMaxSpeedStepsPerSecond: float = 25000.0
    flMotorMaxAccelerationStepsPerSecondSquared: float = 12500.0
    flMotorMinAngleDegrees: float = -45.0
    flMotorMaxAngleDegrees: float = 45.0
    
    # Camera settings
    iCameraDeviceIndex: int = 0
    iCameraWidthPixels: int = 1280
    iCameraHeightPixels: int = 720
    iCameraFramesPerSecond: int = 30
    
    # Pose detection settings
    flPoseMinDetectionConfidence: float = 0.5
    flPoseMinTrackingConfidence: float = 0.5
    bPoseEnableSegmentation: bool = False
    
    # FOV settings
    flFieldOfViewDegrees: float = 6.77
    flInitialAnglePerPixelDegrees: float = 0.00529
    
    # Control settings
    sControlAlgorithmType: str = "P"  # "P", "PID", or "Velocity"
    flControlProportionalGain: float = 1.0
    flControlIntegralGain: float = 0.0
    flControlDerivativeGain: float = 0.1
    flControlDeadbandDegrees: float = 0.3
    flDeadbandMinDegrees: float = 0.01
    flDeadbandMaxDegrees: float = 20.0
    flTrackingMinConfidenceForControl: float = 0.3
    
    
    # Visualization
    bEnableVisualization: bool = True
    bShowDebugInfo: bool = True
    bAllowStartWithoutMotor: bool = True
    bEnableDeadzoneOverlay: bool = True
    iCenterDeadzoneRadiusPixels: int = 40
    flVelocityGain: float = 5.0
    flMaxVelocityDegreesPerSecond: float = 45.0
    flVelocitySmoothingAlpha: float = 0.3


class ConfigurationManager:
    """
    Manages loading and saving system configuration.
    
    Responsibilities:
    - Load configuration from JSON
    - Save configuration to JSON
    - Provide default configuration
    - Validate configuration values
    """
    
    def __init__(self, sConfigFilePath: str = "config/default_config.json"):
        """
        Initialize configuration manager.
        
        Args:
            sConfigFilePath: Path to configuration file
        """
        self.sConfigFilePath = sConfigFilePath
        self.obCurrentConfig = SystemConfiguration()
    
    def load_configuration_from_file(self, sFilePath: Optional[str] = None) -> bool:
        """
        Load configuration from JSON file.
        
        Args:
            sFilePath: Optional path override
            
        Returns:
            True if loaded successfully
        """
        sPathToLoad = sFilePath if sFilePath else self.sConfigFilePath
        
        try:
            obPath = Path(sPathToLoad)
            
            if not obPath.exists():
                logger.warning(f"Config file not found: {sPathToLoad}")
                return False
            
            with open(obPath, 'r') as file:
                dConfigDict = json.load(file)
            
            # Update configuration with loaded values
            for sKey, value in dConfigDict.items():
                if hasattr(self.obCurrentConfig, sKey):
                    setattr(self.obCurrentConfig, sKey, value)
            # Also pick UI/controls FOV if present (does not add schema fields)
            try:
                dUI = dConfigDict.get('ui', {})
                dControls = dUI.get('controls', {})
                dFov = dControls.get('flFieldOfViewDegrees')
                if isinstance(dFov, dict):
                    v = dFov.get('input_value', dFov.get('slider_value', None))
                    if v is not None:
                        setattr(self.obCurrentConfig, 'flFieldOfViewDegrees', float(v))
            except (KeyError, TypeError, AttributeError) as e:
                logger.debug(f"No UI/controls FOV override in config: {e}")

            logger.info(f"Configuration loaded from {sPathToLoad}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to load configuration: {e}")
            return False
    
    def save_configuration_to_file(self, sFilePath: Optional[str] = None) -> bool:
        """
        Save configuration to JSON file.
        
        Args:
            sFilePath: Optional path override
            
        Returns:
            True if saved successfully
        """
        sPathToSave = sFilePath if sFilePath else self.sConfigFilePath
        
        try:
            obPath = Path(sPathToSave)
            obPath.parent.mkdir(parents=True, exist_ok=True)
            
            dConfigDict = asdict(self.obCurrentConfig)
            
            with open(obPath, 'w') as file:
                json.dump(dConfigDict, file, indent=4)
            
            logger.info(f"Configuration saved to {sPathToSave}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to save configuration: {e}")
            return False
    
    def get_system_configuration(self) -> SystemConfiguration:
        """
        Get current configuration.
        
        Returns:
            SystemConfiguration object
        """
        return self.obCurrentConfig
    
    
    
    def validate_configuration(self) -> bool:
        """
        Validate configuration values.
        
        Returns:
            True if configuration is valid
        """
        bValid = True
        
        # Check motor angles
        if self.obCurrentConfig.flMotorMinAngleDegrees >= self.obCurrentConfig.flMotorMaxAngleDegrees:
            logger.error("Invalid motor angle range")
            bValid = False
        
        # Check camera resolution
        if self.obCurrentConfig.iCameraWidthPixels <= 0 or self.obCurrentConfig.iCameraHeightPixels <= 0:
            logger.error("Invalid camera resolution")
            bValid = False
        
        # Check control gains
        if self.obCurrentConfig.flControlProportionalGain <= 0:
            logger.error("Invalid control gain")
            bValid = False
        
        return bValid

    def load_configuration_with_overrides(self, vOverridePaths: Optional[List[str]] = None) -> bool:
        bAnyLoaded = False
        vPaths: List[str] = [self.sConfigFilePath]
        if vOverridePaths:
            vPaths.extend(vOverridePaths)
        for sPath in vPaths:
            try:
                obPath = Path(sPath)
                if not obPath.exists():
                    continue
                with open(obPath, 'r') as file:
                    dConfigDict = json.load(file)
                for sKey, value in dConfigDict.items():
                    if hasattr(self.obCurrentConfig, sKey):
                        setattr(self.obCurrentConfig, sKey, value)
                # Also pick UI/controls FOV if present
                try:
                    dUI = dConfigDict.get('ui', {})
                    dControls = dUI.get('controls', {})
                    dFov = dControls.get('flFieldOfViewDegrees')
                    if isinstance(dFov, dict):
                        v = dFov.get('input_value', dFov.get('slider_value', None))
                        if v is not None:
                            setattr(self.obCurrentConfig, 'flFieldOfViewDegrees', float(v))
                except (KeyError, TypeError, AttributeError) as e:
                    logger.debug(f"No UI/controls FOV override in config: {e}")
                logger.info(f"Configuration loaded from {sPath}")
                bAnyLoaded = True
            except Exception as e:
                logger.error(f"Failed to load configuration: {e}")
                return False
        if not bAnyLoaded:
            logger.warning("No configuration files found")
            return False
        return True
