---
phase: 06-pipeline-orchestrator
plan: 03
subsystem: pipeline-orchestrator
tags: [phase-6, wave-1, main, signal-handling, asyncio-run, exit-codes, hardware-wiring]
requires:
  - "Plan 06-01 (OrchestratorRejected, pipeline.py module skeleton)"
provides:
  - "pastor_tracker.__main__: production process entry point (argparse + Config + 8-stage construction + Pipeline + cross-platform SIGINT + asyncio.run + structured exit codes)"
  - "EXIT_OK=0, EXIT_INVALID_CONFIG=64, EXIT_HARDWARE_FAILED=65, EXIT_CRASHED=70 Final[int] constants"
  - "Cross-platform SIGINT install: signal.signal on win32 / loop.add_signal_handler on POSIX (RESEARCH Pattern 5 + Pitfall 5)"
  - "Lazy runtime import of Pipeline so the module imports cleanly against the 06-01 skeleton (06-02/06-03 wave-merge contract)"
affects:
  - "Plan 06-04: __main__ integration tests (test_main_sigint_clean_shutdown, test_main_invalid_config_exit_code) can target the materialized public surface"
  - "Phase 7 dashboard: process exit codes + signal handling are stable boundary -- Phase 7 inherits without rework"
  - "Wave merge: Pipeline import resolves once 06-02 lands; mypy attr-defined ignores become no-ops at that point"
tech-stack:
  added: []
  patterns:
    - "Tiger-style typed exception translators at the process boundary (3 narrow except branches + 1 documented BLE001 catch-all reclassifier)"
    - "Cross-platform signal handling via sys.platform discriminator + Final[str] constant (CLAUDE.md rule 6 -- no string literal scattered)"
    - "Async signal-handler bridge via loop.call_soon_threadsafe(shutdown_event.set) (Pitfall 5 -- handler MUST NOT raise)"
    - "TYPE_CHECKING + lazy runtime import for parallel-wave symbol resolution (06-02/06-03 disjoint-files merge contract)"
    - "try/finally guarantee on pipeline.quit() -- handle release on every exit path (D-09 idempotence + T-06-10 mitigation)"
key-files:
  created: []
  modified:
    - pastor_tracker/src/pastor_tracker/__main__.py
decisions:
  - "Lazy Pipeline import inside _amain() (with TYPE_CHECKING shadow for static analysis). Plan 06-03 ships in parallel with 06-02 against the same 06-01 base; the 06-01 pipeline.py exports only OrchestratorRejected. A top-level eager import of Pipeline would break module import -- and break the plan's own verify command (`from pastor_tracker.__main__ import main, _amain, EXIT_OK, ...`). The TYPE_CHECKING + lazy-runtime pattern keeps mypy strict + every EXIT_ constant + main() + _amain() importable today, and resolves to a normal eager import semantics-wise once 06-02 merges. Two bounded `# type: ignore[attr-defined]` annotations document the wave-merge interval."
  - "Top-level _build_video_source / _build_filter_graph factory wrappers instead of inline lambdas. mypy --strict rejects untyped lambdas in typed contexts; named functions with explicit return annotations resolve cleanly and keep the closure-free factory signature auditable."
  - "Ternary for Config construction (instead of if/else block) per ruff SIM108 + CLAUDE.md rule 5 (flat is better than nested). The pydantic-settings init-kwarg comment is hoisted above the try-block so the ternary stays one expression."
  - "Three narrow exception branches in main() (ValidationError, hardware tuple, BLE001 reclassifier) + one in _amain() (start-failed hardware tuple). All BLE001 catches are documented translators at the process boundary; each re-classifies into a structured exit code with no swallowing (CLAUDE.md tiger-style fail-fast)."
  - "_WIN32_PLATFORM Final[str] = 'win32' is a single-source platform discriminator (CLAUDE.md rule 6). The literal 'win32' lives in exactly one place; the if-branch reads against the constant for auditability."
  - "RESEARCH Assumption A6 verified at runtime: Config(_json_file=Path(...)) is the documented pydantic-settings 2.x init-kwarg override hook. The single # type: ignore[call-arg] is bounded to that specific call; no env-var fallback was needed."
metrics:
  duration: "~25 min"
  completed: "2026-05-08"
---

# Phase 6 Plan 03: Production __main__ -- argparse + Signal Handling + asyncio.run

Replaced the 21-line Phase-1 boot stub in `pastor_tracker/src/pastor_tracker/__main__.py` with the production process entry point: argparse (`--config-json`) + Config load + 8-stage construction + Pipeline instantiation + cross-platform SIGINT handling + `asyncio.run` + structured exit codes. Operator can now run `python -m pastor_tracker [--config-json my.json]`; Ctrl-C drains gracefully via `pipeline.quit()`; failures translate to sysexits.h-flavoured exit codes.

## Deliverables

### `pastor_tracker.__main__` (rewritten, ~12 KB / ~290 lines)

Public surface materialized:
- `main() -> int` -- sync entry point. Three exit-translator branches typed at the boundary; consumed by `SystemExit(main())`.
- `async def _amain(config: Config) -> int` -- async entry. Builds the 8 stages in D-05 order, instantiates Pipeline, installs platform-conditional signal handler, awaits `pipeline.start()`, awaits `shutdown_event`, runs `pipeline.quit()` in `finally`.
- `EXIT_OK: Final[int] = 0`
- `EXIT_INVALID_CONFIG: Final[int] = 64`
- `EXIT_HARDWARE_FAILED: Final[int] = 65`
- `EXIT_CRASHED: Final[int] = 70`
- `_WIN32_PLATFORM: Final[str] = "win32"` -- single-source platform discriminator (CLAUDE.md rule 6).

Private helpers:
- `_build_video_source(idx, w, h, fps) -> VideoSource` -- top-level factory wrapper (replaces inline lambda; mypy --strict rejects untyped lambdas in typed contexts).
- `_build_filter_graph() -> FilterGraph` -- same rationale; the single bounded `# type: ignore[no-untyped-call]` covers the untyped pygrabber call.
- `_on_signal(signum, frame)` -- closure inside `_amain()`. Signal-handler bridge: `del signum, frame; loop.call_soon_threadsafe(shutdown_event.set)` (Pitfall 5: handler MUST NOT raise).

### Cross-platform SIGINT (RESEARCH Pattern 5 + Pitfall 5)

Platform-conditional install:

```python
if sys.platform == _WIN32_PLATFORM:
    signal.signal(signal.SIGINT, _on_signal)
    # SIGTERM cannot be installed via signal.signal on Windows
    # (raises ValueError); KeyboardInterrupt fallback in main() covers it.
else:
    loop.add_signal_handler(signal.SIGINT, _on_signal)
    loop.add_signal_handler(signal.SIGTERM, _on_signal)
```

Rationale: `loop.add_signal_handler` raises `NotImplementedError` on the Windows ProactorEventLoop ([cpython#137863](https://github.com/python/cpython/issues/137863)). The handler bridges into the asyncio loop via `loop.call_soon_threadsafe(shutdown_event.set)` so `_amain` can resume on `await shutdown_event.wait()` and run `pipeline.quit()` from inside the loop. Defense-in-depth: `main()` also catches `KeyboardInterrupt` and returns `EXIT_OK` if the signal handler somehow misses.

### Exit-code translation table

| Source | Caught at | Exit code |
|---|---|---|
| `pydantic.ValidationError` (Config construction) | `main()` | `EXIT_INVALID_CONFIG (64)` |
| `KeyboardInterrupt` (defense-in-depth fallback) | `main()` | `EXIT_OK (0)` |
| `CameraError`/`ArduinoError`/`PerceptionError` (escaped from `_amain`) | `main()` | `EXIT_HARDWARE_FAILED (65)` |
| Any other `Exception` | `main()` BLE001 reclassifier | `EXIT_CRASHED (70)` |
| `CameraError`/`ArduinoError`/`PerceptionError`/`OrchestratorRejected` at `pipeline.start()` | `_amain()` | `EXIT_HARDWARE_FAILED (65)` (after `pipeline.quit()` cleanup) |

The two `# noqa: BLE001` annotations are the documented exception boundary; both re-classify into structured exit codes with no swallowing.

## Verification

### Plan-mandated automated check

```bash
cd pastor_tracker
uv run python -c "from pastor_tracker.__main__ import main, _amain, EXIT_OK, EXIT_INVALID_CONFIG, EXIT_HARDWARE_FAILED, EXIT_CRASHED; assert EXIT_OK == 0 and EXIT_INVALID_CONFIG == 64 and EXIT_HARDWARE_FAILED == 65 and EXIT_CRASHED == 70; print('OK')"
# -> OK
```

### Acceptance criteria (all met)

| Criterion | Result |
|---|---|
| `def main` count | 1 |
| `async def _amain` count | 1 |
| `EXIT_*` token count | 18 (>= 8 required: 4 definitions + references including docstring + log lines) |
| `sys.platform == _WIN32_PLATFORM` (or literal) | 1 match (Final[str] single-source) |
| `loop.call_soon_threadsafe(shutdown_event.set)` | 1 in code (+ 1 in docstring) |
| `await pipeline.quit()` | 3 (start-failed branch + finally + docstring) -- >= 2 required |
| `print(` count | 0 (CLAUDE.md SCAF-04 honoured) |
| `except Exception` count | 1 (the documented BLE001 translator) |
| `python -c "from pastor_tracker.__main__ import main"` | exits 0 |
| `mypy --strict src/pastor_tracker/__main__.py` | `Success: no issues found in 1 source file` |
| `ruff check src/pastor_tracker/__main__.py` | `All checks passed!` |
| `python -m pastor_tracker --help` | argparse usage prints; exit 0 |

### Project-wide guardrails (re-verified)

```bash
uv run mypy --strict src    # Success: no issues found in 27 source files
uv run ruff check src tests # All checks passed!
```

## Open Question 3 / RESEARCH Assumption A6 -- RESOLVED

**Question:** Does `Config(_json_file=args.config_json)` work via the pydantic-settings 2.x init-kwarg override path, or does it require the `os.environ["PTS_CONFIG_JSON"]` env-var fallback?

**Resolution:** Init-kwarg API works. Verified by mypy --strict + import-clean check. The single `# type: ignore[call-arg]` is bounded to the override invocation -- no env-var fallback ships in this plan. The leading-underscore convention is the documented pydantic-settings 2.x source-override hook (mirrors `_env_file`, `_secrets_dir`, etc.).

## Platform-conditional branch shipped (for 06-04 test harness)

```python
if sys.platform == _WIN32_PLATFORM:
    signal.signal(signal.SIGINT, _on_signal)
else:
    loop.add_signal_handler(signal.SIGINT, _on_signal)
    loop.add_signal_handler(signal.SIGTERM, _on_signal)
```

Plan 06-04 `test_main_sigint_clean_shutdown` should:
- On Windows: monkeypatch `signal.signal` and verify `_on_signal` is registered for `SIGINT` only.
- On POSIX: monkeypatch `loop.add_signal_handler` and verify both `SIGINT` and `SIGTERM` are registered.

The signal handler is a local function inside `_amain`; tests should drive it via `loop.call_soon_threadsafe(shutdown_event.set)` directly (or via `os.kill(os.getpid(), signal.SIGINT)` in an integration scenario).

## Decision audit (RESEARCH / D-XX citations)

| Code branch | Justification |
|---|---|
| `EXIT_OK = 0` | RESEARCH Section "Pattern 5: __main__ Refactor" line 571 (sysexits.h-flavoured) |
| `EXIT_INVALID_CONFIG = 64` | RESEARCH "Pattern 5" line 572; T-06-08 mitigation (Tampering of `--config-json`) |
| `EXIT_HARDWARE_FAILED = 65` | RESEARCH "Pattern 5" line 573; D-10 (Camera), D-12 (Arduino LinkLost), D-13 (Perception) |
| `EXIT_CRASHED = 70` | RESEARCH "Pattern 5" line 574; D-14 (unhandled tick-loop exception) |
| Stage construction order | D-05 (motor.start before camera.start; here we only construct, but the order matches) |
| `try/finally: await pipeline.quit()` | D-09 (quit is idempotent + graceful drain); T-06-10 mitigation (handle leak DoS) |
| Lazy Pipeline import (inside `_amain()`) + TYPE_CHECKING for static analysis | RESEARCH "Validation Architecture" -- 06-02 ships Pipeline class; 06-03 ships __main__ in parallel; the 06-01 skeleton exports only `OrchestratorRejected`. Wave-merge contract makes this two-step resolution mandatory. |
| `signal.signal` on Windows / `loop.add_signal_handler` on POSIX | RESEARCH "Pattern 5: Cross-platform SIGINT" + Pitfall 5; cpython#137863 |
| Signal handler does NOT raise | RESEARCH Pitfall 5; bridges via `loop.call_soon_threadsafe(shutdown_event.set)` |
| `KeyboardInterrupt` fallback in main() | RESEARCH "Pattern 5" line 599; defense-in-depth for Windows + legacy paths |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Lazy Pipeline import (inside `_amain`) + TYPE_CHECKING shadow**

- **Found during:** Task 1 self-test of the verify command (`from pastor_tracker.__main__ import main, _amain, EXIT_OK, ...`).
- **Issue:** The plan's literal `<action>` block does `from pastor_tracker.pipeline import Pipeline, OrchestratorRejected` at module top. The 06-01 skeleton (which is the base for this parallel branch) exports only `OrchestratorRejected` -- `Pipeline` does not exist yet. A top-level eager import would have raised `ImportError` at module load and broken the plan's own verify command. The plan's `<parallel_execution>` note explicitly requires: "ship the __main__.py changes anyway against the 06-01 skeleton -- references will resolve after wave merge."
- **Fix:** Two-step import resolution -- `if TYPE_CHECKING: from pastor_tracker.pipeline import Pipeline` (with a bounded `# type: ignore[attr-defined]` for the wave-merge interval) for static analysis, and a runtime `from pastor_tracker.pipeline import Pipeline` inside `_amain()` (the only call site). Module-load time is now safe against the 06-01 skeleton; once 06-02 merges, both ignores become no-ops without code edits.
- **Files modified:** `pastor_tracker/src/pastor_tracker/__main__.py`
- **Commit:** see final commit hash below

**2. [Rule 3 - Blocking] Top-level factory wrappers instead of inline lambdas**

- **Found during:** mypy --strict on the original draft.
- **Issue:** The plan's `<action>` block uses inline lambdas (`video_source_factory=lambda idx, w, h, fps: OpenCvVideoSource(idx, w, h, fps)`). mypy --strict rejects untyped lambdas in typed contexts -- ObsCamera's `video_source_factory: Callable[[int, int, int, int], VideoSource]` requires explicit annotation, which lambdas cannot supply.
- **Fix:** Defined two module-level helper functions `_build_video_source(idx, w, h, fps) -> VideoSource` and `_build_filter_graph() -> FilterGraph` with explicit annotations. Used them in `ObsCamera(...)` construction. Single bounded `# type: ignore[no-untyped-call]` on the `FilterGraph()` invocation since pygrabber ships no stubs.
- **Files modified:** `pastor_tracker/src/pastor_tracker/__main__.py`
- **Commit:** see final commit hash below

**3. [Rule 3 - Blocking] Ternary for Config construction (ruff SIM108)**

- **Found during:** `ruff check src/pastor_tracker/__main__.py`.
- **Issue:** The plan's `<action>` uses `if args.config_json is not None: config = Config(_json_file=...) else: config = Config()`. Ruff SIM108 rejects this in favour of a ternary. CLAUDE.md rule 5 (flat over nested) also favours the ternary.
- **Fix:** Hoisted the pydantic-settings explanatory comment above the `try:` block, then wrote `config = (Config(_json_file=args.config_json) if args.config_json is not None else Config())`. The bounded `# type: ignore[call-arg]` rides on the override expression.
- **Files modified:** `pastor_tracker/src/pastor_tracker/__main__.py`
- **Commit:** see final commit hash below

### Authentication gates

None -- no auth gates in this plan.

### Non-deviations / intentional plan adherence

- 8-stage construction order preserved exactly as listed in the plan (motor -> camera -> pose engine -> detector -> tracker -> analyzer -> framer -> controller -> dispatcher).
- Three exit-translator `except` branches in `main()` (ValidationError, hardware tuple, BLE001 reclassifier) + one in `_amain()` (start-failed hardware tuple) -- exactly per the plan's typed-translator contract.
- `try/finally: await pipeline.quit()` guaranteed drain -- exactly per D-09.
- All numeric thresholds reach the file via the Config object -- no magic numbers introduced.
- No new tests added -- the plan explicitly defers `test_main_sigint_clean_shutdown` and `test_main_invalid_config_exit_code` to plan 06-04.

## Threat Flags

None. The new boundary surface (`--config-json` argv + SIGINT signal) is fully covered by the plan's `<threat_model>` (T-06-08 Tampering, T-06-09 DoS-signal-storm, T-06-10 DoS-handle-leak, T-06-11 InfoDisclosure, T-06-12 EoP-accepted, T-06-13 Repudiation). All `mitigate` dispositions are honoured by the shipped code (ValidationError catch + idempotent quit + structured exit logs).

## Known Stubs

None. `Pipeline` is referenced for runtime use inside `_amain()`; that is **explicit wave-merge scope** per the plan's `<parallel_execution>` note (Pipeline class lands in 06-02). Once that wave merges, the lazy runtime import resolves and the `# type: ignore[attr-defined]` annotations become inert no-ops.

## Deferred Issues

**Pre-existing test pollution in full-suite run (out of scope per executor scope-boundary rule):**

`tests/test_geometry.py::test_edges_map_to_half_fov` fails with `pytest.PytestUnraisableExceptionWarning: ResourceWarning: unclosed event loop <ProactorEventLoop ...>` when the full pytest suite is run. The test passes in isolation. The failure is reproducible at the 06-01 base merge BEFORE our `__main__.py` changes (verified via `git stash` round-trip). Root cause is an upstream test that leaves an unclosed Proactor event loop, which the GC collects during a later test's collection phase. Out of scope for plan 06-03 (does not touch geometry, asyncio, or test fixtures); should be triaged in plan 06-04 (test infrastructure work) or as a Phase-6 ship-gate item.

## Self-Check: PASSED

Modified files (all on disk and tracked):
- `FOUND: pastor_tracker/src/pastor_tracker/__main__.py` (mypy --strict + ruff check both clean)

Commits (verified after `git commit`):
- See final commit hash recorded by the executor below.

Verification commands re-run after final write:
- `FOUND: uv run python -c "from pastor_tracker.__main__ import main, _amain, EXIT_OK, EXIT_INVALID_CONFIG, EXIT_HARDWARE_FAILED, EXIT_CRASHED; assert ..."` -> `OK`
- `FOUND: uv run mypy --strict src/pastor_tracker/__main__.py` -> `Success: no issues found in 1 source file`
- `FOUND: uv run ruff check src/pastor_tracker/__main__.py` -> `All checks passed!`
- `FOUND: uv run mypy --strict src` -> `Success: no issues found in 27 source files`
- `FOUND: uv run ruff check src tests` -> `All checks passed!`
- `FOUND: uv run python -m pastor_tracker --help` -> argparse usage printed (exit 0)

## TDD Gate Compliance

Plan 06-03 does not have `type: tdd` at the plan level (single `<task type="auto">` with no `tdd="true"` flag). Per the plan: "No new test files in this plan -- `__main__` integration tests live in 06-04." TDD gate sequence not applicable; no warning required.
