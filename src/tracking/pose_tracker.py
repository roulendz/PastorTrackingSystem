"""
pose_tracker.py - Person detection using MediaPipe Pose

This module detects people in camera frames using MediaPipe's Pose solution.

Follows:
- SRP: Only handles pose detection
- Optimized for speed with GPU acceleration
"""

import cv2
import numpy as np
import mediapipe as mp
from typing import Optional, Tuple
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass
class PoseResult:
    """Result from pose detection."""
    flPersonCenterXPixels: float
    flPersonCenterYPixels: float
    bPersonWasDetected: bool
    flPersonConfidenceScore: float
    vLandmarks: Optional[list] = None  # Raw landmarks for debugging


class PoseTracker:
    """
    Person detection and tracking using MediaPipe Pose.
    
    Responsibilities:
    - Initialize MediaPipe Pose model
    - Detect person in frame
    - Calculate person's center position
    - Track confidence scores
    """
    
    def __init__(
        self,
        flMinimumDetectionConfidence: float = 0.5,
        flMinimumTrackingConfidence: float = 0.5,
        bEnableSegmentation: bool = False
    ):
        """
        Initialize pose tracker.
        
        Args:
            flMinimumDetectionConfidence: Min confidence for initial detection
            flMinimumTrackingConfidence: Min confidence for tracking
            bEnableSegmentation: Enable pose segmentation (slower but more accurate)
        """
        self.flMinimumDetectionConfidence = flMinimumDetectionConfidence
        self.flMinimumTrackingConfidence = flMinimumTrackingConfidence
        
        # Initialize MediaPipe Pose
        self.obMPPose = mp.solutions.pose
        self.obPoseDetector = self.obMPPose.Pose(
            static_image_mode=False,  # Video mode for better performance
            model_complexity=1,  # 0=Lite, 1=Full, 2=Heavy
            enable_segmentation=bEnableSegmentation,
            min_detection_confidence=flMinimumDetectionConfidence,
            min_tracking_confidence=flMinimumTrackingConfidence
        )
        
        # For drawing (optional)
        self.obMPDrawing = mp.solutions.drawing_utils
        self.obDrawingSpec = self.obMPDrawing.DrawingSpec(
            thickness=2,
            circle_radius=2,
            color=(0, 255, 0)
        )
        
        self._bIsInitialized = True
        logger.info("MediaPipe Pose initialized")
    
    def detect_person_in_frame(self, obFrameImage: np.ndarray) -> PoseResult:
        """
        Detect person in camera frame.
        
        Args:
            obFrameImage: BGR image from camera
            
        Returns:
            PoseResult with person position and confidence
        """
        if not self._bIsInitialized:
            logger.error("Pose tracker not initialized")
            return self._create_empty_result()
        
        try:
            # Convert BGR to RGB (MediaPipe requires RGB)
            obRGBImage = cv2.cvtColor(obFrameImage, cv2.COLOR_BGR2RGB)
            
            # Process frame
            obResults = self.obPoseDetector.process(obRGBImage)
            
            # Check if pose was detected
            if not obResults.pose_landmarks:
                logger.debug("No person detected in frame")
                return self._create_empty_result()
            
            # Calculate person center from landmarks
            flCenterX, flCenterY = self._calculate_person_center(
                obResults.pose_landmarks,
                obFrameImage.shape[1],
                obFrameImage.shape[0]
            )
            
            # Estimate overall confidence (average of visible landmarks)
            flConfidence = self._calculate_overall_confidence(obResults.pose_landmarks)
            
            return PoseResult(
                flPersonCenterXPixels=flCenterX,
                flPersonCenterYPixels=flCenterY,
                bPersonWasDetected=True,
                flPersonConfidenceScore=flConfidence,
                vLandmarks=obResults.pose_landmarks
            )
            
        except Exception as e:
            logger.error(f"Error during pose detection: {e}")
            return self._create_empty_result()
    
    def draw_pose_on_frame(
        self,
        obFrameImage: np.ndarray,
        obPoseResult: PoseResult
    ) -> np.ndarray:
        """
        Draw pose landmarks on frame for visualization.
        
        Args:
            obFrameImage: Frame to draw on
            obPoseResult: Pose detection result
            
        Returns:
            Frame with pose drawn
        """
        if not obPoseResult.bPersonWasDetected or not obPoseResult.vLandmarks:
            return obFrameImage
        
        # Draw pose landmarks
        self.obMPDrawing.draw_landmarks(
            obFrameImage,
            obPoseResult.vLandmarks,
            self.obMPPose.POSE_CONNECTIONS,
            self.obDrawingSpec,
            self.obDrawingSpec
        )
        
        # Draw center point
        iCenterX = int(obPoseResult.flPersonCenterXPixels)
        iCenterY = int(obPoseResult.flPersonCenterYPixels)
        cv2.circle(obFrameImage, (iCenterX, iCenterY), 10, (0, 0, 255), -1)
        
        # Draw confidence text
        sText = f"Conf: {obPoseResult.flPersonConfidenceScore:.2f}"
        cv2.putText(
            obFrameImage,
            sText,
            (iCenterX + 15, iCenterY),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (0, 0, 255),
            2
        )
        
        return obFrameImage
    
    def close_pose_tracker(self):
        """Release MediaPipe resources."""
        try:
            if getattr(self, 'obPoseDetector', None):
                detector = self.obPoseDetector
                self.obPoseDetector = None
                try:
                    detector.close()
                except Exception:
                    pass
                self._bIsInitialized = False
                logger.info("Pose tracker closed")
        except Exception:
            pass
    
    # Private methods
    
    def _calculate_person_center(
        self,
        obLandmarks,
        iImageWidth: int,
        iImageHeight: int
    ) -> Tuple[float, float]:
        """
        Calculate person's center position from pose landmarks.
        
        We use torso landmarks (shoulders and hips) for stable centering.
        
        Args:
            obLandmarks: MediaPipe pose landmarks
            iImageWidth: Image width in pixels
            iImageHeight: Image height in pixels
            
        Returns:
            Tuple of (center_x, center_y) in pixels
        """
        # Key torso landmarks for stable tracking
        # 11: Left shoulder, 12: Right shoulder
        # 23: Left hip, 24: Right hip
        vTorsoIndices = [11, 12, 23, 24]
        
        flSumX = 0.0
        flSumY = 0.0
        iValidCount = 0
        
        for iIndex in vTorsoIndices:
            obLandmark = obLandmarks.landmark[iIndex]
            
            # Check visibility (confidence)
            if obLandmark.visibility > 0.5:
                flSumX += obLandmark.x
                flSumY += obLandmark.y
                iValidCount += 1
        
        if iValidCount == 0:
            # Fallback: use nose (landmark 0)
            obNose = obLandmarks.landmark[0]
            return obNose.x * iImageWidth, obNose.y * iImageHeight
        
        # Average position (normalized 0-1)
        flNormalizedX = flSumX / iValidCount
        flNormalizedY = flSumY / iValidCount
        
        # Convert to pixel coordinates
        flPixelX = flNormalizedX * iImageWidth
        flPixelY = flNormalizedY * iImageHeight
        
        return flPixelX, flPixelY
    
    def _calculate_overall_confidence(self, obLandmarks) -> float:
        """
        Calculate overall confidence from all landmarks.
        
        Args:
            obLandmarks: MediaPipe pose landmarks
            
        Returns:
            Average visibility score (0.0 to 1.0)
        """
        flTotalVisibility = 0.0
        iCount = 0
        
        for obLandmark in obLandmarks.landmark:
            flTotalVisibility += obLandmark.visibility
            iCount += 1
        
        return flTotalVisibility / iCount if iCount > 0 else 0.0
    
    def _create_empty_result(self) -> PoseResult:
        """Create empty result for when no person detected."""
        return PoseResult(
            flPersonCenterXPixels=0.0,
            flPersonCenterYPixels=0.0,
            bPersonWasDetected=False,
            flPersonConfidenceScore=0.0,
            vLandmarks=None
        )
    
    def __del__(self):
        """Cleanup on deletion."""
        try:
            if getattr(self, '_bIsInitialized', False) and getattr(self, 'obPoseDetector', None):
                self.close_pose_tracker()
        except Exception:
            pass
