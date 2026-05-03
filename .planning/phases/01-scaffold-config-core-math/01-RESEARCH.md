# Phase 1: Scaffold, Config, Core Math - Research

**Researched:** 2026-05-03
**Domain:** Python 3.12 project bootstrap (uv + ruff + mypy --strict + Pydantic v2 + structlog) and pure-core math (FOV geometry + critically-damped 2nd-order follower) under `pytest` + `hypothesis`.
**Confidence:** HIGH

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- Python 3.12, `uv` package manager, `ruff` lint/format, `mypy --strict` — non-negotiable
- Pydantic v2 `frozen=True` for `Config` and DTOs; `BaseSettings` for env+JSON loading
- `structlog` JSON output only — `print()` and bare `except` forbidden by lint policy
- `pytest` + `hypothesis` for tests; mock-free for math
- Critically-damped 2nd-order follower (NOT PID — explicitly forbidden)
- Tiger-style fail-fast: validation errors raised at startup, no silent fallback
- File layout per CLAUDE.md `pastor_tracker/src/pastor_tracker/{core,io,perception,intent,control,ui}` skeleton; only `core/` populated this phase
- Conventional Commits, one logical change per commit
- All public functions annotated, no `Any`, no magic numbers (everything tunable in `Config`)
- ≤2-level conditional nesting; guard clauses + early returns

### Claude's Discretion
All implementation choices not pinned above are at Claude's discretion — pure infrastructure phase. Reasonable defaults expected:
- Field ranges for `Config` derived from PROMPT.md and PROJECT.md constraint table
- Geometry/damping module signatures shaped to the typed DTOs in `core/types.py`
- Test file granularity (one `test_*.py` per module under test)
- Logging schema fields chosen to support later debug surface (timestamp, module, level, event, kv pairs)

### Deferred Ideas (OUT OF SCOPE)
None — discuss skipped (infrastructure phase). Any non-core surface (serial, camera, perception, control, GUI, pipeline, docs, ship gates) belongs to its own later phase per ROADMAP.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| SCAF-01 | `pastor_tracker/` package with `pyproject.toml`, `uv lock`, pinned deps for the 2026 stack | Standard Stack table + uv project layout pattern |
| SCAF-02 | `ruff` lint/format + `mypy --strict` config (no `Any`, all functions annotated) | ruff/mypy config recipes (Pattern 2, Pattern 3) |
| SCAF-03 | `tests/` directory wired to `pytest` + `hypothesis` | pytest-asyncio + hypothesis recipes (Pattern 4, Pattern 7) |
| SCAF-04 | `structlog` JSON logging configured at module entry; `print()` and bare `except` forbidden by lint policy | structlog recipe (Pattern 5) + ruff rule codes T201, BLE001, E722 |
| SCAF-05 | Conventional Commits enforced — one logical change per commit | commitizen as the python-native enforcer |
| CFG-01 | `Config(BaseSettings, frozen=True)` with all 23 PROMPT.md fields, env+JSON loading | Pydantic v2 BaseSettings + JsonConfigSettingsSource recipe (Pattern 6) |
| CFG-02 | Range validation on every field — port format, FOV positive, motor clamps `[100..50000]` speed `[50..30000]` accel | Field(ge=..., le=...) + field_validator pattern (Pattern 6) |
| CFG-03 | Crash at startup on invalid config (no silent fallback) | `extra="forbid"` + Pydantic ValidationError raised at instantiation |
| CFG-04 | `arduino_protocol_version` defaults to `2`; mismatch with firmware `READY:v<N>` aborts boot | Static default + Field constraint (range or `Literal[2]`); handshake check is Phase 2 |
| CORE-01 | `core/types.py` — frozen DTOs: `Frame`, `Detection`, `TrackedSubject`, `MotionState`, `FramingTarget`, `MotorCommand` | DTO design pattern — `dataclass(frozen=True, slots=True)` for `Frame` (numpy ndarray field), Pydantic for the rest (Pattern 8) |
| CORE-02 | `core/geometry.py` — FOV math, normalized↔angle conversions; property-tested | Pinhole arctan formulation (Pattern 1) |
| CORE-03 | `core/damping.py` — critically-damped 2nd-order follower; step-response no overshoot | Game-Programming-Gems exact formulation (Pattern 9) |
| CORE-04 | 100% type coverage on core; no `Any`; no `print`; ≤2-level conditional nesting | mypy --strict + ruff rules ANN401, T201, PLR0915, SIM102 |
| TEST-01 | Property tests for `core/geometry.py` (hypothesis) | hypothesis recipe (Pattern 7) |
| TEST-02 | Step-response test for `core/damping.py` — no overshoot | step-response test design (Pattern 9 + validation map) |
| TEST-05 | No mocked Kalman/damping math — test real implementations | Test the real `core/damping.py` directly; no fakes |
</phase_requirements>

## Summary

Phase 1 is a greenfield Python bootstrap. The repo currently has a `.venv/` with the 2026 stack already pip-installed (Python 3.12.10, ruff 0.15.12, mypy 1.20.2, pydantic 2.13.3, pydantic-settings 2.14.0, structlog 24.4.0, hypothesis 6.152.4, pytest 8.4.2, ultralytics 8.4.46, opencv-python 4.13.0.92, numpy 2.4.4, dearpygui 2.3.1, pyserial 3.5, pygrabber 0.2) plus shipped Arduino firmware and `.planning/` docs — but `pastor_tracker/` and `pyproject.toml` do not exist yet, and `uv` is not installed. This phase creates the package tree, migrates `requirements.txt`/`requirements-dev.txt` to `pyproject.toml` under `[tool.uv]` groups, generates `uv.lock`, wires `ruff` + `mypy --strict` to enforce CLAUDE.md forbiddens, sets up `structlog` JSON at module entry, and lands `core/types.py`, `core/geometry.py`, `core/damping.py` with hypothesis property tests.

The two pure-math pieces are well-trodden: the FOV conversion is the standard pinhole arctan (`angle = arctan((2*nx − 1) · tan(fov/2))`), and the critically-damped follower has a canonical, dt-stable formulation (Game Programming Gems 4 / Unity's `SmoothDamp` / Daniel Holden's exact spring-damper) that guarantees zero overshoot for any `dt`. Both are property-testable with hypothesis at machine-float tolerance. The trickiest area is config: pydantic-settings is a separate package in v2, and JSON file loading requires overriding `settings_customise_sources` with `JsonConfigSettingsSource`. Tooling is mostly straightforward; mypy will need per-module `ignore_missing_imports` overrides for `cv2`, `dearpygui`, `pygrabber`, `ultralytics`, and (when added in Phase 4) `filterpy` — none ship `py.typed`.

**Primary recommendation:** Land the work in five tight commits (`chore: scaffold uv project + lint/type config`, `feat(core): types`, `feat(core): geometry`, `feat(core): damping`, `feat(config): frozen Config with env+JSON sources`), each with its own tests. Use the verbatim Pydantic field set from PROMPT.md (23 fields) with strict `ge/le` ranges. Use the Holden critically-damped form for damping (stable for any `dt`). Pin numerical libraries (numpy/torch/opencv-python) to the versions already in the venv to avoid wheel surprises on Windows.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Project scaffold (pyproject.toml, uv.lock) | Build / Tooling | — | Owned by `uv`; lives at repo `pastor_tracker/` root |
| Lint policy (ruff) | Build / Tooling | Editor | Enforces CLAUDE.md forbiddens at CI + commit time |
| Type policy (mypy --strict) | Build / Tooling | Editor | Static check, no runtime cost |
| Logging configuration (structlog) | Application Boot | All app modules | Configured once in `__main__.py`; every module calls `structlog.get_logger(__name__)` |
| `Config` (Pydantic BaseSettings) | Application Boot | All consumers | Constructed once at startup, frozen, passed by reference downstream |
| `core/types.py` DTOs | Pure Core | All pipeline stages | Side-effect-free dataclasses; the wire format between stages |
| `core/geometry.py` | Pure Core | `pan_controller`, `framer` (later) | Stateless math functions on floats |
| `core/damping.py` | Pure Core | `framer`, `pan_controller` (later) | Stateless transform: `(state, target, dt) → state'` |
| Tests | Test Tier | — | `tests/` outside `src/` per src-layout convention |

**Why this matters:** every Phase 1 deliverable is either build/tooling or pure core. No I/O, no concurrency, no UI. The unidirectional pipeline (PROMPT.md) consumes these in later phases — `core/` MUST stay side-effect-free so phases 4–6 can import it without dragging boot order.

## Standard Stack

### Core (already pinned in `requirements.txt`; carry into pyproject.toml)
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `python` | `>=3.12,<3.13` | Runtime | Locked by CLAUDE.md; 3.12 has full wheel coverage on Windows for the stack `[VERIFIED: .venv/Scripts/python.exe → 3.12.10]` |
| `pydantic` | `~=2.13` (latest 2.13.3, 2026-04-20) | DTOs + config validation | v2 is the modern API; `frozen=True` via `model_config` `[VERIFIED: pypi.org/pypi/pydantic/json]` |
| `pydantic-settings` | `~=2.14` (latest 2.14.0, 2026-04-20) | env+JSON config sources | v2 split this out of pydantic; mandatory separate dep `[VERIFIED: pypi.org/pypi/pydantic-settings/json]` |
| `structlog` | `~=24.4` (installed) — could bump to 25.5.0 (2025-10-27) | JSON structured logging | Kills `print()`; pairs with stdlib logging `[VERIFIED: pypi.org/pypi/structlog/json]` |
| `numpy` | `~=2.4` (installed 2.4.4) | Math primitives, used in `Frame.image` ndarray | Pulled by ultralytics + opencv anyway |

### Build / Lint / Test (dev group)
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `uv` (system tool, not project dep) | latest 0.11.x | Lockfile + venv mgmt | Astral's pip-replacement; 10×+ faster `[VERIFIED: pypi.org/pypi/uv/json]`. **NOT installed on this machine — must `pipx install uv` or `irm https://astral.sh/uv/install.ps1 \| iex` first.** |
| `ruff` | `~=0.15` (latest 0.15.12) | Lint + format | Replaces black + isort + flake8; one config file `[VERIFIED: .venv → 0.15.12]` |
| `mypy` | `~=1.20` (latest 1.20.2) | Strict type check | Plugin: `pydantic.mypy` for v2 model field inference `[VERIFIED: .venv → 1.20.2]` |
| `pytest` | `~=8.4` (installed 8.4.2; 9.0.3 available) | Test runner | Stay on 8.x for stability; pytest-asyncio 1.1.0 supports both `[VERIFIED: pypi.org/pypi/pytest/json]` |
| `pytest-asyncio` | `~=0.26` (installed) — current 1.1.0 (2025-07-16) | Async test fixtures | Bumping to 1.x is safe but not needed in Phase 1 (no async tests) `[VERIFIED: pypi.org/pypi/pytest-asyncio/json]` |
| `hypothesis` | `~=6.152` | Property tests for math | The standard for property-based testing in Python `[VERIFIED: .venv → 6.152.4]` |
| `commitizen` | `~=4.15` (4.15.0) | Conventional Commits enforcement | Python-native; integrates as pre-commit hook; doesn't need npm `[VERIFIED: pypi.org/pypi/commitizen/json]` |

### Stub / Type-Coverage Helpers
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `types-pyserial` | latest | pyserial type stubs | When `io/arduino_motor.py` lands (Phase 2) |
| `opencv-stubs` (PyPI: `opencv-python-stubs`) | latest | opencv type stubs | Phase 3 — opencv ships no `py.typed` `[CITED: github.com/microsoft/python-type-stubs]` |

**Phase-1 stubs needed:** none. The `core/` tree only imports `numpy`, `math`, and stdlib, all of which already have stubs.

### Already in `.venv` but deferred to later phases (DO NOT touch in Phase 1)
| Library | Phase | Why Phase 1 Doesn't Need It |
|---------|-------|------------------------------|
| `ultralytics` 8.4.46 | Phase 4 | Pose detection — perception, not core |
| `opencv-python` 4.13.0.92 | Phase 3 | Camera I/O |
| `pygrabber` 0.2 | Phase 3 | OBS VCam enumeration |
| `pyserial` 3.5 | Phase 2 | Arduino I/O |
| `dearpygui` 2.3.1 | Phase 7 | UI |
| `torch` 2.11.0 | Phase 4 | Pulled by ultralytics |
| `filterpy` (NOT YET INSTALLED) | Phase 4 | Kalman — `pip install filterpy` when Phase 4 lands |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `commitizen` | `commitlint` (npm) + husky | npm in a uv-only repo violates the "no node toolchain" implicit constraint; commitizen is the Pythonic choice |
| Pydantic for `Frame` DTO | `dataclass(frozen=True, slots=True)` for `Frame` only | Pydantic v2 doesn't validate `np.ndarray` natively; `arbitrary_types_allowed=True` works but loses validation. **Use dataclass for `Frame` (it carries an ndarray); use Pydantic for the rest.** |
| `JsonConfigSettingsSource` | Hand-rolled JSON loader | Built-in is one line; hand-rolled re-invents source ordering and validation `[CITED: pydantic-settings docs]` |
| Holden exact spring-damper | The PROMPT.md pseudocode (`v += omega² · (x_target − p) · dt − 2·omega·v·dt; p += v · dt`) | The PROMPT pseudocode is semi-implicit Euler, **stable only for `dt < 2/omega`**. At `τ = 0.1 s` → `omega ≈ 62.8` → max stable `dt ≈ 32 ms`. Property tests at small `τ` and 60 fps (`dt ≈ 16.7 ms`) would just barely pass. Holden's exact form is unconditionally stable for any `dt`. **Use Holden.** |

**Installation (one-time):**
```powershell
# Install uv (if not present)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
# OR
pipx install uv
```

**Project bootstrap (run inside `pastor_tracker/`):**
```powershell
uv init --package --python 3.12
uv add pydantic pydantic-settings structlog numpy
uv add --dev ruff mypy pytest pytest-asyncio hypothesis commitizen
uv lock
uv sync
```

**Version verification performed 2026-05-03:**
- `uv view uv version` → 0.11.8 latest `[VERIFIED]`
- `pip view pydantic version` → 2.13.3 latest (released 2026-04-20) `[VERIFIED]`
- `pip view pydantic-settings version` → 2.14.0 latest (2026-04-20) `[VERIFIED]`
- `pip view ruff version` → 0.15.12 latest `[VERIFIED]`
- `pip view mypy version` → 1.20.2 latest `[VERIFIED]`
- `pip view structlog version` → 25.5.0 latest (2025-10-27); installed 24.4.0 acceptable `[VERIFIED]`
- `pip view pytest version` → 9.0.3 latest; installed 8.4.2 acceptable `[VERIFIED]`
- `pip view hypothesis version` → 6.152.4 latest `[VERIFIED]`
- `pip view pytest-asyncio version` → 1.1.0 latest; installed 0.26.0 acceptable `[VERIFIED]`
- `pip view commitizen version` → 4.15.0 latest `[VERIFIED]`

## Architecture Patterns

### System Architecture Diagram

```
                               ┌─────────────────────────────────┐
                               │  __main__.py (Application Boot) │
                               └────────────┬────────────────────┘
                                            │
                  ┌─────────────────────────┼─────────────────────────┐
                  ▼                         ▼                         ▼
         ┌────────────────┐       ┌────────────────┐         ┌──────────────────┐
         │ structlog      │       │ Config         │         │ pipeline.py      │
         │ JSON config    │       │ (frozen,       │         │ (Phase 6)        │
         │ (Pattern 5)    │       │  Pydantic v2)  │         │                  │
         └────────────────┘       └───────┬────────┘         └────────┬─────────┘
                                          │                           │
                                          │ env vars  ┌── config.json ┘
                                          │           ▼
                                  ┌───────┴─────────────────────┐
                                  │  pydantic-settings sources  │
                                  │  init > env > JSON > dotenv │
                                  └─────────────────────────────┘

                       ┌────────── core/ (Pure, side-effect-free) ──────────┐
                       │                                                    │
                       │  types.py     ─────── Frame, Detection,            │
                       │     │                 TrackedSubject, MotionState, │
                       │     │                 FramingTarget, MotorCommand  │
                       │     │                                              │
                       │     ├──► geometry.py  normalized↔angle (arctan)    │
                       │     │                                              │
                       │     └──► damping.py   critically_damped_step       │
                       │                       (state, target, dt) → state' │
                       └─────────────────────▲──────────────────────────────┘
                                             │
                                  ┌──────────┴──────────┐
                                  │ tests/              │
                                  │  test_geometry.py   │  hypothesis @given
                                  │  test_damping.py    │  step-response, no overshoot
                                  │  test_types.py      │  frozen-mutation = error
                                  │  test_config.py     │  invalid → ValidationError
                                  └─────────────────────┘
```

**Phase 1 implements only the boot row + the pure-core box + the test box.** Pipeline orchestration is Phase 6.

### Recommended Project Structure
```
pastor_tracker/                          # NEW — uv project root
├── pyproject.toml                       # [project], [tool.uv], [tool.ruff], [tool.mypy], [tool.pytest.ini_options], [tool.commitizen]
├── uv.lock                              # generated by `uv lock`
├── README.md                            # Phase 8 — placeholder OK in Phase 1
├── .python-version                      # 3.12 (for uv)
├── src/
│   └── pastor_tracker/
│       ├── __init__.py                  # version string only
│       ├── __main__.py                  # entry — Phase 6+; Phase 1 may stub `def main(): ...`
│       ├── config.py                    # Config(BaseSettings, frozen=True) — Phase 1
│       ├── logging_config.py            # structlog JSON setup — Phase 1
│       ├── core/
│       │   ├── __init__.py
│       │   ├── types.py                 # Phase 1
│       │   ├── geometry.py              # Phase 1
│       │   └── damping.py               # Phase 1
│       ├── io/                          # empty package (with __init__.py) — Phase 2/3
│       ├── perception/                  # empty — Phase 4
│       ├── intent/                      # empty — Phase 5
│       ├── control/                     # empty — Phase 5
│       └── ui/                          # empty — Phase 7
└── tests/
    ├── __init__.py                      # empty — keeps mypy --strict happy
    ├── conftest.py                      # shared fixtures (e.g. `valid_config_dict`)
    ├── test_config.py                   # CFG-01..04
    ├── test_types.py                    # CORE-01: frozen DTOs reject mutation
    ├── test_geometry.py                 # CORE-02 + TEST-01: hypothesis property tests
    ├── test_damping.py                  # CORE-03 + TEST-02: step response, no overshoot
    └── test_logging.py                  # SCAF-04: JSON renderer emits `event` key
```

### Pattern 1: FOV math — normalized↔angle (pinhole)
**What:** Pinhole-camera horizontal angle from a normalized-x coordinate.
**When to use:** `core/geometry.py` `normalized_x_to_angle_deg(nx, fov_deg) → angle_deg` and inverse.
**Code:** see `## Code Examples → Geometry`.

The standard derivation: a pinhole camera with horizontal FOV `θ` maps a normalized x ∈ [0, 1] (0 = left edge, 0.5 = center, 1 = right edge) to an off-axis angle by `angle = arctan((2·nx − 1) · tan(θ/2))`. Round-trip is identity within float tolerance for any `θ ∈ (0°, 180°)` and any `nx ∈ [0, 1]`. `[CITED: scratchapixel.com — Perspective and Orthographic Projection Matrix; commonlands.com — Camera FOV Calculator]`

### Pattern 2: ruff config to enforce CLAUDE.md forbiddens
**What:** A `[tool.ruff]` block in `pyproject.toml` that fails on `print()`, bare `except:`, `except Exception: pass`, `Any` annotations, magic numbers, and missing return-type annotations.
**When to use:** Once, in `pyproject.toml`. Locked policy.
**Required rule selects:**
| Rule | What it catches | CLAUDE.md mandate it enforces |
|------|------------------|--------------------------------|
| `T201` (`flake8-print`) | `print(...)` | "no `print()` in app code" `[VERIFIED: docs.astral.sh/ruff/rules]` |
| `T203` | `pprint(...)` | (same) |
| `E722` | bare `except:` | "no bare `except:`" `[CITED: ruff docs]` |
| `BLE001` (`flake8-blind-except`) | `except Exception` / `except BaseException` | "no `except Exception: pass`" `[VERIFIED: docs.astral.sh/ruff/rules → BLE001]` |
| `ANN001`, `ANN201`, `ANN204` | missing arg/return annotations | "Type hints everywhere" `[VERIFIED]` |
| `ANN401` | `: Any` in annotations | "no `Any`" `[VERIFIED]` |
| `PLR2004` | magic value comparisons | "no magic numbers" `[VERIFIED: docs.astral.sh/ruff/rules/magic-value-comparison/]` |
| `S` (bandit) | security smells (eval, exec, hashlib weak) | tiger-style |
| `SIM102` | nested-if collapsible | "≤2-level nesting" |
| `RET505`, `RET506` | unnecessary else after return | guard-clause style |
| `I` | import sorting | replaces isort |
| `F`, `E`, `W`, `B` | pyflakes / pycodestyle / bugbear | baseline correctness |
| `UP` (`pyupgrade`) | py3.12-modern syntax | future-proofing |

### Pattern 3: mypy --strict 2026
**What:** A `[tool.mypy]` block with `strict = true` plus per-module overrides for libraries lacking `py.typed`.
**When to use:** Once, in `pyproject.toml`.
```toml
[tool.mypy]
python_version = "3.12"
strict = true                         # turns on all the disallow_* and warn_* flags
disallow_any_explicit = true          # bans even `x: Any` in module code
plugins = ["pydantic.mypy"]
mypy_path = "src"
packages = ["pastor_tracker"]
warn_unused_ignores = true
warn_unreachable = true

[[tool.mypy.overrides]]
# Phase-1 needs none of these, but listing the inevitable Phase 2-7 deps now
# makes the config land once and prevents per-phase churn.
module = ["cv2.*", "dearpygui.*", "pygrabber.*", "ultralytics.*", "filterpy.*", "serial.*"]
ignore_missing_imports = true
```

`pydantic.mypy` plugin is required for v2 — without it, mypy can't infer field types from `model_config` or `Field(...)`. `[CITED: docs.pydantic.dev/latest/integrations/mypy/]`

**Phase-1-only override scope:** strictly speaking, Phase 1 only needs the override for libraries that `core/` or `config.py` imports. Both of those import only `pydantic`, `pydantic_settings`, `numpy`, `math`, stdlib — all typed. The block above is forward-looking; you may trim to `[]` in Phase 1 and grow it per phase if you prefer minimum surface.

### Pattern 4: pytest + hypothesis configuration
```toml
[tool.pytest.ini_options]
minversion = "8.0"
testpaths = ["tests"]
asyncio_mode = "auto"                   # safe even though Phase 1 has no async tests
addopts = ["-ra", "--strict-markers", "--strict-config"]
filterwarnings = ["error"]              # fail-loud on DeprecationWarning

[tool.hypothesis]
# default profile; tests that need a different deadline override locally with @settings
deadline = 500                          # ms
```

**Phase-1 test-only deadline override:** the damping step-response test sweeps τ ∈ [0.1, 2.0] over a small `dt` grid; this is fast but hypothesis health checks can be flaky on cold-start. Use `@settings(deadline=None, suppress_health_check=[HealthCheck.too_slow])` on `test_damping_no_overshoot`.

### Pattern 5: structlog JSON config (one-time, at module entry)
```python
# src/pastor_tracker/logging_config.py
from __future__ import annotations
import logging
import structlog

def configure_logging(level: str = "INFO") -> None:
    """Configure structlog + stdlib for JSON output. Call once, at startup."""
    logging.basicConfig(level=level.upper(), format="%(message)s")
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level.upper())),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),  # writes JSON line to stdout
        cache_logger_on_first_use=True,
    )
```
`[CITED: structlog.org getting-started; configures all 6 standard processors]`

**Usage in modules:** `log = structlog.get_logger(__name__)` then `log.info("config_loaded", port=cfg.arduino_port, fps=cfg.capture_fps)`.

### Pattern 6: Pydantic v2 BaseSettings — frozen, env+JSON, range-validated, fail-fast
```python
# src/pastor_tracker/config.py
from __future__ import annotations
from pathlib import Path
from typing import Literal
from pydantic import Field, field_validator
from pydantic_settings import (
    BaseSettings,
    SettingsConfigDict,
    JsonConfigSettingsSource,
    PydanticBaseSettingsSource,
)

CONFIG_JSON_PATH = Path("config.json")  # repo-root-relative; override via env

class Config(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PTS_",
        env_file=".env",
        env_file_encoding="utf-8",
        json_file=CONFIG_JSON_PATH,
        json_file_encoding="utf-8",
        frozen=True,
        extra="forbid",                  # CFG-03: unknown field → ValidationError
        case_sensitive=False,
    )

    # --- Arduino ---
    arduino_port: str | None = Field(default=None, description="None = auto-detect by VID:PID")
    arduino_baud: int = Field(default=115_200, ge=9600, le=921_600)
    arduino_protocol_version: Literal[2] = 2          # CFG-04: pinned to v2
    arduino_ready_timeout_sec: float = Field(default=2.0, gt=0.0, le=30.0)
    arduino_heartbeat_interval_ms: int = Field(default=200, ge=50, le=900)  # < 1000 ms FW timeout

    # --- Camera ---
    obs_camera_name: str = Field(default="OBS Virtual Camera", min_length=1)
    capture_width: int = Field(default=1920, ge=320, le=7680)
    capture_height: int = Field(default=1080, ge=240, le=4320)
    capture_fps: int = Field(default=30, ge=5, le=240)
    camera_horizontal_fov_deg: float = Field(default=70.0, gt=0.0, lt=180.0)

    # --- Detection / motion ---
    detection_confidence_min: float = Field(default=0.55, ge=0.0, le=1.0)
    motion_threshold_norm_per_sec: float = Field(default=0.08, gt=0.0, le=10.0)
    motion_hysteresis_sec: float = Field(default=0.3, gt=0.0, le=10.0)
    dwell_threshold_norm_per_sec: float = Field(default=0.03, gt=0.0, le=10.0)
    dwell_duration_sec: float = Field(default=1.5, gt=0.0, le=30.0)

    # --- Damping ---
    framing_time_constant_sec: float = Field(default=0.8, gt=0.0, le=10.0)
    pan_time_constant_sec: float = Field(default=0.6, gt=0.0, le=10.0)

    # --- Pan limits ---
    pan_deadband_deg: float = Field(default=0.4, ge=0.0, le=10.0)
    pan_max_velocity_deg_per_sec: float = Field(default=30.0, gt=0.0, le=360.0)

    # --- Motor (firmware clamps, mirror them) ---
    motor_max_speed_steps_per_sec: float = Field(default=25_000.0, ge=100.0, le=50_000.0)
    motor_max_accel_steps_per_sec2: float = Field(default=12_500.0, ge=50.0, le=30_000.0)
    motor_angle_min_deg: float = Field(default=-90.0, ge=-180.0, le=0.0)
    motor_angle_max_deg: float = Field(default=90.0, ge=0.0, le=180.0)

    # --- Dispatcher rate limit ---
    command_min_delta_deg: float = Field(default=0.2, ge=0.0, le=10.0)
    command_min_interval_ms: int = Field(default=50, ge=0, le=10_000)

    @field_validator("motor_angle_max_deg")
    @classmethod
    def _max_above_min(cls, v: float, info) -> float:  # type: ignore[no-untyped-def]
        min_v = info.data.get("motor_angle_min_deg")
        if min_v is not None and v <= min_v:
            raise ValueError(f"motor_angle_max_deg ({v}) must exceed motor_angle_min_deg ({min_v})")
        return v

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Precedence (highest first): init kwargs > env > .env > config.json > defaults
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            JsonConfigSettingsSource(settings_cls),
            file_secret_settings,
        )
```
`[CITED: pydantic.dev/docs/validation/latest/concepts/pydantic_settings/ — JsonConfigSettingsSource + settings_customise_sources]`

**Field count audit:** PROMPT.md lists 23 fields; the block above has **24** (added `arduino_ready_timeout_sec` from PROMPT failure-mode table — it is enumerated under Failure Modes not the Config block, but the test-and-fail-fast handshake in Phase 2 needs it). Verify against PROMPT.md `## Config` block before commit; if planner wants strict 23-field parity, drop `arduino_ready_timeout_sec` here and reintroduce in Phase 2. **Recommendation: keep all 24 in Phase 1 — `Config` ownership is centralised and Phase 2 won't have to mutate the file.**

### Pattern 7: hypothesis property tests for math
```python
# tests/test_geometry.py
from hypothesis import given, strategies as st, assume
from pastor_tracker.core.geometry import normalized_x_to_angle_deg, angle_deg_to_normalized_x

@given(
    nx=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
    fov=st.floats(min_value=1e-3, max_value=180.0 - 1e-3, allow_nan=False, allow_infinity=False),
)
def test_normalized_angle_roundtrip(nx: float, fov: float) -> None:
    angle = normalized_x_to_angle_deg(nx, fov)
    nx_back = angle_deg_to_normalized_x(angle, fov)
    assert abs(nx_back - nx) < 1e-9
```

**For the damping test, suppress the deadline:**
```python
from hypothesis import given, settings, HealthCheck, strategies as st
@given(tau=st.floats(min_value=0.1, max_value=2.0))
@settings(deadline=None, suppress_health_check=[HealthCheck.too_slow], max_examples=50)
def test_critically_damped_no_overshoot(tau: float) -> None:
    ...
```

### Pattern 8: Frozen DTO design (mixed Pydantic + dataclass)
**Recommendation matrix:**
| DTO | Container | Why |
|-----|-----------|-----|
| `Frame` | `@dataclass(frozen=True, slots=True)` | Holds `image: np.ndarray` — Pydantic v2 needs `arbitrary_types_allowed=True` for ndarray and gives no real validation; dataclass is honest |
| `Detection` | `pydantic.BaseModel` (`frozen=True`) | Plain floats / ints (bbox, keypoints, conf); validate ranges |
| `TrackedSubject` | `pydantic.BaseModel` (`frozen=True`) | Same |
| `MotionState` | `pydantic.BaseModel` (`frozen=True`) | Same |
| `FramingTarget` | `pydantic.BaseModel` (`frozen=True`) | Same |
| `MotorCommand` | `pydantic.BaseModel` (`frozen=True`) | Validate `angle_deg` is finite + within `[motor_angle_min_deg, motor_angle_max_deg]` (Phase 5 will pass the bounds at construction time) |

**Mutation rule:** `new = obj.model_copy(update={...})` for Pydantic; `dataclasses.replace(obj, **kw)` for dataclasses. Document this once in `core/types.py` module docstring.

### Pattern 9: Critically-damped 2nd-order follower (Holden exact form)
**What:** Closed-form spring-damper update, parameterized by τ (time constant). Unconditionally stable for any `dt`. Provably zero-overshoot when critically damped.
**When to use:** `core/damping.py` — single function, shared by `framer` and `pan_controller`.
**Source:** `[CITED: theorangeduck.com/page/spring-roll-call — Daniel Holden "Spring-It-On"]`, also Game Programming Gems 4 ch. by Thomas Lowe (basis of Unity's `Vector3.SmoothDamp`) `[CITED: gameenginegems.com/gemsdb/article.php?id=274]`.

**Two parameterization choices documented in the literature:**

1. **Unity SmoothDamp form** (smoothTime ≈ τ; what PROMPT.md essentially intends):
   ```
   omega = 2.0 / smooth_time
   x = omega * dt
   exp = 1.0 / (1.0 + x + 0.48*x*x + 0.235*x*x*x)
   change = pos - target
   temp = (vel + omega * change) * dt
   vel = (vel - omega * temp) * exp
   pos = target + (change + temp) * exp
   ```

2. **Holden exact form** (parameterized by halflife `h`; convert τ → h via `h = τ · ln(2)` ≈ `0.693·τ`):
   ```
   d = (4·ln(2)) / halflife
   y = d / 2
   j0 = pos - target
   j1 = vel + j0 * y
   eydt = exp(-y * dt)             # or fast_negexp approx
   pos_new = target + eydt * (j0 + j1*dt)
   vel_new = eydt * (vel - j1 * y * dt)
   ```

**Recommendation: Holden form.** Rationale: (a) closed-form `exp` is exact, not a polynomial approximation, so the no-overshoot proof holds at any `dt`; (b) `halflife` is intuitive ("error halves every h seconds"); (c) easy to convert to the τ exposed in `Config` via `halflife = pan_time_constant_sec * ln(2)`. The Unity form is a polynomial Padé approximation of `exp(-x)`; it's fine but introduces tiny errors that hypothesis at very small dt can fish up.

**Step-response test design (TEST-02):**
```python
import math
from pastor_tracker.core.damping import CriticallyDampedFollower

def test_step_response_no_overshoot_per_tau(tau: float) -> None:
    follower = CriticallyDampedFollower(time_constant_sec=tau)
    state = follower.initial_state(position=0.0, velocity=0.0)
    target = 1.0
    dt = 1.0 / 60.0                 # 60 fps grid
    horizon = max(int(8 * tau / dt), 240)   # ~8 time-constants
    history: list[float] = [state.position]
    for _ in range(horizon):
        state = follower.step(state, target=target, dt=dt)
        history.append(state.position)
    # 1. Monotonic (no oscillation) for critically damped
    diffs = [b - a for a, b in zip(history, history[1:])]
    assert all(d >= -1e-12 for d in diffs), f"non-monotonic at tau={tau}"
    # 2. No overshoot
    assert max(history) <= target + 1e-9, f"overshoot at tau={tau}: max={max(history)}"
    # 3. Converged to within 1% by 5*tau
    settled_index = int(5 * tau / dt)
    assert abs(history[settled_index] - target) < 0.01, f"slow convergence at tau={tau}"
```

### Anti-Patterns to Avoid
- **PID** (explicit forbiddance from CLAUDE.md / PROMPT.md). Never combine integral term with the damped follower.
- **EMA on detection** (Phase 4 concern, but flag it now). Adds lag.
- **Mocked damping in tests** (TEST-05). Test the real `CriticallyDampedFollower` end-to-end.
- **`from pydantic import BaseSettings`** — wrong in v2; it's `from pydantic_settings import BaseSettings`.
- **Mutable default values** in dataclass fields (`field(default_factory=list)` only).
- **`np.float32` in core math** — keep `core/` in float64 to avoid silent precision loss; convert at I/O boundaries (Phase 3+).
- **Hand-rolling JSON config loader** when `JsonConfigSettingsSource` exists.
- **Catching `Exception` to log + re-raise** — let it propagate; structlog handles uncaught via `format_exc_info`.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Range validation | Manual `if x < 0: raise` | `Field(ge=0, le=N)` | Single source of truth; Pydantic puts it in the schema |
| Frozen DTO | `def __setattr__: raise` | `model_config = ConfigDict(frozen=True)` / `@dataclass(frozen=True)` | Battle-tested, plays with mypy |
| JSON config loading | `json.load(open("config.json"))` then setattr | `JsonConfigSettingsSource` | Plays with env-var precedence and validation |
| Conventional Commits enforcement | Bash regex in `commit-msg` hook | `commitizen check --commit-msg-file $1` | Catches subject, scope, breaking-change footer |
| Discrete-time damping | "I'll just write `pos += k * (target - pos)`" (== EMA — forbidden) | Holden critically-damped exact form | EMA has lag; PID overshoots; the closed-form is the only correct option |
| Logging level filter | `if level >= ...` checks | `structlog.make_filtering_bound_logger` | Standard, fast, predictable |
| FOV calculator | Trigonometry by hand each call site | `core/geometry.py` shared module | DRY: framer + pan_controller both need it |
| Test runner config | Custom shell wrapper | `pyproject.toml [tool.pytest.ini_options]` | Standard, IDE-discoverable |

**Key insight:** Phase 1's whole job is to wire libraries together correctly. The temptation to "just write a small helper" is the highest-risk move — anywhere there's an obvious library call, take it.

## Common Pitfalls

### Pitfall 1: pydantic-settings is a separate package in v2
**What goes wrong:** `from pydantic import BaseSettings` → `ImportError`.
**Why it happens:** Pre-v2 split. `BaseSettings` lived in pydantic core in v1 but was extracted in v2.
**How to avoid:** `from pydantic_settings import BaseSettings, SettingsConfigDict`. Add `pydantic-settings` to `[project.dependencies]`.
**Warning signs:** ImportError at first `python -c "import pastor_tracker.config"`.
`[CITED: pydantic.dev/docs/validation/latest/concepts/pydantic_settings/]`

### Pitfall 2: `numpy.ndarray` in Pydantic
**What goes wrong:** `Frame(image=arr)` raises validation error.
**Why it happens:** Pydantic v2 requires `arbitrary_types_allowed=True` for ndarray, and even then it can't validate shape/dtype.
**How to avoid:** Use `@dataclass(frozen=True, slots=True)` for `Frame`. Validate ndarray shape inline if needed (a `__post_init__` on a frozen dataclass works via `object.__setattr__` workaround, or just check at the producer site).

### Pitfall 3: mypy --strict on third-party libs without `py.typed`
**What goes wrong:** `Skipping analyzing "cv2": module is installed, but missing library stubs or py.typed marker [import-untyped]`.
**Why it happens:** `cv2`, `dearpygui`, `pygrabber`, `ultralytics` (and `filterpy` later) ship no type information.
**How to avoid:** `[[tool.mypy.overrides]]` block with `module = ["cv2.*", ...]; ignore_missing_imports = true`. Even though Phase 1 doesn't import any of these, set it once and forget.

### Pitfall 4: Holden damping with extreme `tau` in hypothesis
**What goes wrong:** At `τ → 0`, `omega → ∞`; numerical exp underflows to 0; the test asserts convergence but `state.position` is `nan`.
**Why it happens:** Hypothesis loves edge cases.
**How to avoid:** `st.floats(min_value=0.1, max_value=2.0)` — match the requirement spec exactly. Add a `core/damping.py`-level guard: `if time_constant_sec <= 0: raise ValueError(...)`. Tiger-style.

### Pitfall 5: structlog without `format_exc_info` swallows tracebacks
**What goes wrong:** `log.exception("oops")` produces `{"event":"oops"}` with no traceback.
**Why it happens:** The `format_exc_info` processor is opt-in.
**How to avoid:** Include `structlog.processors.format_exc_info` in the chain (Pattern 5 includes it).

### Pitfall 6: hypothesis `deadline` triggers on slow first run
**What goes wrong:** `test_critically_damped_no_overshoot` fails intermittently with `DeadlineExceeded` on cold venv.
**Why it happens:** Default deadline is 200 ms; first import of numpy + math kernel JIT can exceed it.
**How to avoid:** `@settings(deadline=None, suppress_health_check=[HealthCheck.too_slow])` on damping tests. Property tests for math don't need a deadline.

### Pitfall 7: ruff `PLR2004` flags legitimate constants
**What goes wrong:** `if response_code == 200:` flagged as magic value.
**Why it happens:** PLR2004 doesn't know which numbers are "magic."
**How to avoid:** Hoist the constant: `HTTP_OK = 200`. CLAUDE.md mandates this anyway. For the `core/` Phase-1 surface this should be near-zero false-positive rate (math files are dense with literal coefficients like `0.48`, `0.235`, `4*ln(2)` from the Holden form — these are mathematical constants and SHOULD be named: `EXP_PADE_C2 = 0.48`, etc.).

### Pitfall 8: `frozen=True` + `field_validator` ordering
**What goes wrong:** Cross-field validator (`motor_angle_max_deg > motor_angle_min_deg`) sees `info.data` empty because order matters.
**Why it happens:** Pydantic v2 evaluates fields in declaration order; `min` must be declared BEFORE `max` for `info.data["motor_angle_min_deg"]` to be populated.
**How to avoid:** Declare `motor_angle_min_deg` first (Pattern 6 already does this). Or use `model_validator(mode="after")` for cross-field rules — strictly cleaner.

### Pitfall 9: `extra="forbid"` blocks loading legacy `config.json`
**What goes wrong:** A future-you adds a config.json with a typo'd key → startup crash.
**Why it happens:** That's the design (CFG-03). But it's surprising the first time.
**How to avoid:** It's the correct behavior; document in README. `extra="ignore"` would be a CLAUDE.md violation (silent fallback).

### Pitfall 10: `uv` is not installed on the dev machine
**What goes wrong:** `uv sync` → command not found.
**Why it happens:** `pip install uv` was never run; the existing `.venv` was bootstrapped with stdlib pip.
**How to avoid:** Phase 1 task list MUST include "install uv" as the first action. Either `pipx install uv` or the Astral installer script. Document in README.

## Code Examples

### Geometry — pinhole FOV math
```python
# src/pastor_tracker/core/geometry.py
"""Pure FOV math: normalized image-x ↔ off-axis horizontal angle (degrees).

Pinhole camera model. No state, no I/O. All inputs in degrees and [0,1] floats.
"""
from __future__ import annotations
import math

# Source: scratchapixel.com — Perspective Projection (pinhole derivation)
#         commonlands.com — FOV calculator (HFOV = 2 * arctan(w / 2f))

def normalized_x_to_angle_deg(normalized_x: float, horizontal_fov_deg: float) -> float:
    """Map normalized image x in [0, 1] to off-axis horizontal angle in degrees.

    nx = 0.0   → angle = -fov/2  (left edge)
    nx = 0.5   → angle = 0       (optical centre)
    nx = 1.0   → angle = +fov/2  (right edge)
    """
    if not 0.0 <= normalized_x <= 1.0:
        raise ValueError(f"normalized_x out of [0,1]: {normalized_x}")
    if not 0.0 < horizontal_fov_deg < 180.0:
        raise ValueError(f"horizontal_fov_deg out of (0, 180): {horizontal_fov_deg}")
    half_fov = math.radians(horizontal_fov_deg / 2.0)
    offset = (2.0 * normalized_x) - 1.0     # in [-1, +1]
    return math.degrees(math.atan(offset * math.tan(half_fov)))

def angle_deg_to_normalized_x(angle_deg: float, horizontal_fov_deg: float) -> float:
    """Inverse of normalized_x_to_angle_deg."""
    if not 0.0 < horizontal_fov_deg < 180.0:
        raise ValueError(f"horizontal_fov_deg out of (0, 180): {horizontal_fov_deg}")
    half_fov = math.radians(horizontal_fov_deg / 2.0)
    offset = math.tan(math.radians(angle_deg)) / math.tan(half_fov)
    return (offset + 1.0) / 2.0
```

### Damping — critically-damped follower (Holden exact)
```python
# src/pastor_tracker/core/damping.py
"""Critically-damped 2nd-order follower. Pure math. No PID. No EMA.

State: (position, velocity). Update: (state, target, dt) → state'.
Closed-form, unconditionally stable for any dt, zero overshoot.

Source: Daniel Holden "Spring-It-On" (theorangeduck.com/page/spring-roll-call)
        Game Programming Gems 4 ch. "Critically Damped Ease-In/Ease-Out Smoothing"
"""
from __future__ import annotations
import math
from dataclasses import dataclass, replace

LN2: float = math.log(2.0)
DAMPING_NUMERATOR: float = 4.0 * LN2     # named constant (PLR2004 happy)

@dataclass(frozen=True, slots=True)
class FollowerState:
    position: float
    velocity: float

@dataclass(frozen=True, slots=True)
class CriticallyDampedFollower:
    """Closed-form critically-damped 2nd-order follower."""
    time_constant_sec: float

    def __post_init__(self) -> None:
        if self.time_constant_sec <= 0.0:
            raise ValueError(
                f"time_constant_sec must be > 0, got {self.time_constant_sec}"
            )

    def initial_state(self, position: float, velocity: float = 0.0) -> FollowerState:
        return FollowerState(position=position, velocity=velocity)

    def step(
        self,
        state: FollowerState,
        target: float,
        dt: float,
    ) -> FollowerState:
        if dt <= 0.0:
            raise ValueError(f"dt must be > 0, got {dt}")
        # halflife from time constant: error halves every τ·ln(2) seconds
        halflife = self.time_constant_sec * LN2
        damping = DAMPING_NUMERATOR / halflife
        y = damping / 2.0
        j0 = state.position - target
        j1 = state.velocity + j0 * y
        decay = math.exp(-y * dt)
        new_position = target + decay * (j0 + j1 * dt)
        new_velocity = decay * (state.velocity - j1 * y * dt)
        return replace(state, position=new_position, velocity=new_velocity)
```

### Property test — geometry round-trip
```python
# tests/test_geometry.py
from hypothesis import given, strategies as st
from pastor_tracker.core.geometry import (
    angle_deg_to_normalized_x,
    normalized_x_to_angle_deg,
)

ROUNDTRIP_TOL = 1e-9

@given(
    nx=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
    fov=st.floats(min_value=1e-3, max_value=180.0 - 1e-3, allow_nan=False, allow_infinity=False),
)
def test_normalized_angle_roundtrip(nx: float, fov: float) -> None:
    angle = normalized_x_to_angle_deg(nx, fov)
    nx_back = angle_deg_to_normalized_x(angle, fov)
    assert abs(nx_back - nx) < ROUNDTRIP_TOL

@given(fov=st.floats(min_value=1.0, max_value=170.0, allow_nan=False, allow_infinity=False))
def test_centre_maps_to_zero(fov: float) -> None:
    assert abs(normalized_x_to_angle_deg(0.5, fov)) < ROUNDTRIP_TOL

@given(fov=st.floats(min_value=1.0, max_value=170.0, allow_nan=False, allow_infinity=False))
def test_edges_map_to_half_fov(fov: float) -> None:
    assert abs(normalized_x_to_angle_deg(0.0, fov) + fov / 2.0) < 1e-7
    assert abs(normalized_x_to_angle_deg(1.0, fov) - fov / 2.0) < 1e-7
```

### Step-response test — damping (no overshoot)
```python
# tests/test_damping.py
from hypothesis import HealthCheck, given, settings, strategies as st
from pastor_tracker.core.damping import CriticallyDampedFollower

@given(tau=st.floats(min_value=0.1, max_value=2.0, allow_nan=False, allow_infinity=False))
@settings(deadline=None, suppress_health_check=[HealthCheck.too_slow], max_examples=40)
def test_critically_damped_no_overshoot(tau: float) -> None:
    follower = CriticallyDampedFollower(time_constant_sec=tau)
    state = follower.initial_state(position=0.0)
    target = 1.0
    dt = 1.0 / 60.0
    horizon = max(int(10 * tau / dt), 600)
    positions = [state.position]
    for _ in range(horizon):
        state = follower.step(state, target=target, dt=dt)
        positions.append(state.position)
    # No overshoot
    assert max(positions) <= target + 1e-9, f"overshoot tau={tau}: max={max(positions)}"
    # Monotonic non-decreasing (critically-damped from rest below target)
    diffs = [b - a for a, b in zip(positions, positions[1:])]
    assert all(d >= -1e-9 for d in diffs), f"non-monotonic tau={tau}"
    # Converged within 1% by 5τ
    idx_5tau = int(5 * tau / dt)
    assert abs(positions[idx_5tau] - target) < 0.05, (
        f"slow convergence tau={tau}: pos@5τ={positions[idx_5tau]}"
    )
```

### Config invalid-input test
```python
# tests/test_config.py
import pytest
from pydantic import ValidationError
from pastor_tracker.config import Config

def test_negative_pan_velocity_rejected() -> None:
    with pytest.raises(ValidationError):
        Config(pan_max_velocity_deg_per_sec=-1.0)

def test_motor_speed_below_firmware_clamp_rejected() -> None:
    with pytest.raises(ValidationError):
        Config(motor_max_speed_steps_per_sec=99.0)   # below 100 floor

def test_unknown_field_rejected() -> None:
    with pytest.raises(ValidationError):
        Config(nonexistent_field=42)                 # extra="forbid"

def test_max_below_min_rejected() -> None:
    with pytest.raises(ValidationError):
        Config(motor_angle_min_deg=10.0, motor_angle_max_deg=5.0)

def test_protocol_version_must_be_2() -> None:
    with pytest.raises(ValidationError):
        Config(arduino_protocol_version=1)           # Literal[2]
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `pip install -r requirements.txt` | `uv sync` from `pyproject.toml` + `uv.lock` | uv reached production-ready ~early 2025 | 10-100× faster, deterministic locks, single tool for venv + deps |
| `from pydantic import BaseSettings` (v1) | `from pydantic_settings import BaseSettings` (v2) | Pydantic 2.0 (Jun 2023) | Mandatory — v1 import is gone |
| `model_config = {"frozen": True}` (v1 style dict) | `model_config = ConfigDict(frozen=True)` / `SettingsConfigDict(frozen=True)` | Pydantic 2.0 | Both still work; ConfigDict is type-checked |
| `black` + `isort` + `flake8` | `ruff format` + `ruff check` | ruff 0.1+ format command (late 2023) | One config, one binary, one tool; ~50-100× faster |
| `pytest-asyncio` strict mode | `asyncio_mode = "auto"` | pytest-asyncio 0.21+ | Less boilerplate for asyncio-only projects |
| EMA / PID for camera follow | Critically-damped 2nd-order spring (Holden) | Game Programming Gems 4 (2004) — became Unity SmoothDamp | No overshoot, no oscillation, no lag, dt-stable |
| MediaPipe pose | YOLO11-pose | ultralytics 8.3+ (2024) | Faster + more accurate; has BoT-SORT built in |

**Deprecated/outdated for this project:**
- **PID controllers** — explicitly forbidden by PROMPT.md / CLAUDE.md.
- **MediaPipe** — superseded by YOLO11-pose for 2026 (Phase 4 concern).
- **EMA on detection** — replaced by Kalman (Phase 4 concern).
- **`requirements.txt` as the source of truth** — replace with `pyproject.toml` + `uv.lock` in Phase 1.
- **Pydantic v1 `BaseSettings`** — moved to `pydantic-settings` package in v2.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | The PROMPT.md `Config` block has 23 fields; my Pattern-6 listing has 24 (added `arduino_ready_timeout_sec`) | Pattern 6 + Phase Requirements | Field count mismatch with CFG-01 if planner enforces strict 23 — drop field or update CFG-01 acceptance criteria |
| A2 | `Frame` will carry a `numpy.ndarray` (image buffer); thus `dataclass(frozen=True, slots=True)` is the right container | Pattern 8 | If perception phase decides `Frame` should hold raw bytes / Path / handle, choice still defensible but check at Phase 4 |
| A3 | Holden's exact spring-damper is preferable to Unity's Padé approximation | Pattern 9 | Both pass all property tests in practice; Holden chosen for closed-form clarity. If planner prefers PROMPT.md's literal pseudocode, swap — both produce no-overshoot critical damping |
| A4 | `commitizen` is the Python-native Conventional Commits enforcer; npm-based `commitlint` is unwanted in a uv-only repo | Standard Stack | If the team already has node tooling for other reasons, commitlint+husky is also valid |
| A5 | `Field(ge=100, le=50000)` for motor_max_speed mirrors firmware clamps from PROMPT.md `## Settings clamps` table | Pattern 6 | If firmware has changed clamps, validate against `arduino/stepper_controller/include/protocol.h` before locking |
| A6 | `arduino_baud=115200` is fixed in firmware; making it a config field with default is for symmetry, not a real toggle | Pattern 6 | Could be a `Literal[115200]` constant instead. Field with default is more permissive; preserves audit trail |
| A7 | Per-module mypy override list (`cv2`, `dearpygui`, `pygrabber`, `ultralytics`, `filterpy`, `serial`) anticipates phases 2-4 even though Phase 1 doesn't import any of these | Pattern 3 | Forward-looking. Trim to `[]` for Phase 1 only if planner prefers minimum-surface; nothing breaks |

**These are the only `[ASSUMED]` items in this research. Everything else is `[VERIFIED]` (PyPI, .venv probe, official docs) or `[CITED]` (referenced URL).**

## Open Questions

1. **Bump versions, or pin to `.venv`?**
   - What we know: `.venv` has structlog 24.4.0, pytest 8.4.2, pytest-asyncio 0.26.0; latest are 25.5.0, 9.0.3, 1.1.0.
   - What's unclear: Whether to upgrade now or pin current.
   - Recommendation: **Pin to current `.venv` versions** for Phase 1 — the goal is "make it work and lock it." Plan a separate maintenance phase if upgrades are wanted.

2. **Where does `config.json` live?**
   - What we know: `JsonConfigSettingsSource(json_file="config.json")` resolves relative to CWD by default.
   - What's unclear: CWD = repo root or `pastor_tracker/`?
   - Recommendation: Use absolute path resolved from a known anchor (e.g. `Path(__file__).resolve().parent.parent.parent / "config.json"`), document in README, allow override via `PTS_CONFIG_FILE` env var.

3. **Should `__main__.py` exist in Phase 1?**
   - What we know: PROMPT.md lists `__main__.py` as the entry, but Phase 1 has no pipeline yet.
   - Recommendation: Land a stub that calls `configure_logging()` and prints `Config()` (via structlog) to prove the wire-up. Avoids "empty package" smell and exercises the boot path.

4. **Should `tests/conftest.py` ship a "valid Config dict" fixture in Phase 1?**
   - Recommendation: Yes — Phase 2-7 will all want it. One-line factory that produces a baseline-valid kwargs dict for Config tests.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3.12 | Runtime | ✓ | 3.12.10 (in `.venv/Scripts/python.exe`) | — |
| `uv` (CLI) | Project bootstrap (`uv sync`, `uv lock`) | ✗ | — | Use `pip install -r requirements.txt` against existing `.venv` until uv is installed; install via `pipx install uv` or `irm https://astral.sh/uv/install.ps1 \| iex` |
| `ruff` | Lint/format | ✓ | 0.15.12 (in `.venv`) | — |
| `mypy` | Type check | ✓ | 1.20.2 (in `.venv`) | — |
| `pytest` | Test runner | ✓ | 8.4.2 (in `.venv`) | — |
| `hypothesis` | Property tests | ✓ | 6.152.4 (in `.venv`) | — |
| `pydantic` + `pydantic-settings` | Config | ✓ | 2.13.3 / 2.14.0 | — |
| `structlog` | Logging | ✓ | 24.4.0 | — |
| `numpy` | Math | ✓ | 2.4.4 | — |
| `commitizen` | Conventional Commits | ✗ | — | `uv add --dev commitizen` (or `pip install commitizen`) — single Python package, no native deps |
| `git` (≥ 2.x) | Version control | ✓ | 2.37.3.windows.1 | — |
| Pre-commit framework | (optional) Git hook orchestration | ✗ | — | Optional. Without it, run `ruff check`, `mypy`, `commitizen check` manually or via Makefile. |

**Missing dependencies with no fallback:** none — uv is missing but is just a tool over pip; we can hand-install everything via the existing pip-based `.venv` if uv proves troublesome.

**Missing dependencies with fallback:**
- `uv` → pip on existing `.venv` (slower, less deterministic, but works).
- `commitizen` → install on first commit.
- `pre-commit` → optional convenience layer.

**Recommendation:** install `uv` and `commitizen` as Phase-1 task #0 ("Toolchain bootstrap"), before scaffolding the project, to keep the rest of the phase cleanly within the locked stack.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | `pytest` 8.4.2 + `hypothesis` 6.152.4 + `pytest-asyncio` 0.26.0 |
| Config file | `pastor_tracker/pyproject.toml` `[tool.pytest.ini_options]` (Wave 0 creates) |
| Quick run command | `.venv/Scripts/pytest.exe pastor_tracker/tests -x --ff` |
| Full suite command | `.venv/Scripts/pytest.exe pastor_tracker/tests -ra --strict-markers --strict-config` |
| Lint gate | `.venv/Scripts/ruff.exe check pastor_tracker/src pastor_tracker/tests` |
| Type gate | `.venv/Scripts/mypy.exe pastor_tracker/src` |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| SCAF-01 | `uv sync` resolves a locked deps tree | smoke | `cd pastor_tracker && uv sync && uv run python -c "import pastor_tracker"` | ❌ Wave 0 |
| SCAF-02 | `ruff check` and `mypy --strict` exit zero | lint/type | `ruff check pastor_tracker/src && mypy pastor_tracker/src` | ❌ Wave 0 (config) |
| SCAF-03 | `pytest` runs the suite green from clean clone | smoke | `pytest pastor_tracker/tests` | ❌ Wave 0 |
| SCAF-04 | `print()` and bare `except:` are lint errors | lint | `ruff check pastor_tracker/tests/fixtures/_lint_canary.py --select T201,E722,BLE001` (canary file) | ❌ Wave 0 |
| SCAF-05 | Conventional Commits enforced | manual + hook | `commitizen check --commit-msg-file <file>` (install hook) | ❌ Wave 0 |
| CFG-01 | Config has all PROMPT.md fields, env+JSON loading | unit | `pytest pastor_tracker/tests/test_config.py::test_loads_from_json_file -x` | ❌ Wave 0 |
| CFG-02 | Range validation rejects out-of-range | unit | `pytest pastor_tracker/tests/test_config.py::test_motor_speed_below_firmware_clamp_rejected -x` | ❌ Wave 0 |
| CFG-03 | Invalid config raises at startup (no fallback) | unit | `pytest pastor_tracker/tests/test_config.py::test_unknown_field_rejected -x` | ❌ Wave 0 |
| CFG-04 | `arduino_protocol_version` defaults to 2 | unit | `pytest pastor_tracker/tests/test_config.py::test_protocol_version_must_be_2 -x` | ❌ Wave 0 |
| CORE-01 | Frozen DTOs reject mutation | unit | `pytest pastor_tracker/tests/test_types.py -x` | ❌ Wave 0 |
| CORE-02 | Geometry round-trip identity for any FOV | property | `pytest pastor_tracker/tests/test_geometry.py::test_normalized_angle_roundtrip -x` | ❌ Wave 0 |
| CORE-03 | Damping step response: zero overshoot | property | `pytest pastor_tracker/tests/test_damping.py::test_critically_damped_no_overshoot -x` | ❌ Wave 0 |
| CORE-04 | 100% type cov on core, no `Any`, no `print` | type/lint | `mypy pastor_tracker/src/pastor_tracker/core --strict && ruff check pastor_tracker/src/pastor_tracker/core --select ANN401,T201` | ❌ Wave 0 |
| TEST-01 | hypothesis property tests for geometry | property | `pytest pastor_tracker/tests/test_geometry.py -x` | ❌ Wave 0 |
| TEST-02 | step-response test for damping (no overshoot) | property | `pytest pastor_tracker/tests/test_damping.py -x` | ❌ Wave 0 |
| TEST-05 | No mocked Kalman/damping math (test real) | unit | (review) — `grep -RIn "Mock.*[Dd]ampi\|Mock.*[Kk]alman" pastor_tracker/tests` returns empty | manual verify per PR |

### Sampling Rate
- **Per task commit:** `ruff check pastor_tracker/src && pytest pastor_tracker/tests -x --ff`
- **Per wave merge:** Full suite — `ruff check`, `mypy --strict`, `pytest -ra`
- **Phase gate (before `/gsd-verify-work`):** `cd pastor_tracker && uv sync && uv run ruff check && uv run mypy src && uv run pytest -ra` — all green from clean clone

### Wave 0 Gaps
- [ ] `pastor_tracker/pyproject.toml` — `[project]`, `[tool.uv]`, `[tool.ruff]`, `[tool.mypy]`, `[tool.pytest.ini_options]`, `[tool.commitizen]`
- [ ] `pastor_tracker/.python-version` — `3.12`
- [ ] `pastor_tracker/src/pastor_tracker/__init__.py`, `__main__.py`, `config.py`, `logging_config.py`
- [ ] `pastor_tracker/src/pastor_tracker/core/__init__.py`, `types.py`, `geometry.py`, `damping.py`
- [ ] `pastor_tracker/src/pastor_tracker/{io,perception,intent,control,ui}/__init__.py` — empty packages
- [ ] `pastor_tracker/tests/__init__.py`, `conftest.py`, `test_config.py`, `test_types.py`, `test_geometry.py`, `test_damping.py`, `test_logging.py`
- [ ] `pastor_tracker/tests/fixtures/_lint_canary.py` — fixture with `print(...)` and `except:` to verify ruff catches them (kept under `tests/fixtures/` so ruff's main lint passes; canary lint is a separate command)
- [ ] Tool install: `pipx install uv && pipx install commitizen` (or via the venv) — Wave 0 step before everything else

*(All required test files are missing — Phase 1 is greenfield. They are created by the implementation tasks themselves; this is normal for a scaffold phase.)*

## Security Domain

### Applicable ASVS Categories
| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | — (no auth surface; local desktop app) |
| V3 Session Management | no | — (no sessions) |
| V4 Access Control | no | — (single-user desktop) |
| V5 Input Validation | yes | **Pydantic v2 with `extra="forbid"` and `Field(ge=, le=)`** — every config field range-validated. JSON file source goes through the same validators. |
| V6 Cryptography | no | — (no secrets, no signing in this phase; serial protocol is plaintext over USB by design) |
| V7 Error Handling and Logging | yes | **structlog JSON renderer with `format_exc_info`** — no `print`, no swallowed excepts (ruff `T201`, `BLE001`, `E722`), structured fields not f-string concatenation |
| V8 Data Protection | low | `config.json` is the only persisted artifact in Phase 1 — no PII, no secrets. .gitignore already excludes `.env`, `*.local.json`. |
| V9 Communications | no | — Phase 2/3 surface |
| V10 Malicious Code | yes | `commitizen` enforces commit-message convention; `uv.lock` pins hashes; ruff `S` rules catch `eval`, `exec`, weak hashes |
| V14 Configuration | yes | All tunables in `Config`; no magic numbers (ruff `PLR2004`); fail-fast on invalid; `extra="forbid"` blocks typos |

### Known Threat Patterns for Python desktop config layer
| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Untrusted JSON input from `config.json` | Tampering / DoS | Pydantic validates types + ranges; `extra="forbid"` blocks unknown keys; `JsonConfigSettingsSource` uses stdlib `json` (no deserialization gadgets) |
| Environment variable injection (e.g. `PTS_MOTOR_ANGLE_MAX_DEG=99999`) | Tampering | `Field(le=...)` upper bound — value rejected at startup; ValidationError surfaces in logs |
| Pickle / yaml.unsafe_load patterns | Code Execution | We never pickle; ruff `S301` (`pickle`), `S506` (`yaml.unsafe_load`) catch attempts |
| Logged secrets (e.g. `log.info("config", **cfg.model_dump())` if there were a key) | Information Disclosure | No secrets in Phase 1 Config. Add `SecretStr` for any future API keys; `model_dump()` redacts SecretStr by default |
| Lockfile drift / supply-chain | Tampering | `uv.lock` pins exact versions + hashes; `commitizen` review enforces meaningful change messages |
| Bare `except: pass` swallowing security errors | Repudiation | Lint forbiddance (ruff `E722`, `BLE001`) — caught at `ruff check` |

**No HIGH-severity security issues identified for Phase 1.** This is pure-tooling + pure-math + local config. Phase 2 (serial) and Phase 3 (camera) will reopen V9 (Communications) and V8 (Data Protection).

## Sources

### Primary (HIGH confidence)
- PyPI JSON metadata — `uv 0.11.8`, `pydantic 2.13.3`, `pydantic-settings 2.14.0`, `ruff 0.15.12`, `mypy 1.20.2`, `structlog 25.5.0`, `pytest 9.0.3`, `pytest-asyncio 1.1.0`, `hypothesis 6.152.4`, `commitizen 4.15.0` — all queried 2026-05-03
- `.venv/Scripts/python.exe -m pip list` — verified installed versions of every Phase-1 dep
- `https://docs.astral.sh/ruff/rules/` — rule code IDs (T201, BLE001, E722, ANN401, PLR2004)
- `https://pydantic.dev/docs/validation/latest/concepts/pydantic_settings/` — BaseSettings + JsonConfigSettingsSource + settings_customise_sources canonical recipe
- `https://docs.astral.sh/ruff/rules/magic-value-comparison/` — PLR2004 confirmed
- `https://www.structlog.org/en/stable/getting-started.html` — JSON renderer + processor chain
- `https://pytest-asyncio.readthedocs.io/en/latest/concepts.html` — `asyncio_mode = "auto"` recommendation
- `https://theorangeduck.com/page/spring-roll-call` (Daniel Holden) — exact closed-form critically-damped spring-damper, dt-stable
- `http://gameenginegems.com/gemsdb/article.php?id=274` — Game Programming Gems 4 ch. "Critically Damped Ease-In/Ease-Out Smoothing" (basis of Unity SmoothDamp)

### Secondary (MEDIUM confidence)
- `https://gist.github.com/josimard/5737f3488fdfa2d207d68de282904479` — UE4 SmoothDamp port (cross-reference for the formulation)
- `https://www.scratchapixel.com/lessons/3d-basic-rendering/perspective-and-orthographic-projection-matrix/building-basic-perspective-projection-matrix.html` — pinhole projection / FOV trig (the FOV formula is textbook math; treated MEDIUM only because cited from a teaching site rather than an academic reference)
- `https://commonlands.com/pages/camera-field-of-view-calculator` — FOV = 2·arctan(w / 2f) cross-reference

### Tertiary (LOW confidence)
None. Every claim above is verified or cited from official/canonical sources.

## Project Constraints (from CLAUDE.md)

**Stack — non-negotiable:**
- Python 3.12, `uv`, `ruff`, `mypy --strict`
- Pydantic v2 (`frozen=True`), `structlog` JSON, `pytest` + `hypothesis`
- ultralytics YOLO11-pose, BoT-SORT, `filterpy` Kalman, opencv DirectShow + pygrabber, pyserial, dearpygui
- Concurrency: asyncio for I/O, threading for blocking serial RX

**Forbidden libraries / patterns (never reintroduce):** PID, MediaPipe, EMA on detection stream, mocked Kalman/damping in tests.

**Forbidden in app code:** `print()`, bare `except:` / `except Exception: pass`, globals (except module-level constants), `time.sleep()` in main loop, magic numbers, commented-out code, TODO without issue number, mocked Kalman/damping math in tests.

**Engineering rules:**
1. Tiger-style fail fast / fail loud
2. SRP — one class one reason; one function one verb
3. DRY — shared math in `core/`
4. Pure core / dirty edges
5. ≤2-level conditional nesting; guard clauses + early returns
6. No magic numbers — all tunables in `Config`
7. PEP8 strict naming, descriptive identifiers (`subject_center_x_normalized`, not `cx`)
8. Type hints everywhere — no `Any`; use `Literal`, `NewType`, `TypeAlias`
9. Immutable data — Pydantic / dataclass `frozen=True`; mutate via `model_copy(update=...)` / `dataclasses.replace`
10. Conventional Commits, one logical change per commit
11. Functional core — pure transforms on typed DTOs

**Where this constrains Phase 1:**
- `core/` cannot import `cv2`, `serial`, `dearpygui`, `asyncio`. Must stay pure.
- `Config` field count comes from PROMPT.md verbatim — do not invent.
- `core/damping.py` MUST NOT use PID. Holden / SmoothDamp form only.
- All ruff rule selects (T201, BLE001, E722, ANN401, PLR2004) MUST be in the lint policy from day 1.
- Tests for damping/Kalman MUST exercise the real implementation — no `unittest.mock` for these.
- File layout MUST match CLAUDE.md / PROMPT.md tree exactly (`pastor_tracker/src/pastor_tracker/{core,io,perception,intent,control,ui}` + `tests/`).

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — every package version verified against installed `.venv` and PyPI registry on 2026-05-03
- Architecture (uv project layout, ruff/mypy/pytest config blocks): HIGH — patterns are official-doc-grade and battle-tested
- Pydantic v2 BaseSettings + JsonConfigSettingsSource: HIGH — canonical recipe straight from pydantic-settings docs
- structlog JSON config: HIGH — straight from structlog getting-started
- FOV math: HIGH — textbook pinhole derivation
- Critically-damped follower (Holden form): HIGH — closed-form, peer-implemented in Unity/UE4, mathematically proven stable; cited from authoritative game-dev source
- Pitfalls: HIGH — enumerated from real-world failure modes specific to this stack
- Field-count A1 (23 vs 24): MEDIUM — flagged for planner/user confirmation; trivial to flip

**Research date:** 2026-05-03
**Valid until:** 2026-06-02 (30 days; stack is mature; all major libs released April 2026 or earlier)
