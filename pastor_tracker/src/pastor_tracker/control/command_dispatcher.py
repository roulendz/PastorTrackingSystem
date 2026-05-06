"""Synchronous Delta-and-interval emission gate (Phase 5 CTRL-04, D-11).

Pure transform: ``decide(angle_deg | None, now_ns) -> MotorCommand | None``.
Synchronous because the dispatcher does no I/O. Phase 6 orchestrator calls
``decide()`` after ``await controller.consume(...)`` and forwards the
returned MotorCommand to ``motor.send_motor_angle(...)``.

Gate semantics (D-11):
- First non-None call always emits (seeds gate state).
- Subsequent: emit iff |angle - last_emitted| > command_min_delta_deg
  AND (now_ns - last_emit_ts_ns) >= command_min_interval_ms * 1e6 ns.
- decide(None, now_ns) returns None and does NOT update state (Pitfall 7) --
  preserves Delta/interval semantics across upstream gaps.

The gate is split into two ``if`` blocks (one per gate) instead of a combined
``and`` so the suppression reason can be logged distinguishably (Pattern 9 --
``command_suppressed_delta`` vs ``command_suppressed_interval``) and so each
branch is independently coverable for D-13's 100% gate-coverage requirement.

Pattern 9 logger events (all DEBUG -- emission rate <= 20 Hz floods INFO):
- command_emitted: angle_deg, delta_deg, interval_ms
- command_suppressed_delta: angle_deg, delta_deg
- command_suppressed_interval: angle_deg, interval_ms
"""
from __future__ import annotations

from typing import Final

import structlog

from pastor_tracker.config import Config
from pastor_tracker.core.types import MotorCommand

_NS_PER_MS_INT: Final[int] = 1_000_000


class CommandDispatcher:
    """Phase 5 control stage: synchronous emission gate (CTRL-04, D-11)."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._logger = structlog.get_logger(module="command_dispatcher")
        self._last_emitted_angle_deg: float | None = None
        self._last_emit_ts_ns: int | None = None

    # ---------- read-only dashboard surface ----------

    @property
    def last_emitted_angle_deg(self) -> float | None:
        return self._last_emitted_angle_deg

    @property
    def last_emit_ts_ns(self) -> int | None:
        return self._last_emit_ts_ns

    # ---------- per-frame decide (SYNC) ----------

    def decide(
        self,
        angle_deg: float | None,
        now_ns: int,
    ) -> MotorCommand | None:
        """Decide whether to emit a MotorCommand for this frame.

        SYNC method (no awaitable) -- the dispatcher does no I/O. Phase 6
        orchestrator calls this directly after ``await controller.consume(...)``.
        """
        if angle_deg is None:
            # Pitfall 7: do NOT update state; preserve Delta/interval semantics.
            return None
        if self._last_emitted_angle_deg is None or self._last_emit_ts_ns is None:
            return self._emit(angle_deg, now_ns, delta_deg=0.0, interval_ms=0.0)
        delta_deg = abs(angle_deg - self._last_emitted_angle_deg)
        interval_ns = now_ns - self._last_emit_ts_ns
        min_interval_ns = self._config.command_min_interval_ms * _NS_PER_MS_INT
        if delta_deg <= self._config.command_min_delta_deg:
            self._logger.debug(
                "command_suppressed_delta",
                angle_deg=angle_deg,
                delta_deg=delta_deg,
            )
            return None
        if interval_ns < min_interval_ns:
            self._logger.debug(
                "command_suppressed_interval",
                angle_deg=angle_deg,
                interval_ms=interval_ns / _NS_PER_MS_INT,
            )
            return None
        return self._emit(
            angle_deg,
            now_ns,
            delta_deg=delta_deg,
            interval_ms=interval_ns / _NS_PER_MS_INT,
        )

    # ---------- internals ----------

    def _emit(
        self,
        angle_deg: float,
        now_ns: int,
        *,
        delta_deg: float,
        interval_ms: float,
    ) -> MotorCommand:
        self._last_emitted_angle_deg = angle_deg
        self._last_emit_ts_ns = now_ns
        self._logger.debug(
            "command_emitted",
            angle_deg=angle_deg,
            delta_deg=delta_deg,
            interval_ms=interval_ms,
        )
        return MotorCommand(
            target_angle_deg=angle_deg,
            timestamp_ns=now_ns,
        )
