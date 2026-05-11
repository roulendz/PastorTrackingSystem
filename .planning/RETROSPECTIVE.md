# Pastor Tracker — Living Retrospective

This document accumulates milestone retrospectives. Each milestone appends its section below; cross-milestone trends grow at the bottom.

---

## Milestone: v1.0 — Pastor Tracker MVP

**Shipped:** 2026-05-11
**Phases:** 8 | **Plans:** 26 | **Tests:** 561 passed, 1 skipped

### What Was Built

A Python 3.12 desktop application that auto-pans a stage camera to track a single primary speaker, driving an Arduino stepper over USB serial. Eight phases delivered: scaffold + frozen `Config` + pure-core math (CORE), Arduino driver with VID:PID auto-detect and watchdog recovery (IO-ARD), OBS Virtual Camera capture with stale-frame drop (IO-CAM), YOLO11-pose + BoT-SORT + 4-state Kalman perception, two-stage critically-damped pan control with rule-of-thirds framing, asyncio pipeline orchestrator with 6-state lifecycle, DearPyGui dashboard with live preview + tuning sliders + hotkeys, and an operator-facing README + ship-gate verification.

### What Worked

- **Pure-core / dirty-edges separation** held across all 8 phases. `core/` stayed side-effect-free and unit-testable to the end; downstream stages composed by pure transforms on frozen DTOs without back-channel state access.
- **Tiger-style fail-fast** caught real defects early: Pydantic `extra="forbid"` aborted boot on invalid configs; `Config(frozen=True)` made mid-session mutation impossible; `_FrozenModel` base prevented cross-stage state leak.
- **Two-stage damping (no PID)** validated in unit tests with hypothesis-property tests asserting no overshoot. Closed-form Holden integrator (`pos_new = target + exp(-y*dt)*(j0+j1*dt)`) is unconditionally stable for any `dt` — better than semi-implicit Euler.
- **Plan-checker pre-execution gate** caught a CONTEXT.md field-name typo (`camera_h_fov_deg` vs real `camera_horizontal_fov_deg`) before the README was written — would have caused boot-abort for operators copying README into `config.json`.
- **Two-commit phase-close pattern** (Phase 7 → Phase 8) gave reviewable atoms: one commit for the artifact, one for the verification record. Easy to revert either independently.
- **Worktree isolation FALLBACK to sequential** when worktree base mismatched (Windows file-lock during forced reset) — workflow recovered without losing work.

### What Was Inefficient

- **REQUIREMENTS.md traceability drift**: many `[ ]` boxes stayed unchecked for items that were demonstrably implemented and tested. The `phase.complete` CLI updated some but not all rows. Result: milestone audit had to manually reconcile against VERIFICATION.md + SUMMARY.md frontmatter.
- **`human_needed` cascade**: 7 of 8 phases ended with `verification_result: human_needed` solely because the on-stage smoke was deferred to Phase 8 QA-04. Each repeat carried no new information — a single hardware-dependent gate would have sufficed. Future milestones with hardware acceptance should hoist the gate to one explicit deferral instead of seven inherited ones.
- **mypy 1.20.2 wheel env regression** surfaced only at Phase 8 ship gate. The broken `mypy.expandtype.ExpandTypeVisitor` symbol crashed the pydantic.mypy plugin on test fixtures, producing 925 spurious `[arg-type]` errors. Earlier pinning of the mypy version (or per-phase `mypy --strict tests` in pre-commit) would have caught this in Phase 1 or 2 rather than at milestone close.
- **`ruff format --check` was never gated** in `.pre-commit-config.yaml`. By Phase 8 there were 73 files of accumulated format drift. Adding `ruff format --check` to pre-commit on day one would have prevented the debt.

### Patterns Established

- **Doc-grep validation**: every backtick-quoted config key in README must match `config.py`; every `--flag` must match `__main__.py` argparse. Caught two CONTEXT.md typos before execution.
- **Atomic two-commit phase close**: `docs(NN): <artifact>` + `docs(NN): record phase verification (<status>)` mirrors Phase 7's `d55c9d9` + `8aea0d8`. Reviewable in isolation.
- **Latched-error gate-ordering**: in `ArduinoMotor.send_motor_angle`, the latched-error check runs BEFORE the pause check, so a faulted+paused state still raises the typed exception (Phase 2 BLOCKER fix).
- **Test-fixture trajectory generators**: four pure deterministic helpers (`step`, `ramp`, `dwell_then_walk`, `borderline_chatter`) with 18 self-tests pin every helper's contract. Re-used across Phases 4-5.
- **Process-pool YOLO inference**: keeps the GIL out of the capture loop. PoseDetector + `_pose_worker` translate `Detection` DTOs out of the worker.
- **Surgical STATE.md edits over wholesale rewrites**: phase.complete uses targeted field updates; the executor adds specific Phase-NN tracking rows without disturbing surrounding history.

### Key Lessons

1. **Pin tooling versions in `pyproject.toml`**, not just dependency floors. `mypy>=1.20,<2.0` accepted a broken minor wheel; `mypy==1.19.x` would not have.
2. **Gate every formal check in pre-commit on day one**. Optional gates accumulate drift; mandatory gates self-correct.
3. **Hoist human-validation gates to one explicit phase** instead of letting them cascade. The milestone audit conflated 7 inherited `human_needed` statuses with 7 separate gates when there was really one.
4. **Caveman/concise communication mode dramatically reduced orchestrator token use** while preserving full technical accuracy. Useful for long autonomous loops.
5. **Workflow `--no-transition` correctly returned to the autonomous orchestrator** instead of recursing into transition.md — the depth contract matters for autonomous execution.
6. **`/gsd-autonomous` worked end-to-end** for a single-phase milestone close, including: smart-discuss → research → plan → plan-check → execute → code-review → fix-loop → audit → milestone close. One human pause point per real decision (grey-area defaults, gap-handling, defer-vs-stop).

### Cost Observations

- Model mix: predominantly Opus 4.7 across orchestrator, planner, executor, and reviewer agents; Sonnet for plan-checker and verifier.
- Sessions: this milestone close ran in a single autonomous session.
- Notable efficiency: code-review fix loop converged in 1 iteration (6 warnings → all fixed → re-review clean). Plan-check passed first try with 2 non-blocking warnings.

---

## Cross-Milestone Trends

*Tables grow as additional milestones close.*

### Velocity

| Milestone | Phases | Plans | Tests | Duration (calendar) | Notes |
|-----------|--------|-------|-------|---------------------|-------|
| v1.0 | 8 | 26 | 561 | ~8 days (2026-05-03 → 2026-05-11) | First milestone; pure-core architecture, hardware-integrated |

### Patterns Adopted Project-Wide

| Pattern | First Adopted | Status |
|---------|--------------|--------|
| Pure-core / dirty-edges separation | v1.0 Phase 1 | Established |
| Two-stage critically-damped follower (no PID) | v1.0 Phase 5 | Established |
| Two-commit phase-close (artifact + verification) | v1.0 Phase 7 | Established |
| Doc-grep validation (README vs source-of-truth) | v1.0 Phase 8 | Established |
| Frozen Pydantic / dataclass DTOs across stage boundaries | v1.0 Phase 1 | Established |

### Anti-Patterns Avoided

| Anti-Pattern | Forbidden Since | Notes |
|--------------|----------------|-------|
| PID control | v1.0 Phase 1 (PROMPT.md) | Overshoot/oscillation incompatible with cinematic feel |
| MediaPipe pose | v1.0 (replaced by YOLO11-pose) | Faster + more accurate in 2026 |
| EMA on detection stream | v1.0 (replaced by Kalman) | EMA adds lag; Kalman predicts |
| `print()` debugging | v1.0 (structlog only) | JSON output required |
| `time.sleep()` in main loop | v1.0 | Async everywhere |
| Mocked Kalman/damping math in tests | v1.0 | Test real implementations |
| Bare `except:` / `except Exception: pass` | v1.0 (Tiger-style) | Fail fast, fail loud |

### Lessons Library

- **Pin tooling versions**: mypy>=1.20,<2.0 accepted a broken minor (1.20.2 wheel); always pin point versions for build/lint/type tools. (v1.0)
- **Gate formal checks in pre-commit on day one**: ruff format drift on 73 files because the check was optional. (v1.0)
- **Hoist hardware-dependent validation to one explicit deferred phase**: 7 inherited `human_needed` cascades is noise — one clear deferral is signal. (v1.0)
