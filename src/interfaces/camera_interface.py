"""
camera_interface.py - Camera frame capture using OpenCV

This module handles all camera operations: opening, capturing, configuration.

Follows:
- SRP: Only handles camera, nothing else
- Fast capture with minimal latency
"""

import cv2
import time
import numpy as np
from typing import Optional, Tuple
import logging

logger = logging.getLogger(__name__)


class CameraInterface:
    """
    Interface for camera capture using OpenCV.
    
    Responsibilities:
    - Open/close camera device
    - Configure resolution and FPS
    - Capture frames with timestamps
    - Handle camera errors gracefully
    """
    
    def __init__(
        self,
        iCameraDeviceIndex: int = 0,
        iCameraWidthPixels: int = 1280,
        iCameraHeightPixels: int = 720,
        iCameraFramesPerSecond: int = 30,
        sVideoFilePath: str = ""
    ):
        """
        Initialize camera interface.

        Args:
            iCameraDeviceIndex: Camera index (0 for default)
            iCameraWidthPixels: Desired frame width
            iCameraHeightPixels: Desired frame height
            iCameraFramesPerSecond: Desired FPS
            sVideoFilePath: Path to video file (replaces camera if non-empty)
        """
        self.iCameraDeviceIndex = iCameraDeviceIndex
        self.iCameraWidthPixels = iCameraWidthPixels
        self.iCameraHeightPixels = iCameraHeightPixels
        self.iCameraFramesPerSecond = iCameraFramesPerSecond
        self.sVideoFilePath = sVideoFilePath

        self.obVideoCapture: Optional[cv2.VideoCapture] = None
        self._bIsOpen = False
    
    def open_camera_device(self) -> bool:
        """
        Open camera device or video file and configure settings.

        When sVideoFilePath is set, opens a video file instead of a live camera.
        Camera-only properties (resolution, FPS, autofocus) are not set on video files.

        Returns:
            True if camera/video opened successfully
        """
        try:
            if self.sVideoFilePath:
                # Open video file
                self.obVideoCapture = cv2.VideoCapture(self.sVideoFilePath)

                if not self.obVideoCapture.isOpened():
                    logger.error(f"Failed to open video file: {self.sVideoFilePath}")
                    return False

                # Read actual dimensions from the file (do NOT set camera-only properties)
                iActualWidth = int(self.obVideoCapture.get(cv2.CAP_PROP_FRAME_WIDTH))
                iActualHeight = int(self.obVideoCapture.get(cv2.CAP_PROP_FRAME_HEIGHT))
                iActualFPS = int(self.obVideoCapture.get(cv2.CAP_PROP_FPS))

                logger.info(f"Video file opened: {self.sVideoFilePath} ({iActualWidth}x{iActualHeight} @ {iActualFPS} FPS)")

                # Update dimensions with actual values
                self.iCameraWidthPixels = iActualWidth
                self.iCameraHeightPixels = iActualHeight
                if iActualFPS > 0:
                    self.iCameraFramesPerSecond = iActualFPS
            else:
                # Open live camera
                self.obVideoCapture = cv2.VideoCapture(self.iCameraDeviceIndex)

                if not self.obVideoCapture.isOpened():
                    logger.error(f"Failed to open camera {self.iCameraDeviceIndex}")
                    return False

                # Configure camera
                self.obVideoCapture.set(cv2.CAP_PROP_FRAME_WIDTH, self.iCameraWidthPixels)
                self.obVideoCapture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.iCameraHeightPixels)
                self.obVideoCapture.set(cv2.CAP_PROP_FPS, self.iCameraFramesPerSecond)

                # Enable auto-focus if available
                self.obVideoCapture.set(cv2.CAP_PROP_AUTOFOCUS, 1)

                # Verify actual settings
                iActualWidth = int(self.obVideoCapture.get(cv2.CAP_PROP_FRAME_WIDTH))
                iActualHeight = int(self.obVideoCapture.get(cv2.CAP_PROP_FRAME_HEIGHT))
                iActualFPS = int(self.obVideoCapture.get(cv2.CAP_PROP_FPS))

                logger.info(f"Camera opened: {iActualWidth}x{iActualHeight} @ {iActualFPS} FPS")

                # Update dimensions with actual values
                self.iCameraWidthPixels = iActualWidth
                self.iCameraHeightPixels = iActualHeight

            self._bIsOpen = True
            return True

        except Exception as e:
            logger.error(f"Failed to open camera: {e}")
            return False
    
    def close_camera_device(self):
        """Release camera resources."""
        if self.obVideoCapture is not None:
            self.obVideoCapture.release()
            self._bIsOpen = False
            logger.info("Camera closed")
    
    def is_camera_device_open(self) -> bool:
        """Check if camera is currently open."""
        return self._bIsOpen and self.obVideoCapture is not None
    
    def capture_frame_with_timestamp(self) -> Tuple[Optional[np.ndarray], float]:
        """
        Capture single frame from camera.
        
        Returns:
            Tuple of (frame, timestamp_seconds)
            Frame is None if capture failed
        """
        if not self.is_camera_device_open():
            logger.error("Cannot capture - camera not open")
            return None, 0.0
        
        # Capture frame
        bSuccess, obFrame = self.obVideoCapture.read()

        # Get timestamp IMMEDIATELY after capture
        dTimestampSeconds = time.perf_counter()

        # Video file looping: if read fails on a video file, seek to start and retry
        if (not bSuccess or obFrame is None) and self.sVideoFilePath:
            self.obVideoCapture.set(cv2.CAP_PROP_POS_FRAMES, 0)
            bSuccess, obFrame = self.obVideoCapture.read()
            dTimestampSeconds = time.perf_counter()

        if not bSuccess or obFrame is None:
            logger.warning("Failed to capture frame")
            return None, dTimestampSeconds
        
        return obFrame, dTimestampSeconds
    
    def get_frame_dimensions(self) -> Tuple[int, int]:
        """
        Get actual frame dimensions.
        
        Returns:
            Tuple of (width, height) in pixels
        """
        return self.iCameraWidthPixels, self.iCameraHeightPixels
    
    def __del__(self):
        """Cleanup on deletion."""
        self.close_camera_device()
