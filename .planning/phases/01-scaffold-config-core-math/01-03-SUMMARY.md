---
phase: 01
plan: 03
subsystem: core-math
tags: [core, types, geometry, damping, hypothesis, pure-math, frozen-dto, holden-spring]
requirements: [CORE-01, CORE-02, CORE-03, CORE-04, TEST-01, TEST-02, TEST-05]
dependency_graph:
  requires:
    - "01-scaffold-config-core-math/01 — uv project, ruff/mypy/pytest wired, [tool.pydantic-mypy] block"
    - "01-scaffold-config-core-math/02 — frozen Pydantic Config established the BaseModel pattern reused here"
  provides:
    - "pastor_tracker.core.types — 6 frozen DTOs (Frame dataclass + 5 Pydantic models) for the pipeline data path"
    - "pastor_tracker.core.geometry — pure pinhole FOV math (normalized↔angle) with hypothesis-proven round-trip identity"
    - "pastor_tracker.core.damping.CriticallyDampedFollower — Holden exact closed-form spring-damper, dt-unconditionally stable, hypothesis-proven zero overshoot"
    - "ImageArray = npt.NDArray[np.uint8] type alias for ndarray fields under mypy disallow_any_explicit"
  affects:
    - "Phase 4 (perception) — consumes Frame + produces Detection / TrackedSubject"
    - "Phase 5 (intent + control) — consumes TrackedSubject, produces MotionState / FramingTarget / MotorCommand; instantiates two CriticallyDampedFollower (framing 0.8 s, pan 0.6 s)"
    - "Phase 6 (pipeline) — wires the DTO chain end-to-end"
    - "Phase 7 (UI) — reads frozen DTOs for the dashboard view"
tech_stack:
  added:
    - "numpy.typing.NDArray[np.uint8] (ImageArray alias) — Pitfall 2 mitigation for ndarray under disallow_any_explicit"
    - "Holden exact closed-form critically-damped follower (dataclass(frozen=True, slots=True))"
    - "hypothesis property tests with @settings(deadline=None, suppress_health_check=[too_slow])"
    - "itertools.pairwise for monotonic-diff computation (RUF007 clean)"
  patterns:
    - "Mixed DTO container choice (Pattern 8): dataclass for ndarray-carrying Frame, Pydantic BaseModel(frozen=True, extra='forbid') for the other 5 DTOs via shared _FrozenModel base"
    - "Module-level _SCREAMING_SNAKE / SCREAMING_SNAKE numeric constants for every bound (CLAUDE.md rule 6, ruff PLR2004 clean)"
    - "Tiger-style fail-fast: out-of-range floats raise ValueError at function entry before any math.tan / math.exp call"
    - "Pure transforms returning new state via dataclasses.replace (immutable mutation)"
key_files:
  created:
    - "pastor_tracker/src/pastor_tracker/core/types.py"
    - "pastor_tracker/src/pastor_tracker/core/geometry.py"
    - "pastor_tracker/src/pastor_tracker/core/damping.py"
    - "pastor_tracker/tests/test_types.py"
    - "pastor_tracker/tests/test_geometry.py"
    - "pastor_tracker/tests/test_damping.py"
  modified: []
decisions:
  - "Frame uses @dataclass(frozen=True, slots=True) (NOT Pydantic) because numpy.ndarray under Pydantic v2 needs arbitrary_types_allowed=True and gives no real validation — dataclass is honest. The other 5 DTOs use Pydantic BaseModel(frozen=True, extra='forbid') for runtime range validation. (Pattern 8.)"
  - "Introduced ImageArray = npt.NDArray[np.uint8] type alias. Bare np.ndarray expanded to np.ndarray[Any, Any] under mypy --strict + disallow_any_explicit, breaking the build (Pitfall 2). Parameterising the alias resolves it without weakening the gate."
  - "Damper uses Holden's exact closed-form (pos_new = target + exp(-y*dt) * (j0 + j1*dt)) instead of PROMPT.md's semi-implicit Euler pseudocode. Holden is unconditionally stable for any dt; semi-implicit Euler is conditionally stable (dt < 2/omega) and would push the property test at tau=0.1 s, dt=1/30 s to the stability edge. RESEARCH.md Pattern 9 + Alternatives Considered."
  - "Hypothesis property tests use @settings(deadline=None, suppress_health_check=[HealthCheck.too_slow], max_examples=200 for geometry / 40 for damping). Deadline disabled per Pitfall 6 — first-call import + JIT warm-up routinely exceeds the 200 ms default and causes flake; max_examples=40 keeps the damping property test under 1 s while still sweeping the [0.1, 2.0] s tau band densely."
  - "All inputs validated at function entry (tiger-style). Out-of-range nx/fov/tau/dt raises ValueError before reaching math.atan / math.tan / math.exp — no NaN can leak downstream."
  - "Frame.image dataclass field is reassignment-frozen but cannot prevent in-place numpy buffer mutation. Documented in the module docstring; later perception stages copy when they need to write."
metrics:
  duration_minutes: 5
  completed_date: "2026-05-03"
  tasks_completed: 3
  tasks_total: 3
  files_created: 6
  files_modified: 0
  commits: 3
---

# Phase 01 Plan 03: Core Math + Frozen DTOs Summary

Pure-core math + DTO foundation shipped: 6 frozen DTOs (`Frame` dataclass carrying `ImageArray = npt.NDArray[np.uint8]`; `Detection`, `TrackedSubject`, `MotionState`, `FramingTarget`, `MotorCommand` as Pydantic v2 `BaseModel(frozen=True, extra="forbid")` via shared `_FrozenModel` base), `core/geometry.py` with pinhole `normalized_x_to_angle_deg` ↔ `angle_deg_to_normalized_x`, and `core/damping.py` with Holden's exact closed-form `CriticallyDampedFollower`. 19 new tests (8 types + 6 geometry + 5 damping), all green; full phase suite 30/30 green; `mypy --strict` clean across `core/`; ruff `ANN401,T201` gate clean; TEST-05 grep gate (no mocks against damping math) returns empty after comment-stripping.

## Objective

Phases 4 (perception), 5 (intent + control), 6 (pipeline), and 7 (UI) all import these modules. The math MUST be provably damped before any motor command can be issued. Without it every later phase would ship on sand. This plan landed the DTO contract, the FOV math contract, and the damping contract, and proved each gate fires under hypothesis property tests against the real implementations (TEST-05 forbids mocks of Kalman/damping math).

## What Was Built

### Task 1 — `core/types.py` + `tests/test_types.py` (commit `0519fb2`)

- 6 frozen DTOs in 98 lines:
  - `Frame` — `@dataclass(frozen=True, slots=True)` with `image: ImageArray` (the `npt.NDArray[np.uint8]` alias), `width: int`, `height: int`, `timestamp_ns: int`.
  - `Detection`, `TrackedSubject`, `MotionState`, `FramingTarget`, `MotorCommand` — Pydantic v2 models via `_FrozenModel(BaseModel)` base with `model_config = ConfigDict(frozen=True, extra="forbid")`. Every numeric field range-validated via `Field(ge=, le=)`.
  - `MotionIntent = Literal["moving_left", "moving_right", "dwelling", "indeterminate"]` typedef shared with Phase 5.
- 8 tests (105 lines) covering:
  - `test_frame_is_frozen_dataclass` — `dataclasses.FrozenInstanceError` on attribute reassignment.
  - `test_frame_replace_returns_new_instance` — documents the `dataclasses.replace` mutation path.
  - `test_detection_rejects_mutation` + `test_detection_rejects_out_of_range` — `ValidationError` on assignment + on construction with `subject_center_x_normalized=1.5`.
  - `test_tracked_subject_rejects_mutation`, `test_framing_target_rejects_mutation`, `test_motor_command_rejects_mutation` — frozen contract per DTO.
  - `test_motion_state_rejects_mutation_and_bad_intent` — frozen contract + `Literal` rejection of an unknown intent string.

### Task 2 — `core/geometry.py` + `tests/test_geometry.py` (commit `d29ec88`)

- Pure pinhole FOV math (61 lines):
  - `normalized_x_to_angle_deg(nx, fov_deg) -> deg` via `degrees(atan((2*nx - 1) * tan(radians(fov_deg/2))))`.
  - `angle_deg_to_normalized_x(angle_deg, fov_deg) -> [0,1]` as the analytic inverse.
  - Both raise `ValueError` on `nx ∉ [0,1]` or `fov ∉ (0, 180)` BEFORE calling `math.tan` (tiger-style fail-fast — no NaN leak).
  - Module-level `NORMALIZED_X_MIN/MAX`, `FOV_DEG_MIN/MAX_EXCLUSIVE`, `HALF`, `TWO`, `NORMALIZED_RANGE` constants — ruff PLR2004 clean.
- 6 tests (108 lines):
  - `test_normalized_angle_roundtrip` — hypothesis sweep `nx ∈ [0,1]`, `fov ∈ (1e-3, 180-1e-3)`, 200 examples, asserts round-trip identity within `1e-9`.
  - `test_centre_maps_to_zero` — `nx=0.5` always maps to `0°` for `fov ∈ [1, 170]`.
  - `test_edges_map_to_half_fov` — `nx=0.0 → -fov/2`, `nx=1.0 → +fov/2` within `1e-7`.
  - `test_default_fov_70_known_values` — fixed spot-check at `fov=70°`: `0.5→0`, `1.0→35`, `0.0→-35`.
  - `test_invalid_normalized_x_raises`, `test_invalid_fov_raises` — `ValueError` on `nx=1.5` and on `fov ∈ {0.0, 180.0}` (boundary closed-out).

### Task 3 — `core/damping.py` + `tests/test_damping.py` (commit `a4ddcd5`)

- Holden's exact closed-form spring-damper (76 lines):
  - `FollowerState(position, velocity)` — frozen dataclass with `slots=True`.
  - `CriticallyDampedFollower(time_constant_sec)` — frozen dataclass; `__post_init__` rejects non-positive `tau`.
  - `step(state, target, dt) -> FollowerState` — pure transform; rejects non-positive `dt`; returns a new `FollowerState` via `dataclasses.replace`.
  - Update equations:
    - `halflife = tau * ln(2)`
    - `damping = 4*ln(2) / halflife`
    - `y = damping * 0.5`
    - `j0 = position - target`; `j1 = velocity + j0*y`
    - `decay = exp(-y*dt)`
    - `new_position = target + decay * (j0 + j1*dt)`
    - `new_velocity = decay * (velocity - j1*y*dt)`
  - NO PID terms (`integral|kp|ki|kd|pid`) and NO EMA terms anywhere in code (comment-stripped grep returns 0 — only the docstring's "No PID. No EMA." line matches, which is intentional negative documentation).
- 5 tests (105 lines):
  - `test_critically_damped_no_overshoot` — hypothesis sweep `tau ∈ [0.1, 2.0]` s, 40 examples, dt=1/60 s, horizon=`max(10*tau/dt, 600)`. Three assertions:
    1. `max(positions) <= 1.0 + 1e-9` (no overshoot).
    2. `pairwise(positions)` all non-decreasing within `1e-9` (monotonic from rest below target).
    3. `|positions[5*tau/dt] - 1.0| < 0.05` (settled within 5% by 5τ).
  - `test_zero_or_negative_time_constant_rejected` — `ValueError` on `tau ∈ {0.0, -0.5}`.
  - `test_zero_or_negative_dt_rejected` — `ValueError` on `dt ∈ {0.0, -0.01}`.
  - `test_at_target_stays_at_target` — running 120 steps at `position=target=1.0, velocity=0.0` keeps `position=1.0, velocity=0.0` to `1e-12`.
  - `test_known_tau_06_settles_within_5pct_by_3sec` — deterministic spot-check using the planned `Config.pan_time_constant_sec=0.6 s` default: 95%+ of target after 3 s (5τ).

## Wave 2 Verification (all green)

| Gate | Command | Exit | Result |
|------|---------|------|--------|
| Types tests | `pytest tests/test_types.py -x -ra` | 0 | 8 passed |
| Geometry tests | `pytest tests/test_geometry.py -x -ra` | 0 | 6 passed |
| Geometry property test (named) | `pytest tests/test_geometry.py::test_normalized_angle_roundtrip -x` | 0 | 1 passed (200 hypothesis examples) |
| Damping tests | `pytest tests/test_damping.py -x -ra` | 0 | 5 passed |
| Damping property test (named) | `pytest tests/test_damping.py::test_critically_damped_no_overshoot -x` | 0 | 1 passed (40 hypothesis examples) |
| Full phase suite | `pytest tests/ -ra` | 0 | **30 passed in 1.59 s** (10 config + 1 logging + 8 types + 6 geometry + 5 damping) |
| Lint (core only) | `ruff check src/pastor_tracker/core` | 0 | All checks passed |
| Lint (CORE-04 — ANN401, T201) | `ruff check src/pastor_tracker/core --select ANN401,T201` | 0 | All checks passed (no `Any`, no `print`) |
| Lint (full src) | `ruff check src` | 0 | All checks passed |
| Lint (tests) | `ruff check tests/test_types.py tests/test_geometry.py tests/test_damping.py` | 0 | All checks passed |
| Type (core --strict) | `mypy src/pastor_tracker/core --strict` | 0 | no issues found in 4 source files |
| Type (full src) | `mypy src` | 0 | no issues found in 13 source files |
| Core has no dirty-edge imports | `grep -RnE '^(import\|from) (cv2\|serial\|dearpygui\|asyncio\|pastor_tracker\.(io\|perception\|intent\|control\|ui))' src/pastor_tracker/core` | 0 matches | core/ stays pure |
| TEST-05 grep gate (comment-stripped) | comment+docstring-stripped match for `Mock.*[Dd]ampi`, `Mock.*[Kk]alman`, `unittest.mock`, `MagicMock` in `tests/test_damping.py` | 0 matches | clean (only docstring mention is the one asserting absence) |
| PID/EMA gate (comment-stripped) | comment+docstring-stripped match for `\b(integral\|kp\|ki\|kd\|pid\|ema\|exponential_moving_average)\b` in `core/damping.py` | 0 matches | clean (only docstring mentions are the "No PID. No EMA." negative documentation) |
| Frame frozen dataclass marker | `grep -cE '@dataclass\(frozen=True, slots=True\)' src/pastor_tracker/core/types.py` | — | 1 |
| `frozen=True` configured (Pydantic + Frame) | `grep -c 'frozen=True' src/pastor_tracker/core/types.py` | — | 2 (one for `_FrozenModel.model_config`, one for `Frame` dataclass) |
| Damper uses `math.exp` (Holden closed-form) | `grep -c 'math.exp' src/pastor_tracker/core/damping.py` | — | 1 |
| Geometry uses `math.atan` (pinhole) | `grep -c 'math.atan' src/pastor_tracker/core/geometry.py` | — | 1 |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 — Blocking] mypy `disallow_any_explicit` fired on `Frame.image: np.ndarray`**

- **Found during:** Task 1 verification (`mypy src/pastor_tracker/core/types.py`)
- **Issue:** `mypy --strict` + `disallow_any_explicit = true` (Plan 01-01 policy) raised `error: Explicit "Any" is not allowed [explicit-any]` on the `Frame` dataclass declaration. Root cause: bare `np.ndarray` (Pitfall 2 in 01-RESEARCH.md) is the unparameterised generic alias `np.ndarray[Any, Any]`, which the dataclass `__init__` synthesizer then exposes via `Any`.
- **Fix:** Introduced `ImageArray = npt.NDArray[np.uint8]` at module scope and changed `Frame.image: np.ndarray` → `Frame.image: ImageArray`. Documented Pitfall 2 inline so future maintainers don't revert it.
- **Verification:** `mypy src/pastor_tracker/core --strict` → "Success: no issues found in 4 source files".
- **Files modified:** `pastor_tracker/src/pastor_tracker/core/types.py`
- **Commit:** `0519fb2` (folded into Task 1 because the alias is the enabling fix for the DTO).

**2. [Rule 3 — Blocking] ruff RUF007 forbids `zip(seq, seq[1:])` for pairwise iteration**

- **Found during:** Task 3 verification (`ruff check tests/test_damping.py`)
- **Issue:** Initial `diffs = [b - a for a, b in zip(positions, positions[1:], strict=False)]` triggered `RUF007 Prefer itertools.pairwise() over zip() when iterating over successive pairs` — modern lint policy.
- **Fix:** Imported `from itertools import pairwise` and switched to `diffs = [b - a for a, b in pairwise(positions)]`. Same semantics, idiomatic.
- **Verification:** `ruff check tests/test_damping.py` → "All checks passed!"
- **Files modified:** `pastor_tracker/tests/test_damping.py`
- **Commit:** `a4ddcd5` (folded into Task 3).

**3. [Rule 3 — Lint] ruff `I001` import-block reorder in `tests/test_geometry.py`**

- **Found during:** Task 2 verification.
- **Issue:** `from hypothesis import HealthCheck, given, settings, strategies as st` — ruff's `I001` requires `strategies as st` to be its own import line (mixing `import x as y` with bare imports in the same statement is a sort-stability issue).
- **Fix:** `ruff check tests/test_geometry.py --fix` split it into two import lines.
- **Verification:** `ruff check src/pastor_tracker/core/geometry.py tests/test_geometry.py` → "All checks passed!"
- **Files modified:** `pastor_tracker/tests/test_geometry.py`
- **Commit:** `d29ec88` (folded into Task 2).

### Authentication gates

None — purely local Python + Pydantic + hypothesis + numpy. No external services touched.

## Key Decisions

1. **Mixed DTO container choice (Pattern 8)** — `Frame` as `dataclass(frozen=True, slots=True)`, the other 5 DTOs as Pydantic `BaseModel(frozen=True, extra="forbid")` via shared `_FrozenModel` base. Rationale: ndarray fields need `arbitrary_types_allowed=True` under Pydantic v2 (no real validation), so dataclass is the honest choice for `Frame`; the other DTOs benefit from Pydantic's `Field(ge=, le=)` range validation as a contract-on-construction surface for downstream phases.
2. **`ImageArray = npt.NDArray[np.uint8]` type alias** (Deviation #1) — surgical mitigation for Pitfall 2. Keeps `disallow_any_explicit` strict instead of weakening it.
3. **Holden's exact closed-form damper, NOT semi-implicit Euler** — RESEARCH.md Pattern 9. Holden is unconditionally stable for any `dt`; the PROMPT.md pseudocode (semi-implicit Euler) is only stable for `dt < 2/omega`. At `tau=0.1 s` the natural angular frequency is `omega = 2*ln(2)/halflife ≈ 20 rad/s`, giving stability boundary `dt < 0.1 s`; the property test at 30 Hz (`dt=0.033 s`) is well within that, but at the corner case `tau=0.1 s` it is uncomfortably close to the edge. Closed-form sidesteps the question entirely.
4. **Hypothesis settings: `deadline=None, suppress_health_check=[HealthCheck.too_slow], max_examples=40` (damping) / 200 (geometry)** — Pitfall 6. First-call NumPy + math import warm-up routinely exceeds the default 200 ms deadline and produces flaky CI output. The 40-example damping cap keeps total runtime under 1 s while sweeping the entire `tau ∈ [0.1, 2.0]` band densely (Δτ ≈ 0.05 s).
5. **Tiger-style fail-fast guards at every function entry** — `normalized_x_to_angle_deg` rejects `nx ∉ [0,1]` BEFORE calling `math.tan`; `step()` rejects `dt ≤ 0` BEFORE calling `math.exp`; `__post_init__` rejects `tau ≤ 0` so a misconstructed follower cannot exist. No NaN can ever propagate to the motor.
6. **`Frame.image` reassignment-frozen, not buffer-frozen** — documented in module docstring. Pure-core stages never write into the buffer; perception copies on intent to write. Acceptable trade-off for the ergonomics of holding ~6 MB ndarrays without round-trips through Pydantic copy machinery.

## Stub / Threat Notes

### Known Stubs

None. Every module ships its complete declared functionality:
- `core/types.py` — all 6 DTOs are concrete and consumed-ready; no fields default to placeholders.
- `core/geometry.py` — both functions are full implementations (no `raise NotImplementedError`, no TODO).
- `core/damping.py` — `CriticallyDampedFollower.step` is the real Holden update; no scaffold body.
- All 19 new tests assert real behaviour against real instances — no mocks, no fakes (TEST-05 grep gate empty).

### Threat Surface

All 7 STRIDE rows in the plan's `<threat_model>` are mitigated by the implementation:

| Threat ID | Mitigation in shipped code |
|-----------|----------------------------|
| T-1.03-01 (out-of-range nx/fov to geometry) | `raise ValueError` guards at top of both geometry functions; reject before `math.tan` / `math.atan` can produce `inf` |
| T-1.03-02 (negative/zero `tau` to damper) | `__post_init__` raises `ValueError`; `test_zero_or_negative_time_constant_rejected` proves the gate fires |
| T-1.03-03 (negative `dt` in `step`) | Guard at `step()` entry; `test_zero_or_negative_dt_rejected` proves it |
| T-1.03-04 (mutated DTO carrying stale state) | `dataclass(frozen=True)` for `Frame` + `FollowerState` + `CriticallyDampedFollower`; `BaseModel(frozen=True)` for all 5 Pydantic DTOs; documented `dataclasses.replace` / `model_copy(update=...)` mutation paths; 7 mutation-rejection tests |
| T-1.03-05 (mocked damping math hides real bugs) | TEST-05 grep gate passes (comment-stripped, 0 matches for `Mock.*[Dd]ampi\|unittest.mock\|MagicMock`); test imports the real `CriticallyDampedFollower` and steps it 600+ times per hypothesis example |
| T-1.03-06 (hypothesis flake at extreme `tau`) | `min_value=0.1, max_value=2.0` matches spec; `@settings(deadline=None, suppress_health_check=[too_slow])` per Pitfall 6 |
| T-1.03-07 (PID re-introduction) | Comment-stripped grep against `core/damping.py` for `\b(integral\|kp\|ki\|kd\|pid)\b` returns 0; module docstring's "No PID. No EMA." line is intentional negative documentation |

No new threat surface beyond the registered model. No I/O, no network, no shell-out, no schema changes at trust boundaries.

## Self-Check: PASSED

Files created (all exist):

- `pastor_tracker/src/pastor_tracker/core/types.py` (98 lines, ≥ 80) — FOUND
- `pastor_tracker/src/pastor_tracker/core/geometry.py` (61 lines, ≥ 25) — FOUND
- `pastor_tracker/src/pastor_tracker/core/damping.py` (76 lines, ≥ 40) — FOUND
- `pastor_tracker/tests/test_types.py` (105 lines) — FOUND
- `pastor_tracker/tests/test_geometry.py` (108 lines) — FOUND
- `pastor_tracker/tests/test_damping.py` (105 lines) — FOUND

Commits exist on `fresh-2026`:

- `0519fb2 feat(01-03): add 6 frozen DTOs in core/types.py with mutation-rejection tests` — FOUND
- `d29ec88 feat(01-03): add pinhole FOV math in core/geometry.py with hypothesis property tests` — FOUND
- `a4ddcd5 feat(01-03): add Holden critically-damped follower in core/damping.py with step-response tests` — FOUND

All 10 prompt-level must_haves green. All 7 plan-level must_haves green. Phase 01 plan 03 complete; Phase 01 fully done (3/3 plans).
