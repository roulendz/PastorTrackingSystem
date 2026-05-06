# Phase 05 Deferred Items

Items discovered during phase execution that are out-of-scope for the active plan
per CLAUDE.md scope boundary; carried forward for explicit triage.

## Pre-existing Flake — `test_geometry.py::test_inverse_map_output_in_unit_interval`

**Discovered during:** Plan 05-04 execution (full-suite verification).

**Symptom:** Hypothesis-driven test fails when run as part of the full suite (`pytest -q`)
with `PytestUnraisableExceptionWarning: Exception ignored in
<function BaseEventLoop.__del__>`. Promoted to error by
`pyproject.toml:filterwarnings=["error"]`.

**Root cause (suspected):** A leaked asyncio event loop in some upstream test
(likely an `asyncio.run` exit path that doesn't fully drain in one of the
arduino_motor / obs_camera / framer / pan_controller test modules). The leak
surfaces during garbage collection and is captured as an unraisable warning by
pytest.

**Reproducibility:**
- Fails: `pytest -q` (full suite) — fails on `81e67dc` Task 1 only and on plan-tip.
- Passes: `pytest tests/test_geometry.py` (isolated).
- Passes: `pytest --ignore=tests/test_pan_controller.py` ALSO fails — i.e. NOT
  introduced by Plan 05-04.

**Status:** Out-of-scope for Plan 05-04. The failure exists prior to this plan's
changes. Logged for triage in a future hygiene plan.

**Suggested next step:** Add a debug session that runs the suite with
`-p no:cacheprovider --asyncio-mode=strict` and `tracemalloc` enabled to identify
which test leaks the event loop. Likely fix is `asyncio.Runner` context manager
or explicit `loop.close()` in the offending test. Not blocking Phase 05 close-out.
