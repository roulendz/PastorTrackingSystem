"""
conftest.py - Shared pytest fixtures for PastorTrackingSystem tests

Provides reusable fixtures for simulated motor, synthetic camera (video file),
test configuration, and mock pose tracker. All fixtures use Hungarian notation.
"""

import pytest
import cv2
import numpy as np
from unittest.mock import Mock

from interfaces.motor_interface import SimulatedMotorInterface
from interfaces.camera_interface import CameraInterface
from tracking.pose_tracker import PoseResult
from utilities.config_manager import SystemConfiguration
from utilities.clock import FakeClock


def generate_test_video(
    sOutputPath: str,
    iFrameCount: int = 90,
    iWidth: int = 1280,
    iHeight: int = 720,
    iFramesPerSecond: int = 30,
    flStartX: float = 0.25,
    flEndX: float = 0.75
) -> str:
    """
    Generate a simple test video with a moving red circle and shoulder markers.

    Args:
        sOutputPath: Path to write the video file
        iFrameCount: Number of frames to generate
        iWidth: Frame width in pixels
        iHeight: Frame height in pixels
        iFramesPerSecond: Video frame rate
        flStartX: Starting X position as fraction of width
        flEndX: Ending X position as fraction of width

    Returns:
        Path to the created video file
    """
    obFourcc = cv2.VideoWriter_fourcc(*'mp4v')
    obWriter = cv2.VideoWriter(sOutputPath, obFourcc, iFramesPerSecond, (iWidth, iHeight))

    iCenterY = iHeight // 2

    for iFrame in range(iFrameCount):
        obFrame = np.zeros((iHeight, iWidth, 3), dtype=np.uint8)

        # Calculate circle X position (linear interpolation from start to end)
        flProgress = iFrame / max(iFrameCount - 1, 1)
        iCenterX = int(iWidth * (flStartX + flProgress * (flEndX - flStartX)))

        # Draw red circle (person center)
        cv2.circle(obFrame, (iCenterX, iCenterY), 30, (0, 0, 255), -1)

        # Draw green shoulder markers at +/- 40px from center circle
        cv2.circle(obFrame, (iCenterX - 40, iCenterY - 20), 10, (0, 255, 0), -1)
        cv2.circle(obFrame, (iCenterX + 40, iCenterY - 20), 10, (0, 255, 0), -1)

        obWriter.write(obFrame)

    obWriter.release()
    return sOutputPath


@pytest.fixture
def obFakeClock():
    """Create a FakeClock starting at time 0.0 for deterministic tests."""
    return FakeClock(dStartTimeSeconds=0.0)


@pytest.fixture
def obSimulatedMotorWithClock(obFakeClock):
    """
    Create a SimulatedMotorInterface with an injected FakeClock.

    Uses FakeClock for deterministic timestamp control in tests.
    """
    obMotor = SimulatedMotorInterface(obClock=obFakeClock)
    obMotor.connect_to_motor_controller()
    return obMotor


@pytest.fixture
def obSimulatedMotor():
    """
    Create a SimulatedMotorInterface with default physics settings.

    Returns the motor instance without starting background thread.
    Tests call advance_simulation() explicitly for deterministic behavior.
    """
    obMotor = SimulatedMotorInterface()
    obMotor.connect_to_motor_controller()
    return obMotor


@pytest.fixture
def obSyntheticCamera(tmp_path):
    """
    Create a CameraInterface backed by a generated test video file.

    Generates a 90-frame (3 seconds at 30fps), 1280x720 video with a
    red circle moving from left to right, plus green shoulder markers.

    Yields the camera interface (opens before yield, closes after).
    """
    sVideoPath = str(tmp_path / "test_video.mp4")
    generate_test_video(sVideoPath)

    obCamera = CameraInterface(sVideoFilePath=sVideoPath)
    bOpened = obCamera.open_camera_device()
    assert bOpened, f"Failed to open test video at {sVideoPath}"

    yield obCamera

    obCamera.close_camera_device()


@pytest.fixture
def obTestConfig():
    """
    Create a SystemConfiguration with test-appropriate defaults.

    Overrides visualization and hardware settings for headless test execution.
    """
    obConfig = SystemConfiguration()
    obConfig.bAllowStartWithoutMotor = True
    obConfig.bEnableVisualization = False
    obConfig.bShowDebugInfo = False
    return obConfig


@pytest.fixture
def obMockPoseTracker():
    """
    Create a mock PoseTracker that does NOT use MediaPipe.

    Returns a Mock object with the same interface as PoseTracker.
    detect_person_in_frame() returns a PoseResult with person detected
    at the center of the frame (640, 360) with 0.9 confidence.
    """
    obMock = Mock()
    obMock.detect_person_in_frame.return_value = PoseResult(
        flPersonCenterXPixels=640.0,
        flPersonCenterYPixels=360.0,
        bPersonWasDetected=True,
        flPersonConfidenceScore=0.9,
        vLandmarks=None
    )
    return obMock
