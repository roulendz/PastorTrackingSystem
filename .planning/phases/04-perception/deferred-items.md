# Phase 04 Perception — Deferred Items

Items discovered during Phase 4 execution that are out of scope for the current plan.
Pre-existing issues are recorded here per `execute-plan` SCOPE BOUNDARY rule rather than
fixed in-line.

## Plan 04-01

### Pre-existing mypy errors in tests/ (498 baseline → 591 after Plan 01)

- **Files affected:** `tests/test_arduino_motor_*.py`, `tests/test_obs_camera_*.py`,
  `tests/fixtures/arduino_traces.py`, `tests/fixtures/camera_traces.py`,
  `tests/test_config.py`, `tests/test_types.py`, `tests/fixtures/_lint_canary.py`.
- **Pattern:** `dict[str, Any]` parameter annotations and `**cfg_dict` unpacking into
  `Config(...)` constructor — Pydantic-mypy emits one error per Literal/Path/Optional
  variant in the unpacked kwargs, scaling super-linearly with the number of Config
  fields.
- **Scope verdict:** `pyproject.toml` declares `disallow_any_explicit = true` for
  strict mypy; `tests/` ruff ignores ANN but mypy still applies. Phase 3 was completed
  with this baseline. Adding `yolo_model_path: Path` + `yolo_device: Literal[...]` +
  `botsort_yaml_path: Path | None` widens the union enumeration and cascades to every
  call-site — purely a test-tooling cascade, no source-code regression. `mypy src`
  remains clean.
- **Recommendation:** Single-pass cleanup task in a future doc/test-hygiene plan: replace
  `dict[str, Any]` fixture annotation with a `TypedDict` or per-test explicit kwargs.
  Out of scope for Plan 04-01/02/03 (perception focus).

### filterpy 1.4.5 cosmetic SyntaxWarning (deviation Rule 3 — applied inline)

- **File affected:** `.venv/Lib/site-packages/filterpy/common/helpers.py:367`.
- **Issue:** Upstream docstring contains the literal sequence `\Sum` which Python 3.12
  raises as `SyntaxWarning: invalid escape sequence '\S'`. Project-wide
  `pytest filterwarnings = ["error"]` promoted it to a hard SyntaxError at filterpy
  import time, before any numpy 2.4 interop was exercised.
- **Resolution applied in Plan 01:** Added `@pytest.mark.filterwarnings("ignore::SyntaxWarning")`
  to `test_filterpy_smoke_import` ONLY. Numpy `DeprecationWarning` remains escalated
  globally so RESEARCH 04 Pitfall A1 still fires loud on any genuine numpy 2.4 / filterpy
  math incompatibility.
- **Long-term fix:** When upstream filterpy ships a Python 3.12-clean release, drop the
  per-test ignore. No action required for Phase 4.
