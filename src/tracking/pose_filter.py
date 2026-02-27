"""
pose_filter.py - OneEuroFilter for pose jitter suppression and confidence scaling

Implements the 1-Euro Filter (Casiez et al., CHI 2012) for suppressing
high-frequency pose detection noise while maintaining responsiveness to
real movement. No external dependencies -- hand-rolled from the reference
algorithm.

Also provides a confidence-based scale factor using cubic smoothstep
interpolation between hold and full thresholds.

Follows Hungarian notation per CLAUDE.md.
"""

import math


def _compute_smoothing_factor(flTimeDelta: float, flCutoffHz: float) -> float:
    """
    Compute exponential smoothing alpha from time delta and cutoff frequency.

    The cutoff frequency determines how aggressively the filter smooths.
    Higher cutoff = less smoothing (more responsive).

    Args:
        flTimeDelta: Time since last sample in seconds
        flCutoffHz: Cutoff frequency in Hz

    Returns:
        Smoothing factor alpha in [0, 1]
    """
    flR = 2.0 * math.pi * flCutoffHz * flTimeDelta
    return flR / (flR + 1.0)


def _apply_exponential_smoothing(flAlpha: float, flRaw: float, flPrevious: float) -> float:
    """
    Apply standard exponential moving average (EMA).

    Args:
        flAlpha: Smoothing factor in [0, 1]
        flRaw: New raw value
        flPrevious: Previous smoothed value

    Returns:
        Smoothed value
    """
    return flAlpha * flRaw + (1.0 - flAlpha) * flPrevious


class OneEuroFilter:
    """
    1-Euro Filter for adaptive low-pass filtering of noisy signals.

    The filter adapts its cutoff frequency based on the signal's rate of change:
    - When the signal is stationary, the cutoff is low (aggressive smoothing)
    - When the signal moves quickly, the cutoff increases (responsive tracking)

    This provides the best of both worlds: no jitter on a stationary subject,
    and minimal lag when the subject moves.

    Default parameters tuned for person tracking at 30 FPS:
    - flMinCutoffHz = 0.01: Very aggressive smoothing when stationary (suppresses
      5px-std jitter to < 1px range)
    - flBeta = 0.007: Responsive at walking speed (< 10% lag on linear ramp)
    - flDerivativeCutoffHz = 0.1: Low derivative cutoff prevents noise-amplified
      derivative from inflating the adaptive cutoff on stationary input
    """

    def __init__(
        self,
        dInitialTimestamp: float,
        flInitialValue: float,
        flMinCutoffHz: float = 0.01,
        flBeta: float = 0.007,
        flDerivativeCutoffHz: float = 0.1,
    ):
        """
        Initialize the 1-Euro Filter.

        Args:
            dInitialTimestamp: Timestamp of the first sample (seconds)
            flInitialValue: Initial signal value
            flMinCutoffHz: Minimum cutoff frequency (Hz) -- controls jitter smoothing
            flBeta: Speed coefficient -- controls lag reduction on fast movement
            flDerivativeCutoffHz: Cutoff frequency for derivative estimation (Hz)
        """
        self._flMinCutoffHz = flMinCutoffHz
        self._flBeta = flBeta
        self._flDerivativeCutoffHz = flDerivativeCutoffHz

        self._flPreviousRaw = flInitialValue
        self._flPreviousFiltered = flInitialValue
        self._flPreviousDerivative = 0.0
        self._dPreviousTimestamp = dInitialTimestamp

    def filter_value(self, dTimestamp: float, flRawValue: float) -> float:
        """
        Filter one raw value and return the smoothed result.

        If dt <= 0 (same or earlier timestamp), returns previous filtered value
        unchanged to avoid division issues.

        Args:
            dTimestamp: Current sample timestamp (seconds)
            flRawValue: Raw (noisy) signal value

        Returns:
            Smoothed signal value
        """
        flDt = dTimestamp - self._dPreviousTimestamp

        if flDt <= 0.0:
            return self._flPreviousFiltered

        # 1. Estimate derivative (rate of change)
        flRawDerivative = (flRawValue - self._flPreviousRaw) / flDt

        # 2. Smooth the derivative
        flDerivativeAlpha = _compute_smoothing_factor(flDt, self._flDerivativeCutoffHz)
        flSmoothedDerivative = _apply_exponential_smoothing(
            flDerivativeAlpha, flRawDerivative, self._flPreviousDerivative
        )

        # 3. Compute adaptive cutoff: minCutoff + beta * |smoothedDerivative|
        flAdaptiveCutoff = self._flMinCutoffHz + self._flBeta * abs(flSmoothedDerivative)

        # 4. Smooth the raw value with the adaptive cutoff
        flSignalAlpha = _compute_smoothing_factor(flDt, flAdaptiveCutoff)
        flFilteredValue = _apply_exponential_smoothing(
            flSignalAlpha, flRawValue, self._flPreviousFiltered
        )

        # 5. Update state
        self._flPreviousRaw = flRawValue
        self._flPreviousFiltered = flFilteredValue
        self._flPreviousDerivative = flSmoothedDerivative
        self._dPreviousTimestamp = dTimestamp

        return flFilteredValue

    def reset(self, dTimestamp: float, flValue: float):
        """
        Reinitialize all filter state (for when tracking restarts fresh).

        Args:
            dTimestamp: New initial timestamp (seconds)
            flValue: New initial signal value
        """
        self._flPreviousRaw = flValue
        self._flPreviousFiltered = flValue
        self._flPreviousDerivative = 0.0
        self._dPreviousTimestamp = dTimestamp


def compute_confidence_scale_factor(
    flConfidence: float,
    flHoldThreshold: float = 0.3,
    flFullThreshold: float = 0.7,
) -> float:
    """
    Compute a scale factor [0.0, 1.0] based on detection confidence.

    Returns 0.0 below hold threshold (camera holds position),
    1.0 above full threshold (full tracking correction),
    and a smooth cubic interpolation (smoothstep) between.

    The smoothstep function t*t*(3-2*t) provides C1 continuity at both
    boundaries, avoiding any visible kinks in the tracking response.

    Args:
        flConfidence: Detection confidence score in [0, 1]
        flHoldThreshold: Below this, scale factor is 0.0
        flFullThreshold: Above this, scale factor is 1.0

    Returns:
        Scale factor in [0.0, 1.0]
    """
    if flConfidence <= flHoldThreshold:
        return 0.0
    if flConfidence >= flFullThreshold:
        return 1.0

    # Normalize to [0, 1] between thresholds
    flT = (flConfidence - flHoldThreshold) / (flFullThreshold - flHoldThreshold)

    # Cubic smoothstep: t*t*(3 - 2*t)
    return flT * flT * (3.0 - 2.0 * flT)
