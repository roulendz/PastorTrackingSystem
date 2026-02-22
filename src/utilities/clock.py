"""
clock.py - Monotonic clock abstraction for deterministic timing

Provides a Clock ABC with two implementations:
- RealClock: wraps time.perf_counter() for production use
- FakeClock: manually controlled clock for deterministic testing

All components that need time should accept an injected Clock instance
rather than calling time.time() or time.perf_counter() directly.
"""

import time
from abc import ABC, abstractmethod


class Clock(ABC):
    """Abstract base class for monotonic time sources."""

    @abstractmethod
    def get_time_seconds(self) -> float:
        """
        Return the current time in seconds (monotonic).

        Returns:
            Current time as a float in seconds
        """
        pass


class RealClock(Clock):
    """Production clock wrapping time.perf_counter()."""

    def get_time_seconds(self) -> float:
        """Return current monotonic time via time.perf_counter()."""
        return time.perf_counter()


class FakeClock(Clock):
    """
    Manually controlled clock for deterministic testing.

    Time only advances when explicitly told to via advance_time_seconds()
    or set_time_seconds(). This allows tests to produce identical results
    regardless of real wall-clock timing.
    """

    def __init__(self, dStartTimeSeconds: float = 0.0):
        """
        Initialize FakeClock at a given start time.

        Args:
            dStartTimeSeconds: Initial time value in seconds
        """
        self._dCurrentTimeSeconds = float(dStartTimeSeconds)

    def get_time_seconds(self) -> float:
        """Return the current manually-controlled time."""
        return self._dCurrentTimeSeconds

    def advance_time_seconds(self, dDeltaSeconds: float):
        """
        Advance the clock by a given number of seconds.

        Args:
            dDeltaSeconds: Number of seconds to advance (must be >= 0)
        """
        self._dCurrentTimeSeconds += float(dDeltaSeconds)

    def set_time_seconds(self, dTimeSeconds: float):
        """
        Set the clock to an absolute time value.

        Args:
            dTimeSeconds: Absolute time in seconds
        """
        self._dCurrentTimeSeconds = float(dTimeSeconds)
