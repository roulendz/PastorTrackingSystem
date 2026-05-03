---
phase: 01-scaffold-config-core-math
reviewed: 2026-05-03T00:00:00Z
depth: standard
iteration: 2
files_reviewed: 24
files_reviewed_list:
  - pastor_tracker/src/pastor_tracker/__init__.py
  - pastor_tracker/src/pastor_tracker/__main__.py
  - pastor_tracker/src/pastor_tracker/logging_config.py
  - pastor_tracker/src/pastor_tracker/config.py
  - pastor_tracker/src/pastor_tracker/core/__init__.py
  - pastor_tracker/src/pastor_tracker/core/types.py
  - pastor_tracker/src/pastor_tracker/core/geometry.py
  - pastor_tracker/src/pastor_tracker/core/damping.py
  - pastor_tracker/src/pastor_tracker/io/__init__.py
  - pastor_tracker/src/pastor_tracker/perception/__init__.py
  - pastor_tracker/src/pastor_tracker/intent/__init__.py
  - pastor_tracker/src/pastor_tracker/control/__init__.py
  - pastor_tracker/src/pastor_tracker/ui/__init__.py
  - pastor_tracker/tests/__init__.py
  - pastor_tracker/tests/conftest.py
  - pastor_tracker/tests/test_logging.py
  - pastor_tracker/tests/test_config.py
  - pastor_tracker/tests/test_types.py
  - pastor_tracker/tests/test_geometry.py
  - pastor_tracker/tests/test_damping.py
  - pastor_tracker/tests/fixtures/__init__.py
  - pastor_tracker/tests/fixtures/_lint_canary.py
  - pastor_tracker/pyproject.toml
  - pastor_tracker/.pre-commit-config.yaml
findings:
  critical: 0
  warning: 0
  info: 0
  total: 0
status: clean
---

# Phase 1: Code Review Report (Iteration 2)

**Reviewed:** 2026-05-03
**Depth:** standard
**Iteration:** 2 (re-review of iter-1 fixes)
**Files Reviewed:** 24
**Status:** clean

## Summary

Iter-1 closed all six in-scope findings (CR-01 + WR-01..WR-05). I re-verified each
fix against the iter-1 backup at `01-REVIEW.iter2.md` and audited every newly
introduced construct (named-constant additions, `Frame.__post_init__` validators,
`Detection._bbox_well_ordered` model_validator, `gt=0` config bounds, geometry
boundary-tolerance constant). All fixes land cleanly. No regressions against
CLAUDE.md forbiddens. No new defects introduced by the fix code.

## Verified Fixes

| ID    | Finding                                          | Iter-1 fix location                                                                 | Verdict |
|-------|--------------------------------------------------|-------------------------------------------------------------------------------------|---------|
| CR-01 | `angle_deg_to_normalized_x` no input validation  | `geometry.py:72-79` — tolerant guard with `_HALF_FOV_BOUNDARY_TOL_DEG = 1e-9` const | closed  |
| WR-01 | `_TIME_CONSTANT_MAX_SEC` reused for velocity     | `config.py:56, 132, 136` — split into `_VELOCITY_THRESHOLD_MAX_NORM_PER_SEC = 1.0`  | closed  |
| WR-02 | `Frame.width/height` not tied to `image.shape`   | `types.py:62-84` — `__post_init__` with named axis constants (`_IMAGE_*`)           | closed  |
| WR-03 | `Detection.bbox` x2/y2 ordering not enforced     | `types.py:110-122` — `model_validator(mode="after")` with strict `<=`                | closed  |
| WR-04 | `configure_logging` false idempotency claim      | `logging_config.py:14-23` — docstring rewritten to honest one-shot contract         | closed  |
| WR-05 | `command_min_interval_ms` allows zero            | `config.py:176-177` — both `command_*` fields tightened to `gt=0`                   | closed  |

## Audit of New Code Introduced by Iter-1 Fixes

**1. `_HALF_FOV_BOUNDARY_TOL_DEG = 1e-9` (`geometry.py:29`)** — Tolerance value is
well-justified in the docstring (eight orders of magnitude below the smallest
"real" angle the app handles, the 0.2 deg dispatcher delta). The post-condition
relaxation it introduces is bounded: with fov=70 deg and the worst-case input
`half_fov + 1e-9`, the returned normalized-x exceeds 1.0 by ~2e-11 — well below
the test suite's `ROUNDTRIP_TOL = 1e-9` and far below any downstream consumer's
sensitivity. Hypothesis round-trip (200 examples) still green. No defect.

**2. `Frame.__post_init__` axis constants (`types.py:36-41`)** — All five
`_IMAGE_*` constants are referenced in the validator body (channel-count check
at line 68, height/width axis access at 73-74, ndim check at 63). The `ndim`
check fires first, so the subsequent `.shape[CHANNEL_AXIS]` access cannot hit
`IndexError` on a 2-D array — the test `test_frame_rejects_non_bgr_image`
exercises both the 2-D and 4-channel paths and matches the right error message
("HxWx3 BGR" appears in the ndim error string, "channels" in the channels
error string).

**3. `Detection._bbox_well_ordered` (`types.py:110-122`)** — Strict `<=` is
correct (rejects degenerate zero-area bboxes per the new test
`test_detection_rejects_zero_area_bbox`). X check runs before Y, so the
inverted-Y test correctly arranges valid-X + inverted-Y to reach the Y branch.
Mirrors the existing `Config._max_above_min` pattern. Returns `self` per
Pydantic v2 model_validator contract.

**4. `_VELOCITY_THRESHOLD_MAX_NORM_PER_SEC = 1.0` (`config.py:56`)** — Value
is conservative (one full frame width per second is well above the PROMPT.md
default 0.08 norm/sec motion threshold and the 0.03 dwell threshold), and the
inline comment names the unit-confusion failure mode that drove the split.
Time-constant fields correctly retain the original `_TIME_CONSTANT_MAX_SEC`.

**5. `command_min_delta_deg: gt=0.0` (`config.py:176`)** — Tightened beyond
the iter-1 review's strict requirement (review noted it was "less acute
because the deadband backstops it"). Symmetry with `command_min_interval_ms`
is defensible. New test `test_command_min_delta_zero_rejected` pins it.

**6. `logging_config.configure_logging` docstring (`logging_config.py:14-23`)** —
Now correctly names *both* root causes (`logging.basicConfig` no-op semantics
AND `cache_logger_on_first_use=True` freezing previously-bound wrappers) and
defers the runtime-slider reconfigure path to Phase 7. No behaviour change,
no test impact.

## Regression Sweep — CLAUDE.md Forbiddens

| Forbidden                                            | Status                                                                  |
|------------------------------------------------------|-------------------------------------------------------------------------|
| `print()` in `src/`                                  | clean (only in `tests/fixtures/_lint_canary.py`, ruff-excluded)         |
| Bare `except:` / `except Exception: pass`            | clean (only in lint canary)                                             |
| `Any` in src                                         | clean (no new `Any`; pre-existing test-only `Any` not introduced now)   |
| `time.sleep()` in main loop                          | clean (no `time.sleep` anywhere in src)                                 |
| Magic numbers in core math                           | clean (all new validators use named module-level constants)             |
| PID / EMA / MediaPipe references                     | clean (no introductions)                                                |
| `core/` importing `cv2`/`serial`/`dearpygui`/`asyncio`/sibling subpackages | clean (`core/types.py` imports only `numpy`+`pydantic`; `core/geometry.py` only `math`; `core/damping.py` only `math`+`dataclasses`) |
| Mocked Kalman/damping in tests                       | clean (`test_damping.py` uses real `CriticallyDampedFollower`)          |
| Commented-out code                                   | clean                                                                   |
| TODO without issue number                            | clean (the "Phase 7" reference in logging docstring is a forward-pointer to a planned phase, not a TODO) |

## Standard-Depth Checks

- **Type-hint completeness:** all functions/methods annotated. mypy `--strict` +
  `disallow_any_explicit` already enforced via `pyproject.toml`. No new
  signatures introduced by iter-1 lack annotations.
- **Error handling:** every new validator path uses tiger-style `raise
  ValueError(...)` with informative messages naming both the field and the
  bad value. No silent fallbacks added.
- **Pydantic v2 correctness:** `model_validator(mode="after")` on `Detection`
  is the right mode (post-construction cross-field check, returns `self`).
  `frozen=True` + `extra="forbid"` preserved on all DTOs.
- **Math correctness:** geometry forward + inverse round-trip property test
  still passes (200 hypothesis examples). Damping closed-form (Holden) is
  unchanged from iter-1; step-response property test still passes.
- **Config bounds vs PROMPT.md:** all 25 fields' defaults still match
  PROMPT.md `## Config` block. The two newly tightened bounds
  (`command_min_*` `gt=0`) are stricter than PROMPT.md (which doesn't specify
  a floor) but consistent with PROMPT.md `## Anti-jitter` requiring the
  throttle to be active.

## Known Out-of-Scope Items (carried from iter-1, deferred per fix_scope)

These remain Info-severity in the iter-1 backup and were explicitly deferred:
IN-01 (`_FrozenModel` pattern divergence between `config.py` and `core/types.py`),
IN-02 (`_DELTA_DEG_MAX = 10.0` ceiling could be tighter), IN-03 (`HALF = 0.5`
duplicated between `damping.py` and `geometry.py`), IN-04 (`configure_logging`
`level` could be `Literal[...]`), IN-05 (`test_loads_from_json_file` does not
pre-clear `PTS_*` env vars), IN-06 (`_lint_canary.py` cosmetic). None are
blockers; flag for Phase 7 or a future style-pass iteration.

---

_Reviewed: 2026-05-03_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
_Iteration: 2_
