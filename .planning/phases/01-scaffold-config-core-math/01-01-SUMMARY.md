---
phase: 01
plan: 01
subsystem: scaffold
tags: [scaffold, tooling, python, uv, ruff, mypy, structlog, commitizen, pre-commit]
requirements: [SCAF-01, SCAF-02, SCAF-03, SCAF-04, SCAF-05]
dependency_graph:
  requires: []
  provides:
    - "uv-locked Python 3.12 project at pastor_tracker/"
    - "[tool.ruff] policy banning print()/bare except/blind except"
    - "[tool.mypy] strict + disallow_any_explicit"
    - "[tool.pytest.ini_options] auto async, strict markers"
    - "structlog JSON logger (configure_logging) at module entry"
    - "Conventional Commits enforcement via pre-commit commit-msg hook"
    - "Empty package tree: core, io, perception, intent, control, ui"
  affects:
    - "All Phase 1-8 plans depend on this scaffold for uv sync, ruff, mypy, pytest"
tech_stack:
  added:
    - "uv 0.11.8 (installed into root .venv)"
    - "pydantic 2.13.3, pydantic-settings 2.14.0"
    - "structlog 25.5.0 (>=24.4 range allowed bump to 25.5.0 — same JSON API)"
    - "numpy 2.4.4"
    - "ruff 0.15.12, mypy 1.20.2"
    - "pytest 8.4.2, pytest-asyncio 1.3.0, hypothesis 6.152.4"
    - "commitizen 4.15.0, pre-commit 4.6.0"
    - "hatchling (build backend)"
  patterns:
    - "src layout (src/pastor_tracker/) with hatchling wheel target"
    - "pure-core / dirty-edges sub-package skeleton (core/io/perception/intent/control/ui)"
    - "JSON logging via structlog with format_exc_info processor (Pitfall 5 honored)"
    - "Lint canary fixture excluded from main lint, asserted non-zero by dedicated SCAF-04 command"
key_files:
  created:
    - "pastor_tracker/pyproject.toml"
    - "pastor_tracker/.python-version"
    - "pastor_tracker/uv.lock"
    - "pastor_tracker/README.md"
    - "pastor_tracker/.pre-commit-config.yaml"
    - "pastor_tracker/src/pastor_tracker/__init__.py"
    - "pastor_tracker/src/pastor_tracker/__main__.py"
    - "pastor_tracker/src/pastor_tracker/logging_config.py"
    - "pastor_tracker/src/pastor_tracker/core/__init__.py"
    - "pastor_tracker/src/pastor_tracker/io/__init__.py"
    - "pastor_tracker/src/pastor_tracker/perception/__init__.py"
    - "pastor_tracker/src/pastor_tracker/intent/__init__.py"
    - "pastor_tracker/src/pastor_tracker/control/__init__.py"
    - "pastor_tracker/src/pastor_tracker/ui/__init__.py"
    - "pastor_tracker/tests/__init__.py"
    - "pastor_tracker/tests/conftest.py"
    - "pastor_tracker/tests/test_logging.py"
    - "pastor_tracker/tests/fixtures/__init__.py"
    - "pastor_tracker/tests/fixtures/_lint_canary.py"
  modified: []
decisions:
  - "Installed uv (0.11.8) into the existing repo-root .venv via pip rather than via pipx (pipx not available on this machine; uv binary still resolves to .venv\\Scripts\\uv.exe)"
  - "structlog dep range '>=24.4,<26.0' resolved to 25.5.0 — JSON API identical, pinned ceiling avoids breaking-change drift"
  - "Pre-commit hook installed at repo-root .git/hooks/commit-msg (pastor_tracker/ is a sub-directory, not a nested git repo) — config path passed explicitly via --config pastor_tracker/.pre-commit-config.yaml"
  - "Used direct pastor_tracker/.venv/Scripts/{ruff,mypy,pytest,cz}.exe for verification — bypasses PowerShell stderr-routing artifacts that obscure native exit codes"
metrics:
  duration_minutes: 9
  completed_date: "2026-05-03"
  tasks_completed: 3
  tasks_total: 3
  files_created: 19
  files_modified: 0
  commits: 3
---

# Phase 01 Plan 01: Scaffold (uv + ruff/mypy/pytest + structlog + commitizen) Summary

uv-locked Python 3.12 project at `pastor_tracker/` with strict ruff (T20/BLE/ANN/PLR/S/SIM/RET), mypy `--strict` + `disallow_any_explicit`, pytest+hypothesis+pytest-asyncio wiring, structlog JSON logger with `format_exc_info`, commitizen Conventional Commits hook, empty `core/io/perception/intent/control/ui` skeleton, and a lint-canary fixture that proves the forbidden-pattern gates fire.

## Objective

Bootstrap the foundation that every later phase builds on: deterministic dep lock (47 packages, hash-pinned in `uv.lock`), lint policy enforcing every CLAUDE.md forbidden, type policy banning `Any`, JSON logging at module entry, package tree matching the CLAUDE.md target, and Conventional Commits enforced by a `commit-msg` git hook. Without this scaffold, Plan 02 (`Config`) and Plan 03 (core math + property tests) cannot run.

## What Was Built

### Task 1 — uv project + pyproject.toml + lockfile (commit `9e612af`)

- Wrote `pastor_tracker/pyproject.toml` with all 5 required tool blocks (`[tool.uv]`, `[tool.ruff]`, `[tool.mypy]`, `[tool.pytest.ini_options]`, `[tool.commitizen]`).
- Ruff `select` declares 13 rule families: F, E, W, B, I, UP, ANN (incl. ANN401 → no `Any` in signatures), T20 (no `print()`), BLE (no blind `except Exception:`), S (bandit), SIM, RET, PLR, RUF.
- Mypy `strict = true` + `disallow_any_explicit = true` + pydantic plugin + per-module `ignore_missing_imports` for the unstubbed deps that land in later phases (`cv2`, `dearpygui`, `pygrabber`, `ultralytics`, `filterpy`, `serial`).
- Runtime deps pinned: `pydantic>=2.13`, `pydantic-settings>=2.14`, `structlog>=24.4`, `numpy>=2.4`. Dev deps: `ruff>=0.15`, `mypy>=1.20`, `pytest>=8.4`, `pytest-asyncio>=0.26`, `hypothesis>=6.152`, `commitizen>=4.15`, `pre-commit>=4.0`.
- `.python-version` = `3.12`. Hatchling wheel target = `src/pastor_tracker`.
- `uv lock` resolved 47 packages; `uv sync` installed them into `pastor_tracker/.venv/`.

### Task 2 — Source/test tree + structlog + lint canary (commit `92baaca`)

- 7 `__init__.py` package markers (root + `core` + `io` + `perception` + `intent` + `control` + `ui`).
- `logging_config.py` ships `configure_logging(level)` with `merge_contextvars`, `add_log_level`, `TimeStamper(iso, utc)`, `StackInfoRenderer`, **`format_exc_info`** (Pitfall 5 — without it exceptions render as `<traceback object at 0x...>`), and `JSONRenderer`. Idempotent.
- `__main__.py` boots structlog and emits a single `boot` JSON event — no `print()`.
- `tests/test_logging.py` calls `configure_logging`, emits an event, parses captured stdout as JSON, asserts `event`, `port`, `level`, `timestamp` fields.
- `tests/fixtures/_lint_canary.py` contains `print()`, bare `except:`, `except Exception: pass` — excluded from main `ruff check` via `extend-exclude`. SCAF-04 verifier runs `ruff check ... --select T201,E722,BLE001` against this file and exits non-zero (3 errors found).

### Task 3 — Conventional Commits via pre-commit `commit-msg` hook (commit `71724bc`)

- `.pre-commit-config.yaml` pins `commitizen` v4.15.0 to the `commit-msg` stage.
- Hook installed at repo-root `.git/hooks/commit-msg` (config path `pastor_tracker/.pre-commit-config.yaml`); fired successfully against the Task 3 commit itself, proving the pipeline works end-to-end.

## Wave 0 Verification (all green)

| Gate | Command | Exit | Result |
|------|---------|------|--------|
| SCAF-01 | `uv sync` | 0 | 47 packages installed |
| SCAF-02 (lint) | `ruff check src` | 0 | "All checks passed!" |
| SCAF-02 (type) | `mypy src` | 0 | "no issues found in 9 source files" |
| SCAF-03 | `pytest tests/test_logging.py -x` | 0 | 1 passed in 0.36s |
| SCAF-04 | `ruff check tests/fixtures/_lint_canary.py --select T201,E722,BLE001` | **1** | 3 errors fired (T201, E722, BLE001) |
| SCAF-05 (valid) | `cz check --message "chore(scaffold): bootstrap pastor_tracker uv project"` | 0 | "Commit validation: successful!" |
| SCAF-05 (invalid) | `cz check --message "broken commit message no type"` | 14 | regex mismatch reported |
| Tree | `find src/pastor_tracker -name __init__.py | wc -l` | — | 7 (root + 6 sub-packages) |

### Lint canary output (proof SCAF-04 forbiddens fire)

```
T201 `print` found
  --> tests\fixtures\_lint_canary.py:11:5

E722 Do not use bare `except`
  --> tests\fixtures\_lint_canary.py:17:5

BLE001 Do not catch blind exception: `Exception`
  --> tests\fixtures\_lint_canary.py:25:12

Found 3 errors.
```

### Locked dep versions (sample from `pastor_tracker/uv.lock`)

| Package | Version |
|---------|---------|
| pydantic | 2.13.3 |
| pydantic-core | 2.46.3 |
| pydantic-settings | 2.14.0 |
| structlog | 25.5.0 |
| numpy | 2.4.4 |
| ruff | 0.15.12 |
| mypy | 1.20.2 |
| pytest | 8.4.2 |
| pytest-asyncio | 1.3.0 |
| hypothesis | 6.152.4 |
| commitizen | 4.15.0 |
| pre-commit | 4.6.0 |
| hatchling (build) | bundled |

47 total packages, all hash-pinned in `uv.lock`.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 — Blocking] `uv` not on PATH; pipx unavailable**
- **Found during:** Task 1, Step 1.1
- **Issue:** Plan recommended `pipx install uv` or the astral.sh PowerShell installer. Neither pipx nor a system uv binary was available on this Windows machine, and the project root `.venv/` (Python 3.12) was already present.
- **Fix:** Installed `uv 0.11.8` into the repo-root `.venv` via `python -m pip install uv`; invoked it via `.venv\Scripts\uv.exe`. Same uv functionality, no PATH pollution.
- **Files modified:** none (system tooling only).

**2. [Rule 3 — Blocking] PowerShell stderr-routing eats native exit codes**
- **Found during:** Task 1 verification, Task 2 canary verification.
- **Issue:** When invoking native Windows .exe through PowerShell from this agent's Bash tool, stderr lines (including uv's `Using CPython ...` notice) get re-emitted as PowerShell `RemoteException` objects, which cause the wrapper `Exit code 1` even when the underlying process exited 0. Also `$LASTEXITCODE` did not propagate cleanly through the bash bridge.
- **Fix:** Switched verification to invoke the `pastor_tracker/.venv/Scripts/{ruff,mypy,pytest,cz}.exe` binaries directly (without PowerShell), letting bash's `$?` capture the true exit code. Confirmed canary truly exits `1`, all other gates exit `0`.
- **Files modified:** none (verification protocol only).

**3. [Rule 3 — Path adjustment] commit-msg hook path differs from plan**
- **Found during:** Task 3, Step 3.2
- **Issue:** Plan acceptance criteria expected `pastor_tracker/.git/hooks/commit-msg`, but `pastor_tracker/` is a sub-directory of the parent git repo, not its own nested repo. There is no `pastor_tracker/.git`.
- **Fix:** Installed the hook into the parent repo at `D:/System/Documents/PastorTrackingSystem/.git/hooks/commit-msg`, passing `--config pastor_tracker/.pre-commit-config.yaml` explicitly so pre-commit picks up the right config from a non-default location. The hook fires successfully on every parent-repo commit (proven by Task 3's commit being validated by the hook itself).
- **Files modified:** none beyond `.pre-commit-config.yaml` (which was planned).

### Authentication gates

None — no external services touched.

## Key Decisions

1. **Use root `.venv` to host uv binary** (Decision under Rule 3): pipx unavailable on this Windows machine; bootstrapping uv via `pip install uv` into the existing 3.12 venv kept tooling self-contained without polluting global PATH.
2. **Direct `.venv\Scripts\<tool>.exe` for CI verification** (Decision under Rule 3): PowerShell wrapper artifacts obscured native exit codes; using the binaries directly via bash gives correct gating.
3. **Hook lives at parent `.git/hooks/commit-msg`**: pastor_tracker is a sub-directory, not a sub-repo. Passing `--config pastor_tracker/.pre-commit-config.yaml` keeps the config co-located with the Python project.
4. **structlog 25.5.0** chosen by uv resolver inside the `>=24.4,<26.0` range — confirmed JSON API and `format_exc_info` processor identical to 24.4.

## Stub / Threat Notes

- **No stubs introduced.** Every module either ships its declared functionality (`logging_config.configure_logging`, `__main__.main`, lint canary) or is an intentional empty docstring-only `__init__.py` for a future-phase package (`core`, `io`, `perception`, `intent`, `control`, `ui`). Each empty `__init__.py` is documented in the plan and matches the CLAUDE.md target tree — these are not stubs in the user-facing sense.
- **No new threat surface beyond the registered model.** Plan introduced uv lockfile (T-1.01-01 mitigated), commitizen hook (T-1.01-02 mitigated), structlog JSON logging (T-1.01-03 mitigated), lint-canary exclusion (T-1.01-04 accepted), bandit ruleset (T-1.01-05 mitigated), strict mypy (T-1.01-06 mitigated). All dispositions match the plan's `<threat_model>`.

## Self-Check: PASSED

Files created (all exist):

- `pastor_tracker/pyproject.toml` — FOUND
- `pastor_tracker/.python-version` — FOUND
- `pastor_tracker/uv.lock` — FOUND
- `pastor_tracker/.pre-commit-config.yaml` — FOUND
- `pastor_tracker/README.md` — FOUND
- `pastor_tracker/src/pastor_tracker/__init__.py` — FOUND
- `pastor_tracker/src/pastor_tracker/__main__.py` — FOUND
- `pastor_tracker/src/pastor_tracker/logging_config.py` — FOUND
- `pastor_tracker/src/pastor_tracker/{core,io,perception,intent,control,ui}/__init__.py` — FOUND (6 files)
- `pastor_tracker/tests/__init__.py` — FOUND
- `pastor_tracker/tests/conftest.py` — FOUND
- `pastor_tracker/tests/test_logging.py` — FOUND
- `pastor_tracker/tests/fixtures/__init__.py` — FOUND
- `pastor_tracker/tests/fixtures/_lint_canary.py` — FOUND
- `.git/hooks/commit-msg` (pre-commit installed) — FOUND

Commits exist on `fresh-2026`:

- `9e612af chore(scaffold): bootstrap pastor_tracker uv project` — FOUND
- `92baaca feat(scaffold): structlog JSON logging + package skeleton + lint canary` — FOUND
- `71724bc chore(scaffold): wire commitizen commit-msg hook via pre-commit` — FOUND

All 7 must_haves green. Plan 01-01 complete.
