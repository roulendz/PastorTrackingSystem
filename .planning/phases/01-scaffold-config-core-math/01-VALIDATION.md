---
phase: 1
slug: scaffold-config-core-math
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-05-03
---

# Phase 1 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | `pytest` 8.4.2 + `hypothesis` 6.152.4 + `pytest-asyncio` 0.26.0 |
| **Config file** | `pastor_tracker/pyproject.toml` `[tool.pytest.ini_options]` (Wave 0 creates) |
| **Quick run command** | `uv run --project pastor_tracker pytest pastor_tracker/tests -x --ff` |
| **Full suite command** | `uv run --project pastor_tracker pytest pastor_tracker/tests -ra --strict-markers --strict-config` |
| **Lint gate** | `uv run --project pastor_tracker ruff check pastor_tracker/src pastor_tracker/tests` |
| **Type gate** | `uv run --project pastor_tracker mypy pastor_tracker/src` |
| **Estimated runtime** | ~10 s (pure-math + config tests; no I/O) |

---

## Sampling Rate

- **After every task commit:** `ruff check pastor_tracker/src && pytest pastor_tracker/tests -x --ff`
- **After every plan wave:** Full suite — `ruff check`, `mypy --strict`, `pytest -ra`
- **Before `/gsd-verify-work`:** `cd pastor_tracker && uv sync && uv run ruff check && uv run mypy src && uv run pytest -ra` — all green from clean clone
- **Max feedback latency:** 15 seconds per task

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 1-01-01 | 01 | 0 | SCAF-01 | — | uv-locked deps tree imports cleanly | smoke | `cd pastor_tracker && uv sync && uv run python -c "import pastor_tracker"` | ❌ W0 | ⬜ pending |
| 1-01-02 | 01 | 0 | SCAF-02 | T-V14 | ruff + mypy --strict exit zero | lint/type | `ruff check pastor_tracker/src && mypy pastor_tracker/src` | ❌ W0 | ⬜ pending |
| 1-01-03 | 01 | 0 | SCAF-03 | — | pytest collects + runs from clean clone | smoke | `pytest pastor_tracker/tests` | ❌ W0 | ⬜ pending |
| 1-01-04 | 01 | 0 | SCAF-04 | T-V7 | `print()` and bare `except:` are lint errors | lint | `ruff check pastor_tracker/tests/fixtures/_lint_canary.py --select T201,E722,BLE001` | ❌ W0 | ⬜ pending |
| 1-01-05 | 01 | 0 | SCAF-05 | T-V10 | Conventional Commits enforced via commitizen hook | manual+hook | `cz check --rev-range HEAD~1..HEAD` | ❌ W0 | ⬜ pending |
| 1-02-01 | 02 | 1 | CFG-01 | T-V14 | Config has all PROMPT.md fields, env+JSON loading | unit | `pytest pastor_tracker/tests/test_config.py::test_loads_from_json_file -x` | ❌ W0 | ⬜ pending |
| 1-02-02 | 02 | 1 | CFG-02 | T-V5 | Range validation rejects out-of-range values | unit | `pytest pastor_tracker/tests/test_config.py::test_motor_speed_out_of_range_rejected -x` | ❌ W0 | ⬜ pending |
| 1-02-03 | 02 | 1 | CFG-03 | T-V14 | Invalid config raises Pydantic ValidationError at startup, no silent fallback | unit | `pytest pastor_tracker/tests/test_config.py::test_unknown_field_rejected -x` | ❌ W0 | ⬜ pending |
| 1-02-04 | 02 | 1 | CFG-04 | T-V14 | `arduino_protocol_version` defaults to `2`; mismatch aborts boot | unit | `pytest pastor_tracker/tests/test_config.py::test_protocol_version_must_be_2 -x` | ❌ W0 | ⬜ pending |
| 1-03-01 | 03 | 2 | CORE-01 | — | Frozen DTOs reject mutation (`Frame, Detection, TrackedSubject, MotionState, FramingTarget, MotorCommand`) | unit | `pytest pastor_tracker/tests/test_types.py -x` | ❌ W0 | ⬜ pending |
| 1-03-02 | 03 | 2 | CORE-02 / TEST-01 | — | Geometry: normalized↔angle round-trip identity for any FOV in (0, 180) | property | `pytest pastor_tracker/tests/test_geometry.py::test_normalized_angle_roundtrip -x` | ❌ W0 | ⬜ pending |
| 1-03-03 | 03 | 2 | CORE-03 / TEST-02 | — | Damping step response: zero overshoot for τ ∈ [0.1, 2.0] s (Holden closed-form, real math) | property | `pytest pastor_tracker/tests/test_damping.py::test_critically_damped_no_overshoot -x` | ❌ W0 | ⬜ pending |
| 1-03-04 | 03 | 2 | CORE-04 | T-V7 | Core has 100% type coverage, no `Any`, no `print` | type/lint | `mypy pastor_tracker/src/pastor_tracker/core --strict && ruff check pastor_tracker/src/pastor_tracker/core --select ANN401,T201` | ❌ W0 | ⬜ pending |
| 1-03-05 | 03 | 2 | TEST-05 | — | No mocked Kalman/damping math (test real implementations) | static | `! grep -RIn "Mock.*[Dd]ampi\\|Mock.*[Kk]alman" pastor_tracker/tests` | ❌ W0 | ⬜ pending (manual per PR) |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

**Sampling continuity check:** No 3 consecutive tasks lack automated verify — every task above has a runnable command.

---

## Wave 0 Requirements

- [ ] `pastor_tracker/pyproject.toml` — `[project]`, `[tool.uv]`, `[tool.ruff]`, `[tool.mypy]`, `[tool.pytest.ini_options]`, `[tool.commitizen]` blocks
- [ ] `pastor_tracker/.python-version` — `3.12`
- [ ] `pastor_tracker/uv.lock` — produced by `uv lock`
- [ ] `pastor_tracker/src/pastor_tracker/__init__.py`, `__main__.py`, `config.py`, `logging_config.py`
- [ ] `pastor_tracker/src/pastor_tracker/core/__init__.py`, `types.py`, `geometry.py`, `damping.py`
- [ ] `pastor_tracker/src/pastor_tracker/{io,perception,intent,control,ui}/__init__.py` — empty packages (preserve target tree)
- [ ] `pastor_tracker/tests/__init__.py`, `conftest.py`, `test_config.py`, `test_types.py`, `test_geometry.py`, `test_damping.py`, `test_logging.py`
- [ ] `pastor_tracker/tests/fixtures/_lint_canary.py` — fixture with `print(...)` and bare `except:` so canary lint command exits non-zero (kept under `tests/fixtures/`; main `ruff check` excludes the path)
- [ ] Tool install: `uv` and `commitizen` available on PATH (via `pipx` or `uv tool install`) — Wave 0 step before everything else
- [ ] `.pre-commit-config.yaml` (or equivalent) wiring `commitizen` to `commit-msg` hook

*All required test files are missing — Phase 1 is greenfield. They are created by the implementation tasks themselves; this is normal for a scaffold phase.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Conventional Commit history clean for the phase | SCAF-05 | Commit metadata is created by humans/agents during execution; no automated assertion of past commits beyond `cz check` on the latest | `cz check --rev-range $(git merge-base HEAD main)..HEAD` |
| Subjective code-style review (descriptive identifiers, ≤2-level nesting, guard clauses) | CLAUDE.md culture | Lint cannot capture intent | Reviewer reads diff, flags `cx`-style abbreviations and >2-level nests |

---

## Validation Sign-Off

- [ ] All tasks have automated verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references (pyproject, lockfile, package tree, test tree, lint canary, tool install)
- [ ] No watch-mode flags (`pytest --watch`, `ruff --watch` forbidden in CI gates)
- [ ] Feedback latency < 15 s per task; < 60 s for full wave
- [ ] `nyquist_compliant: true` set in frontmatter once planner confirms map is complete

**Approval:** pending
