"""
fov_estimator.py - Field of View learning algorithm

This module learns the angle-per-pixel relationship by observing
motor movement and corresponding pixel changes.

Follows:
- SRP: Only learns FOV, doesn't control anything
- Exponential moving average for stable learning
"""

import numpy as np
from collections import deque
from typing import Optional
import logging

from ..core.tracking_sample import TrackingSample

logger = logging.getLogger(__name__)


class FieldOfViewEstimator:
    """
    Learns camera's angle-per-pixel ratio through observation.
    
    Responsibilities:
    - Compare consecutive samples
    - Calculate Δθ (angle delta) and Δx (pixel delta)
    - Update running estimate with exponential moving average
    - Track convergence
    """
    
    def __init__(
        self,
        flInitialAnglePerPixelDegrees: float = 0.05,
        flMinimumAngleChangeDegrees: float = 1.0,
        flMinimumPixelChangeHorizontal: float = 10.0,
        flLearningRateAlpha: float = 0.05
    ):
        """
        Initialize FOV estimator.
        
        Args:
            flInitialAnglePerPixelDegrees: Starting guess
            flMinimumAngleChangeDegrees: Min motor movement for learning
            flMinimumPixelChangeHorizontal: Min pixel movement for learning
            flLearningRateAlpha: Learning rate (0.01=slow, 0.2=fast)
        """
        self.flCurrentAnglePerPixelEstimate = flInitialAnglePerPixelDegrees
        self.flMinimumAngleChangeDegrees = flMinimumAngleChangeDegrees
        self.flMinimumPixelChangeHorizontal = flMinimumPixelChangeHorizontal
        self.flLearningRateAlpha = flLearningRateAlpha
        
        # Previous sample for delta calculation
        self.obPreviousSample: Optional[TrackingSample] = None
        
        # Statistics
        self.iSamplesUsedForLearningCount = 0
        self.qRecentEstimates = deque(maxlen=100)  # Last 100 estimates
        self.flMinimumObservedAnglePerPixel = float('inf')
        self.flMaximumObservedAnglePerPixel = float('-inf')
        
        logger.info(f"FOV Estimator initialized: {flInitialAnglePerPixelDegrees:.4f} deg/px")
    
    def reset_field_of_view_estimator_with_initial_angle_per_pixel(
        self,
        flInitialAnglePerPixelDegrees: float
    ):
        """
        Reset estimator to new initial value.
        
        Args:
            flInitialAnglePerPixelDegrees: New starting estimate
        """
        self.flCurrentAnglePerPixelEstimate = flInitialAnglePerPixelDegrees
        self.obPreviousSample = None
        self.iSamplesUsedForLearningCount = 0
        self.qRecentEstimates.clear()
        self.flMinimumObservedAnglePerPixel = float('inf')
        self.flMaximumObservedAnglePerPixel = float('-inf')
        
        logger.info(f"FOV Estimator reset to {flInitialAnglePerPixelDegrees:.4f} deg/px")
    
    def update_field_of_view_estimator_with_sample(self, obNewSample: TrackingSample):
        """
        Update FOV estimate with new sample.
        
        This is the core learning algorithm:
        1. Compare with previous sample
        2. Calculate Δθ and Δx
        3. If motion is significant, update estimate
        4. Use exponential moving average for stability
        
        Args:
            obNewSample: Latest tracking sample
        """
        # Skip if person not detected
        if not obNewSample.is_valid_for_tracking():
            self.obPreviousSample = None
            return
        
        # Need previous sample for comparison
        if self.obPreviousSample is None:
            self.obPreviousSample = obNewSample
            return
        
        # Calculate deltas
        flAngleDeltaDegrees = self._calculate_angle_delta_degrees(
            self.obPreviousSample.flMotorAngleDegrees,
            obNewSample.flMotorAngleDegrees
        )
        
        flPixelDeltaHorizontal = self._calculate_pixel_delta_horizontal(
            self.obPreviousSample.flPersonCenterXPixels,
            obNewSample.flPersonCenterXPixels
        )
        
        # Check if motion is significant enough
        bMotionIsSufficient = self._is_motion_sufficient_for_learning(
            abs(flAngleDeltaDegrees),
            abs(flPixelDeltaHorizontal)
        )
        
        if not bMotionIsSufficient:
            # Update baseline but don't learn
            self.obPreviousSample = obNewSample
            return
        
        # Calculate new angle-per-pixel sample
        # Formula: angle_per_pixel = Δθ / Δx
        flNewAnglePerPixelSample = flAngleDeltaDegrees / flPixelDeltaHorizontal
        
        # Reject obvious outliers (>10x different from current estimate)
        flRatioToCurrentEstimate = abs(
            flNewAnglePerPixelSample / self.flCurrentAnglePerPixelEstimate
        )
        if flRatioToCurrentEstimate > 10.0 or flRatioToCurrentEstimate < 0.1:
            logger.warning(f"Rejecting outlier: {flNewAnglePerPixelSample:.4f} deg/px")
            self.obPreviousSample = obNewSample
            return
        
        # Update estimate using exponential moving average
        # new_estimate = (1 - alpha) * old + alpha * new_sample
        self.flCurrentAnglePerPixelEstimate = (
            (1.0 - self.flLearningRateAlpha) * self.flCurrentAnglePerPixelEstimate +
            self.flLearningRateAlpha * flNewAnglePerPixelSample
        )
        
        # Update statistics
        self.qRecentEstimates.append(self.flCurrentAnglePerPixelEstimate)
        self.flMinimumObservedAnglePerPixel = min(
            self.flMinimumObservedAnglePerPixel,
            flNewAnglePerPixelSample
        )
        self.flMaximumObservedAnglePerPixel = max(
            self.flMaximumObservedAnglePerPixel,
            flNewAnglePerPixelSample
        )
        self.iSamplesUsedForLearningCount += 1
        
        # Update baseline
        self.obPreviousSample = obNewSample
        
        logger.debug(
            f"FOV updated: {self.flCurrentAnglePerPixelEstimate:.4f} deg/px "
            f"(sample {self.iSamplesUsedForLearningCount})"
        )
    
    def get_estimated_angle_per_pixel_ratio(self) -> float:
        """
        Get current angle-per-pixel estimate.
        
        Returns:
            Degrees of rotation per pixel of horizontal movement
        """
        return self.flCurrentAnglePerPixelEstimate
    
    def get_estimated_field_of_view_degrees(self, iImageWidthPixels: int) -> float:
        """
        Get total horizontal field of view.
        
        Args:
            iImageWidthPixels: Camera image width
            
        Returns:
            Total FOV in degrees
        """
        return self.flCurrentAnglePerPixelEstimate * iImageWidthPixels
    
    def has_field_of_view_estimate_converged(self) -> bool:
        """
        Check if estimate has converged to stable value.
        
        Returns:
            True if estimate is stable
        """
        if self.iSamplesUsedForLearningCount < 50:
            return False
        
        if len(self.qRecentEstimates) < 50:
            return False
        
        # Calculate standard deviation of recent estimates
        flStandardDeviation = np.std(list(self.qRecentEstimates))
        
        # Consider converged if std dev is < 1% of mean
        flRelativeStdDev = flStandardDeviation / self.flCurrentAnglePerPixelEstimate
        
        return flRelativeStdDev < 0.01
    
    def get_number_of_samples_used_for_learning(self) -> int:
        """Get count of samples that contributed to learning."""
        return self.iSamplesUsedForLearningCount
    
    def get_learning_statistics(self) -> dict:
        """
        Get detailed learning statistics.
        
        Returns:
            Dictionary with statistics
        """
        if len(self.qRecentEstimates) > 0:
            flStdDev = np.std(list(self.qRecentEstimates))
        else:
            flStdDev = 0.0
        
        return {
            'current_estimate': self.flCurrentAnglePerPixelEstimate,
            'samples_used': self.iSamplesUsedForLearningCount,
            'std_deviation': flStdDev,
            'min_observed': self.flMinimumObservedAnglePerPixel,
            'max_observed': self.flMaximumObservedAnglePerPixel,
            'converged': self.has_field_of_view_estimate_converged()
        }
    
    # Private methods
    
    def _calculate_angle_delta_degrees(
        self,
        flFirstAngleDegrees: float,
        flSecondAngleDegrees: float
    ) -> float:
        """Calculate angle change between samples."""
        return flSecondAngleDegrees - flFirstAngleDegrees
    
    def _calculate_pixel_delta_horizontal(
        self,
        flFirstPositionPixels: float,
        flSecondPositionPixels: float
    ) -> float:
        """Calculate horizontal pixel movement."""
        return flSecondPositionPixels - flFirstPositionPixels
    
    def _is_motion_sufficient_for_learning(
        self,
        flAbsoluteAngleDeltaDegrees: float,
        flAbsolutePixelDelta: float
    ) -> bool:
        """
        Check if motion is large enough to learn from.
        
        Small motions are noisy and unreliable.
        """
        return (
            flAbsoluteAngleDeltaDegrees >= self.flMinimumAngleChangeDegrees and
            flAbsolutePixelDelta >= self.flMinimumPixelChangeHorizontal
        )
