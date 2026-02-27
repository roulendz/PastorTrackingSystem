"""
test_pose_filter.py - Tests for OneEuroFilter and confidence scaling

Verifies:
- OneEuroFilter suppresses jitter on stationary input
- OneEuroFilter tracks fast movement with low lag
- Edge cases (zero dt, reset)
- Confidence scale factor at extremes and midpoint
"""

import math
import random
import pytest

from tracking.pose_filter import OneEuroFilter, compute_confidence_scale_factor


class TestOneEuroFilter:
    """Tests for the OneEuroFilter jitter suppression."""

    def test_stationary_input_suppresses_jitter(self):
        """Feed 100 samples at 30 FPS with position 640.0 + random noise (std=5px).
        After warmup (first 10 frames), filtered output range should be < 1.0 px."""
        random.seed(42)
        flCenter = 640.0
        flStdDev = 5.0
        dTimestamp = 0.0
        flDt = 1.0 / 30.0

        obFilter = OneEuroFilter(
            dInitialTimestamp=dTimestamp,
            flInitialValue=flCenter,
        )

        vFilteredAfterWarmup = []
        for i in range(100):
            dTimestamp += flDt
            flNoisy = flCenter + random.gauss(0.0, flStdDev)
            flFiltered = obFilter.filter_value(dTimestamp, flNoisy)
            if i >= 10:  # skip warmup
                vFilteredAfterWarmup.append(flFiltered)

        flRange = max(vFilteredAfterWarmup) - min(vFilteredAfterWarmup)
        assert flRange < 1.0, (
            f"Filtered output range {flRange:.4f} px exceeds 1.0 px on stationary input"
        )

    def test_fast_movement_tracks_with_low_lag(self):
        """Feed 60 samples at 30 FPS with linear ramp from 0 to 600 px.
        Filtered output at final frame should be within 10% of raw value."""
        dTimestamp = 0.0
        flDt = 1.0 / 30.0
        flStart = 0.0
        flEnd = 600.0

        obFilter = OneEuroFilter(
            dInitialTimestamp=dTimestamp,
            flInitialValue=flStart,
        )

        flFiltered = flStart
        for i in range(60):
            dTimestamp += flDt
            flRaw = flStart + (flEnd - flStart) * (i + 1) / 60.0
            flFiltered = obFilter.filter_value(dTimestamp, flRaw)

        # Final raw value is 600.0
        flLagFraction = abs(flEnd - flFiltered) / flEnd
        assert flLagFraction < 0.10, (
            f"Filtered value {flFiltered:.2f} is {flLagFraction*100:.1f}% away from "
            f"raw {flEnd:.2f} (exceeds 10% lag limit)"
        )

    def test_zero_dt_returns_previous(self):
        """Call filter_value with same timestamp. Should return previous filtered value."""
        dTimestamp = 0.0
        obFilter = OneEuroFilter(
            dInitialTimestamp=dTimestamp,
            flInitialValue=100.0,
        )

        dTimestamp += 1.0 / 30.0
        flFirst = obFilter.filter_value(dTimestamp, 110.0)
        # Same timestamp again
        flSecond = obFilter.filter_value(dTimestamp, 200.0)
        assert flSecond == flFirst, (
            f"Zero-dt should return previous filtered value {flFirst}, got {flSecond}"
        )

    def test_reset_reinitializes_state(self):
        """Filter some values, call reset with new initial value,
        assert next filter_value returns close to new initial value."""
        dTimestamp = 0.0
        obFilter = OneEuroFilter(
            dInitialTimestamp=dTimestamp,
            flInitialValue=100.0,
        )

        # Filter several values far from initial
        for i in range(30):
            dTimestamp += 1.0 / 30.0
            obFilter.filter_value(dTimestamp, 500.0)

        # Reset to new value
        dTimestamp += 1.0 / 30.0
        obFilter.reset(dTimestamp, 200.0)

        # Next filter call should be close to 200.0
        dTimestamp += 1.0 / 30.0
        flFiltered = obFilter.filter_value(dTimestamp, 205.0)
        assert abs(flFiltered - 200.0) < 10.0, (
            f"After reset to 200.0, filtered value {flFiltered} is too far from reset value"
        )


class TestConfidenceScaleFactor:
    """Tests for the confidence-based scale factor function."""

    def test_confidence_scale_below_hold_returns_zero(self):
        """Below hold threshold should return exactly 0.0."""
        flResult = compute_confidence_scale_factor(0.1, 0.3, 0.7)
        assert flResult == 0.0, f"Expected 0.0 below hold threshold, got {flResult}"

    def test_confidence_scale_above_full_returns_one(self):
        """Above full threshold should return exactly 1.0."""
        flResult = compute_confidence_scale_factor(0.9, 0.3, 0.7)
        assert flResult == 1.0, f"Expected 1.0 above full threshold, got {flResult}"

    def test_confidence_scale_midpoint_smooth(self):
        """At midpoint (t=0.5), smoothstep gives exactly 0.5."""
        # Midpoint between 0.3 and 0.7 is 0.5
        flResult = compute_confidence_scale_factor(0.5, 0.3, 0.7)
        assert abs(flResult - 0.5) < 0.001, (
            f"Expected ~0.5 at midpoint, got {flResult}"
        )

    def test_confidence_scale_at_hold_threshold_returns_zero(self):
        """At exactly the hold threshold, should return 0.0."""
        flResult = compute_confidence_scale_factor(0.3, 0.3, 0.7)
        assert flResult == 0.0, f"Expected 0.0 at hold threshold, got {flResult}"

    def test_confidence_scale_at_full_threshold_returns_one(self):
        """At exactly the full threshold, should return 1.0."""
        flResult = compute_confidence_scale_factor(0.7, 0.3, 0.7)
        assert flResult == 1.0, f"Expected 1.0 at full threshold, got {flResult}"
