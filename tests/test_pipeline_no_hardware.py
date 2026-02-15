"""
test_pipeline_no_hardware.py - Integration tests for full tracking pipeline

Verifies that the complete tracking pipeline (camera -> pose -> control -> motor)
runs without any physical hardware by using:
- SimulatedMotorInterface (trapezoidal velocity physics)
- CameraInterface with synthetic video file
- Mock PoseTracker (no MediaPipe dependency)
- ProportionalController
"""

import pytest
from control.control_algorithm import ProportionalController
from control.tracker_controller import TrackerController
from tracking.pose_tracker import PoseResult


class TestPipelineNoHardware:
    """Integration tests for full pipeline without hardware."""

    def _create_tracker_controller(self, obSimulatedMotor, obSyntheticCamera,
                                    obMockPoseTracker, obTestConfig):
        """Helper to create a fully configured TrackerController."""
        obControlAlgorithm = ProportionalController(1.0)
        obController = TrackerController(
            obSimulatedMotor,
            obSyntheticCamera,
            obMockPoseTracker,
            obControlAlgorithm
        )
        obController.apply_configuration(obTestConfig)
        return obController

    def test_full_pipeline_runs_without_hardware(
        self, obSimulatedMotor, obSyntheticCamera, obTestConfig, obMockPoseTracker
    ):
        """Verify full tracking pipeline runs end-to-end without hardware.

        Creates TrackerController with all simulated/mock dependencies,
        starts tracking, executes 10 ticks, and verifies samples are produced.
        """
        obController = self._create_tracker_controller(
            obSimulatedMotor, obSyntheticCamera, obMockPoseTracker, obTestConfig
        )

        obController.start_tracking_mode()

        vSamples = []
        for _ in range(10):
            obSample = obController.execute_main_tracking_loop_tick()
            vSamples.append(obSample)
            # Advance motor simulation by 1/30th second (one frame period)
            obSimulatedMotor.advance_simulation(1.0 / 30.0)

        # At least 8 of 10 ticks should return a valid TrackingSample
        iNonNoneCount = sum(1 for s in vSamples if s is not None)
        assert iNonNoneCount >= 8, (
            f"Only {iNonNoneCount}/10 ticks returned samples, expected >= 8"
        )

        # At least one sample should have detected a person
        bAnyPersonDetected = any(
            s.bPersonWasDetected for s in vSamples if s is not None
        )
        assert bAnyPersonDetected, (
            "No sample detected a person -- mock pose tracker should always detect"
        )

        obController.stop_tracking_mode()

    def test_pipeline_creates_valid_tracking_samples(
        self, obSimulatedMotor, obSyntheticCamera, obTestConfig, obMockPoseTracker
    ):
        """Verify pipeline produces TrackingSamples with valid fields."""
        obController = self._create_tracker_controller(
            obSimulatedMotor, obSyntheticCamera, obMockPoseTracker, obTestConfig
        )

        obController.start_tracking_mode()

        vSamples = []
        for _ in range(5):
            obSample = obController.execute_main_tracking_loop_tick()
            vSamples.append(obSample)
            obSimulatedMotor.advance_simulation(1.0 / 30.0)

        obController.stop_tracking_mode()

        # Verify each non-None sample has valid fields
        for obSample in vSamples:
            if obSample is not None:
                assert obSample.dSampleTimestampSeconds > 0, (
                    f"Sample timestamp should be positive, got {obSample.dSampleTimestampSeconds}"
                )
                assert obSample.iSampleSequenceNumber >= 0, (
                    f"Sample sequence number should be >= 0, got {obSample.iSampleSequenceNumber}"
                )
                assert obSample.flPersonConfidenceScore >= 0.0, (
                    f"Confidence should be >= 0.0, got {obSample.flPersonConfidenceScore}"
                )

    def test_pipeline_tracks_person_center(
        self, obSimulatedMotor, obSyntheticCamera, obTestConfig, obMockPoseTracker
    ):
        """Verify pipeline responds to off-center person by adjusting motor target.

        Sets mock pose tracker to return person at x=700 (off-center to the right)
        and verifies the controller commands the motor to move.
        """
        # Override mock to return person at x=700 (right of center at 640)
        obMockPoseTracker.detect_person_in_frame.return_value = PoseResult(
            flPersonCenterXPixels=700.0,
            flPersonCenterYPixels=360.0,
            bPersonWasDetected=True,
            flPersonConfidenceScore=0.9,
            vLandmarks=None
        )

        obController = self._create_tracker_controller(
            obSimulatedMotor, obSyntheticCamera, obMockPoseTracker, obTestConfig
        )

        obController.start_tracking_mode()

        # Execute 5 tracking ticks with motor advancing
        for _ in range(5):
            obController.execute_main_tracking_loop_tick()
            obSimulatedMotor.advance_simulation(1.0 / 30.0)

        # Motor target should have changed from 0 (controller responded to off-center)
        obMotorState = obSimulatedMotor.get_latest_motor_state()
        flTargetAngle = obMotorState.flMotorTargetAngleDegrees

        assert flTargetAngle != 0.0, (
            "Motor target angle should have changed from 0.0 in response to "
            "off-center person at x=700"
        )

        # Target should be positive (person is right of center, motor should move right)
        assert flTargetAngle > 0.0, (
            f"Motor should move right (positive) for person right of center, "
            f"got target = {flTargetAngle}"
        )

        obController.stop_tracking_mode()
