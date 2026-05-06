"""Rule-of-thirds framer with stage-1 critically-damped smoothing.

INTENT-03: maps MotionIntent -> target_x_normalized in {1/3, 0.5, 2/3} via an
exhaustive ``match``-over-Literal (Pattern 6 -- ``case _:`` raises IntentError
per WR-08 fix discipline).

INTENT-04: smooths the discrete third transitions through
``CriticallyDampedFollower`` (Holden exact form) with ``time_constant_sec =
config.framing_time_constant_sec``.

D-05: damper steps in NORMALIZED-X DOMAIN. Degree conversion lives in PanController.
D-06: first non-indeterminate motion seeds FollowerState(position=current_target,
       velocity=0.0); no warmup transient toward zero.
D-07: motion=None or intent="indeterminate" returns None; damper state cleared
       so the next non-indeterminate motion re-seeds at the new target (Pitfall 6
       recommended implementation).

Pattern 9 logger events:
- framing_target_change (INFO): emitted once per discrete intent-driven target
  shift (e.g. dwelling -> moving_right flips center -> left third). Bounded by
  Plan 02 hysteresis (motion_hysteresis_sec ~ 0.3 s) so log frequency is capped
  at ~3 Hz worst case.
- framer_seeded (DEBUG): emitted when state transitions from None -> seeded.
"""
from __future__ import annotations

from typing import Final

import structlog

from pastor_tracker.config import Config
from pastor_tracker.core.damping import CriticallyDampedFollower, FollowerState
from pastor_tracker.core.types import FramingTarget, MotionIntent, MotionState

_NS_PER_SEC: Final[float] = 1_000_000_000.0
_TARGET_LEFT_THIRD: Final[float] = 1.0 / 3.0
_TARGET_RIGHT_THIRD: Final[float] = 2.0 / 3.0
_TARGET_CENTER: Final[float] = 0.5
_NORM_MIN: Final[float] = 0.0
_NORM_MAX: Final[float] = 1.0
_CAPTURE_FPS_FLOOR: Final[int] = 1


class IntentError(Exception):
    """Raised when an unhandled MotionIntent reaches the dispatch (WR-08 guard)."""


class Framer:
    """Phase 5 stage-1: intent -> rule-of-thirds target + critically-damped smoothing."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._logger = structlog.get_logger(module="framer")
        self._damper = CriticallyDampedFollower(
            time_constant_sec=config.framing_time_constant_sec,
        )
        self._state: FollowerState | None = None
        self._last_upstream_ts_ns: int | None = None
        self._current_target_x_normalized: float | None = None
        # Discrete intent-driven target (one of {1/3, 0.5, 2/3}), used to gate
        # the framing_target_change INFO log. Distinct from the continuous
        # _current_target_x_normalized which holds the damper's clamped position.
        self._last_discrete_target_x_normalized: float | None = None

    # ---------- read-only dashboard surface ----------

    @property
    def current_target_x_normalized(self) -> float | None:
        return self._current_target_x_normalized

    # ---------- per-frame consume ----------

    async def consume(
        self,
        motion: MotionState | None,
        now_ns: int,
    ) -> FramingTarget | None:
        if motion is None or motion.intent == "indeterminate":
            self._reset()
            return None
        target = self._intent_to_target(motion.intent)
        # _intent_to_target only returns None for "indeterminate" -- filtered above.
        assert target is not None, "unreachable: indeterminate filtered"
        if self._state is None:
            return self._seed_and_emit(target, motion.timestamp_ns, now_ns)
        return self._step_and_emit(target, motion.timestamp_ns, now_ns)

    # ---------- internals ----------

    def _seed_and_emit(
        self, target: float, upstream_ts_ns: int, now_ns: int,
    ) -> FramingTarget:
        self._state = FollowerState(position=target, velocity=0.0)
        self._last_upstream_ts_ns = upstream_ts_ns
        self._current_target_x_normalized = target
        self._last_discrete_target_x_normalized = target
        self._logger.debug("framer_seeded", position=target)
        return FramingTarget(
            target_x_normalized=target,
            timestamp_ns=now_ns,
        )

    def _step_and_emit(
        self, target: float, upstream_ts_ns: int, now_ns: int,
    ) -> FramingTarget:
        assert self._state is not None
        dt_sec = self._compute_dt_sec(upstream_ts_ns)
        self._state = self._damper.step(self._state, target=target, dt=dt_sec)
        self._last_upstream_ts_ns = upstream_ts_ns
        clamped = min(_NORM_MAX, max(_NORM_MIN, self._state.position))
        # Pattern 9: framing_target_change INFO once per discrete intent-driven
        # shift (left third / center / right third). Gated on the discrete
        # target rather than the continuous clamped position -- analyzer
        # hysteresis bounds intent flips at <= 1 / motion_hysteresis_sec.
        if target != self._last_discrete_target_x_normalized:
            self._logger.info(
                "framing_target_change",
                old=self._last_discrete_target_x_normalized,
                new=target,
            )
            self._last_discrete_target_x_normalized = target
        self._current_target_x_normalized = clamped
        return FramingTarget(
            target_x_normalized=clamped,
            timestamp_ns=now_ns,
        )

    def _reset(self) -> None:
        self._state = None
        self._last_upstream_ts_ns = None
        self._current_target_x_normalized = None
        self._last_discrete_target_x_normalized = None

    @staticmethod
    def _intent_to_target(intent: MotionIntent) -> float | None:
        match intent:
            case "moving_right":
                return _TARGET_LEFT_THIRD
            case "moving_left":
                return _TARGET_RIGHT_THIRD
            case "dwelling":
                return _TARGET_CENTER
            case "indeterminate":
                return None
            case _:
                raise IntentError(
                    f"unhandled MotionIntent in _intent_to_target: {intent!r}"
                )

    def _compute_dt_sec(self, current_upstream_ts_ns: int) -> float:
        if self._last_upstream_ts_ns is None:
            return 1.0 / max(self._config.capture_fps, _CAPTURE_FPS_FLOOR)
        delta_ns = current_upstream_ts_ns - self._last_upstream_ts_ns
        if delta_ns <= 0:
            return 1.0 / max(self._config.capture_fps, _CAPTURE_FPS_FLOOR)
        return float(delta_ns) / _NS_PER_SEC
