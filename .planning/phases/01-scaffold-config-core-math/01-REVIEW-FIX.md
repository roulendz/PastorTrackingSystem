---
phase: 01-scaffold-config-core-math
fixed_at: 2026-05-03T00:00:00Z
review_path: .planning/phases/01-scaffold-config-core-math/01-REVIEW.md
iteration: 1
findings_in_scope: 6
fixed: 6
skipped: 0
status: all_fixed
---

# Phase 1: Code Review Fix Report

**Fixed at:** 2026-05-03
**Source review:** `.planning/phases/01-scaffold-config-core-math/01-REVIEW.md`
**Iteration:** 1

**Summary:**
- Findings in scope: 6 (CR-01 + WR-01..WR-05; Info IN-01..IN-06 deferred per scope)
- Fixed: 6
- Skipped: 0
- Test suite: 39/39 green (baseline 30 + 9 new regression tests)
- Lint: `ruff check src tests` — All checks passed
- Pre-commit `commitizen check`: Passed on every commit

## Fixed Issues

### CR-01: `angle_deg_to_normalized_x` performs no input validation on `angle_deg`

**Files modified:** `pastor_tracker/src/pastor_tracker/core/geometry.py`, `pastor_tracker/tests/test_geometry.py`
**Commit:** `748ef96`
**Applied fix:** Added a fail-fast guard `if angle_deg < -half_fov_deg - tol or angle_deg > half_fov_deg + tol: raise ValueError(...)` mirroring the symmetric guard already on the forward map. The guard required a small `_HALF_FOV_BOUNDARY_TOL_DEG = 1e-9` named constant (CLAUDE.md rule 6) because the forward map `atan(±1 * tan(half_fov))` is mathematically exactly `±half_fov_deg` but drifts by ~1 ULP for some FOV values — the property-based round-trip test feeds these drifted values straight back into the inverse, and a strict guard would falsely reject them. Tolerance of 1e-9 deg is eight orders of magnitude smaller than the smallest plausible real angle in this app (the 0.2° dispatcher delta), so no real out-of-domain caller is admitted. Added `test_invalid_angle_raises` covering 50°, 90°, 180°, -90° at fov=70° (all far outside the half-FOV bound). Hypothesis round-trip test still passes 200 examples.

### WR-01: `Config` reuses `_TIME_CONSTANT_MAX_SEC` as the ceiling for three velocity fields

**Files modified:** `pastor_tracker/src/pastor_tracker/config.py`
**Commit:** `0a3558b`
**Applied fix:** Introduced `_VELOCITY_THRESHOLD_MAX_NORM_PER_SEC: float = 1.0` (subject crosses the full frame in 1 s — well above any plausible pastor motion vs. the PROMPT.md default of 0.08 norm/sec) and rewired `motion_threshold_norm_per_sec` and `dwell_threshold_norm_per_sec` to use it. Time-constant fields (`motion_hysteresis_sec`, `dwell_duration_sec`, `framing_time_constant_sec`, `pan_time_constant_sec`) keep the original `_TIME_CONSTANT_MAX_SEC = 10.0`. Added an inline comment naming the unit-confusion failure mode CLAUDE.md rule 6 was written to prevent. No new tests needed — existing tests cover both still-valid defaults and still-rejected negatives.

### WR-02: `Frame.width` / `Frame.height` not validated against `image.shape`

**Files modified:** `pastor_tracker/src/pastor_tracker/core/types.py`, `pastor_tracker/tests/test_types.py`
**Commit:** `a7747f9`
**Applied fix:** Added `Frame.__post_init__` (the dataclass equivalent of a Pydantic validator) enforcing three invariants: image must be HxWx3 BGR (`ndim == 3` AND last axis == 3), `width`/`height` scalars must match `image.shape`, and `timestamp_ns >= 0` (matching the sibling Pydantic DTOs in the same module). All thresholds live in named module constants (`_IMAGE_NDIM_EXPECTED`, `_IMAGE_CHANNELS_EXPECTED`, `_IMAGE_HEIGHT_AXIS`, `_IMAGE_WIDTH_AXIS`, `_IMAGE_CHANNEL_AXIS`, `_TIMESTAMP_NS_MIN`) per CLAUDE.md rule 6. Added three tests (`test_frame_rejects_dimension_mismatch`, `test_frame_rejects_non_bgr_image` covering both the ndim==2 and channels==4 cases, `test_frame_rejects_negative_timestamp`) and updated the existing `test_frame_replace_returns_new_instance` to keep image and width coherent (since `dataclasses.replace` re-runs `__post_init__`, the old test that mutated `width` without a matching new image would now correctly fail).

### WR-03: `Detection.bbox_x2 > bbox_x1` and `bbox_y2 > bbox_y1` not enforced

**Files modified:** `pastor_tracker/src/pastor_tracker/core/types.py`, `pastor_tracker/tests/test_types.py`
**Commit:** `a2192e7`
**Applied fix:** Added a `model_validator(mode="after")` on `Detection` named `_bbox_well_ordered` that mirrors the existing `Config._max_above_min` pattern. Strict inequality (`<=`) so degenerate zero-area boxes are also rejected. Added three tests: `test_detection_rejects_inverted_bbox_x` (x1 > x2), `test_detection_rejects_inverted_bbox_y` (y1 > y2), and `test_detection_rejects_zero_area_bbox` (x1 == x2). The validator's docstring documents the new cross-field invariants on the class.

### WR-04: `configure_logging()` is not actually idempotent across differing levels

**Files modified:** `pastor_tracker/src/pastor_tracker/logging_config.py`
**Commit:** `60b34b2`
**Applied fix:** Took option A from the review (drop the false claim — simpler than option B's reconfigure rewrite, and idempotency is not a Phase 1 must_have). Replaced the misleading `"Idempotent."` docstring with an explicit one-shot contract that names *both* reasons the function cannot self-reconfigure (`logging.basicConfig` is a no-op once handlers exist; `cache_logger_on_first_use=True` freezes the wrapper on previously-bound loggers) and defers the runtime level-slider reconfigure path to Phase 7. No code or test changes — the existing single-call usage and test continue to work.

### WR-05: `command_min_interval_ms` allows zero

**Files modified:** `pastor_tracker/src/pastor_tracker/config.py`, `pastor_tracker/tests/test_config.py`
**Commit:** `d181e54`
**Applied fix:** Tightened both `command_min_interval_ms` and `command_min_delta_deg` from `ge=0` / `ge=0.0` to `gt=0` / `gt=0.0`. Added an inline comment naming the PROMPT.md `## Anti-jitter` four-mitigation contract (deadband / vel-clamp / **command throttle** / staleness-drop) so future readers see why the floor is strict. Added two tests: `test_command_min_interval_zero_rejected` and `test_command_min_delta_zero_rejected`. The review noted `command_min_delta_deg` is "less acute because the deadband backstops it" — tightened anyway for symmetry per the user's WR-05 watch-out instruction.

## Skipped Issues

None. All six in-scope findings (CR-01 + WR-01..WR-05) were fixed cleanly. Info findings (IN-01..IN-06) were out of scope for this run by user instruction (`fix_scope: critical_warning`).

## Verification Summary

| Tier | Check | Result |
|------|-------|--------|
| Tier 1 | Re-read every modified file post-edit | All clean |
| Tier 2 | `pytest tests` after each fix | Green per fix |
| Tier 2 | `ruff check src tests` after each fix | Clean (one RUF043 caught + repaired during WR-02; commit was made post-fix) |
| Tier 2 (final) | `pytest tests -ra` (full suite) | **39/39 passed** (baseline 30 + 9 new regression tests) |
| Tier 2 (final) | `ruff check src tests` (full) | **All checks passed** |
| Pre-commit | `commitizen check` on every commit | Passed (Conventional Commits enforced) |

Mypy reported four pre-existing `explicit-any` / `unused-ignore` errors in `tests/fixtures/_lint_canary.py`, `tests/test_types.py`, and `tests/test_config.py` — none in files this run modified meaningfully (added test methods follow existing patterns and do not introduce new `Any`). Per `verification_strategy`, pre-existing errors in unmodified locations are not gating.

## Known follow-ups (out of scope this iteration)

- **IN-01..IN-06** deferred (Info severity, fix_scope was critical_warning). IN-04 (`configure_logging` `Literal` tightening) is a natural pairing with WR-04 if Phase 7 wants the slider work; flag for next planning round.
- **WR-02** also added Frame image-shape validation that goes one step beyond the review suggestion (the review only mentioned width/height; the new guards also reject 2-D arrays and non-3-channel arrays). This is a stricter contract than reviewer asked for, but is consistent with CLAUDE.md rule 1 (validate every contract at the boundary) and prevents downstream "RGBA passed where BGR expected" bugs.

---

_Fixed: 2026-05-03_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
