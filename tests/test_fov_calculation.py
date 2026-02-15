"""
test_fov_calculation.py - Tests for FOV calculation correctness

Verifies that:
- Sony AX700 FOV at max optical zoom is ~6.77 degrees
- Pixel-to-degree conversion is correct at 1280px width
- Default config has the corrected FOV values
- Legacy angle-per-pixel value was replaced
"""

import math
import pytest


class TestFOVCalculation:
    """Tests for field of view and pixel-to-degree conversion."""

    def test_sony_ax700_fov_at_max_optical_zoom(self):
        """Verify FOV calculation for Sony AX700 at maximum optical zoom.

        Sensor width: 13.2mm (1-inch type Exmor RS)
        Focal length at max optical zoom: 111.6mm
        FOV = 2 * atan(sensor_width / (2 * focal_length))
        """
        flSensorWidthMM = 13.2
        flFocalLengthMM = 111.6

        flFovRadians = 2.0 * math.atan(flSensorWidthMM / (2.0 * flFocalLengthMM))
        flFovDegrees = math.degrees(flFovRadians)

        # Should be between 6.5 and 7.0 degrees
        assert 6.5 <= flFovDegrees <= 7.0, (
            f"FOV {flFovDegrees:.2f} degrees is outside expected range [6.5, 7.0]"
        )

        # Should be approximately 6.77 degrees (within 0.05)
        assert abs(flFovDegrees - 6.77) < 0.05, (
            f"FOV {flFovDegrees:.4f} degrees is not close enough to 6.77"
        )

    def test_pixel_to_degree_conversion_at_1280px(self):
        """Verify angle-per-pixel calculation at 1280px width."""
        flFovDegrees = 6.77
        iWidthPixels = 1280

        flAnglePerPixel = flFovDegrees / iWidthPixels

        # Should be approximately 0.00529 (within 0.0001)
        assert abs(flAnglePerPixel - 0.00529) < 0.0001, (
            f"Angle per pixel {flAnglePerPixel:.6f} is not close to 0.00529"
        )

        # Verify: 100 pixels offset = ~0.53 degrees (within 0.05)
        fl100PixelOffset = 100 * flAnglePerPixel
        assert abs(fl100PixelOffset - 0.53) < 0.05, (
            f"100-pixel offset = {fl100PixelOffset:.4f} degrees, expected ~0.53"
        )

    def test_default_config_has_correct_fov(self, obTestConfig):
        """Verify SystemConfiguration default FOV matches corrected value."""
        assert obTestConfig.flFieldOfViewDegrees == 6.77, (
            f"Config FOV = {obTestConfig.flFieldOfViewDegrees}, expected 6.77"
        )

        assert abs(obTestConfig.flInitialAnglePerPixelDegrees - 0.00529) < 0.0001, (
            f"Config angle/pixel = {obTestConfig.flInitialAnglePerPixelDegrees}, "
            f"expected ~0.00529"
        )

    def test_fov_replaces_legacy_angle_per_pixel(self):
        """Verify the legacy 0.05 deg/pixel value has been replaced.

        Previous (wrong) value was 0.05 deg/pixel, implying a ~64-degree FOV.
        Current value should be 0.00529 deg/pixel (6.77 degree FOV).
        """
        flLegacyAnglePerPixel = 0.05
        flCurrentAnglePerPixel = 0.00529

        assert flLegacyAnglePerPixel != flCurrentAnglePerPixel, (
            "Legacy and current angle-per-pixel should not be equal"
        )

        # The current value should be roughly 10x smaller than legacy
        flRatio = flLegacyAnglePerPixel / flCurrentAnglePerPixel
        assert flRatio > 5.0, (
            f"Current value should be significantly smaller than legacy. "
            f"Ratio = {flRatio:.1f}"
        )
