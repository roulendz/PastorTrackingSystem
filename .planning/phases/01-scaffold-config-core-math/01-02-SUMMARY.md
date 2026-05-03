---
phase: 01
plan: 02
subsystem: config
tags: [config, pydantic, validation, fail-fast, settings, frozen, env, json]
requirements: [CFG-01, CFG-02, CFG-03, CFG-04]
dependency_graph:
  requires:
    - "01-scaffold-config-core-math/01 — uv project, pydantic+pydantic-settings installed, ruff/mypy/pytest wired"
  provides:
    - "pastor_tracker.config.Config — frozen Pydantic v2 BaseSettings, 25 validated fields"
    - "PTS_* env-var loading with PROMPT.md ## Config defaults"
    - "config.json loading via JsonConfigSettingsSource (precedence init > env > .env > config.json > defaults)"
    - "Cross-field rule motor_angle_max_deg > motor_angle_min_deg (model_validator(after))"
    - "valid_config_dict pytest fixture (single source of truth for downstream Phase 1-7 Config tests)"
  affects:
    - "Phases 2-7 — every later stage consumes Config; range validation now blocks bad values at startup"
    - "pyproject.toml — added [tool.pydantic-mypy] block (init_forbid_extra, init_typed) so disallow_any_explicit stays clean for every future BaseModel"
tech_stack:
  added:
    - "pydantic.Field range validators (ge/le/gt/lt) on every numeric field"
    - "pydantic.model_validator(mode='after') for cross-field invariant"
    - "pydantic_settings.JsonConfigSettingsSource wired in settings_customise_sources"
    - "typing.Literal[2] pinning arduino_protocol_version (CFG-04 enforced statically)"
  patterns:
    - "Module-level _SCREAMING_SNAKE constants for every numeric bound (CLAUDE.md rule 6 — no magic numbers; ruff PLR2004 clean)"
    - "Source precedence override via settings_customise_sources classmethod (per pydantic-settings 2.14 contract)"
    - "Frozen config + extra='forbid' = tiger-style fail-fast — invalid data crashes at construction, no silent fallbacks"
key_files:
  created:
    - "pastor_tracker/src/pastor_tracker/config.py"
    - "pastor_tracker/tests/test_config.py"
  modified:
    - "pastor_tracker/tests/conftest.py (replaced placeholder body with valid_config_dict fixture)"
    - "pastor_tracker/pyproject.toml (added [tool.pydantic-mypy] init_forbid_extra+init_typed to keep disallow_any_explicit clean)"
decisions:
  - "Field count is 25, NOT 24. Plan narrative said '23 PROMPT.md + 1 = 24', but PROMPT.md ## Config block already lists arduino_ready_timeout_sec inline (line 254) — the field was added in commit e886032. Counted both directly: PROMPT.md = 25 fields, plan acceptance name-set = 25 names, plan verbatim module body = 25 declarations. Implemented 25; documented as Deviation #1 (Rule 1 — fix doc bug)."
  - "Tightened pydantic-mypy plugin via [tool.pydantic-mypy] init_forbid_extra=true + init_typed=true. Without it, mypy's disallow_any_explicit fires on EVERY BaseSettings subclass (the plugin synthesizes __init__(**kwargs: Any) by default). One-time fix that benefits every future Pydantic model in Phases 2-7."
  - "Used UTC-style underscore-grouped numeric literals (115_200, 25_000.0) and PEP 8 '_SCREAMING_SNAKE' module constants — both required to keep ruff PLR2004 quiet without sprinkling # noqa."
  - "model_validator(mode='after') chosen over field_validator (Pitfall 8 from RESEARCH.md): mode='after' runs once per construction with the validated, cast field values; using a per-field validator would not see motor_angle_min_deg when validating motor_angle_max_deg."
  - "Cross-field validator returns the model instance (-> Config, not 'Config'), enabled by `from __future__ import annotations` (PEP 563) — ruff UP037 enforces."
metrics:
  duration_minutes: 7
  completed_date: "2026-05-03"
  tasks_completed: 2
  tasks_total: 2
  files_created: 2
  files_modified: 2
  commits: 2
---

# Phase 01 Plan 02: Frozen Pydantic Config (25 fields, env+JSON, fail-fast) Summary

Frozen `pastor_tracker.config.Config(BaseSettings)` ships every PROMPT.md `## Config` field (25 total, all range-validated, `extra="forbid"`, `frozen=True`), with `PTS_*` env vars and `config.json` loading wired through `settings_customise_sources` in the documented precedence (init > env > .env > config.json > defaults). `arduino_protocol_version: Literal[2]` enforces CFG-04 statically. A cross-field `@model_validator(mode="after")` rejects `motor_angle_max_deg <= motor_angle_min_deg`. Ten unit tests prove every CFG-01..04 contract via `pytest.raises(ValidationError)` — no mocks. `valid_config_dict` fixture in `conftest.py` becomes the single source of truth for downstream Phase 1-7 Config tests.

## Objective

Phases 2-7 consume `Config`; without a fail-fast frozen settings object, downstream stages would silently absorb bad values. CFG-03 — "crash at startup on invalid config, no silent fallback" — is non-negotiable per CLAUDE.md tiger-style. This plan landed that contract end-to-end and proved every gate fires.

## What Was Built

### Task 1 — `Config(BaseSettings)` with 25 fields (commit `7d3267b`)

- `pastor_tracker/src/pastor_tracker/config.py` (191 lines) — frozen Pydantic v2 BaseSettings
- 25 fields covering full PROMPT.md `## Config` block:
  - **Arduino transport (5):** `arduino_port`, `arduino_baud`, `arduino_protocol_version` (`Literal[2]`), `arduino_ready_timeout_sec`, `arduino_heartbeat_interval_ms`
  - **Camera (5):** `obs_camera_name`, `capture_width`, `capture_height`, `capture_fps`, `camera_horizontal_fov_deg`
  - **Detection / motion (5):** `detection_confidence_min`, `motion_threshold_norm_per_sec`, `motion_hysteresis_sec`, `dwell_threshold_norm_per_sec`, `dwell_duration_sec`
  - **Damping (2):** `framing_time_constant_sec`, `pan_time_constant_sec`
  - **Pan limits (2):** `pan_deadband_deg`, `pan_max_velocity_deg_per_sec`
  - **Motor (4):** `motor_max_speed_steps_per_sec`, `motor_max_accel_steps_per_sec2`, `motor_angle_min_deg`, `motor_angle_max_deg`
  - **Dispatcher (2):** `command_min_delta_deg`, `command_min_interval_ms`
- 17 module-level `_SCREAMING_SNAKE` constants for every numeric bound (e.g. `_MOTOR_SPEED_FLOOR_STEPS_PER_SEC = 100.0`) — ruff PLR2004 clean.
- `model_config = SettingsConfigDict(env_prefix="PTS_", env_file=".env", json_file=Path("config.json"), frozen=True, extra="forbid", case_sensitive=False)`.
- `@model_validator(mode="after")` enforces `motor_angle_max_deg > motor_angle_min_deg`.
- `settings_customise_sources` override locks precedence: `init > env > dotenv > JsonConfigSettingsSource > file_secret`.
- `pyproject.toml` extended with `[tool.pydantic-mypy]` (`init_forbid_extra = true`, `init_typed = true`, `warn_required_dynamic_aliases = true`) — see Deviation #2.

### Task 2 — Test suite + `valid_config_dict` fixture (commit `ad3b67c`)

- `pastor_tracker/tests/conftest.py` — replaced placeholder body with `valid_config_dict` fixture returning all 25 fields. Documented as the single source-of-truth baseline for downstream phases.
- `pastor_tracker/tests/test_config.py` — 10 test functions (all passing), names mirror VALIDATION.md per-task map exactly:

| Test | Requirement | Behavior verified |
|------|------------|-------------------|
| `test_loads_from_json_file` | CFG-01 | `config.json` field values flow into `Config()` when no env override |
| `test_env_overrides_json` | CFG-01 | `PTS_ARDUINO_PORT=COM9` beats `config.json arduino_port=COM7` |
| `test_negative_pan_velocity_rejected` | CFG-02 | `pan_max_velocity_deg_per_sec=-1` → `ValidationError` |
| `test_motor_speed_out_of_range_rejected` | CFG-02 | `motor_max_speed_steps_per_sec=99` (below firmware floor 100) → `ValidationError` |
| `test_motor_speed_above_ceiling_rejected` | CFG-02 | `motor_max_speed_steps_per_sec=60_000` (above firmware ceiling 50_000) → `ValidationError` |
| `test_max_below_min_rejected` | CFG-02 (cross-field) | `motor_angle_min_deg=10, motor_angle_max_deg=5` → `ValidationError` from `@model_validator` |
| `test_unknown_field_rejected` | CFG-03 | `Config(nonexistent_field=42)` → `ValidationError` via `extra="forbid"` |
| `test_default_construction_succeeds_and_is_frozen` | CFG-01 + CFG-03 | Defaults valid; `c.arduino_baud = 9600` raises (frozen) |
| `test_protocol_version_must_be_2` | CFG-04 | `Config(arduino_protocol_version=1)` → `ValidationError` via `Literal[2]` |
| `test_protocol_version_default_is_2` | CFG-04 | Default value is the only legal value |

- Zero mocks (`grep -c '(unittest\.mock\|mocker\.\|MagicMock\|Mock\()' tests/test_config.py` → 0) — CFG-03 contract demands real ValidationError flow.

## Wave 1 Verification (all green)

| Gate | Command | Exit | Result |
|------|---------|------|--------|
| Field count | `python -c "from pastor_tracker.config import Config; print(len(Config.model_fields))"` | 0 | `25` |
| Field name set | `python` set-equality check vs Plan acceptance name list | 0 | `missing: set(); extra: set()` |
| Class declaration | `grep -c '^class Config(BaseSettings):' src/pastor_tracker/config.py` | 0 | `1` |
| `Literal[2]` present | `grep -c 'arduino_protocol_version: Literal\[2\]' src/pastor_tracker/config.py` | 0 | `1` |
| `extra="forbid"` configured | `grep -nE 'extra="forbid"' src/pastor_tracker/config.py` | 0 | line 7 (docstring) + line 82 (model_config) — see Deviation #3 |
| `frozen=True` configured | `grep -c 'frozen=True' src/pastor_tracker/config.py` | 0 | `1` |
| `JsonConfigSettingsSource` wired | `grep -c 'JsonConfigSettingsSource' src/pastor_tracker/config.py` | 0 | `2` (import + use) |
| Cross-field validator | `grep -c '@model_validator' src/pastor_tracker/config.py` | 0 | `1` |
| Frozen mutation | `c = Config(); c.arduino_baud = 9600` | non-zero | `ValidationError` raised |
| CFG-02 below-floor | `Config(motor_max_speed_steps_per_sec=99.0)` | non-zero | `ValidationError` |
| CFG-02 above-ceiling | `Config(motor_max_speed_steps_per_sec=60_000.0)` | non-zero | `ValidationError` |
| CFG-02 negative | `Config(pan_max_velocity_deg_per_sec=-1.0)` | non-zero | `ValidationError` |
| CFG-02 cross-field | `Config(motor_angle_min_deg=10, motor_angle_max_deg=5)` | non-zero | `ValidationError` |
| CFG-03 unknown field | `Config(nonexistent_field=42)` | non-zero | `ValidationError` |
| CFG-04 wrong proto | `Config(arduino_protocol_version=1)` | non-zero | `ValidationError` |
| Lint (config) | `ruff check src/pastor_tracker/config.py` | 0 | "All checks passed!" |
| Lint (tests) | `ruff check tests/test_config.py tests/conftest.py` | 0 | "All checks passed!" |
| Lint (full src) | `ruff check src/` | 0 | "All checks passed!" |
| Type (config) | `mypy src/pastor_tracker/config.py` | 0 | "no issues found in 1 source file" |
| Type (full src) | `mypy src/` | 0 | "no issues found in 10 source files" |
| Tests (config) | `pytest tests/test_config.py -x -ra` | 0 | "10 passed in 0.55s" |
| Tests (full suite) | `pytest tests/ -ra` | 0 | "11 passed in 0.59s" (10 config + 1 logging) |
| No mocks | `grep -cE '(unittest\.mock\|mocker\.\|MagicMock\|Mock\()' tests/test_config.py` | 0 | `0` |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 — Bug] Plan field-count claim was off-by-one (24 vs 25)**

- **Found during:** Task 1 design review
- **Issue:** Plan narrative + acceptance grep claimed "23 from PROMPT.md + 1 = 24" and `assert len(Config.model_fields) == 24`. Direct count of PROMPT.md `## Config` block (lines 230-254) yields 25 fields — `arduino_ready_timeout_sec` is *already* in PROMPT.md (line 254), added by commit `e886032`. The plan's verbatim module body in Task 1 also defines exactly 25 fields, and the plan's explicit acceptance name-set (line 306 of `01-02-PLAN.md`) lists 25 names. Only the narrative arithmetic and the `len() == 24` assertion are stale.
- **Fix:** Implemented 25 fields matching PROMPT.md and the plan's verbatim body + name-set. Per `<plan_specifics>` directive ("If your reading of PROMPT.md disagrees with the plan's name list by ±1, prefer PROMPT.md"), this is the contract-honoring resolution.
- **Verification:** `len(Config.model_fields) == 25`; set-equality against the plan's name-set returns `missing: set(); extra: set()`.
- **Files modified:** `pastor_tracker/src/pastor_tracker/config.py`
- **Commit:** `7d3267b`
- **Recommended doc patch (out-of-scope this plan):** `01-02-PLAN.md` line 26, line 47-48, line 295, line 305 — replace `24` with `25`. Logged for next planning pass.

**2. [Rule 3 — Blocking] mypy `disallow_any_explicit` fired on every BaseSettings subclass**

- **Found during:** Task 1 verification (`mypy src/pastor_tracker/config.py`)
- **Issue:** `mypy --strict` + `disallow_any_explicit = true` (configured in `pyproject.toml` by Plan 01-01) raised `error: Explicit "Any" is not allowed [explicit-any]` on the `class Config(BaseSettings):` line. Root cause: the `pydantic.mypy` plugin synthesizes `__init__(self, *, ..., **kwargs: Any)` by default — exposing an explicit `Any`. Without a fix, no Pydantic class in any future phase can pass mypy.
- **Fix:** Added `[tool.pydantic-mypy]` block to `pyproject.toml` with `init_forbid_extra = true`, `init_typed = true`, `warn_required_dynamic_aliases = true`. The plugin then synthesizes a typed kw-only `__init__` matching the field annotations exactly, no `**kwargs: Any`.
- **Verification:** `mypy src/` → "Success: no issues found in 10 source files".
- **Files modified:** `pastor_tracker/pyproject.toml`
- **Commit:** `7d3267b` (bundled with Task 1 because the plugin tightening is config.py's enabling fix)
- **Side benefit:** Every future `BaseModel` / `BaseSettings` in Phases 2-7 inherits this hardening for free; the plugin will also reject construction-site typos at type-check time (init_forbid_extra catches kwargs not in the model).

**3. [Rule 1 — Doc cosmetic] `extra="forbid"` appears twice in config.py**

- **Found during:** Task 1 acceptance grep
- **Issue:** Plan acceptance criterion `grep -c 'extra="forbid"' returns 1` actually returns 2 — the module docstring also references `extra="forbid"` to explain the class behavior (line 7), in addition to the actual `model_config` setting (line 82).
- **Fix:** None — the docstring reference is intentional documentation, and the semantic invariant (`extra="forbid"` configured exactly once in `model_config`) holds. Adjusting the docstring to obscure the contract would harm readability.
- **Recommended doc patch:** Plan acceptance criterion should read `grep -cE '^\s*extra="forbid",' returns 1` (anchor to indented module_config setting). Logged for next planning pass.

**4. [Rule 1 — Bug] `from __future__ import annotations` made `-> "Config"` quotes unnecessary (UP037)**

- **Found during:** Task 1 first ruff pass
- **Issue:** Initial validator return type `def _max_above_min(self) -> "Config":` triggered `UP037 Remove quotes from type annotation` because PEP 563 deferred evaluation makes the forward-reference quotes redundant.
- **Fix:** Changed to `def _max_above_min(self) -> Config:` (no quotes). PEP 563 means this resolves at type-check time without runtime errors.
- **Verification:** `ruff check src/pastor_tracker/config.py` → "All checks passed!"
- **Files modified:** `pastor_tracker/src/pastor_tracker/config.py`
- **Commit:** `7d3267b`

**5. [Rule 1 — Bug] Test file initial import order failed I001**

- **Found during:** Task 2 ruff pass
- **Issue:** Initial test file had `from pydantic import ValidationError` and `from pastor_tracker.config import Config` placed inside the `import` group rather than separated, triggering `I001 Import block is un-sorted or un-formatted`.
- **Fix:** Auto-applied via `ruff check tests/test_config.py --fix` — separates third-party (`pytest`, `pydantic`) from first-party (`pastor_tracker.config`) with a blank line.
- **Verification:** `ruff check tests/test_config.py tests/conftest.py` → "All checks passed!"
- **Files modified:** `pastor_tracker/tests/test_config.py`
- **Commit:** `ad3b67c`

### Auth gates

None — purely local file editing + Pydantic + pytest. No external services touched.

## Key Decisions

1. **Field count is 25, not 24** — see Deviation #1. Anchored to PROMPT.md `## Config` block (the source of truth) and the plan's own explicit name-set, not the plan's narrative arithmetic.
2. **Tightened `[tool.pydantic-mypy]`** — see Deviation #2. One-time pyproject change; benefits every future BaseModel/BaseSettings in Phases 2-7.
3. **`@model_validator(mode="after")` for cross-field rule** — Pitfall 8 from RESEARCH.md. `mode="after"` is the only mode that sees both motor_angle_min_deg and motor_angle_max_deg as validated typed floats.
4. **Source precedence locked in `settings_customise_sources`** — explicit return tuple `(init_settings, env_settings, dotenv_settings, JsonConfigSettingsSource(settings_cls), file_secret_settings)`. Verified by `test_env_overrides_json` (env beats JSON) and `test_loads_from_json_file` (JSON beats defaults).
5. **17 named module-level constants for every numeric bound** — kept ruff PLR2004 clean without sprinkling `# noqa`. Each constant carries a comment naming its source (e.g. `_FIRMWARE_PC_HEARTBEAT_TIMEOUT_MS = 1_000  # firmware halts on > 1000 ms heartbeat silence`).
6. **`valid_config_dict` fixture as a flat 25-key dict** — easiest baseline for downstream phases to override per test (`{**valid_config_dict, "arduino_baud": 9600}`). Could have been a `Config` factory but that would couple every test to the constructor when many only need a kwargs map.

## Stub / Threat Notes

- **No stubs introduced.** Every field has a real default sourced from PROMPT.md, every validator runs real Pydantic checks, every test asserts a real `ValidationError`. Mocks are explicitly absent (and grep-verified).
- **Threat surface matches plan's `<threat_model>` exactly.** All 6 STRIDE rows (T-1.02-01 through T-1.02-06) are mitigated by the implementation:
  - T-1.02-01 (out-of-range from `config.json`): mitigated by `Field(le=...)` on every numeric field; `test_motor_speed_above_ceiling_rejected` proves the gate fires.
  - T-1.02-02 (env-var injection): same `Field(le=...)` mitigations — env values flow through identical Pydantic validation.
  - T-1.02-03 (unknown field via typo): mitigated by `extra="forbid"`; `test_unknown_field_rejected` proves it.
  - T-1.02-04 (wrong protocol version): mitigated by `Literal[2]`; `test_protocol_version_must_be_2` proves it.
  - T-1.02-05 (info disclosure): no secrets in Phase 1 Config; if added later, use `pydantic.SecretStr`.
  - T-1.02-06 (pickle gadget via custom JSON loader): we use only `JsonConfigSettingsSource` (stdlib `json` under the hood, no eval, no pickle).
- **No new threat surface beyond the registered model.** No network paths, no file-system writes, no shell-out, no schema changes at trust boundaries.

## Self-Check: PASSED

Files created (all exist):

- `pastor_tracker/src/pastor_tracker/config.py` — FOUND
- `pastor_tracker/tests/test_config.py` — FOUND
- `.planning/phases/01-scaffold-config-core-math/01-02-SUMMARY.md` — FOUND (this file)

Files modified (all exist):

- `pastor_tracker/tests/conftest.py` — FOUND (replaced placeholder body with `valid_config_dict`)
- `pastor_tracker/pyproject.toml` — FOUND (`[tool.pydantic-mypy]` block added)

Commits exist on `fresh-2026`:

- `7d3267b feat(01-02): add frozen Pydantic v2 Config with all 25 fields, env+JSON sources` — FOUND
- `ad3b67c test(01-02): cover CFG-01..04 with 10 ValidationError-driven tests + valid_config_dict fixture` — FOUND

All 7 must_haves green. All 12 prompt-level must_haves green (modulo Deviation #1: field count = 25 not 24, by direct count of PROMPT.md). Plan 01-02 complete.
