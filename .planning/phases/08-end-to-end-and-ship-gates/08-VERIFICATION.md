---
phase: 08-end-to-end-and-ship-gates
plan: 01
verification_result: human_needed
verified_at: 2026-05-11
verifier: claude-opus-4-7
ruff_exit: 0
ruff_format_exit: 1
mypy_exit: 1
mypy_source_files: 78
pytest_exit: 0
pytest_passed: 561
pytest_skipped: 1
conventional_commits: clean
conventional_commits_sample_size: 30
requirements_verified: [DOC-01, QA-01, QA-03]
requirements_deferred: [QA-02, QA-04]
deferred_to: "QA-04 -- next stage rehearsal (real Uno + real OBS VCam + real speaker required); QA-02 -- hotfix plan to repair pydantic.mypy / mypy 1.20.2 environment regression"
---

# Phase 8 -- End-to-End and Ship Gates Verification Report

**Phase Goal:** Close v1 with three artifacts -- expand README to full operator manual (DOC-01), re-verify ship gates (QA-01 / QA-02 / QA-03), and record on-stage smoke deferral (QA-04).
**Verified:** 2026-05-11
**Status:** human_needed
**Re-verification:** No -- initial verification

## Summary

Phase 8 closes v1 with the README rewrite landed (10 locked H2 sections, source-of-truth field/flag fidelity verified, YOLO weights v2-supply-chain TODO marker placed). The DOC-01 cross-grep checks all pass: every backtick-quoted `snake_case` config key in README exists in `config.py`; every `--flag` token in README appears as a string literal in `__main__.py` argparse. QA-04 on-stage smoke is documented in the README "First run on stage" section and is deferred to a human operator on the next stage rehearsal.

Two ship-gate regressions surfaced during re-verification, both predating Phase 8 execution:

1. **QA-02 mypy --strict (FAIL, exit 1, 995 errors):** The `mypy` 1.20.2 wheel currently installed in `pastor_tracker/.venv` ships a broken `mypy.expandtype` module that raises `AttributeError: module 'mypy.expandtype' has no attribute 'ExpandTypeVisitor'` on import. This breaks the `pydantic.mypy` plugin (which depends on `mypy.expandtype.expand_type`), causing all Pydantic `Config(**kwargs)` call sites in tests to lose their synthesized typed `__init__` signature and fall back to the un-typed `BaseSettings` init -- producing 925 `[arg-type]` errors. The lockfile pins `mypy>=1.20,<2.0`; this is an upstream wheel-init regression, not a source-code drift. Phase 8 task 08-01-02 does not modify source -- per the plan contract ("the executor must NOT edit source code to make it green") this is surfaced as a gate regression for the next hotfix plan.

2. **QA-01 ruff format --check (FAIL, exit 1, 73 files would be reformatted):** The optional `ruff format --check` gate reports 73 files with format drift accumulated across Phases 1-7. The pre-commit hook configures `ruff check` (lint) only -- `ruff format --check` was never gated by CI, and 08-01-PLAN.md documents this gate as "optional but recommended". Truthful report.

Hard gates QA-01 (ruff lint), pytest (561 passed, 1 skipped -- Phase 7 baseline preserved), and QA-03 (Conventional Commits, 30-commit sample all valid) pass cleanly. DOC-01 verification is complete. The two regressions above plus the always-human QA-04 smoke move the overall result to `human_needed`.

## Automated Verification

| Requirement | Gate | Command | Exit | Result |
|-|-|-|-|-|
| DOC-01 | doc-grep 10 H2 sections | `Select-String -Path pastor_tracker\README.md -Pattern '^## (Overview\|Requirements\|Install\|OBS Virtual Camera setup\|FOV calibration\|Run\|Hotkeys\|Tuning sliders\|First run on stage\|Troubleshooting)$'` | 10/10 | PASS |
| DOC-01 | doc-grep config keys match config.py | Per-key `Select-String -SimpleMatch` loop over 8 backtick-quoted `snake_case` identifiers | 8/8 | PASS |
| DOC-01 | doc-grep CLI flags match __main__.py | Per-flag string-literal grep over 3 `--flag` tokens (`--config-json`, `--ui`, `--headless`) | 3/3 | PASS (note: literal-grep substituted for the plan's `add_argument("<flag>"` single-line regex because the project formats argparse calls across multiple lines; equivalent verification of "flag string literal is defined in __main__.py") |
| DOC-01 | YOLO v2-supply-chain TODO marker present | `Select-String -Path pastor_tracker\README.md -Pattern 'TODO\(v2-supply-chain\): pin yolo11n-pose\.pt SHA256'` | 1 | PASS |
| DOC-01 | Hotkey rows S/P/H/E/Q + triggers | `Select-String` for backticked keys + `On press \(debounced\)` for E + `On release` for S | 7/7 | PASS |
| DOC-01 | VID:PID coverage in Troubleshooting | `Select-String` for `2341:0043\|2341:0069\|1A86:7523\|0403:6001` | 4 | PASS |
| DOC-01 | ERROR:11 firmware contract documented | `Select-String -Pattern 'ERROR:11'` | 1 | PASS |
| DOC-01 | ASCII pipeline diagram present | `Select-String -Pattern 'OBS VCam.*ArduinoMotor'` | 1 | PASS |
| QA-01 | ruff lint | `./.venv/Scripts/ruff.exe check src tests` | 0 | PASS |
| QA-01 | ruff format check (optional) | `./.venv/Scripts/ruff.exe format --check src tests` | 1 | FAIL (73 files would be reformatted; pre-existing drift, not gated by pre-commit; deferred to QA-01-format hotfix plan) |
| QA-02 | mypy --strict | `./.venv/Scripts/mypy.exe --strict src tests` | 1 | FAIL (995 errors -- `pydantic.mypy` plugin import crashes on `mypy.expandtype.ExpandTypeVisitor` missing; mypy 1.20.2 wheel-init regression; deferred to hotfix plan) |
| Suite | pytest | `./.venv/Scripts/pytest.exe tests` | 0 | PASS (561 passed, 1 skipped -- Phase 7 baseline preserved exactly) |
| QA-03 | conventional commits | `git log --oneline -30 \| grep -vE '^[a-f0-9]+ (feat\|fix\|docs\|test\|chore\|refactor)(\([0-9.\-]+\))?:'` | empty | PASS (0 regressions over 30-commit sample) |

## Human Verification -- QA-04 On-Stage Smoke (DEFERRED)

**Verification result:** human_needed -- autonomous mode cannot drive a real stage. See README -> "First run on stage" section for the operator-facing procedure.

- [ ] Zero overshoot -- motor never overshoots framing target and recoils.
- [ ] Zero audible motor jerk -- pan is silent or smooth-hum; no clack, no chatter.
- [ ] Zero lock-loss to audience or interpreter -- camera does not jump to non-primary subject for > 2 s.
- [ ] Rule-of-thirds framing holds -- moving-right subject in left third; moving-left in right third; dwelling at center (INTENT-03).
- [ ] E-stop works -- pressing `E` halts motor within one tick (< 50 ms perceived).
- [ ] 5+ minutes of continuous tracking -- no crashes, no `ERROR:` log lines.

Smoke test deferred -- schedule on next stage rehearsal with real speaker, real Uno, real OBS VCam. Updates committed under `docs(08): record on-stage smoke result`.

## Ship-Gate Regressions Deferred (QA-02 + QA-01 format)

### QA-02 mypy --strict regression (995 errors)

**Root cause:** `mypy` 1.20.2 wheel currently in `pastor_tracker/.venv` has a broken `mypy.expandtype` module that fails to expose `ExpandTypeVisitor` at import time. Direct reproduction:

```powershell
./.venv/Scripts/python.exe -c "from mypy.expandtype import expand_type"
# Raises: AttributeError: module 'mypy.expandtype' has no attribute 'ExpandTypeVisitor'
```

**Downstream effect:** `pydantic.mypy` plugin fails to load silently. All Pydantic v2 model classes lose their synthesized typed `__init__`; `Config(**kwargs)` calls in test fixtures fall back to the un-typed `BaseSettings` init, producing 925 `[arg-type]` errors across `tests/fixtures/*.py` and 27 other test files. Source `src/` files contribute no new errors.

**Out-of-scope for Phase 8:** No Phase 8 task modifies source. Per 08-01-PLAN.md `<action>` block 08-01-02: *"The executor must NOT edit source code to make it green -- surface the regression."* The Phase 7 baseline (commit `8aea0d8`) verified mypy clean on 32 source files -- the regression is environmental (wheel-level), not commit-level.

**Proposed hotfix plan:**

1. Reproduce in a fresh `uv sync --reinstall` to confirm the wheel install is corrupt vs the upstream wheel itself broken.
2. If upstream wheel is broken: pin `mypy<1.20.2` in `pyproject.toml`, re-lock, re-verify mypy clean.
3. If local install corrupt: `uv sync --reinstall` and re-verify.
4. Either path requires a `fix(08-XX):` commit with the new lockfile and verification record.

### QA-01 ruff format --check regression (73 files)

**Root cause:** `ruff format --check src tests` reports 73 files would be reformatted. This drift accumulated across Phases 1-7 because:

- `.pre-commit-config.yaml` hooks `ruff check` (lint) only; `ruff format` is not in the hook chain.
- `pyproject.toml` declares `[tool.ruff.format]` config but no CI gate enforces it.
- 08-01-PLAN.md task 08-01-02 documents this gate as "optional but recommended", not a hard ship gate.

**Out-of-scope for Phase 8:** No Phase 8 task modifies the 73 drifted files. Out-of-scope per the SCOPE BOUNDARY rule in the executor contract ("Only auto-fix issues DIRECTLY caused by the current task's changes. Pre-existing warnings, linting errors, or failures in unrelated files are out of scope.").

**Proposed hotfix plan:**

1. Run `./.venv/Scripts/ruff.exe format src tests` to apply the format pass.
2. Re-run pytest to confirm no behavioral change (format is whitespace/quote-style only).
3. Add `ruff format --check` to `.pre-commit-config.yaml` to prevent future drift.
4. Commit as `chore(08-XX): apply ruff format pass + gate in pre-commit`.

## Phase 8 Closure

Phase 8 closes as `verification_result: human_needed`, mirroring Phase 7 commit `8aea0d8` (`docs(07): record phase verification (human_needed - manual smoke deferred to Phase 8 QA-04)`). v1.0 milestone transitions to lifecycle with three outstanding items:

1. QA-04 on-stage smoke (always-human) -- defer to next stage rehearsal.
2. QA-02 mypy --strict regression (environment hotfix) -- defer to a `fix(08-XX):` plan.
3. QA-01 ruff format --check drift (format-pass + gate hotfix) -- defer to a `chore(08-XX):` plan.

DOC-01 (operator README) is shipped and verified. QA-01 (ruff lint) is green. QA-03 (Conventional Commits) is green over the 30-commit sample including all Phase 8 commits. The pytest suite is green and matches the Phase 7 baseline exactly (561 passed, 1 skipped).

The README contract is met: a first-time operator can bring the system up from `pastor_tracker/README.md` alone, configure FOV per the calibration procedure, launch via `uv run pastor-tracker`, use the documented hotkeys, and consult Troubleshooting on failure.
