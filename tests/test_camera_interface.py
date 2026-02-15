"""
test_camera_interface.py - Tests for CameraInterface video file input

Verifies that CameraInterface can:
- Read frames from a video file
- Read all frames from a video
- Loop the video at the end
- Provide timestamps with each frame
- Handle missing video files gracefully
"""

import pytest
from interfaces.camera_interface import CameraInterface


class TestCameraVideoFileInput:
    """Tests for CameraInterface reading from video files."""

    def test_camera_reads_from_video_file(self, obSyntheticCamera):
        """Verify camera can read a single frame from a video file."""
        obFrame, dTimestamp = obSyntheticCamera.capture_frame_with_timestamp()

        assert obFrame is not None, "Failed to read frame from video file"
        assert obFrame.shape == (720, 1280, 3), (
            f"Unexpected frame shape: {obFrame.shape}, expected (720, 1280, 3)"
        )

    def test_camera_reads_all_frames_from_video(self, obSyntheticCamera):
        """Verify camera can read all 90 frames from the test video."""
        iSuccessfulReads = 0

        for iFrame in range(90):
            obFrame, dTimestamp = obSyntheticCamera.capture_frame_with_timestamp()
            if obFrame is not None:
                iSuccessfulReads += 1

        assert iSuccessfulReads == 90, (
            f"Only read {iSuccessfulReads}/90 frames from video file"
        )

    def test_camera_loops_video_at_end(self, obSyntheticCamera):
        """Verify camera loops back to start after reaching end of video."""
        # Read all 90 frames
        for _ in range(90):
            obSyntheticCamera.capture_frame_with_timestamp()

        # Read 5 more frames (after loop)
        vPostLoopFrames = []
        for _ in range(5):
            obFrame, dTimestamp = obSyntheticCamera.capture_frame_with_timestamp()
            vPostLoopFrames.append(obFrame)

        # Frame 91 (first after loop) should not be None
        assert vPostLoopFrames[0] is not None, (
            "Video did not loop -- frame after end of video is None"
        )

        # All post-loop frames should be valid
        for iIdx, obFrame in enumerate(vPostLoopFrames):
            assert obFrame is not None, (
                f"Post-loop frame {iIdx + 91} is None"
            )

    def test_camera_frame_has_timestamp(self, obSyntheticCamera):
        """Verify each captured frame has a positive float timestamp."""
        obFrame, dTimestamp = obSyntheticCamera.capture_frame_with_timestamp()

        assert dTimestamp > 0.0, (
            f"Timestamp should be positive, got {dTimestamp}"
        )
        assert isinstance(dTimestamp, float), (
            f"Timestamp should be a float, got {type(dTimestamp)}"
        )

    def test_camera_video_file_not_found(self):
        """Verify camera handles missing video file gracefully."""
        obCamera = CameraInterface(sVideoFilePath="nonexistent_video_file.mp4")
        bOpened = obCamera.open_camera_device()

        assert bOpened is False, (
            "Camera should fail to open a nonexistent video file"
        )
