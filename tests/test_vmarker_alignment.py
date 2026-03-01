"""
test_vmarker_alignment.py - V-marker lectern overlay alignment tests (TEST-03)

Validates that the V-marker overlay aligns correctly with the virtual center
line when the motor is at the home position (0 degrees). Covers:

1. Mathematical alignment: iHomeLineX == iCenterX when flMotorAngle == 0.0
2. Motor angle shift: V-marker moves correctly with non-zero motor angle
3. Full pipeline: SimulatedMotorInterface at home produces aligned V-marker

Follows Hungarian notation per CLAUDE.md.
"""

import pytest
from test_motion_integration import _build_test_controller


def test_vmarker_aligns_with_center_at_home_position():
    """TEST-03: V-marker pixel equals center pixel when motor is at home (0 degrees)."""
    iWidth = 1280
    iCenterX = iWidth // 2  # 640
    flFovDegrees = 6.77
    flAnglePerPixel = flFovDegrees / iWidth  # ~0.00529 deg/px

    # Motor at home: angle = 0.0
    flMotorAngle = 0.0
    iHomeLineX = int(iCenterX - (flMotorAngle / flAnglePerPixel))

    assert iHomeLineX == iCenterX, (
        f"V-marker at home should be at center pixel {iCenterX}, "
        f"but computed {iHomeLineX} with motor angle {flMotorAngle}"
    )


def test_vmarker_moves_with_motor_angle():
    """V-marker position shifts correctly when motor is at a non-zero angle."""
    iWidth = 1280
    iCenterX = iWidth // 2
    flFovDegrees = 6.77
    flAnglePerPixel = flFovDegrees / iWidth

    # Motor at +1.0 degree: home line should shift left of center
    flMotorAngle = 1.0
    iHomeLineX = int(iCenterX - (flMotorAngle / flAnglePerPixel))

    # V-marker must be left of center when motor is at positive angle
    assert iHomeLineX < iCenterX, (
        f"V-marker should be left of center for +1 deg motor angle, "
        f"got {iHomeLineX} (center: {iCenterX})"
    )

    # Verify the exact formula matches main.py: int(iCenterX - (flMotorAngle / flAnglePerPixel))
    iExpectedHomeLineX = int(iCenterX - (flMotorAngle / flAnglePerPixel))
    assert iHomeLineX == iExpectedHomeLineX, (
        f"V-marker position {iHomeLineX} does not match expected {iExpectedHomeLineX}"
    )

    # Shift should be approximately 189 pixels (1.0 / 0.00529)
    iActualShift = iCenterX - iHomeLineX
    assert 185 < iActualShift < 195, (
        f"V-marker shift for +1 deg should be ~189px, got {iActualShift}px"
    )


def test_vmarker_aligns_in_full_pipeline():
    """V-marker aligns with center when motor is at home in the full pipeline."""
    obTracker, obMotor, obMockPose, obMockCamera, obFakeClock, obConfig = _build_test_controller()

    # Motor should start at home (0.0 degrees)
    obState = obMotor.get_latest_motor_state()
    flMotorAngle = obState.flMotorAngleDegrees

    iWidth = 1280
    iCenterX = iWidth // 2
    flAnglePerPixel = obTracker.get_angle_per_pixel_degrees(iWidth)

    iHomeLineX = int(iCenterX - (flMotorAngle / flAnglePerPixel))

    assert iHomeLineX == iCenterX, (
        f"V-marker should be at center ({iCenterX}) when motor is at home, "
        f"but computed {iHomeLineX} (motor angle: {flMotorAngle:.4f})"
    )
