"""
test_motion_integration.py - Integration tests for full motion smoothing pipeline

Validates that the TrackerController correctly integrates all Phase 3 modules:
- OneEuroFilter on pose input (jitter suppression)
- Confidence-based gain scaling
- S-curve velocity profiling via MotionProfiler
- HomeReturnController state machine for safe zone behavior

Uses FakeClock for deterministic timing, SimulatedMotorInterface for motor
simulation, and mock PoseTracker for configurable pose results.

Follows Hungarian notation per CLAUDE.md.
"""

import pytest
import numpy as np
from unittest.mock import Mock, call

from control.tracker_controller import TrackerController, TrackerState
from control.control_algorithm import ProportionalController
from interfaces.motor_interface import SimulatedMotorInterface
from tracking.pose_tracker import PoseResult
from utilities.config_manager import SystemConfiguration
from utilities.clock import FakeClock


def _build_test_controller(
    obFakeClock: FakeClock = None,
    flProportionalGain: float = 1.0,
    obConfig: SystemConfiguration = None,
) -> tuple:
    """
    Build a TrackerController wired with all test dependencies.

    Returns:
        Tuple of (obTracker, obMotor, obMockPose, obMockCamera, obFakeClock, obConfig)
    """
    if obFakeClock is None:
        obFakeClock = FakeClock(dStartTimeSeconds=0.0)

    # Simulated motor with FakeClock
    obMotor = SimulatedMotorInterface(obClock=obFakeClock)
    obMotor.connect_to_motor_controller()

    # Mock camera that returns fixed dimensions
    obMockCamera = Mock()
    obMockCamera.get_frame_dimensions.return_value = (1280, 720)
    # capture_frame_with_timestamp returns a synthetic frame + timestamp
    obMockCamera.capture_frame_with_timestamp.return_value = (
        np.zeros((720, 1280, 3), dtype=np.uint8),
        obFakeClock.get_time_seconds()
    )

    # Mock pose tracker
    obMockPose = Mock()
    obMockPose.detect_person_in_frame.return_value = PoseResult(
        flPersonCenterXPixels=640.0,
        flPersonCenterYPixels=360.0,
        bPersonWasDetected=True,
        flPersonConfidenceScore=0.9,
        vLandmarks=None
    )

    # Control algorithm
    obAlgo = ProportionalController(flProportionalGain)

    # Config
    if obConfig is None:
        obConfig = SystemConfiguration()
    obConfig.bAllowStartWithoutMotor = True
    obConfig.bEnableVisualization = False
    obConfig.flFieldOfViewDegrees = 6.77

    # Build controller
    obTracker = TrackerController(
        obMotorInterface=obMotor,
        obCameraInterface=obMockCamera,
        obPoseTracker=obMockPose,
        obControlAlgorithm=obAlgo,
        obClock=obFakeClock
    )
    obTracker.apply_configuration(obConfig)
    obTracker.start_tracking_mode()

    return (obTracker, obMotor, obMockPose, obMockCamera, obFakeClock, obConfig)


def _run_frames(
    obTracker: TrackerController,
    obMockPose: Mock,
    obMockCamera: Mock,
    obFakeClock: FakeClock,
    obMotor: SimulatedMotorInterface,
    iFrameCount: int,
    flFrameIntervalSeconds: float = 1.0 / 30.0,
    flPersonX: float = 640.0,
    flConfidence: float = 0.9,
    bDetected: bool = True,
    flNoiseStd: float = 0.0,
    vPersonXSequence: list = None,
):
    """
    Run multiple frames through the tracking pipeline.

    Returns:
        List of commanded target angles (from motor spy)
    """
    vCommandedAngles = []

    for i in range(iFrameCount):
        # Advance time
        obFakeClock.advance_time_seconds(flFrameIntervalSeconds)
        dNow = obFakeClock.get_time_seconds()

        # Determine person X for this frame
        if vPersonXSequence is not None:
            flX = vPersonXSequence[i]
        else:
            flX = flPersonX
        if flNoiseStd > 0.0:
            flX += np.random.normal(0.0, flNoiseStd)

        # Update mock pose result
        obMockPose.detect_person_in_frame.return_value = PoseResult(
            flPersonCenterXPixels=flX,
            flPersonCenterYPixels=360.0,
            bPersonWasDetected=bDetected,
            flPersonConfidenceScore=flConfidence,
            vLandmarks=None
        )

        # Update mock camera timestamp
        obMockCamera.capture_frame_with_timestamp.return_value = (
            np.zeros((720, 1280, 3), dtype=np.uint8),
            dNow
        )

        # Advance motor simulation
        obMotor.advance_simulation(flFrameIntervalSeconds)

        # Record the current commanded angle BEFORE the tick
        flPrevCommanded = obTracker._flLastCommandedAngle

        # Execute one tracking loop tick
        obTracker.execute_main_tracking_loop_tick()

        # Record the commanded angle AFTER the tick
        flNewCommanded = obTracker._flLastCommandedAngle
        if flNewCommanded is not None:
            vCommandedAngles.append(flNewCommanded)
        elif flPrevCommanded is not None:
            vCommandedAngles.append(flPrevCommanded)
        else:
            vCommandedAngles.append(0.0)

    return vCommandedAngles


class TestMotionIntegration:
    """Integration tests for the full Phase 3 motion smoothing pipeline."""

    def test_filtered_pose_reduces_jitter_in_motor_commands(self):
        """
        Stationary person with jittery pose detection produces filtered pose X
        values with less variation than the raw noisy input, confirming the
        OneEuroFilter is actively suppressing jitter in the pipeline.
        """
        np.random.seed(42)
        obTracker, obMotor, obMockPose, obMockCamera, obFakeClock, obConfig = _build_test_controller()

        # Generate 100 frames of stationary person at center + 5px noise
        flDt = 1.0 / 30.0
        vRawXValues = []
        vFilteredXValues = []

        for i in range(100):
            obFakeClock.advance_time_seconds(flDt)
            dNow = obFakeClock.get_time_seconds()

            flRawX = 640.0 + np.random.normal(0.0, 5.0)
            vRawXValues.append(flRawX)

            obMockPose.detect_person_in_frame.return_value = PoseResult(
                flPersonCenterXPixels=flRawX,
                flPersonCenterYPixels=360.0,
                bPersonWasDetected=True,
                flPersonConfidenceScore=0.9,
                vLandmarks=None
            )
            obMockCamera.capture_frame_with_timestamp.return_value = (
                np.zeros((720, 1280, 3), dtype=np.uint8),
                dNow
            )
            obMotor.advance_simulation(flDt)
            obTracker.execute_main_tracking_loop_tick()

            # Record the filtered X value from the pose filter
            if obTracker.obPoseFilter is not None:
                vFilteredXValues.append(obTracker.obPoseFilter._flPreviousFiltered)

        # Compare raw vs filtered jitter
        # Use last 80 values (after filter warmup)
        vRawSteady = vRawXValues[20:]
        vFilteredSteady = vFilteredXValues[20:]

        flRawStd = float(np.std(vRawSteady))
        flFilteredStd = float(np.std(vFilteredSteady))

        # OneEuroFilter with minCutoff=0.01Hz should reduce jitter significantly
        assert flFilteredStd < flRawStd, (
            f"Filtered pose std {flFilteredStd:.4f}px is not less than "
            f"raw pose std {flRawStd:.4f}px. OneEuroFilter should suppress jitter."
        )
        # Additionally, filtered jitter should be well below 1px (per 03-01 verification)
        assert flFilteredStd < 1.0, (
            f"Filtered pose std {flFilteredStd:.4f}px exceeds 1px target. "
            f"OneEuroFilter should suppress stationary jitter to <1px."
        )

    def test_scurve_profiler_prevents_instant_velocity_jump(self):
        """
        First motor command for an off-center person should be smaller than
        what unsmoothed control would produce (S-curve limits acceleration).
        """
        obTracker, obMotor, obMockPose, obMockCamera, obFakeClock, obConfig = _build_test_controller()

        # Person far off center (X=900, center=640)
        # Pixel offset: 260px, angle error: 260 * 6.77/1280 = 1.375 deg
        # With P gain=1.0, unsmoothed correction = 1.375 deg
        vCommandedAngles = _run_frames(
            obTracker, obMockPose, obMockCamera, obFakeClock, obMotor,
            iFrameCount=1,
            flPersonX=900.0,
            flConfidence=0.9
        )

        # The first commanded angle should be smaller than the full correction
        # because the S-curve profiler limits initial acceleration
        flFirstCommand = vCommandedAngles[0]
        flFullCorrection = 260.0 * (6.77 / 1280.0)  # ~1.375 deg

        # Due to S-curve profiling, first frame command should be less than
        # what un-profiled control would produce
        assert abs(flFirstCommand) < abs(flFullCorrection), (
            f"First commanded angle {flFirstCommand:.4f} deg is not less than "
            f"full correction {flFullCorrection:.4f} deg -- S-curve profiler should limit initial acceleration"
        )

    def test_low_confidence_holds_position(self):
        """
        Frames with confidence below hold threshold should not produce
        new motor commands (confidence gate blocks corrections).
        """
        obTracker, obMotor, obMockPose, obMockCamera, obFakeClock, obConfig = _build_test_controller()

        # First, send a few high-confidence frames to establish baseline commanded angle
        _run_frames(
            obTracker, obMockPose, obMockCamera, obFakeClock, obMotor,
            iFrameCount=5,
            flPersonX=700.0,  # Slightly off center
            flConfidence=0.9
        )
        flBaselineCommanded = obTracker._flLastCommandedAngle

        # Now feed frames with very low confidence (below hold threshold 0.3)
        # for less than the low-confidence timeout
        vCommandedAngles = _run_frames(
            obTracker, obMockPose, obMockCamera, obFakeClock, obMotor,
            iFrameCount=30,  # 1 second at 30fps -- less than 5s timeout
            flPersonX=900.0,  # Person far off center, but confidence too low
            flConfidence=0.1
        )

        # The last commanded angle should not have changed from baseline
        # because the confidence gate blocks all corrections
        flFinalCommanded = obTracker._flLastCommandedAngle
        assert flFinalCommanded == flBaselineCommanded, (
            f"Commanded angle should not change during low-confidence hold. "
            f"Baseline: {flBaselineCommanded:.4f}, Final: {flFinalCommanded:.4f}"
        )

    def test_sustained_low_confidence_triggers_home_return(self):
        """
        Low confidence exceeding timeout should trigger home return.
        Then high confidence should reset timer and resume tracking.
        """
        obConfig = SystemConfiguration()
        obConfig.flConfidenceLowTimeoutSeconds = 2.0  # Short timeout for test
        obConfig.flHomeReturnDelaySeconds = 0.5  # Short delay too
        obTracker, obMotor, obMockPose, obMockCamera, obFakeClock, obConfig = _build_test_controller(
            obConfig=obConfig
        )

        # First, move motor off home with high-confidence tracking
        _run_frames(
            obTracker, obMockPose, obMockCamera, obFakeClock, obMotor,
            iFrameCount=30,
            flPersonX=900.0,
            flConfidence=0.9
        )
        flPreReturnCommanded = obTracker._flLastCommandedAngle
        assert abs(flPreReturnCommanded) > 0.1, (
            f"Commanded angle should be off home, got {flPreReturnCommanded:.4f}"
        )

        # Now feed low confidence for longer than timeout
        # 2.0s timeout + 0.5s delay + some frames for return motion
        vReturnAngles = _run_frames(
            obTracker, obMockPose, obMockCamera, obFakeClock, obMotor,
            iFrameCount=200,  # ~6.67 seconds at 30fps -- well past 2.5s
            flPersonX=900.0,
            flConfidence=0.1
        )

        # Commanded angle should have moved toward home (0.0 degrees)
        flFinalCommanded = obTracker._flLastCommandedAngle
        assert abs(flFinalCommanded) < abs(flPreReturnCommanded), (
            f"Commanded angle should have moved toward home after low-confidence timeout. "
            f"Pre-return: {flPreReturnCommanded:.4f}, Final: {flFinalCommanded:.4f}"
        )

        # Now feed high confidence frames -- timer should reset
        _run_frames(
            obTracker, obMockPose, obMockCamera, obFakeClock, obMotor,
            iFrameCount=30,
            flPersonX=900.0,
            flConfidence=0.9
        )

        # Low-confidence timer should have been reset
        assert obTracker._flLowConfidenceTimer == 0.0, (
            "Low-confidence timer should be reset after high confidence frames"
        )

    def test_home_return_activates_after_delay(self):
        """
        Person in deadband (safe zone) for longer than delay should trigger home return.
        Tests the HomeReturnController state machine integration by directly verifying
        state transitions through the TrackerController's internal controller.

        Note: In a real system, as the motor tracks the person, the person moves back
        toward center of the camera, reducing the angle error. This test simulates
        that feedback by dynamically positioning the person to keep them in the deadband.
        """
        obConfig = SystemConfiguration()
        obConfig.flHomeReturnDelaySeconds = 0.5  # Short delay for test
        obConfig.flControlDeadbandDegrees = 2.0  # Wide deadband to account for OneEuroFilter latency
        obTracker, obMotor, obMockPose, obMockCamera, obFakeClock, obConfig = _build_test_controller(
            obConfig=obConfig
        )

        # First, directly move the motor off home using motor commands
        # (bypass the tracking pipeline -- just set motor position)
        obMotor.send_move_to_angle_command(1.0)
        for _ in range(30):
            obFakeClock.advance_time_seconds(0.033)
            obMotor.advance_simulation(0.033)

        # Reset tracking mode to clear pose filter and profiler state
        obTracker.start_tracking_mode()

        # Now start feeding frames where the person is in the deadband.
        # The key: personAngleRelativeToHome = motor_angle + angle_error
        # We need this to be within 2.0 deg of 0.
        # If we dynamically position the person so angle_error = -motor_angle,
        # then personAngleRelativeToHome = 0 (within deadband).
        flAnglePerPixel = 6.77 / 1280.0
        flDt = 1.0 / 30.0

        # Record the initial commanded angle
        obTracker._flLastCommandedAngle = None  # Clear any stale commanded angle

        vCommandedAngles = []
        for i in range(120):  # 4 seconds, well past 0.5s delay
            obFakeClock.advance_time_seconds(flDt)
            dNow = obFakeClock.get_time_seconds()

            # Get motor state to compute person position
            obMotor.advance_simulation(flDt)
            obState = obMotor.get_latest_motor_state()
            flMotorAngle = obState.flMotorAngleDegrees

            # Position person so they appear at home (0 deg) relative to world
            # angle_error should cancel motor_angle
            # person_x = 640 - (motor_angle / angle_per_pixel)
            flPersonX = 640.0 - (flMotorAngle / flAnglePerPixel)
            flPersonX = max(0.0, min(1280.0, flPersonX))

            obMockPose.detect_person_in_frame.return_value = PoseResult(
                flPersonCenterXPixels=flPersonX,
                flPersonCenterYPixels=360.0,
                bPersonWasDetected=True,
                flPersonConfidenceScore=0.9,
                vLandmarks=None
            )
            obMockCamera.capture_frame_with_timestamp.return_value = (
                np.zeros((720, 1280, 3), dtype=np.uint8),
                dNow
            )

            obTracker.execute_main_tracking_loop_tick()

            if obTracker._flLastCommandedAngle is not None:
                vCommandedAngles.append(obTracker._flLastCommandedAngle)

        # The HomeReturnController should have transitioned through:
        # TRACKING -> SAFE_ZONE_DELAY (at start) -> RETURNING_HOME (after 0.5s)
        # -> eventually AT_HOME or close to home
        from control.home_return_controller import HomeReturnState
        eState = obTracker.obHomeReturnController.get_state()
        assert eState in (HomeReturnState.RETURNING_HOME, HomeReturnState.AT_HOME), (
            f"HomeReturnController should be RETURNING_HOME or AT_HOME after {4}s "
            f"with 0.5s delay, but got {eState}"
        )

        # If there are commanded angles, the last one should be closer to home
        # than where the motor started (1.0 deg)
        if len(vCommandedAngles) > 0:
            flFinalCommanded = vCommandedAngles[-1]
            assert abs(flFinalCommanded) < 1.0, (
                f"Commanded angle {flFinalCommanded:.4f} should be closer to home than start (1.0 deg)"
            )

    def test_direction_reversal_is_smooth(self):
        """
        Switching from person right-of-center to left-of-center should
        produce smooth velocity changes (no sudden flick).
        """
        obTracker, obMotor, obMockPose, obMockCamera, obFakeClock, obConfig = _build_test_controller()

        # Build a sequence: 30 frames right, 30 frames left
        vXSequence = [900.0] * 30 + [380.0] * 30

        vCommandedAngles = _run_frames(
            obTracker, obMockPose, obMockCamera, obFakeClock, obMotor,
            iFrameCount=60,
            flConfidence=0.9,
            vPersonXSequence=vXSequence
        )

        # Compute velocity between consecutive commanded angles
        flDt = 1.0 / 30.0
        vVelocities = []
        for i in range(1, len(vCommandedAngles)):
            flVelocity = (vCommandedAngles[i] - vCommandedAngles[i - 1]) / flDt
            vVelocities.append(flVelocity)

        # Compute acceleration (velocity change between frames)
        vAccelerations = []
        for i in range(1, len(vVelocities)):
            flAccel = (vVelocities[i] - vVelocities[i - 1]) / flDt
            vAccelerations.append(abs(flAccel))

        # The key assertion: the direction reversal (around frame 30)
        # should not produce an instantaneous velocity flip
        # Check the velocity at the reversal point (frames 28-32)
        iReversalStart = max(0, 28)
        iReversalEnd = min(len(vVelocities), 33)
        if iReversalEnd > iReversalStart + 1:
            vReversalVelocities = vVelocities[iReversalStart:iReversalEnd]
            # Consecutive velocities during reversal should not have extreme jumps
            for i in range(1, len(vReversalVelocities)):
                flVelChange = abs(vReversalVelocities[i] - vReversalVelocities[i - 1])
                # The velocity change per frame should be bounded by the S-curve profiler
                assert flVelChange < 200.0, (
                    f"Velocity change at reversal frame {iReversalStart + i}: "
                    f"{flVelChange:.2f} deg/s^2 -- too abrupt, S-curve should smooth this"
                )
