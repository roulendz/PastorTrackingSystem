"""Phase 5 stage-2: FOV conversion + degree-domain damping + clamp + deadband.

CTRL-01: stage-2 critically-damped follower with time_constant_sec =
config.pan_time_constant_sec.
CTRL-02: emission deadband -- suppress motor commands smaller than
config.pan_deadband_deg. Damper continues stepping under the hood.
CTRL-03: velocity clamp at config.pan_max_velocity_deg_per_sec --
anti-windup by overwriting FollowerState.position (D-09 -- NOT PID; no integral).

Order of operations (D-08): normalize->degree -> damp -> velocity-clamp -> deadband.

D-05: damper steps in DEGREE DOMAIN (Framer steps in normalized-x).
D-06: first non-None target seeds at angle, velocity 0.0.
D-07: target=None holds damper FollowerState; emit _last_emitted_angle_deg.
D-09: clamp OVERWRITES FollowerState.position (not just emission). Anti-windup
       without an integrator (Pitfall 2: NOT PID).
D-10: deadband is on _last_emitted_angle_deg, not on _state.position
       (Pitfall 3: damper keeps stepping; only emission gated).
"""
from __future__ import annotations

from dataclasses import replace
from typing import Final

import structlog

from pastor_tracker.config import Config
from pastor_tracker.core.damping import CriticallyDampedFollower, FollowerState
from pastor_tracker.core.geometry import normalized_x_to_angle_deg
from pastor_tracker.core.types import FramingTarget

_NS_PER_SEC: Final[float] = 1_000_000_000.0
_CAPTURE_FPS_FLOOR: Final[int] = 1


class ControlError(Exception):
    """Typed root for control-stage errors (PanController, CommandDispatcher)."""


class PanController:
    """Phase 5 stage-2: FramingTarget -> damped angle (degrees). Pure transform.

    Per-frame ``consume(target, now_ns)`` accepts the framer's normalized-x
    intent, converts to degrees via the FOV bridge, drives a critically-damped
    second-order follower in the degree domain, applies an anti-windup
    velocity clamp (FollowerState.position overwrite -- D-09), and gates the
    emission via a deadband on the last-emitted angle (D-10). The damper
    continues stepping every frame regardless of deadband suppression
    (Pitfall 3) so that small target nudges accumulate cleanly until the
    deadband threshold is exceeded.
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        self._logger = structlog.get_logger(module="pan_controller")
        self._damper = CriticallyDampedFollower(
            time_constant_sec=config.pan_time_constant_sec,
        )
        self._state: FollowerState | None = None
        self._last_upstream_ts_ns: int | None = None
        self._last_emitted_angle_deg: float | None = None
        self._current_angle_deg: float | None = None

    # ---------- read-only dashboard surface ----------

    @property
    def current_angle_deg(self) -> float | None:
        """Most recently emitted angle (degrees) or None pre-seed.

        Surface for the Phase 7 dashboard (must_haves: read-only property).
        Reflects the EMITTED value, which equals ``_last_emitted_angle_deg``
        even during deadband-suppressed ticks (D-10).
        """
        return self._current_angle_deg

    # ---------- per-frame consume ----------

    async def consume(
        self,
        target: FramingTarget | None,
        now_ns: int,
    ) -> float | None:
        """Per-frame transform: FramingTarget -> emitted angle (deg) | None.

        Guard ``target is None`` first (D-07 hold). Otherwise convert nx->deg
        (D-08), seed on first call (D-06), or step the damper -> clamp -> deadband.
        ``now_ns`` is the orchestrator wall-clock (currently unused -- dt is
        derived from the upstream FramingTarget timestamp_ns to stay
        consistent with the Plan-03 Framer discipline).
        """
        del now_ns  # dt derived from upstream timestamp; param kept for D-01 symmetry
        if target is None:
            return self._hold()
        angle_deg = normalized_x_to_angle_deg(
            target.target_x_normalized,
            self._config.camera_horizontal_fov_deg,
        )
        if self._state is None:
            return self._seed_and_emit(angle_deg, target.timestamp_ns)
        return self._step_clamp_deadband_emit(angle_deg, target.timestamp_ns)

    # ---------- internals ----------

    def _seed_and_emit(self, angle_deg: float, upstream_ts_ns: int) -> float:
        """First non-None target: seed FollowerState at angle, velocity 0.0 (D-06)."""
        self._state = FollowerState(position=angle_deg, velocity=0.0)
        self._last_upstream_ts_ns = upstream_ts_ns
        self._last_emitted_angle_deg = angle_deg
        self._current_angle_deg = angle_deg
        self._logger.debug("pan_controller_seeded", angle_deg=angle_deg)
        return angle_deg

    def _step_clamp_deadband_emit(
        self, angle_deg: float, upstream_ts_ns: int,
    ) -> float:
        """Step damper, clamp velocity (D-09 overwrite), gate emission via deadband (D-10)."""
        assert self._state is not None
        assert self._last_emitted_angle_deg is not None
        dt_sec = self._compute_dt_sec(upstream_ts_ns)
        prev_position = self._state.position
        new_state = self._damper.step(self._state, target=angle_deg, dt=dt_sec)
        # --- velocity clamp (CTRL-03 / D-09) ---
        delta = new_state.position - prev_position
        max_delta = self._config.pan_max_velocity_deg_per_sec * dt_sec
        if abs(delta) > max_delta:
            clipped = max(-max_delta, min(max_delta, delta))
            new_state = replace(new_state, position=prev_position + clipped)
            self._logger.debug(
                "pan_clamped",
                unclamped_delta_deg=delta,
                clamped_delta_deg=clipped,
                dt_sec=dt_sec,
            )
        self._state = new_state
        self._last_upstream_ts_ns = upstream_ts_ns
        new_angle = new_state.position
        # --- emission deadband (CTRL-02 / D-10) ---
        if abs(new_angle - self._last_emitted_angle_deg) < self._config.pan_deadband_deg:
            self._current_angle_deg = self._last_emitted_angle_deg
            self._logger.debug(
                "pan_deadband_suppressed",
                delta_deg=new_angle - self._last_emitted_angle_deg,
                last_emitted_angle_deg=self._last_emitted_angle_deg,
            )
            return self._last_emitted_angle_deg
        self._last_emitted_angle_deg = new_angle
        self._current_angle_deg = new_angle
        return new_angle

    def _hold(self) -> float | None:
        """Hold-on-None (D-07 + BL-01 fix).

        Preserves damper FollowerState (no re-seed transient on resume) BUT
        clears ``_last_upstream_ts_ns`` so that the next real frame computes
        ``dt`` from the ``1 / capture_fps`` floor, not from the wall-clock-
        sized gap accumulated across the None run. Without this clear, a
        long None gap produces a single-step "snap" to target on resume:
        (a) the Holden damper decay collapses (``exp(-k * dt)`` -> 0 for
        large ``dt``), so ``new_position ~= target`` in one step, and
        (b) the velocity clamp is parameterised by ``vmax * dt_sec``, so
        with ``dt_sec`` order-seconds the clamp ceiling exceeds the entire
        FOV and never engages. The two failures compound into a one-frame
        full-FOV jump that violates the project core value (no audible
        motor jerk).

        Contrast: ``_state`` is INTENTIONALLY retained so the resume tracks
        from where the damper left off. Only the upstream-timestamp witness
        is cleared, because it is wall-clock evidence, not damper state.
        """
        self._last_upstream_ts_ns = None
        return self._last_emitted_angle_deg

    def _compute_dt_sec(self, current_upstream_ts_ns: int) -> float:
        """Upstream-timestamp dt with a 1/capture_fps floor.

        Matches the SubjectTracker / Framer idiom: pre-seed and non-monotonic
        upstream timestamps both fall through to the floor, never raising
        ``ValueError`` from ``damper.step``.
        """
        if self._last_upstream_ts_ns is None:
            return 1.0 / max(self._config.capture_fps, _CAPTURE_FPS_FLOOR)
        delta_ns = current_upstream_ts_ns - self._last_upstream_ts_ns
        if delta_ns <= 0:
            return 1.0 / max(self._config.capture_fps, _CAPTURE_FPS_FLOOR)
        return float(delta_ns) / _NS_PER_SEC
