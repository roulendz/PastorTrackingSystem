"""
tracking_sample.py - Core data structure for synchronized measurements

This module defines the TrackingSample dataclass, which represents the
atomic measurement unit that pairs camera frame data with motor position.

Follows:
- SRP: Only data container, no logic
- Immutable design for thread safety
"""

from dataclasses import dataclass
from typing import Optional
import numpy as np


@dataclass
class TrackingSample:
    """
    Atomic measurement combining motor state and person detection.
    
    This is the "single source of truth" - every camera frame is paired
    with the actual motor angle at that exact instant.
    
    Attributes:
        dSampleTimestampSeconds: When camera frame was captured
        flMotorAngleDegrees: ACTUAL motor angle (not target)
        flPersonCenterXPixels: Horizontal position of person center
        flPersonCenterYPixels: Vertical position of person center
        bPersonWasDetected: Whether person detection succeeded
        iSampleSequenceNumber: Sequential sample ID
        flPersonConfidenceScore: Detection confidence (0.0 to 1.0)
        obFrameImage: Optional reference to the frame (for debugging)
    """
    
    dSampleTimestampSeconds: float
    flMotorAngleDegrees: float
    flPersonCenterXPixels: float
    flPersonCenterYPixels: float
    bPersonWasDetected: bool
    iSampleSequenceNumber: int
    flPersonConfidenceScore: float = 0.0
    flMinimumConfidenceRequired: float = 0.5
    obFrameImage: Optional[np.ndarray] = None
    obPoseLandmarks: Optional[object] = None
    
    def get_pixel_offset_from_center(self, iImageWidthPixels: int) -> float:
        """
        Calculate how far person is from image center (in pixels).
        
        Args:
            iImageWidthPixels: Width of the camera image
            
        Returns:
            Pixel offset (positive = right of center, negative = left)
        """
        flCenterXPixels = iImageWidthPixels / 2.0
        return self.flPersonCenterXPixels - flCenterXPixels
    
    def is_valid_for_tracking(self) -> bool:
        """
        Check if this sample can be used for tracking/learning.
        
        Returns:
            True if person was detected with sufficient confidence
        """
        return self.bPersonWasDetected and self.flPersonConfidenceScore >= self.flMinimumConfidenceRequired
    
    def __repr__(self) -> str:
        """String representation for debugging."""
        return (f"TrackingSample(seq={self.iSampleSequenceNumber}, "
                f"angle={self.flMotorAngleDegrees:.2f}°, "
                f"person_x={self.flPersonCenterXPixels:.1f}px, "
                f"detected={self.bPersonWasDetected})")
