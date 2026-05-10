"""Asyncio pipeline orchestrator wiring 8 stages with a 6-state lifecycle.

Phase 6 entry point. Composes ObsCamera (Phase 3), PoseDetector (Phase 4),
SubjectTracker (Phase 4), MotionAnalyzer/Framer (Phase 5),
PanController/CommandDispatcher (Phase 5), and ArduinoMotor (Phase 2)
into a single ``asyncio.Task`` per CONTEXT.md D-01..D-04.

Public surface (D-15..D-18):
    * ``Pipeline`` class -- async lifecycle methods (start/pause/resume/home/
      e_stop/quit), ``snapshot()``, ``latest_frame`` slot.
    * ``OrchestratorRejected`` -- single typed exception raised by lifecycle
      methods on invalid state-transition or out-of-limits home.
    * ``PipelineSnapshot``, ``PipelineState`` -- re-exported from ``core.types``
      so callers have a single import surface.

Plan 06-01 shipped the module skeleton + ``OrchestratorRejected``. Plan
06-02 (this revision) lands the full ``Pipeline`` class. The ``__main__``
boot refactor lands in Plan 06-03; integration tests in Plan 06-04.
"""
from __future__ import annotations

import asyncio
import contextlib
import enum
from dataclasses import dataclass
from typing import Final, Literal

import structlog

from pastor_tracker.config import Config
from pastor_tracker.control.command_dispatcher import CommandDispatcher
from pastor_tracker.control.pan_controller import PanController
from pastor_tracker.core.types import (
    Frame,
    MotionIntent,
    PipelineSnapshot,
    PipelineState,
)
from pastor_tracker.intent.framer import Framer
from pastor_tracker.intent.motion_analyzer import MotionAnalyzer
from pastor_tracker.io.arduino_motor import ArduinoMotor
from pastor_tracker.io.obs_camera import ObsCamera
from pastor_tracker.perception.pose_detector import PoseDetector
from pastor_tracker.perception.subject_tracker import SubjectTracker

__all__ = ["OrchestratorRejected", "Pipeline", "PipelineSnapshot", "PipelineState"]


class OrchestratorRejected(Exception):
    """Invalid lifecycle transition or out-of-limits ``home()`` call (D-15, D-08).

    Single typed surface raised by ``Pipeline`` lifecycle methods. Phase 7
    dashboard catches this and surfaces a status-bar message; tests assert
    on ``pytest.raises(OrchestratorRejected)``.

    Examples that raise:
        * ``await pipeline.pause()`` while state is STOPPED (no tick task to pause)
        * ``await pipeline.home()`` when 0.0 is outside [pan_min_deg, pan_max_deg]
        * ``await pipeline.resume()`` while state is STOPPED (no paused tick)
    """


# ---------------------------------------------------------------------------
# Module-level constants (CLAUDE.md rule 6 -- no magic numbers in code).
# ---------------------------------------------------------------------------

# D-08 limit-check reference angle. home() is fire-and-forget per
# RESEARCH Pitfall 7 option 1: state moves HOMING -> PAUSED when the H
# command is SENT, not when the motor reaches 0 deg.
_HOME_TARGET_DEG: Final[float] = 0.0

# CONTEXT.md "Claude's Discretion": Phase 5 dampers handle smoothing;
# firmware AccelStepper PID is left at zeros (firmware v2 default
# disables PID correction). Wiring-only constants, not tunables.
_FIRMWARE_PID_DEFAULT_P: Final[float] = 0.0
_FIRMWARE_PID_DEFAULT_I: Final[float] = 0.0
_FIRMWARE_PID_DEFAULT_D: Final[float] = 0.0


# ---------------------------------------------------------------------------
# Lifecycle state machine (D-05..D-09, D-15).
# ---------------------------------------------------------------------------


class _PipelineState(enum.Enum):
    """6-state lifecycle per CONTEXT.md D-05..D-09. Mirrors PipelineState Literal."""

    STOPPED = "stopped"        # initial / terminal-on-error
    RUNNING = "running"        # tick loop active, dispatch enabled
    PAUSED = "paused"          # tick loop active, dispatch disabled
    HOMING = "homing"          # transient -- H sent, dispatch disabled
    E_STOPPED = "e_stopped"    # E sent; explicit start() to recover
    QUITTING = "quitting"      # terminal -- resources released


_Cmd = Literal["start", "pause", "resume", "home", "e_stop", "quit"]

# 18 valid transitions per RESEARCH §Pattern 2. Missing keys raise
# OrchestratorRejected. Idempotent self-transitions are listed explicitly
# so D-15's "idempotent on target state" contract is enforceable by the
# table alone.
_LIFECYCLE_TABLE: Final[dict[tuple[_PipelineState, _Cmd], _PipelineState]] = {
    # from STOPPED (initial)
    (_PipelineState.STOPPED,   "start"):  _PipelineState.RUNNING,
    (_PipelineState.STOPPED,   "quit"):   _PipelineState.QUITTING,
    # from RUNNING
    (_PipelineState.RUNNING,   "start"):  _PipelineState.RUNNING,    # idempotent
    (_PipelineState.RUNNING,   "pause"):  _PipelineState.PAUSED,
    (_PipelineState.RUNNING,   "home"):   _PipelineState.HOMING,
    (_PipelineState.RUNNING,   "e_stop"): _PipelineState.E_STOPPED,
    (_PipelineState.RUNNING,   "quit"):   _PipelineState.QUITTING,
    # from PAUSED
    (_PipelineState.PAUSED,    "pause"):  _PipelineState.PAUSED,     # idempotent
    (_PipelineState.PAUSED,    "resume"): _PipelineState.RUNNING,
    (_PipelineState.PAUSED,    "home"):   _PipelineState.HOMING,
    (_PipelineState.PAUSED,    "e_stop"): _PipelineState.E_STOPPED,
    (_PipelineState.PAUSED,    "quit"):   _PipelineState.QUITTING,
    # from HOMING (transient)
    (_PipelineState.HOMING,    "e_stop"): _PipelineState.E_STOPPED,
    (_PipelineState.HOMING,    "quit"):   _PipelineState.QUITTING,
    # from E_STOPPED
    (_PipelineState.E_STOPPED, "start"):  _PipelineState.RUNNING,    # explicit re-arm
    (_PipelineState.E_STOPPED, "e_stop"): _PipelineState.E_STOPPED,  # idempotent
    (_PipelineState.E_STOPPED, "quit"):   _PipelineState.QUITTING,
    # from QUITTING (terminal)
    (_PipelineState.QUITTING,  "quit"):   _PipelineState.QUITTING,   # idempotent
}
# Cell count: 18 transitions across 6 states. The HOMING -> PAUSED
# back-edge is performed inline at the end of home() (fire-and-forget per
# RESEARCH Pitfall 7 option 1) -- not table-driven, since it is not a
# public command.


# ---------------------------------------------------------------------------
# Snapshot cache (D-16, D-17).
# ---------------------------------------------------------------------------


@dataclass
class _PipelineCache:
    """Mutable single-writer cache of the snapshot scalars (D-16).

    Tick task writes; ``snapshot()`` reads. Excludes ``Frame`` (the
    dedicated ``latest_frame`` slot per D-17 owns that). ``motor_state``
    is read directly from ``self._motor.state.value`` at snapshot-build
    time -- not cached here -- so it is always coherent with the motor.
    """

    last_frame_ts_ns: int | None = None
    last_intent: MotionIntent = "indeterminate"
    last_target_x_normalized: float | None = None
    last_pan_angle_deg: float | None = None
    last_emitted_angle_deg: float | None = None


# ---------------------------------------------------------------------------
# Pipeline class.
# ---------------------------------------------------------------------------


class Pipeline:
    """Asyncio orchestrator wiring 8 stages + 6-state lifecycle (PIPE-01..03).

    Composition (CONTEXT.md D-02): single sequential await chain inside one
    asyncio.Task. No per-stage queues. All Phase-5 stages are sub-ms pure
    transforms; sequential composition keeps reasoning trivial.

    Public surface (CONTEXT.md D-15..D-18):
        * Async lifecycle: start, pause, resume, home, e_stop, quit
        * Read-only: snapshot() -> PipelineSnapshot, latest_frame: Frame | None

    Failure handling (D-10..D-14): tiger-style fail-fast. Camera, Arduino
    link-lost, and perception exceptions propagate to ``__main__``. Watchdog
    reset is recovered in-place by ``ArduinoMotor._recover()`` (D-11).
    """

    def __init__(
        self,
        config: Config,
        *,
        camera: ObsCamera,
        motor: ArduinoMotor,
        detector: PoseDetector,
        tracker: SubjectTracker,
        analyzer: MotionAnalyzer,
        framer: Framer,
        controller: PanController,
        dispatcher: CommandDispatcher,
    ) -> None:
        self._config = config
        self._camera = camera
        self._motor = motor
        self._detector = detector
        self._tracker = tracker
        self._analyzer = analyzer
        self._framer = framer
        self._controller = controller
        self._dispatcher = dispatcher
        self._logger = structlog.get_logger(module="pipeline")
        self._state: _PipelineState = _PipelineState.STOPPED
        self._dispatch_enabled: bool = False
        self._cache: _PipelineCache = _PipelineCache()
        self._latest_frame: Frame | None = None
        self._tick_task: asyncio.Task[None] | None = None

    # ---------- read-only status surface (D-16, D-17) ----------

    @property
    def state(self) -> PipelineState:
        """Public Literal alias of the internal ``_PipelineState`` (D-15 surface).

        The enum ``.value`` strings map 1:1 to ``PipelineState`` Literal
        members; mypy infers the narrowing automatically because every
        enum member's value is a Literal-compatible string.
        """
        return self._state.value

    @property
    def latest_frame(self) -> Frame | None:
        """D-17: single-attribute slot. CPython STORE_ATTR is atomic under GIL.

        Read by Phase 7 dashboard at its own refresh rate. No lock --
        single writer (tick task) + N readers (UI poll).
        """
        return self._latest_frame

    def snapshot(self) -> PipelineSnapshot:
        """D-16: build a frozen snapshot from the mutable cache. Cheap, no I/O."""
        return PipelineSnapshot(
            state=self._state.value,
            last_frame_ts_ns=self._cache.last_frame_ts_ns,
            last_intent=self._cache.last_intent,
            last_target_x_normalized=self._cache.last_target_x_normalized,
            last_pan_angle_deg=self._cache.last_pan_angle_deg,
            last_emitted_angle_deg=self._cache.last_emitted_angle_deg,
            motor_state=self._motor.state.value,
        )

    # ---------- lifecycle helpers ----------

    def _transition_or_raise(self, cmd: _Cmd) -> _PipelineState:
        """Lookup ``(state, cmd)`` in ``_LIFECYCLE_TABLE``. Missing key raises."""
        key = (self._state, cmd)
        if key not in _LIFECYCLE_TABLE:
            raise OrchestratorRejected(
                f"cannot {cmd!r} from {self._state.value}"
            )
        return _LIFECYCLE_TABLE[key]

    def _log_transition(
        self,
        old: _PipelineState,
        new: _PipelineState,
        reason: str,
    ) -> None:
        """Emit structured ``pipeline_state_change`` event. Phase 7 dashboard subscribes."""
        if old is new:
            return  # idempotent self-transition; no log noise
        self._logger.info(
            "pipeline_state_change",
            old=old.value,
            new=new.value,
            reason=reason,
        )

    # ---------- tick loop (D-01..D-04, D-13/D-14) ----------

    async def _tick_loop(self) -> None:
        """Single-task sequential await chain (D-02). All 8 stages compose here.

        D-01 frame-driven; D-02 sequential; D-03 detector backpressure via
        ``PoseDetector.stream``; D-04 ``now_ns`` from ``frame.timestamp_ns``.
        Any non-cancellation exception escapes to ``_on_tick_task_done``
        (D-13/D-14). No ``try/except`` inside the body per RESEARCH
        §Anti-patterns -- per-frame swallow breaks tiger-style.
        """
        async for frame, detections in self._detector.stream(self._camera.frames()):
            # D-04: clock comes from the camera, never from the orchestrator.
            now_ns = frame.timestamp_ns
            # D-17: single-attribute write; CPython STORE_ATTR is atomic.
            self._latest_frame = frame
            # D-02: sequential pure-transform chain.
            subject = await self._tracker.consume(detections, now_ns)
            motion = await self._analyzer.consume(subject, now_ns)
            target = await self._framer.consume(motion, now_ns)
            angle = await self._controller.consume(target, now_ns)
            command = self._dispatcher.decide(angle, now_ns)  # SYNC
            # Snapshot cache update -- single writer, no lock (D-16).
            self._cache.last_frame_ts_ns = now_ns
            self._cache.last_intent = (
                motion.intent if motion is not None else "indeterminate"
            )
            self._cache.last_target_x_normalized = (
                target.target_x_normalized if target is not None else None
            )
            self._cache.last_pan_angle_deg = angle
            self._cache.last_emitted_angle_deg = (
                self._dispatcher.last_emitted_angle_deg
            )
            # D-06 dispatch gate: pause silences ONLY the motor send; the
            # rest of the chain runs so preview stays live and the
            # dispatcher's gate state still progresses (resume is seamless).
            if command is not None and self._dispatch_enabled:
                await self._motor.send_motor_angle(command)

    def _on_tick_task_done(self, task: asyncio.Task[None]) -> None:
        """Surface tick-task exceptions deterministically (RESEARCH Pitfall 4).

        Without this callback, an unhandled exception in ``_tick_loop``
        would be GC'd as ``Task exception was never retrieved`` -- under
        our ``filterwarnings=['error']`` policy that is a flake source
        (Phase 2 W-05 precedent).

        ``CancelledError`` = graceful (``quit()`` requested). Any other
        exception: log ``pipeline_crashed`` with ``exc_info``, latch
        STOPPED, leave the exception attached so ``quit()``'s
        ``await self._tick_task`` re-raises it to ``__main__`` (D-13/D-14).
        """
        if task.cancelled():
            return
        exc = task.exception()
        if exc is None:
            # Tick task completed cleanly -- only happens when the camera
            # iterator returns (e.g. fake exhausted). Latch STOPPED.
            old = self._state
            self._state = _PipelineState.STOPPED
            self._dispatch_enabled = False
            self._log_transition(old, _PipelineState.STOPPED, reason="tick_loop_drained")
            return
        # Unhandled exception path (D-13/D-14).
        old = self._state
        self._state = _PipelineState.STOPPED
        self._dispatch_enabled = False
        self._logger.error(
            "pipeline_crashed",
            old_state=old.value,
            exc_type=type(exc).__name__,
            exc_msg=str(exc),
            exc_info=(type(exc), exc, exc.__traceback__),
        )

    # ---------- public lifecycle (D-05..D-09, D-15) ----------

    async def start(self) -> None:
        """D-05: motor handshake -> camera open -> settings + limits -> tick.

        Motor first (fail-fast on USB) before DirectShow open. Per
        RESEARCH Pitfall 8: idempotence shortcut BEFORE re-calling
        ``motor.start()`` / ``camera.start()`` -- those are single-shot
        and would raise on a second call.
        """
        # Pitfall 8: idempotence shortcut. Table also returns RUNNING for
        # (RUNNING, "start"), but we must not re-call hardware start().
        if self._state in (
            _PipelineState.RUNNING,
            _PipelineState.PAUSED,
            _PipelineState.HOMING,
        ):
            return
        next_state = self._transition_or_raise("start")
        old = self._state
        # Hardware start: motor first (fail-fast on USB/handshake), camera
        # second. Errors propagate per D-10/D-12/D-13.
        await self._motor.start()
        await self._camera.start()
        # D-11 / Pitfall 6: settings + angle limits are issued ONCE, here,
        # before the tick spawns. ArduinoMotor._recover() owns all
        # subsequent re-issues; this module must not call those methods
        # anywhere else (structurally enforced by an acceptance grep).
        await self._motor.send_settings(
            max_speed=self._config.motor_max_speed_steps_per_sec,
            max_accel=self._config.motor_max_accel_steps_per_sec2,
            pid_p=_FIRMWARE_PID_DEFAULT_P,
            pid_i=_FIRMWARE_PID_DEFAULT_I,
            pid_d=_FIRMWARE_PID_DEFAULT_D,
        )
        await self._motor.send_limits(
            min_deg=self._config.motor_angle_min_deg,
            max_deg=self._config.motor_angle_max_deg,
        )
        # Spawn tick task. Done-callback handles surfacing exceptions (Pitfall 4).
        await self._detector.start()
        self._dispatch_enabled = True
        self._state = next_state
        self._tick_task = asyncio.create_task(self._tick_loop(), name="pipeline-tick")
        self._tick_task.add_done_callback(self._on_tick_task_done)
        self._log_transition(old, next_state, reason="start")

    async def pause(self) -> None:
        """D-06: flip ``_dispatch_enabled=False``; tick loop continues (preview live)."""
        if self._state is _PipelineState.PAUSED:
            return
        next_state = self._transition_or_raise("pause")
        old = self._state
        self._dispatch_enabled = False
        self._state = next_state
        self._logger.info("pipeline_dispatch_disabled", reason="paused")
        self._log_transition(old, next_state, reason="pause")

    async def resume(self) -> None:
        """D-06: flip ``_dispatch_enabled=True``; tick loop already running."""
        if self._state is _PipelineState.RUNNING:
            return
        next_state = self._transition_or_raise("resume")
        old = self._state
        self._dispatch_enabled = True
        self._state = next_state
        self._logger.info("pipeline_dispatch_enabled", reason="resumed")
        self._log_transition(old, next_state, reason="resume")

    async def home(self) -> None:
        """D-08: reject if 0 deg outside limits; else send H, HOMING -> PAUSED.

        Fire-and-forget per RESEARCH Pitfall 7 option 1: state moves
        PAUSED as soon as the H command is SENT, not when the motor
        reaches 0 deg. Phase 7 reads ``motor.events()`` for actual
        completion if needed.
        """
        if not (
            self._config.motor_angle_min_deg
            <= _HOME_TARGET_DEG
            <= self._config.motor_angle_max_deg
        ):
            self._logger.warning(
                "pipeline_home_rejected",
                reason="0_outside_limits",
                pan_min_deg=self._config.motor_angle_min_deg,
                pan_max_deg=self._config.motor_angle_max_deg,
            )
            raise OrchestratorRejected(
                f"home: 0.0 outside limits "
                f"[{self._config.motor_angle_min_deg}, "
                f"{self._config.motor_angle_max_deg}]"
            )
        homing_state = self._transition_or_raise("home")
        old = self._state
        self._state = homing_state  # HOMING transient
        self._dispatch_enabled = False  # silence motor while homing
        self._log_transition(old, homing_state, reason="home")
        await self._motor.send_home()
        # Fire-and-forget: HOMING -> PAUSED inline (back-edge not in table).
        self._state = _PipelineState.PAUSED
        self._log_transition(homing_state, _PipelineState.PAUSED, reason="home_sent")

    async def e_stop(self) -> None:
        """D-07: inline ``send_emergency_stop`` -> E_STOPPED. < 200 ms budget.

        ``send_emergency_stop`` bypasses the motor's pause + latched-error
        gates (arduino_motor.py:923-928) so the wire silences even after
        a fault. Inline (not via tick task) so the heartbeat budget is
        met regardless of detector latency.
        """
        if self._state is _PipelineState.E_STOPPED:
            # Idempotent -- re-send E for safety, do not re-transition.
            await self._motor.send_emergency_stop()
            return
        next_state = self._transition_or_raise("e_stop")
        # Inline send FIRST (silence the wire before any state mutation).
        await self._motor.send_emergency_stop()
        self._dispatch_enabled = False
        old = self._state
        self._state = next_state
        self._logger.warning(
            "pipeline_dispatch_disabled",
            reason="e_stop",
        )
        self._log_transition(old, next_state, reason="e_stop")

    async def quit(self) -> None:
        """D-09: graceful drain. Idempotent (second call no-ops).

        Order mirrors ``arduino_motor.close``: cancel tick task ->
        ``camera.stop()`` -> ``detector.stop()`` -> ``motor.close()`` ->
        QUITTING. ``contextlib.suppress`` wraps ``CancelledError`` +
        ``Exception`` per RESEARCH Pitfall 1 (W-05 precedent,
        arduino_motor.py:271) so a late-arriving close race cannot
        block the drain.
        """
        if self._state is _PipelineState.QUITTING:
            return
        next_state = self._transition_or_raise("quit")
        old = self._state
        self._dispatch_enabled = False
        if self._tick_task is not None and not self._tick_task.done():
            self._tick_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._tick_task
            self._tick_task = None
        with contextlib.suppress(Exception):
            await self._camera.stop()
        with contextlib.suppress(Exception):
            await self._detector.stop()
        with contextlib.suppress(Exception):
            await self._motor.close()
        self._state = next_state
        self._log_transition(old, next_state, reason="quit")
