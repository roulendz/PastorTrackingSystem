---
phase: 05-intent-and-control
reviewed: 2026-05-05T00:00:00Z
depth: standard
files_reviewed: 14
files_reviewed_list:
  - pastor_tracker/src/pastor_tracker/control/__init__.py
  - pastor_tracker/src/pastor_tracker/control/command_dispatcher.py
  - pastor_tracker/src/pastor_tracker/control/pan_controller.py
  - pastor_tracker/src/pastor_tracker/intent/__init__.py
  - pastor_tracker/src/pastor_tracker/intent/framer.py
  - pastor_tracker/src/pastor_tracker/intent/motion_analyzer.py
  - pastor_tracker/tests/fixtures/test_trajectories.py
  - pastor_tracker/tests/fixtures/trajectories.py
  - pastor_tracker/tests/test_command_dispatcher.py
  - pastor_tracker/tests/test_framer.py
  - pastor_tracker/tests/test_intent_control_pipeline.py
  - pastor_tracker/tests/test_logging.py
  - pastor_tracker/tests/test_motion_analyzer.py
  - pastor_tracker/tests/test_pan_controller.py
findings:
  blocker: 1
  warning: 6
  total: 7
status: issues_found
---

# Phase 5: Code Review Report

**Reviewed:** 2026-05-05
**Depth:** standard
**Files Reviewed:** 14
**Status:** issues_found

## Summary

Phase 5 implements four pure-core stages (MotionAnalyzer, Framer, PanController, CommandDispatcher) per the planning DTO contract. Engineering hygiene is generally strong: every threshold is Config-owned, structlog is used throughout, mypy/ruff-friendly types are consistent, and the tests use real damping/geometry without mocks. However, one BLOCKER undermines the project's core value of pan smoothness — the PanController `_hold()` path retains a stale upstream timestamp across None gaps, producing a multi-second `dt` on resume that bypasses the velocity clamp and emits a single-frame angle jump (full FOV span possible). Several WARNINGs cover defensive-coding gaps and inconsistencies in test discipline.

## Blockers

### BL-01: PanController emits unclamped multi-degree jump after a long None-upstream gap

**File:** `pastor_tracker/src/pastor_tracker/control/pan_controller.py:113-148, 150-158, 160-172`
**Issue:**
`_hold()` (line 150-158) deliberately does NOT clear `_state` (D-07 design), but it ALSO does not clear `_last_upstream_ts_ns`. When a None-upstream gap of duration `T_gap` ends and a fresh `FramingTarget` arrives, `_compute_dt_sec()` computes
`dt_sec = (new_upstream_ts_ns - last_real_frame_ts_ns) / 1e9 = T_gap`.

Two bad consequences cascade:
1. The Holden damper update collapses: with `pan_tau = 0.6 s` and `T_gap = 5 s`, `decay = exp(-(4 ln 2 / (0.6 ln 2)) * 0.5 * 5) ≈ 5.4e-8`, so `new_position ≈ target` in a single step — i.e. the damper **snaps** to the new target with no smoothing.
2. The velocity clamp does not save us: `max_delta = pan_max_velocity_deg_per_sec * dt_sec`. With default `vmax = 30 deg/s` and `T_gap = 5 s`, `max_delta = 150 deg` — larger than the entire 70° FOV span. The clamp never engages, and the controller emits a single-frame jump that may exceed 60°.

This violates CLAUDE.md `Core value: No overshoot, no oscillation, no lock-loss…, no audible motor jerk` and PROMPT.md anti-jitter intent. The Framer's symmetric `_compute_dt_sec` is safe because `_reset()` clears `_last_upstream_ts_ns` to `None` (framer.py:128-132), forcing the next call to use the `1/capture_fps` floor. PanController's documented choice to retain damper state across the gap (lines 152-157) is correct, but `_last_upstream_ts_ns` is a separate concern: it is upstream wall-clock evidence, not damper state, and must not survive a known gap.

Test coverage gap: `test_hold_does_not_advance_damper` (test_pan_controller.py:321) only exercises 5 hold ticks at consecutive monotonic timestamps (~200 ms gap) AND resumes with the SAME target — both choices hide the bug. `test_none_upstream_clean_propagation` (test_intent_control_pipeline.py:266) only tests a single None tick with no following resume, and verifies dispatcher state stability rather than controller post-resume behaviour.

**Fix:**
Treat `_last_upstream_ts_ns` as gap-aware in `_hold()`: clear it so the next real frame uses the `1/capture_fps` floor (one-frame `dt`), preserving the FollowerState while preventing the damper from "fast-forwarding" through an absent gap.

```python
def _hold(self) -> float | None:
    """Hold-on-None (D-07).

    Preserves damper FollowerState (no re-seed transient) BUT clears
    _last_upstream_ts_ns so the next real frame computes dt from the
    capture-fps floor, not the wall-clock-sized gap. Without this clear,
    a long None gap produces a single-step "snap" to target on resume
    because (a) damper decay collapses and (b) velocity clamp scales with
    dt and stops binding.
    """
    self._last_upstream_ts_ns = None
    return self._last_emitted_angle_deg
```

Add a regression test:
```python
def test_long_none_gap_then_new_target_does_not_snap(
    valid_config_dict: dict[str, object],
) -> None:
    controller = _make_pan_controller(valid_config_dict)
    seed = asyncio.run(controller.consume(_ft(0.5, _T0_NS), now_ns=_T0_NS))
    assert seed is not None
    # 5-second None gap.
    long_gap_ts = _T0_NS + 5 * 1_000_000_000
    asyncio.run(controller.consume(None, now_ns=long_gap_ts))
    # New target at the opposite FOV edge.
    resume_ts = long_gap_ts + _DT_30HZ_NS
    resumed = asyncio.run(controller.consume(_ft(1.0, resume_ts), now_ns=resume_ts))
    assert resumed is not None
    vmax = float(valid_config_dict["pan_max_velocity_deg_per_sec"])
    max_one_step_jump = vmax * _DT_30HZ_SEC + _CLAMP_VERIFICATION_TOL
    assert abs(resumed - seed) <= max_one_step_jump, (
        f"jump {abs(resumed - seed)} after None gap exceeds vmax*dt {max_one_step_jump}"
    )
```

## Warnings

### WR-01: `Framer._intent_to_target("indeterminate")` branch is unreachable yet retained

**File:** `pastor_tracker/src/pastor_tracker/intent/framer.py:78-86, 134-148`
**Issue:**
`consume()` returns early on `motion is None or motion.intent == "indeterminate"` (line 78), then calls `_intent_to_target(motion.intent)` (line 81) and immediately asserts `target is not None`. The `case "indeterminate": return None` arm at line 143-144 is therefore unreachable. CLAUDE.md forbids dead code paths. The `case _:` raising `IntentError` is the correct exhaustiveness guard for unknown literals; the explicit `indeterminate -> None` is redundant and obscures the actual contract (function never returns `None` to callers).

**Fix:**
Remove the indeterminate arm and tighten the return type:
```python
@staticmethod
def _intent_to_target(intent: MotionIntent) -> float:
    match intent:
        case "moving_right":
            return _TARGET_LEFT_THIRD
        case "moving_left":
            return _TARGET_RIGHT_THIRD
        case "dwelling":
            return _TARGET_CENTER
        case _:
            # "indeterminate" is filtered upstream in consume(); any
            # other value indicates a Literal extension that wasn't
            # propagated here.
            raise IntentError(
                f"unhandled MotionIntent in _intent_to_target: {intent!r}"
            )
```
Also drop the now-unnecessary `assert target is not None` at framer.py:83.

### WR-02: `MotionAnalyzer._classify` returns stale intent in the dead-band between dwell and motion thresholds

**File:** `pastor_tracker/src/pastor_tracker/intent/motion_analyzer.py:122-162`
**Issue:**
When `dwell_thr <= |vx| <= move_thr` (default config: `0.05 <= |vx| <= 0.08`), all three timers are reset on each frame and `_classify` falls through to `return self._current_intent`. If the prior intent was `moving_right` (timer matured), the analyzer continues reporting `moving_right` indefinitely while `vx` sits in the dead-band, even though the operator is no longer crossing the move threshold. Symmetric bug for `moving_left` and `dwelling`. There is no Config-owned exit timer for "neither move-sustained nor dwell-sustained".

The behaviour is consistent with the design intent (avoid chatter), but the docstring at line 21-29 promises "intent flips when continuous duration >= configured hysteresis / dwell-duration window" without acknowledging the sticky-until-opposite-condition-matures path. There is no test covering this transition.

**Fix:**
Either (a) document the sticky semantics in the module docstring and add a test that asserts a `moving_right` intent persists through a sustained dead-band run until either the dwell or opposite-direction timer matures; or (b) add an opposite-condition timer (e.g. release `moving_right` after `motion_hysteresis_sec` of `vx <= move_thr`) — which would require a new Config field. Option (a) is the lower-risk choice unless field testing reveals lingering "ghost moves".

### WR-03: `MotionAnalyzer._classify` does not clamp negative `dwell_threshold` semantics

**File:** `pastor_tracker/src/pastor_tracker/intent/motion_analyzer.py:139-143`
**Issue:**
The check `if abs(vx) < dwell_thr` is correct only when `dwell_thr > 0`. Config currently enforces `dwell_threshold_norm_per_sec > 0` (presumably via `gt=0.0`), but the analyzer does not assert this contractually. If a future Config tweak ever permitted `dwell_thr == 0`, the dwell timer would never start and `dwelling` could only be reached when `vx` is exactly 0.0 — silent degradation rather than fail-fast.

**Fix:**
Add a constructor-time guard (tiger-style, fail-loud at boundary):
```python
def __init__(self, config: Config) -> None:
    if config.dwell_threshold_norm_per_sec <= 0.0:
        raise ValueError(
            f"dwell_threshold_norm_per_sec must be > 0 for analyzer to "
            f"classify dwell, got {config.dwell_threshold_norm_per_sec}"
        )
    if config.motion_threshold_norm_per_sec <= config.dwell_threshold_norm_per_sec:
        raise ValueError(
            "motion_threshold_norm_per_sec must be > dwell_threshold_norm_per_sec "
            "to keep the dead band non-empty"
        )
    ...
```
(The second guard is also defensive — without it the `WR-02` dead-band reduces to zero and a single `vx` value can satisfy both `> move_thr` and `< dwell_thr`, making classification non-deterministic.)

### WR-04: PanController velocity clamp uses asymmetric clip without preserving damper velocity sign

**File:** `pastor_tracker/src/pastor_tracker/control/pan_controller.py:113-134`
**Issue:**
When the clamp engages, line 127 sets `new_state = replace(new_state, position=prev_position + clipped)` — but the damper's `velocity` field is left untouched at the unclamped value. The next `damper.step` call uses this stale velocity to advance from the clamped position, which biases the next-frame trajectory. The Holden update `new_velocity = decay * (state.velocity - j1 * y * dt)` will partially correct this, but for sustained over-velocity drive (the exact regime the clamp targets), the residual energy in `velocity` continues to push the unclamped-position estimate forward. This is mild — `test_velocity_clamp_overwrites_state` proves the per-step delta is bounded — but the velocity-state semantics are inconsistent with the position-state semantics promised by D-09 ("anti-windup by overwriting FollowerState.position").

**Fix:**
Either (a) re-derive `velocity` from the clipped position so the damper state remains physically consistent — e.g. `replace(new_state, position=..., velocity=clipped/dt_sec)` (matches the emitted angular velocity), or (b) document explicitly that only `position` is anti-windup'd and that `velocity` carries unclamped energy by design (with a citation to where this trade-off was decided).

### WR-05: Test `test_no_emission_uses_wall_clock` uses `# pragma: no cover` to mask coverage of dispatcher misbehaviour

**File:** `pastor_tracker/tests/test_command_dispatcher.py:291`
**Issue:**
The `_explode` closure at line 291 carries `# pragma: no cover - only called if dispatcher misbehaves`. While the comment is accurate, hiding code that exists to catch a regression from coverage reports prevents the coverage tool from telling future maintainers "this safety net was never invoked" if the test ever silently stops triggering. Combined with D-13's 100% coverage target, the pragma circumvents the very enforcement it was meant to support.

**Fix:**
Replace the no-op exception body with `pytest.fail(...)` (which gives a clean test failure) and remove the pragma — the function will then either be called (test fails as designed) or not (test passes), and coverage will reflect both states truthfully.
```python
def _explode() -> int:
    pytest.fail("dispatcher must not read time.perf_counter_ns (D-02)")
```

### WR-06: `valid_config_dict` is referenced but not declared as a fixture in this phase

**File:** `pastor_tracker/tests/test_command_dispatcher.py:44`, `pastor_tracker/tests/test_framer.py:41`, `pastor_tracker/tests/test_pan_controller.py:58`, `pastor_tracker/tests/test_motion_analyzer.py:48`, `pastor_tracker/tests/test_intent_control_pipeline.py:65`
**Issue:**
Every Phase-5 test takes `valid_config_dict: dict[str, object]` and passes `Config(**valid_config_dict)` with `# type: ignore[arg-type]`. The fixture must live in `conftest.py` (not under review here). Two risks: (1) reviewers reading these files in isolation cannot verify the fixture's contract — what fields does it set, and do they exercise non-default values? (2) every test passes the SAME dict by reference; any test mutating it (e.g. `cfg = dict(valid_config_dict); cfg["pan_deadband_deg"] = 0.0` at test_pan_controller.py:178) is fine, but a `valid_config_dict.update(...)` would silently leak state across tests. Worth documenting fixture immutability in `conftest.py` (out of scope) AND in a module-level test-file note.

**Fix:**
Add a brief docstring near each `_make_*` factory clarifying the contract:
```python
def _make_pan_controller(valid_config_dict: dict[str, object]) -> PanController:
    """Build a PanController. ``valid_config_dict`` is the conftest-owned
    Config dict; tests that need overrides should ``dict(...)``-copy first
    (NEVER mutate in place — pytest fixture is shared across tests)."""
    return PanController(Config(**valid_config_dict))  # type: ignore[arg-type]
```

### WR-07: Unused import in test_command_dispatcher

**File:** `pastor_tracker/tests/test_command_dispatcher.py:18`
**Issue:**
`import time` at line 18 is used only inside `test_no_emission_uses_wall_clock` (line 294). When that test runs without the wall-clock prohibition assertion (e.g. coverage collection that skips the explode path), the import is technically still used by `monkeypatch.setattr(time, ...)`. This is benign; flagging it because ruff `--select F401` might surface false positives if module-level import discipline is later tightened, and to note that a similar pattern in test_framer.py (line 14) is intentional.

**Fix:**
None required — note for future reference. If ruff complains, scope the import to the function with a `# noqa: PLC0415`.

---

_Reviewed: 2026-05-05_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
