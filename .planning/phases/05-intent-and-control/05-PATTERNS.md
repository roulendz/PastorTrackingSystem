# Phase 5: Intent and Control - Pattern Map

**Mapped:** 2026-05-05
**Files analyzed:** 12 (4 production stages + 2 package re-exports + 1 fixture + 5 tests)
**Analogs found:** 12 / 12 — every Phase 5 file has a strong in-repo analog (Phase 4 is the dominant donor; Phase 1 contributes the damping math idiom; Phase 4 fixture layout contributes the trajectories.py shape).

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `pastor_tracker/src/pastor_tracker/intent/motion_analyzer.py` | service (stateful per-frame transformer) | request-response (per-frame `consume()`, sync state mutation, no I/O) | `pastor_tracker/src/pastor_tracker/perception/subject_tracker.py` | exact (same `async def consume(upstream, now_ns) -> Out \| None` shape, same per-direction-timer hysteresis idiom mirroring `_LockState` lock-loss timer) |
| `pastor_tracker/src/pastor_tracker/intent/framer.py` | service (stateful per-frame transformer + `match` over Literal) | request-response (intent → target lookup + damper step) | `pastor_tracker/src/pastor_tracker/perception/subject_tracker.py` (state-machine + match-exhaustiveness) + `pastor_tracker/src/pastor_tracker/core/damping.py` (damper API contract) | exact for shape; role-match for the damper composition (no existing stage holds a damper yet — Phase 5 is the first) |
| `pastor_tracker/src/pastor_tracker/control/pan_controller.py` | service (FOV bridge + damper + clamp + deadband) | request-response | `pastor_tracker/src/pastor_tracker/perception/subject_tracker.py` (consume shape + `_compute_dt_sec` idiom) | exact for shape; partial for clamp/deadband (no exact analog — pattern is locked in 05-RESEARCH Pattern C) |
| `pastor_tracker/src/pastor_tracker/control/command_dispatcher.py` | service (sync rate-limited gate, pure transform on `float \| None`) | request-response (sync `decide()`) | `pastor_tracker/src/pastor_tracker/perception/subject_tracker.py` (state init + property surface idiom) — synchronous twin | role-match (only sync stage in the project; existing analogs are async) |
| `pastor_tracker/src/pastor_tracker/intent/__init__.py` | config (package re-exports) | n/a | `pastor_tracker/src/pastor_tracker/perception/__init__.py` | exact |
| `pastor_tracker/src/pastor_tracker/control/__init__.py` | config (package re-exports) | n/a | `pastor_tracker/src/pastor_tracker/perception/__init__.py` | exact |
| `pastor_tracker/tests/fixtures/trajectories.py` | utility (pure deterministic generators) | transform (params → `list[TrackedSubject]`) | `pastor_tracker/tests/fixtures/pose_traces.py` (`make_detection` + canned sequences) | exact (same fixture-helpers idiom; 1:1 with Phase 4 layout) |
| `pastor_tracker/tests/test_motion_analyzer.py` | test | request-response (drives `consume()` via `asyncio.run`) | `pastor_tracker/tests/test_subject_tracker_lock.py` | exact |
| `pastor_tracker/tests/test_framer.py` | test (intent dispatch + step-response on real damper) | request-response | `pastor_tracker/tests/test_subject_tracker_lock.py` (consume idiom) + `pastor_tracker/tests/test_damping.py` (step-response invariants) | exact (composite — both analogs cited) |
| `pastor_tracker/tests/test_pan_controller.py` | test (FOV + step + clamp + deadband) | request-response | `pastor_tracker/tests/test_subject_tracker_lock.py` + `pastor_tracker/tests/test_damping.py` | exact |
| `pastor_tracker/tests/test_command_dispatcher.py` | test (sync `decide()`, no `asyncio.run`) | request-response | `pastor_tracker/tests/test_damping.py` (sync test bodies + parametrized assertion idiom) | role-match (sync-only test surface; no existing async-free state-machine test) |
| `pastor_tracker/tests/test_intent_control_pipeline.py` | test (composition smoke across all four stages) | request-response chain | `pastor_tracker/tests/test_subject_tracker_lock.py::test_lock_survives_brief_occlusion` (multi-tick scripted-sequence pattern) | role-match (Phase 5 is the first multi-stage same-thread composition test) |

## Pattern Assignments

### `pastor_tracker/src/pastor_tracker/intent/motion_analyzer.py` (service, request-response)

**Analog:** `pastor_tracker/src/pastor_tracker/perception/subject_tracker.py`

**Imports + module constants pattern** (subject_tracker.py lines 32-67):
```python
from __future__ import annotations

import enum
from typing import Final

import structlog

from pastor_tracker.config import Config
from pastor_tracker.core.types import Detection, TrackedSubject
from pastor_tracker.perception.pose_detector import PerceptionError

# --- Lifecycle thresholds ---
_LOCK_LOSS_TIMEOUT_SEC: Final[float] = 2.0  # PERC-05
_NS_PER_SEC: Final[float] = 1_000_000_000.0
_NS_PER_MS: Final[float] = 1_000_000.0
_CAPTURE_FPS_FLOOR: Final[int] = 1
```
→ Phase 5 copies the `from __future__ import annotations`, `Final` constants for `_NS_PER_SEC`, structlog import, and Config import. NO new exception class needed in `motion_analyzer.py` (analyzer raises nothing — fail-fast is structural via `MotionIntent` Literal exhaustiveness, which lives in `framer.py`).

**Class header + read-only dashboard surface** (subject_tracker.py lines 109-158):
```python
class SubjectTracker:
    def __init__(self, config: Config) -> None:
        self._config = config
        self._logger = structlog.get_logger(module="subject_tracker")
        # ... state init ...

    # ---------- read-only dashboard surface ----------
    @property
    def is_locked(self) -> bool:
        return self._state is _LockState.LOCKED

    @property
    def current_track_id(self) -> int | None:
        return self._locked_track_id
```
→ Copy exactly: `__init__(config)`, `structlog.get_logger(module="motion_analyzer")`, then `@property current_intent` returning `self._current_intent` (CONTEXT.md "Public read-only state for Phase 7 dashboard").

**Async consume signature + flat dispatch** (subject_tracker.py lines 161-198):
```python
async def consume(
    self, detections: list[Detection], now_ns: int,
) -> TrackedSubject | None:
    """Single-frame tick. Returns emitted subject or None ..."""
    self._raise_if_latched()
    # ... pre-processing ...
    match self._state:
        case _LockState.UNLOCKED | _LockState.SEEKING | _LockState.RE_ACQUIRING:
            return self._try_lock(eligible, now_ns)
        case _LockState.LOCKED:
            return self._tick_locked(eligible, now_ns)
        # ...
        case _:
            # WR-08 fix: exhaustiveness guard (Tiger-style fail-loud on
            # contract violation).
            raise PerceptionError(
                f"unhandled _LockState in consume: {self._state!r}"
            )
```
→ MotionAnalyzer's `consume` does NOT need a `match` (its dispatch is by region, not enum); use the early-return guard at the top for `subject is None` (mirrors 05-RESEARCH Pattern A lines 684-721). Flat structure, ≤2 nesting per CLAUDE.md rule 5.

**Per-direction timer pattern** (subject_tracker.py lines 260-275, lock-loss timeout idiom):
```python
if self._last_seen_ts_ns is not None:
    elapsed_sec = (now_ns - self._last_seen_ts_ns) / _NS_PER_SEC
    if elapsed_sec > _LOCK_LOSS_TIMEOUT_SEC:
        # ... transition to LOST ...
```
→ Phase 5 mirrors this `now_ns - first_crossing_ts_ns >= duration_ns` continuous-duration predicate per direction (right / left / dwell). 05-RESEARCH Pattern A lines 723-763 has the exact code; the analog confirms the **idiom** (subtract ts, compare to threshold) is repo-canonical.

**dt computation helper** (subject_tracker.py lines 379-385):
```python
def _compute_dt_sec(self, now_ns: int) -> float:
    if self._last_seen_ts_ns is None:
        return 1.0 / max(self._config.capture_fps, _CAPTURE_FPS_FLOOR)
    delta_ns = now_ns - self._last_seen_ts_ns
    if delta_ns <= 0:
        return 1.0 / max(self._config.capture_fps, _CAPTURE_FPS_FLOOR)
    return float(delta_ns) / _NS_PER_SEC
```
→ MotionAnalyzer does NOT need this (no damper inside) — but `Framer` and `PanController` MUST copy this verbatim for their dt calc (Pitfall 4/5/10 from 05-RESEARCH).

**Logging shapes** (subject_tracker.py lines 236-247):
```python
self._logger.warning(
    "lock_reacquired",
    old_track_id=old_track_id,
    new_track_id=chosen.track_id,
    gap_ms=gap_ms,
)
self._logger.info(
    "lock_acquired", track_id=chosen.track_id,
    cx=chosen.subject_center_x_normalized,
    cy=chosen.subject_center_y_normalized,
)
```
→ Phase 5 mirrors event-name + kwargs convention. Per 05-RESEARCH Pattern 9: `intent_change` (INFO, fields `old`, `new`, `vx`, `sustained_for_sec`), `motion_analyzer_reset` (DEBUG).

---

### `pastor_tracker/src/pastor_tracker/intent/framer.py` (service, request-response)

**Analog A:** `pastor_tracker/src/pastor_tracker/perception/subject_tracker.py` (consume shape + match-exhaustiveness)
**Analog B:** `pastor_tracker/src/pastor_tracker/core/damping.py` (damper instantiation contract)

**Match exhaustiveness over Literal** (subject_tracker.py lines 182-198):
```python
match self._state:
    case _LockState.UNLOCKED | _LockState.SEEKING | _LockState.RE_ACQUIRING:
        return self._try_lock(eligible, now_ns)
    case _LockState.LOCKED:
        return self._tick_locked(eligible, now_ns)
    case _LockState.HOLDING:
        return self._tick_holding(eligible, now_ns)
    case _LockState.LOST:
        return self._try_reacquire(eligible, now_ns)
    case _:
        raise PerceptionError(
            f"unhandled _LockState in consume: {self._state!r}"
        )
```
→ Framer's `_intent_to_target(intent: MotionIntent)` MUST follow this exactly for its `match intent` block, raising a typed `IntentError` in `case _:` (05-RESEARCH Pattern 6 lines 440-449; 05-RESEARCH Pattern B lines 842-856). This is the **WR-08 fix discipline** referenced in the CONTEXT decisions.

**Damper instantiation contract** (damping.py lines 35-57):
```python
@dataclass(frozen=True, slots=True)
class CriticallyDampedFollower:
    time_constant_sec: float

    def __post_init__(self) -> None:
        if self.time_constant_sec <= 0.0:
            raise ValueError(
                f"time_constant_sec must be > 0, got {self.time_constant_sec}"
            )

    def initial_state(
        self, position: float, velocity: float = 0.0
    ) -> FollowerState:
        return FollowerState(position=position, velocity=velocity)
```
→ Framer constructs `CriticallyDampedFollower(time_constant_sec=config.framing_time_constant_sec)` once in `__init__`. Note: do NOT call `initial_state(...)` for the seed — directly construct `FollowerState(position=target, velocity=0.0)` per 05-RESEARCH Pitfall 6 ("seed at target, velocity=0"). The analog is the **API contract**; the Phase-5-specific seeding rule comes from CONTEXT Area 3.

**Damper step contract** (damping.py lines 59-76):
```python
def step(
    self,
    state: FollowerState,
    target: float,
    dt: float,
) -> FollowerState:
    """Advance the follower one timestep. Pure: returns a new state."""
    if dt <= 0.0:
        raise ValueError(f"dt must be > 0, got {dt}")
    # ... Holden closed form ...
    return replace(state, position=new_position, velocity=new_velocity)
```
→ Framer threads state explicitly: `self._state = self._damper.step(self._state, target=target, dt=dt_sec)`. The damper RAISES on `dt<=0`, so Framer's `_compute_dt_sec` MUST floor to `1/capture_fps` (mirror SubjectTracker's `_compute_dt_sec`).

**Re-export of `replace`** (damping.py line 19, used internally):
```python
from dataclasses import replace
```
→ `pan_controller.py` (NOT framer) needs `from dataclasses import replace` for the **velocity-clamp anti-windup overwrite** (Pattern 5 in 05-RESEARCH). Framer doesn't need `replace` — it always uses the new state whole.

**Skeleton (verified against 05-RESEARCH Pattern B lines 766-866):** The full Framer body is given verbatim in 05-RESEARCH lines 794-866. Plan should reference Pattern B; analog adds nothing not already in Pattern B beyond confirming the `__init__` / structlog / property-surface conventions are repo-canonical.

---

### `pastor_tracker/src/pastor_tracker/control/pan_controller.py` (service, request-response)

**Analog A:** `pastor_tracker/src/pastor_tracker/perception/subject_tracker.py` (consume shape + dt helper + property surface)
**Analog B:** `pastor_tracker/src/pastor_tracker/core/damping.py` (damper API)

**`replace`-based state mutation pattern** (damping.py lines 19, 76):
```python
from dataclasses import replace
# ...
return replace(state, position=new_position, velocity=new_velocity)
```
→ PanController **must** copy this idiom for the velocity-clamp anti-windup overwrite (CONTEXT Area 4): `new_state = replace(new_state, position=self._state.position + clipped_delta)`. 05-RESEARCH Pattern 5 lines 418-433 has the exact code.

**dt helper to copy verbatim** (subject_tracker.py lines 379-385): same as MotionAnalyzer above. PanController has TWO upstream timestamps to track: `target.timestamp_ns` is the source of truth for dt (per Pitfall 5 "use upstream ts, not now_ns").

**Hold-on-None idiom** (subject_tracker.py lines 191-198 — the no-emit branches return `None`; lines 300-361 — frozen-posterior emission during HOLDING):
```python
# HOLDING: emit frozen posterior (Pitfall 4)
cx, cy = _KalmanWrapper.hold_posterior(self._kf)
return TrackedSubject(
    track_id=self._locked_track_id,
    # ... frozen values ...
    timestamp_ns=now_ns,
)
```
→ PanController's `_hold()` returns `self._last_emitted_angle_deg` (which may be None pre-seed) — analogous "emit held value, no advance" semantics. 05-RESEARCH Pattern C lines 950-951.

**Skeleton:** 05-RESEARCH Pattern C lines 868-960 is the full PanController body, already verified.

---

### `pastor_tracker/src/pastor_tracker/control/command_dispatcher.py` (service, request-response, sync)

**Analog:** `pastor_tracker/src/pastor_tracker/perception/subject_tracker.py` (`__init__` + state-init + property-surface idioms; signature differs — sync, not async)

**State init pattern** (subject_tracker.py lines 126-137):
```python
def __init__(self, config: Config) -> None:
    self._config = config
    self._logger = structlog.get_logger(module="subject_tracker")
    self._kalman_wrapper = _KalmanWrapper()
    self._state: _LockState = _LockState.UNLOCKED
    self._kf: KalmanFilter | None = None
    self._locked_track_id: int | None = None
    self._last_seen_ts_ns: int | None = None
```
→ Dispatcher copies the `_config` / `_logger` / `_last_*` idiom. Per CONTEXT Area 4: `_last_emitted_angle_deg: float | None = None`, `_last_emit_ts_ns: int | None = None`. NO enum (this is a one-state machine).

**Property surface for dashboard** (subject_tracker.py lines 140-154):
```python
@property
def is_locked(self) -> bool:
    return self._state is _LockState.LOCKED

@property
def current_track_id(self) -> int | None:
    return self._locked_track_id

@property
def last_lock_loss_ts_ns(self) -> int | None:
    return self._last_lock_loss_ts_ns
```
→ Dispatcher exposes `last_emitted_angle_deg` and `last_emit_ts_ns` properties (CONTEXT.md "Public read-only state for Phase 7 dashboard"). 05-RESEARCH Pattern D lines 986-992.

**Sync `decide()` body:** No async analog in repo (Dispatcher is the only sync stage). Skeleton in 05-RESEARCH Pattern D lines 994-1019 is canonical. Key contract: **None upstream returns None and DOES NOT update state** (Pitfall 7).

---

### `pastor_tracker/src/pastor_tracker/intent/__init__.py` and `control/__init__.py` (config, re-exports)

**Analog:** `pastor_tracker/src/pastor_tracker/perception/__init__.py` (lines 1-27):
```python
"""Phase 4 perception: pose detection, BoT-SORT tracking, Kalman smoothing.

Public surface:
    * ``PoseEngine`` Protocol -- DI seam ...
    * ``PoseDetector`` -- orchestrator ...
"""
from pastor_tracker.perception.pose_detector import (
    PerceptionError,
    PoseDetector,
    PoseEngine,
    PoseEngineUnavailableError,
    UltralyticsPoseEngine,
)
from pastor_tracker.perception.subject_tracker import SubjectTracker

__all__ = [
    "PerceptionError",
    "PoseDetector",
    "PoseEngine",
    "PoseEngineUnavailableError",
    "SubjectTracker",
    "UltralyticsPoseEngine",
]
```
→ `intent/__init__.py` re-exports `MotionAnalyzer`, `Framer`, `IntentError`. `control/__init__.py` re-exports `PanController`, `CommandDispatcher`, `ControlError`. Same docstring shape ("Phase 5 …: Public surface: …"). Both are currently single-line stubs.

---

### `pastor_tracker/tests/fixtures/trajectories.py` (utility, transform)

**Analog:** `pastor_tracker/tests/fixtures/pose_traces.py` (lines 1-72)

**Module docstring + factory helper** (pose_traces.py lines 1-45):
```python
"""Canned Detection sequences and async helpers for perception integration tests.

Drive scripted traces via :class:`FakePoseEngine`; pre-load a list of
``list[Detection]`` per-frame outputs and hand the fake to ``SubjectTracker``
or ``PoseDetector`` via the ``PoseEngine`` Protocol injection seam.
"""
from __future__ import annotations

import collections
from collections.abc import Iterable

from pastor_tracker.core.types import Detection, Frame


def make_detection(
    *,
    cx: float,
    cy: float,
    track_id: int | None = 1,
    conf: float = 0.9,
    timestamp_ns: int = 0,
    bbox_half_w: float = 0.05,
    bbox_half_h: float = 0.10,
) -> Detection:
    """Construct a synthetic Detection with a centered bbox around (cx, cy)."""
    return Detection(
        subject_center_x_normalized=cx,
        # ... clamped bbox ...
        timestamp_ns=timestamp_ns,
        track_id=track_id,
    )
```
→ `trajectories.py` mirrors **exactly**: `from __future__ import annotations`, no class — pure module-level helpers with kw-only parameters (`*,`), default `track_id=1` and `t0_ns=1_000_000_000`, return `list[TrackedSubject]`. Each helper builds `TrackedSubject(track_id, subject_center_x_normalized, subject_center_y_normalized, velocity_x_norm_per_sec, velocity_y_norm_per_sec, timestamp_ns)` directly (no `make_*` indirection needed because TrackedSubject has no cross-field validator like Detection's bbox).

**Function shapes** locked in 05-RESEARCH Pattern 8 lines 486-528: `step`, `ramp`, `dwell_then_walk`, `borderline_chatter`. Each kw-only, deterministic, no hypothesis.

**Module-level CANNED CONSTANTS pattern** (pose_traces.py lines 75-): The fixture file also exports module-level constants like `POSE_TRACE_OCCLUSION_3F`, `POSE_TRACE_TRACK_ID_PERSIST`. → `trajectories.py` does NOT need pre-baked constants (the helpers are parametric); generators-only is the right shape.

---

### `pastor_tracker/tests/test_motion_analyzer.py` (test)

**Analog:** `pastor_tracker/tests/test_subject_tracker_lock.py` (lines 1-30, 31-32, 63-69)

**Test imports + helper factory** (test_subject_tracker_lock.py lines 1-32):
```python
"""PERC-02 / PERC-03 / PERC-04 / PERC-05 lock-state-machine tests.

Drives real SubjectTracker + real filterpy.KalmanFilter (CLAUDE.md hard rule:
no mocks). Detection sequences via FakePoseEngine fixtures.
"""
from __future__ import annotations

import asyncio

import numpy as np
import pytest
import structlog
from hypothesis import given, settings
from hypothesis import strategies as st

from pastor_tracker.config import Config
from pastor_tracker.core.types import TrackedSubject
from pastor_tracker.perception.subject_tracker import (
    SubjectTracker,
    _LockState,
    compute_subject_centroid,
)
from tests.fixtures.pose_traces import (
    POSE_TRACE_OCCLUSION_3F,
    POSE_TRACE_TRACK_ID_PERSIST,
    POSE_TRACE_TWO_PERSON_CENTRAL,
    make_detection,
)


def _make_tracker(valid_config_dict: dict[str, object]) -> SubjectTracker:
    return SubjectTracker(Config(**valid_config_dict))  # type: ignore[arg-type]
```
→ Phase 5 copies: docstring referencing INTENT-01..02 + TEST-03 + "no mocks" disclaimer; `from __future__ import annotations`; `import asyncio`; `from pastor_tracker.config import Config`; `from pastor_tracker.intent.motion_analyzer import MotionAnalyzer`; `from tests.fixtures.trajectories import step, ramp, dwell_then_walk, borderline_chatter`; helper `_make_analyzer(valid_config_dict)`.

**`asyncio.run(consume(...))` from sync test body** (test_subject_tracker_lock.py lines 63-69):
```python
def test_low_conf_detection_rejected(valid_config_dict: dict[str, object]) -> None:
    """PERC-02: mean kp conf < 0.55 floor -> no lock."""
    tracker = _make_tracker(valid_config_dict)
    low_conf = make_detection(cx=0.5, cy=0.5, track_id=1, conf=0.40, timestamp_ns=1_000_000)
    result = asyncio.run(tracker.consume([low_conf], now_ns=1_000_000))
    assert result is None
    assert tracker.is_locked is False
```
→ Every Phase 5 analyzer / framer / pan-controller test uses **sync test body + `asyncio.run(stage.consume(...))`** (Assumption A3 in 05-RESEARCH; pytest-asyncio's `asyncio_mode="auto"` does not interfere). For dispatcher (sync `decide()`), no `asyncio.run()` — call directly.

**Hypothesis idiom** (test_subject_tracker_lock.py lines 35-44):
```python
@given(
    kp=st.lists(
        st.tuples(
            st.floats(min_value=0.0, max_value=1.0),
            st.floats(min_value=0.0, max_value=1.0),
        ),
        min_size=17, max_size=17,
    ),
)
@settings(deadline=None, max_examples=40)
def test_centroid_weighted_mean_property(kp: list[tuple[float, float]]) -> None:
```
→ Phase 5 borderline-chatter property test uses `@given(...) + @settings(deadline=None, max_examples=...)` on a **sync** function; constrain `vx` strategy to `[threshold * 0.5, threshold * 1.5]` per 05-RESEARCH Pitfall 9. No `@given` on async test functions (Assumption A3).

**Multi-tick scripted-trace iteration** (test_subject_tracker_lock.py lines 110-130):
```python
def test_lock_survives_brief_occlusion(valid_config_dict: dict[str, object]) -> None:
    tracker = _make_tracker(valid_config_dict)
    last_seen_ts = 0
    for k, frame in enumerate(POSE_TRACE_OCCLUSION_3F):
        if frame:
            ts_ns = frame[0].timestamp_ns
            last_seen_ts = ts_ns
            asyncio.run(tracker.consume(frame, now_ns=ts_ns))
        else:
            last_seen_ts += 33_000_000
            asyncio.run(tracker.consume([], now_ns=last_seen_ts))
        del k
    assert tracker.current_track_id == 1
```
→ Phase 5 analyzer/framer/controller tests iterate over `for subject in trajectories.ramp(...)` and `asyncio.run(stage.consume(subject, now_ns=subject.timestamp_ns))`. Final assertion checks `analyzer.current_intent` / `framer.current_target_x_normalized` / `controller.current_angle_deg` via the property surface.

**Structlog log-capture pattern** (test_subject_tracker_lock.py lines 136, 155-157):
```python
with structlog.testing.capture_logs() as caplog:
    # ... drive trace ...
events = [e["event"] for e in caplog]
assert "lock_loss" in events
assert "lock_reacquired" in events
```
→ `intent_change` event assertions in `test_motion_analyzer.py` use this pattern. Mirror exactly.

---

### `pastor_tracker/tests/test_framer.py` (test)

**Analog A:** `pastor_tracker/tests/test_subject_tracker_lock.py` (consume idiom)
**Analog B:** `pastor_tracker/tests/test_damping.py` (step-response invariants)

**Step-response invariant** (test_damping.py lines 42-68):
```python
def test_critically_damped_no_overshoot(tau: float) -> None:
    follower = CriticallyDampedFollower(time_constant_sec=tau)
    state = follower.initial_state(position=0.0)
    horizon = max(int(HORIZON_MULTIPLE * tau / DT_60HZ), MIN_HORIZON_STEPS)
    positions: list[float] = [state.position]
    for _ in range(horizon):
        state = follower.step(state, target=TARGET_UNIT_STEP, dt=DT_60HZ)
        positions.append(state.position)
    # 1) No overshoot.
    assert max(positions) <= TARGET_UNIT_STEP + OVERSHOOT_TOL
    # 2) Monotonic non-decreasing.
    diffs = [b - a for a, b in pairwise(positions)]
    assert all(d >= -MONOTONIC_TOL for d in diffs)
    # 3) Settled to within 5% by 5*tau.
    idx_5tau = int(SETTLE_TAU_MULTIPLE * tau / DT_60HZ)
    assert abs(positions[idx_5tau] - TARGET_UNIT_STEP) < SETTLED_TOL
```
→ Phase 5 framer step-response test: feed `dwell_then_walk` (or scripted intent flip from `moving_left` to `moving_right`), capture the **emitted `FramingTarget.target_x_normalized`** sequence, assert the same three invariants (no overshoot above target third, monotonic toward target, settled within 5% by `5 * config.framing_time_constant_sec`). Same for `test_pan_controller.py::test_step_response_no_overshoot`. Tolerances `OVERSHOOT_TOL=1e-9`, `MONOTONIC_TOL=1e-9`, `SETTLED_TOL=0.05` are the established constants — copy them.

**Module-level test constants** (test_damping.py lines 17-29):
```python
OVERSHOOT_TOL: float = 1e-9
MONOTONIC_TOL: float = 1e-9
SETTLED_TOL: float = 0.05
TARGET_UNIT_STEP: float = 1.0
DT_60HZ: float = 1.0 / 60.0
HORIZON_MULTIPLE: int = 10
MIN_HORIZON_STEPS: int = 600
SETTLE_TAU_MULTIPLE: int = 5
HYP_MAX_EXAMPLES: int = 40
TWO_SECONDS_AT_60HZ: int = 120
```
→ Mirror this **module-level named-constant** discipline to keep CLAUDE.md rule 6 ("no magic numbers") in test bodies. Phase 5 test files re-export ONLY non-Config constants; everything else flows through `valid_config_dict` fixture.

---

### `pastor_tracker/tests/test_pan_controller.py` (test)

Same analogs as `test_framer.py`. Specific to pan controller:

**Velocity-clamp assertion** — feed a target far enough that an unclamped damper step would exceed `30°/s * dt`. Iterate via `asyncio.run(controller.consume(...))`; capture per-frame deltas; assert `abs(emitted[i] - emitted[i-1]) <= config.pan_max_velocity_deg_per_sec * dt_sec + 1e-9`. Then assert `controller._state.position == emitted[i]` (CONTEXT Area 4: clamp **overwrites** state, not just emission).

**Deadband assertion** — feed a sequence of nearly-identical `FramingTarget` (delta < `pan_deadband_deg`). Assert `controller.consume()` emits the **same value across the whole window**. Then feed a target that nudges the damper past the deadband cumulatively — assert eventual emission catches up smoothly (Pitfall 3 in 05-RESEARCH).

No new analog excerpt needed — the discipline (named test constants, sync body + `asyncio.run`, real damping, parameter values from `valid_config_dict` fixture) is identical to test_framer.

---

### `pastor_tracker/tests/test_command_dispatcher.py` (test)

**Analog:** `pastor_tracker/tests/test_damping.py` (sync test pattern, no `asyncio.run` — dispatcher is sync)

Dispatcher tests follow `test_damping.py` shape exactly:
```python
def test_zero_or_negative_time_constant_rejected() -> None:
    with pytest.raises(ValueError, match="time_constant_sec"):
        CriticallyDampedFollower(time_constant_sec=0.0)
```
→ All dispatcher tests are **sync** (no `asyncio.run`). Drive `dispatcher.decide(angle_deg, now_ns)` directly. Assert the four CTRL-04 cases:
- First call always emits → assert `MotorCommand` returned, `last_emitted_angle_deg` and `last_emit_ts_ns` populated
- Δ ≤ `command_min_delta_deg` → assert returned `None`, state UNCHANGED
- Interval < `command_min_interval_ms` → assert returned `None`, state UNCHANGED
- None upstream → assert returned `None`, state UNCHANGED (Pitfall 7)
- Synthetic ramp emission count ≤ `total_sec * 1000 / command_min_interval_ms` (Open Q 3 analytic bound; pull from `valid_config_dict`)

---

### `pastor_tracker/tests/test_intent_control_pipeline.py` (test, composition)

**Analog:** `pastor_tracker/tests/test_subject_tracker_lock.py::test_lock_survives_brief_occlusion` (multi-tick scripted-sequence pattern, lines 110-130)

→ Phase 5 composition test instantiates all four stages with the same `Config`, drives `dwell_then_walk` and `borderline_chatter` traces through `analyzer.consume → framer.consume → controller.consume → dispatcher.decide`, then asserts:
- `borderline_chatter` produces **zero** dispatcher emissions after the initial seed
- `dwell_then_walk` produces a center-then-third progression in the framer's emitted target_x, monotonic damped angle in the controller, and bounded emission count in the dispatcher

Skeleton structure: same `for subject in trajectory: asyncio.run(...)` loop as test_lock_survives_brief_occlusion lines 120-128.

## Shared Patterns

### Pattern S1 — Stage `__init__` + structlog binding

**Source:** `pastor_tracker/src/pastor_tracker/perception/subject_tracker.py` lines 126-138

```python
def __init__(self, config: Config) -> None:
    self._config = config
    self._logger = structlog.get_logger(module="subject_tracker")
    # ... per-stage state init ...
```

**Apply to:** `MotionAnalyzer.__init__`, `Framer.__init__`, `PanController.__init__`, `CommandDispatcher.__init__`. Module name in `structlog.get_logger(module=...)` must match the per-stage 05-RESEARCH Pattern 9 table (`motion_analyzer`, `framer`, `pan_controller`, `command_dispatcher`).

### Pattern S2 — Read-only dashboard property surface

**Source:** `pastor_tracker/src/pastor_tracker/perception/subject_tracker.py` lines 140-158

```python
# ---------- read-only dashboard surface ----------
@property
def is_locked(self) -> bool:
    return self._state is _LockState.LOCKED

@property
def current_track_id(self) -> int | None:
    return self._locked_track_id
```

**Apply to:** `MotionAnalyzer.current_intent`, `Framer.current_target_x_normalized`, `PanController.current_angle_deg`, `CommandDispatcher.last_emitted_angle_deg` + `last_emit_ts_ns`. Use the `# ---------- read-only dashboard surface ----------` comment banner so Phase 7 can grep all four files identically.

### Pattern S3 — `_compute_dt_sec` helper

**Source:** `pastor_tracker/src/pastor_tracker/perception/subject_tracker.py` lines 379-385

```python
def _compute_dt_sec(self, now_ns: int) -> float:
    if self._last_seen_ts_ns is None:
        return 1.0 / max(self._config.capture_fps, _CAPTURE_FPS_FLOOR)
    delta_ns = now_ns - self._last_seen_ts_ns
    if delta_ns <= 0:
        return 1.0 / max(self._config.capture_fps, _CAPTURE_FPS_FLOOR)
    return float(delta_ns) / _NS_PER_SEC
```

**Apply to:** `Framer._compute_dt_sec(current_upstream_ts_ns)` and `PanController._compute_dt_sec(current_upstream_ts_ns)`. Critical: per Pitfall 5, the input parameter is the **upstream's `timestamp_ns`** (not `now_ns`). Mirror exactly — same `delta_ns <= 0` floor (handles re-acquire path with stale or non-monotonic upstream).

### Pattern S4 — `match` exhaustiveness `case _:` raise (WR-08 lesson)

**Source:** `pastor_tracker/src/pastor_tracker/perception/subject_tracker.py` lines 191-198

```python
case _:
    # WR-08 fix: exhaustiveness guard (Tiger-style fail-loud on
    # contract violation). All six current _LockState members are
    # covered above; this catches a future enum member added
    # without a matching dispatch arm.
    raise PerceptionError(
        f"unhandled _LockState in consume: {self._state!r}"
    )
```

**Apply to:** `Framer._intent_to_target` `match intent` block — raises `IntentError` (typed exception defined in `framer.py`). 05-RESEARCH Pattern 6 lines 440-449.

### Pattern S5 — Module-level `Final` time constants

**Source:** `pastor_tracker/src/pastor_tracker/perception/subject_tracker.py` lines 56-59

```python
_NS_PER_SEC: Final[float] = 1_000_000_000.0
_NS_PER_MS: Final[float] = 1_000_000.0
_CAPTURE_FPS_FLOOR: Final[int] = 1
```

**Apply to:** Every Phase 5 production file that handles ns. Per 05-RESEARCH "Don't Hand-Roll" table, these are the canonical unit-conversion constants — module-level `Final`, no per-stage proliferation. `command_dispatcher.py` uses `_NS_PER_MS_INT: Final[int] = 1_000_000` instead (integer-domain comparison; per Pattern D).

### Pattern S6 — Test factory helper + `valid_config_dict` fixture parametrization

**Source:** `pastor_tracker/tests/test_subject_tracker_lock.py` lines 31-32

```python
def _make_tracker(valid_config_dict: dict[str, object]) -> SubjectTracker:
    return SubjectTracker(Config(**valid_config_dict))  # type: ignore[arg-type]
```

**Apply to:** Every Phase 5 test file. `_make_analyzer`, `_make_framer`, `_make_pan_controller`, `_make_dispatcher` — all take `valid_config_dict` (defined in `pastor_tracker/tests/conftest.py` lines 7-41), construct `Config(**valid_config_dict)`, instantiate the stage. The `# type: ignore[arg-type]` is needed because Pydantic Config has typed kwargs but `dict[str, object]` is broader.

### Pattern S7 — Pydantic `_FrozenModel` + `model_copy(update=...)` for DTO mutation

**Source:** `pastor_tracker/src/pastor_tracker/core/types.py` lines 113-116

```python
class _FrozenModel(BaseModel):
    """Base for all Pydantic DTOs in this module — frozen + extra-forbidden."""

    model_config = ConfigDict(frozen=True, extra="forbid")
```

**Apply to:** Phase 5 emits `MotionState`, `FramingTarget`, `MotorCommand` — all already inherit from `_FrozenModel`. **No DTO additions** in Phase 5 (CONTEXT-locked). Constructors only.

### Pattern S8 — Logger `module=` bind + event-name shape

**Source:** `pastor_tracker/src/pastor_tracker/perception/subject_tracker.py` lines 236-247

```python
self._logger.warning(
    "lock_reacquired",
    old_track_id=old_track_id,
    new_track_id=chosen.track_id,
    gap_ms=gap_ms,
)
```

**Apply to:** Per 05-RESEARCH Pattern 9 table:
| Stage | INFO events | DEBUG events |
|-------|-------------|--------------|
| MotionAnalyzer | `intent_change` | `motion_analyzer_reset` |
| Framer | `framing_target_change` | `framer_seeded` |
| PanController | (none — all DEBUG to avoid 30 Hz spam) | `pan_clamped`, `pan_deadband_suppressed`, `pan_controller_seeded` |
| CommandDispatcher | (none — DEBUG only at 20 Hz) | `command_emitted`, `command_suppressed_delta`, `command_suppressed_interval` |

## No Analog Found

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| (none) | — | — | Every Phase 5 file has at least a role-match analog. The composition test (`test_intent_control_pipeline.py`) and the sync `CommandDispatcher` are the weakest matches but inherit the multi-tick scripted-sequence and state-init+property-surface patterns from Phase 4, respectively. |

## Metadata

**Analog search scope:**
- `pastor_tracker/src/pastor_tracker/core/` — damping, geometry, types
- `pastor_tracker/src/pastor_tracker/perception/` — subject_tracker (the dominant donor), `__init__.py`
- `pastor_tracker/src/pastor_tracker/config.py` — Config field reference
- `pastor_tracker/tests/` — test_damping, test_subject_tracker_lock, conftest
- `pastor_tracker/tests/fixtures/` — pose_traces (the donor for trajectories.py)

**Files scanned:** 9 source files + 3 test files + 2 fixture files = 14 in-repo references. Plus the two upstream context files (CONTEXT.md, RESEARCH.md).

**Pattern extraction date:** 2026-05-05

**Key insight for the planner:** Phase 5 is **almost a direct transcription** of two existing repo patterns: (1) the Phase 4 `consume()` + `_compute_dt_sec` + property-surface state-machine shape from `subject_tracker.py`, and (2) the Phase 1 step-response invariant test discipline from `test_damping.py`. The 05-RESEARCH Patterns A-D give verbatim production-file skeletons (Pattern A = MotionAnalyzer, Pattern B = Framer, Pattern C = PanController, Pattern D = CommandDispatcher); the analog files in this map confirm those skeletons are repo-canonical and supply the **shared idioms** (Patterns S1-S8) that are not in the per-file skeletons. Every plan action should reference both the relevant 05-RESEARCH Pattern letter AND the corresponding Pattern S# from this map.

## PATTERN MAPPING COMPLETE
