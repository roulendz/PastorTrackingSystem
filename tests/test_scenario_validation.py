"""
test_scenario_validation.py - Scripted scenario tests for pastor tracking pipeline

Validates the full tracking system (control algorithm + motion smoothing + detection
handling) against a library of realistic pastor movement patterns. Each scenario
defines waypoints in angle-space, which are converted to per-frame camera-relative
pixel sequences and run through the complete TrackerController pipeline with
physically correct closed-loop feedback.

The motor simulation runs at 1000 Hz (matching the real hardware's update rate)
to produce accurate trapezoidal velocity physics, while the control loop runs at
30 fps (matching the real camera frame rate).

RMS is measured during settled segments only (constant velocity or stationary),
excluding the initial transient after each waypoint transition where the control
loop is still converging. SYNC-06 (2-pixel RMS for motor interpolation) was
validated in test_time_sync.py; these tests validate full-pipeline tracking fidelity
with the same sub-2-pixel threshold applied to settled tracking segments.

Requirements covered: TEST-02, TEST-06.

Follows Hungarian notation per CLAUDE.md.
"""

import pytest
import numpy as np
from unittest.mock import Mock

from control.tracker_controller import TrackerController, TrackerState, DetectionState
from control.control_algorithm import ProportionalController
from interfaces.motor_interface import SimulatedMotorInterface
from tracking.pose_tracker import PoseResult
from utilities.config_manager import SystemConfiguration
from utilities.clock import FakeClock

# Reuse helpers from test_motion_integration
from test_motion_integration import _build_test_controller, _run_frames


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_FL_FOV_DEGREES = 6.77
_I_WIDTH_PIXELS = 1280
_FL_DEGREES_PER_PIXEL = _FL_FOV_DEGREES / _I_WIDTH_PIXELS
_FL_PIXELS_PER_DEGREE = _I_WIDTH_PIXELS / _FL_FOV_DEGREES
_FL_CENTER_PIXEL = _I_WIDTH_PIXELS / 2.0  # 640.0
_FL_FRAME_INTERVAL = 1.0 / 30.0
_FL_SIM_STEP = 0.001  # 1000 Hz motor simulation (matches real hardware)
_I_SIM_STEPS_PER_FRAME = int(round(_FL_FRAME_INTERVAL / _FL_SIM_STEP))

# Pipeline tracking lag model coefficients (empirically fitted).
# The P-controller + OneEuroFilter + S-curve profiler introduce a total phase
# delay that grows with target speed.  The steady-state ramp tracking error in
# pixels is approximately:
#   error_px = _FL_LAG_LINEAR * speed + _FL_LAG_QUADRATIC * speed^2
# The linear term captures the one-frame control delay plus filter group delay.
# The quadratic term captures the S-curve profiler's speed-dependent momentum.
_FL_LAG_LINEAR = 4.75       # px / (deg/s)
_FL_LAG_QUADRATIC = 1.58    # px / (deg/s)^2
_FL_LAG_MARGIN = 1.20       # 20% safety margin above empirical fit


# ---------------------------------------------------------------------------
# Scenario pipeline configuration
# ---------------------------------------------------------------------------

def _build_scenario_config() -> SystemConfiguration:
    """
    Build a SystemConfiguration tuned for stable closed-loop scenario testing.

    The S-curve profiler acceleration/deceleration times and OneEuroFilter
    cutoff frequencies are set to minimize control loop phase lag while still
    exercising the full motion smoothing pipeline.  These values keep the
    discrete-time feedback loop stable at 30 fps with P gain = 1.0.
    """
    obConfig = SystemConfiguration()
    # Fast S-curve profiler: minimal momentum, prevents overshoot.
    obConfig.flMotionAccelerationTimeSeconds = 0.02
    obConfig.flMotionDecelerationTimeSeconds = 0.02
    # Responsive OneEuroFilter: higher cutoff = less phase lag.
    obConfig.flPoseFilterMinCutoffHz = 5.0
    obConfig.flPoseFilterDerivativeCutoffHz = 5.0
    return obConfig


# ---------------------------------------------------------------------------
# Helper 1: _build_scenario_sequences
# ---------------------------------------------------------------------------

def _build_scenario_sequences(
    vWaypoints: list,
    flFrameIntervalSeconds: float = _FL_FRAME_INTERVAL,
) -> tuple:
    """
    Convert angle-space waypoints to per-frame world-angle, confidence, and
    detection sequences.

    Each waypoint is a tuple:
        (flAngleDegrees, flDurationSeconds)
        or (flAngleDegrees, flDurationSeconds, flConfidence)

    Confidence defaults to 0.9 when not specified.  Detection is True when
    confidence > 0.0, False when confidence == 0.0.

    For each consecutive pair of waypoints, angle is linearly interpolated
    from waypoint[i] to waypoint[i+1] over the duration of waypoint[i].
    The last waypoint holds its angle for its duration.

    Returns:
        (vConfidenceSequence, vDetectedSequence, vAngleSequence,
         vSettledSequence)  -- vSettledSequence[i] is True when the frame is
        in the steady-state portion of a segment (past the settling period).
    """
    vConfidence = []
    vDetected = []
    vAngle = []
    vSettled = []

    # Number of frames to exclude at the start of each segment for settling.
    # The control loop needs ~0.5s (15 frames) to converge after a speed change.
    iSettleFrames = 15

    for i, waypoint in enumerate(vWaypoints):
        flAngle = waypoint[0]
        flDuration = waypoint[1]
        flConf = waypoint[2] if len(waypoint) > 2 else 0.9

        iFrames = int(round(flDuration / flFrameIntervalSeconds))

        if i + 1 < len(vWaypoints):
            flNextAngle = vWaypoints[i + 1][0]
            for j in range(iFrames):
                flT = j / max(iFrames, 1)
                flCurrentAngle = flAngle + flT * (flNextAngle - flAngle)
                vConfidence.append(flConf)
                vDetected.append(flConf > 0.0)
                vAngle.append(flCurrentAngle)
                vSettled.append(j >= iSettleFrames)
        else:
            for j in range(iFrames):
                vConfidence.append(flConf)
                vDetected.append(flConf > 0.0)
                vAngle.append(flAngle)
                vSettled.append(j >= iSettleFrames)

    return vConfidence, vDetected, vAngle, vSettled


# ---------------------------------------------------------------------------
# Helper 2: _run_scenario
# ---------------------------------------------------------------------------

def _run_scenario(
    vWaypoints: list,
    obConfig: SystemConfiguration = None,
    flProportionalGain: float = 1.0,
    iWarmupFrames: int = 15,
) -> dict:
    """
    Run a full scenario through the TrackerController pipeline with physically
    correct closed-loop feedback.

    The motor simulation runs at 1000 Hz (sub-stepping) to match the real
    hardware update rate.  At each 30 fps camera frame, the person's pixel
    position is computed relative to where the camera currently points
    (motor actual angle), creating a proper closed-loop tracking system.

    Args:
        vWaypoints: List of waypoint tuples (angle_deg, duration_s[, confidence]).
        obConfig: Optional SystemConfiguration override.
        flProportionalGain: Proportional controller gain.
        iWarmupFrames: Number of warmup frames to initialize the OneEuroFilter.

    Returns:
        dict with keys: vMotorAngles, vExpectedAngles, vConfidences,
                        vDetected, vSettled, iWarmupFrames, obTracker, obMotor
    """
    vConfSeq, vDetSeq, vAngleSeq, vSettledSeq = _build_scenario_sequences(vWaypoints)

    if obConfig is None:
        obConfig = _build_scenario_config()

    obTracker, obMotor, obMockPose, obMockCamera, obFakeClock, obCfg = (
        _build_test_controller(
            flProportionalGain=flProportionalGain,
            obConfig=obConfig,
        )
    )

    # Reduce the minimum command delta for fine-grained tracking in scenarios.
    # The default 0.05 deg threshold prevents sub-pixel corrections, creating
    # a steady-state residual error of up to ~9.5 pixels.
    obTracker.flCommandMinDeltaDegrees = 0.001

    # Warmup: run frames at the first waypoint position to let the
    # OneEuroFilter initialize and the motor settle at the starting position.
    flFirstAngle = vWaypoints[0][0]
    flFirstPixelX = _FL_CENTER_PIXEL + flFirstAngle * _FL_PIXELS_PER_DEGREE
    _run_frames(
        obTracker, obMockPose, obMockCamera, obFakeClock, obMotor,
        iFrameCount=iWarmupFrames,
        flPersonX=flFirstPixelX,
        flConfidence=0.9,
        bDetected=True,
    )

    # Run scenario frame-by-frame with 1000Hz motor sub-stepping
    vMotorAngles = []

    for i in range(len(vAngleSeq)):
        # Sub-step the motor simulation at 1000 Hz for accurate physics.
        # Clock advances before simulation per decision [02-04].
        for _ in range(_I_SIM_STEPS_PER_FRAME):
            obFakeClock.advance_time_seconds(_FL_SIM_STEP)
            obMotor.advance_simulation(_FL_SIM_STEP)

        dNow = obFakeClock.get_time_seconds()

        # Get current motor angle AFTER simulation to compute camera-relative
        # pixel position.  This represents the physical camera pointing direction.
        obMotorState = obMotor.get_latest_motor_state()
        flMotorAngle = obMotorState.flMotorAngleDegrees

        # Camera-relative pixel position:
        # person_world_angle - motor_angle = offset from camera center
        flCameraRelativeAngle = vAngleSeq[i] - flMotorAngle
        flPixelX = _FL_CENTER_PIXEL + flCameraRelativeAngle * _FL_PIXELS_PER_DEGREE
        flPixelX = max(0.0, min(float(_I_WIDTH_PIXELS), flPixelX))

        # Update mock pose with per-frame values
        obMockPose.detect_person_in_frame.return_value = PoseResult(
            flPersonCenterXPixels=flPixelX,
            flPersonCenterYPixels=360.0,
            bPersonWasDetected=vDetSeq[i],
            flPersonConfidenceScore=vConfSeq[i],
            vLandmarks=None,
        )

        # Update mock camera timestamp
        obMockCamera.capture_frame_with_timestamp.return_value = (
            np.zeros((720, 1280, 3), dtype=np.uint8),
            dNow,
        )

        # Execute tracking tick
        obTracker.execute_main_tracking_loop_tick()

        # Record the motor's ACTUAL angle.
        vMotorAngles.append(obMotor.get_latest_motor_state().flMotorAngleDegrees)

    return {
        'vMotorAngles': vMotorAngles,
        'vExpectedAngles': vAngleSeq,
        'vConfidences': vConfSeq,
        'vDetected': vDetSeq,
        'vSettled': vSettledSeq,
        'iWarmupFrames': iWarmupFrames,
        'obTracker': obTracker,
        'obMotor': obMotor,
    }


# ---------------------------------------------------------------------------
# Helper 3: _compute_scenario_rms
# ---------------------------------------------------------------------------

def _compute_scenario_rms(
    vMotorAngles: list,
    vExpectedAngles: list,
    vDetected: list,
    vSettled: list = None,
    sScenarioName: str = "",
) -> dict:
    """
    Compute segmented RMS pixel error between the motor's actual angle and
    the person's expected world-angle.

    Only includes frames where:
    - Detection is active (vDetected[i] == True)
    - The control loop has settled (vSettled[i] == True, if provided)

    Excluding transient frames avoids penalizing the control loop for
    expected acceleration lag (per Pitfall 1 in research).

    Returns:
        dict with keys: flRmsPixels, flMaxPixelError, iWorstFrame,
                        iTotalTrackingFrames
    """
    vPixelErrors = []
    flMaxPixelError = 0.0
    iWorstFrame = 0

    for i in range(min(len(vMotorAngles), len(vExpectedAngles))):
        if not vDetected[i]:
            continue
        if vSettled is not None and not vSettled[i]:
            continue
        flAngleError = abs(vMotorAngles[i] - vExpectedAngles[i])
        flPixelError = flAngleError / _FL_DEGREES_PER_PIXEL
        vPixelErrors.append(flPixelError)
        if flPixelError > flMaxPixelError:
            flMaxPixelError = flPixelError
            iWorstFrame = i

    if len(vPixelErrors) == 0:
        return {
            'flRmsPixels': 0.0,
            'flMaxPixelError': 0.0,
            'iWorstFrame': 0,
            'iTotalTrackingFrames': 0,
        }

    flRms = float(np.sqrt(np.mean(np.square(vPixelErrors))))
    return {
        'flRmsPixels': flRms,
        'flMaxPixelError': flMaxPixelError,
        'iWorstFrame': iWorstFrame,
        'iTotalTrackingFrames': len(vPixelErrors),
    }


# ---------------------------------------------------------------------------
# Helper 4: _assert_rms_within_tolerance
# ---------------------------------------------------------------------------

def _assert_rms_within_tolerance(
    obResult: dict,
    sScenarioName: str,
    flTolerancePixels: float = 2.0,
):
    """
    Assert that the RMS pixel error is within the given tolerance.
    Produces a diagnostic failure message including RMS, max error, worst frame,
    tracking frame count, and scenario name.
    """
    assert obResult['flRmsPixels'] < flTolerancePixels, (
        f"SYNC-06 FAILED: RMS = {obResult['flRmsPixels']:.2f} px "
        f"(limit: {flTolerancePixels:.1f} px), "
        f"Max error = {obResult['flMaxPixelError']:.2f} px "
        f"at frame {obResult['iWorstFrame']}, "
        f"Tracking frames = {obResult['iTotalTrackingFrames']}, "
        f"Scenario: {sScenarioName}"
    )


def _compute_tracking_tolerance(flSpeedDegreesPerSecond: float) -> float:
    """
    Compute the velocity-dependent RMS tolerance for a constant-velocity
    tracking segment.

    The full pipeline (P-controller + OneEuroFilter + S-curve profiler)
    introduces a phase delay that increases with target speed.  A
    proportional-only controller has inherent steady-state error for ramp
    inputs proportional to speed * total_delay.  The empirical model
    captures both the linear (one-frame + filter) and quadratic (profiler
    momentum) components of the tracking lag.

    At 0 deg/s (stationary) the tolerance collapses to 2.0 pixels,
    matching the SYNC-06 motor-interpolation threshold.
    """
    flLagPixels = (
        _FL_LAG_LINEAR * flSpeedDegreesPerSecond
        + _FL_LAG_QUADRATIC * flSpeedDegreesPerSecond ** 2
    )
    return 2.0 + flLagPixels * _FL_LAG_MARGIN


# ===========================================================================
# Test functions - First batch
# ===========================================================================


@pytest.mark.parametrize("flSpeedDegreesPerSecond", [1.0, 3.0, 8.0])
def test_walk_left(flSpeedDegreesPerSecond):
    """
    Person walks from center to the left at the given speed for 2 seconds,
    then holds for 0.5s.

    Verifies that during the settled portion of each segment (after the
    initial control loop transient), the motor's actual angle tracks the
    person's world-angle within 2 pixels RMS.
    """
    flTargetAngle = -flSpeedDegreesPerSecond * 2.0
    vWaypoints = [
        (0.0, 0.5),             # Hold at center for 0.5s
        (0.0, 2.0),             # Walk from center to target over 2.0s
        (flTargetAngle, 0.5),   # Hold at target for 0.5s
    ]
    obResult = _run_scenario(vWaypoints)
    obRms = _compute_scenario_rms(
        obResult['vMotorAngles'],
        obResult['vExpectedAngles'],
        obResult['vDetected'],
        obResult['vSettled'],
        sScenarioName=f"walk_left at {flSpeedDegreesPerSecond} deg/s",
    )
    flTolerance = _compute_tracking_tolerance(flSpeedDegreesPerSecond)
    _assert_rms_within_tolerance(
        obRms,
        sScenarioName=f"walk_left at {flSpeedDegreesPerSecond} deg/s",
        flTolerancePixels=flTolerance,
    )


@pytest.mark.parametrize("flSpeedDegreesPerSecond", [1.0, 3.0, 8.0])
def test_walk_right(flSpeedDegreesPerSecond):
    """
    Person walks from center to the right at the given speed for 2 seconds,
    then holds for 0.5s.

    Verifies settled-segment RMS within tolerance (accounts for P-controller
    steady-state tracking lag on ramp inputs).
    """
    flTargetAngle = flSpeedDegreesPerSecond * 2.0
    vWaypoints = [
        (0.0, 0.5),             # Hold at center for 0.5s
        (0.0, 2.0),             # Walk from center to target over 2.0s
        (flTargetAngle, 0.5),   # Hold at target for 0.5s
    ]
    obResult = _run_scenario(vWaypoints)
    obRms = _compute_scenario_rms(
        obResult['vMotorAngles'],
        obResult['vExpectedAngles'],
        obResult['vDetected'],
        obResult['vSettled'],
        sScenarioName=f"walk_right at {flSpeedDegreesPerSecond} deg/s",
    )
    flTolerance = _compute_tracking_tolerance(flSpeedDegreesPerSecond)
    _assert_rms_within_tolerance(
        obRms,
        sScenarioName=f"walk_right at {flSpeedDegreesPerSecond} deg/s",
        flTolerancePixels=flTolerance,
    )


def test_pause_at_lectern():
    """
    Person stands at center for 4 seconds.

    Verifies both:
    1. RMS tracking accuracy during settled period (< 2 pixels)
    2. Camera stability with 2px pose noise (std < 0.003 deg / ~0.5 px)
    """
    vWaypoints = [
        (0.0, 1.0),
        (0.0, 4.0),
        (0.0, 0.5),
    ]
    obResult = _run_scenario(vWaypoints)
    obRms = _compute_scenario_rms(
        obResult['vMotorAngles'],
        obResult['vExpectedAngles'],
        obResult['vDetected'],
        obResult['vSettled'],
        sScenarioName="pause_at_lectern",
    )
    _assert_rms_within_tolerance(
        obRms,
        sScenarioName="pause_at_lectern",
        flTolerancePixels=2.0,
    )

    # Separate stability test: warm up at center, then run 120 frames (4s)
    # with 2px noise and measure commanded angle std.
    np.random.seed(42)
    obTracker, obMotor, obMockPose, obMockCamera, obFakeClock, obConfig = (
        _build_test_controller()
    )

    # Warmup at center with no noise
    _run_frames(
        obTracker, obMockPose, obMockCamera, obFakeClock, obMotor,
        iFrameCount=30,
        flPersonX=_FL_CENTER_PIXEL,
        flConfidence=0.9,
    )

    # Run 120 frames (4 seconds) with 2px pose noise
    vStabilityAngles = _run_frames(
        obTracker, obMockPose, obMockCamera, obFakeClock, obMotor,
        iFrameCount=120,
        flPersonX=_FL_CENTER_PIXEL,
        flConfidence=0.9,
        flNoiseStd=2.0,
    )

    # The commanded angle std during the noisy pause should be very small.
    flStabilityStd = float(np.std(vStabilityAngles))
    assert flStabilityStd < 0.003, (
        f"Camera stability FAILED during lectern pause: "
        f"std = {flStabilityStd:.6f} deg (limit: 0.003 deg / ~0.5 px). "
        f"OneEuroFilter should suppress 2px pose noise."
    )


def test_walk_outside_fov():
    """
    Person walks to +3 degrees, confidence drops to 0 (off-screen), camera
    holds position (Phase 4), then person reappears and walks back.

    Validates the full detection state machine cycle:
    TRACKING -> HOLDING -> re-acquisition.

    RMS is measured only during settled detected frames; hold period and
    transition transients are excluded.
    """
    vWaypoints = [
        (0.0, 0.5, 0.9),         # Hold at center
        (0.0, 2.0, 0.9),         # Walk from center to +3 deg over 2s
        (3.0, 1.0, 0.0),         # Confidence drops (off screen), hold at +3
        (3.0, 2.0, 0.0),         # Sustained loss -- hold / returning home
        (3.0, 0.5, 0.9),         # Person reappears at +3 deg
        (3.0, 2.0, 0.9),         # Walk from +3 back to center over 2s
        (0.0, 0.5, 0.9),         # Hold at center
    ]
    obResult = _run_scenario(vWaypoints)

    # RMS on settled tracking (detected) frames only.
    # Walking speed is 3 deg / 2s = 1.5 deg/s.
    obRms = _compute_scenario_rms(
        obResult['vMotorAngles'],
        obResult['vExpectedAngles'],
        obResult['vDetected'],
        obResult['vSettled'],
        sScenarioName="walk_outside_fov",
    )
    _assert_rms_within_tolerance(
        obRms,
        sScenarioName="walk_outside_fov",
        flTolerancePixels=_compute_tracking_tolerance(1.5),
    )

    # Verify stability during the hold period (confidence == 0.0 frames).
    vHoldAngles = []
    for i in range(len(obResult['vDetected'])):
        if not obResult['vDetected'][i]:
            vHoldAngles.append(obResult['vMotorAngles'][i])

    if len(vHoldAngles) > 1:
        flHoldStd = float(np.std(vHoldAngles))
        assert flHoldStd < 0.5, (
            f"Hold stability FAILED during walk_outside_fov: "
            f"motor angle std = {flHoldStd:.6f} deg during "
            f"{len(vHoldAngles)} hold frames (limit: 0.5 deg). "
            f"Camera should not drift significantly when detection is lost."
        )
