"""
test_clock.py - Tests for Clock abstraction module

Validates that:
- FakeClock is fully deterministic (starts at specified time, advances only when told)
- RealClock returns positive, monotonic values from time.perf_counter()

Part of Phase 2 SYNC-01 validation: all components use a single monotonic clock.
"""

import time
import pytest

from utilities.clock import Clock, RealClock, FakeClock


class TestFakeClock:
    """Tests for FakeClock deterministic time control."""

    def test_fake_clock_starts_at_specified_time(self):
        """FakeClock(5.0) should return 5.0 on first get_time_seconds() call."""
        obClock = FakeClock(dStartTimeSeconds=5.0)
        dTime = obClock.get_time_seconds()
        assert dTime == 5.0, f"Expected 5.0, got {dTime}"

    def test_fake_clock_starts_at_zero_by_default(self):
        """FakeClock() with no argument should start at 0.0."""
        obClock = FakeClock()
        dTime = obClock.get_time_seconds()
        assert dTime == 0.0, f"Expected 0.0, got {dTime}"

    def test_fake_clock_advances_by_delta(self):
        """Advancing by 0.5 from start=0 should give 0.5."""
        obClock = FakeClock(dStartTimeSeconds=0.0)
        obClock.advance_time_seconds(0.5)
        dTime = obClock.get_time_seconds()
        assert dTime == pytest.approx(0.5), f"Expected 0.5, got {dTime}"

    def test_fake_clock_set_absolute_time(self):
        """set_time_seconds(10.0) should make get_time_seconds() return 10.0."""
        obClock = FakeClock(dStartTimeSeconds=0.0)
        obClock.set_time_seconds(10.0)
        dTime = obClock.get_time_seconds()
        assert dTime == 10.0, f"Expected 10.0, got {dTime}"

    def test_fake_clock_multiple_advances_accumulate(self):
        """Advancing 0.1 ten times should yield 1.0."""
        obClock = FakeClock(dStartTimeSeconds=0.0)
        for _ in range(10):
            obClock.advance_time_seconds(0.1)
        dTime = obClock.get_time_seconds()
        assert dTime == pytest.approx(1.0), f"Expected 1.0, got {dTime}"

    def test_fake_clock_does_not_advance_without_call(self):
        """Two consecutive get_time_seconds() calls should return the same value."""
        obClock = FakeClock(dStartTimeSeconds=3.0)
        dTime1 = obClock.get_time_seconds()
        dTime2 = obClock.get_time_seconds()
        assert dTime1 == dTime2, (
            f"FakeClock advanced between calls: {dTime1} vs {dTime2}"
        )

    def test_fake_clock_is_instance_of_clock_abc(self):
        """FakeClock should be an instance of the Clock ABC."""
        obClock = FakeClock()
        assert isinstance(obClock, Clock), "FakeClock should be a Clock subclass"

    def test_fake_clock_advance_preserves_precision(self):
        """Many small advances should not lose floating point precision significantly."""
        obClock = FakeClock(dStartTimeSeconds=0.0)
        iSteps = 1000
        dStep = 0.001
        for _ in range(iSteps):
            obClock.advance_time_seconds(dStep)
        dTime = obClock.get_time_seconds()
        assert dTime == pytest.approx(1.0, abs=1e-9), (
            f"Expected 1.0 after {iSteps} x {dStep}, got {dTime}"
        )


class TestRealClock:
    """Tests for RealClock wrapping time.perf_counter()."""

    def test_real_clock_returns_positive_time(self):
        """RealClock.get_time_seconds() should return a positive value."""
        obClock = RealClock()
        dTime = obClock.get_time_seconds()
        assert dTime > 0.0, f"Expected positive time, got {dTime}"

    def test_real_clock_is_monotonic(self):
        """Two successive calls should give t2 >= t1."""
        obClock = RealClock()
        dTime1 = obClock.get_time_seconds()
        dTime2 = obClock.get_time_seconds()
        assert dTime2 >= dTime1, (
            f"RealClock is not monotonic: {dTime2} < {dTime1}"
        )

    def test_real_clock_advances_with_sleep(self):
        """After sleeping 10ms, time should advance by at least ~9ms."""
        obClock = RealClock()
        dTime1 = obClock.get_time_seconds()
        time.sleep(0.01)
        dTime2 = obClock.get_time_seconds()
        dElapsed = dTime2 - dTime1
        assert dElapsed >= 0.009, (
            f"Expected at least 9ms elapsed, got {dElapsed * 1000:.1f}ms"
        )

    def test_real_clock_is_instance_of_clock_abc(self):
        """RealClock should be an instance of the Clock ABC."""
        obClock = RealClock()
        assert isinstance(obClock, Clock), "RealClock should be a Clock subclass"
