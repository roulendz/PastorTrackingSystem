---
phase: 02-arduino-i-o
plan: 01
subsystem: arduino-protocol-parser
tags:
  - arduino
  - protocol
  - parser
  - pure-core
  - io-ard-04
requirements:
  - IO-ARD-04
dependency_graph:
  requires:
    - pastor_tracker.config (frozen Config)        # via pyproject.toml unchanged
    - arduino/stepper_controller/include/protocol.h # firmware-side source of truth
  provides:
    - pastor_tracker.io.arduino_protocol.parse_line
    - pastor_tracker.io.arduino_protocol.ProtocolEvent (closed union of 11 DTOs)
    - pastor_tracker.io.arduino_protocol.ErrorCode (12 members 0..11)
    - pastor_tracker.io.arduino_protocol.ProtocolParseError
    - pastor_tracker.io.arduino_protocol.{PROTOCOL_VERSION_MAJOR, FIRMWARE_INPUT_BUFFER_USABLE,
        FIRMWARE_INPUT_BUFFER_SIZE, FIRMWARE_HEARTBEAT_TIMEOUT_MS, FIRMWARE_FEEDBACK_INTERVAL_MS,
        SEQ_MODULUS, FEEDBACK_SEQ_GAP_WARN_THRESHOLD, MAX_RX_LINE_BYTES}
  affects:
    - Plan 02-02 (transport)    # imports SerialTransport-targets nothing here, but uses MAX_RX_LINE_BYTES indirectly
    - Plan 02-03 (orchestrator) # imports parse_line + every DTO + ErrorCode + ProtocolParseError
tech_stack:
  added:
    - pyserial>=3.5,<4.0      # production runtime — not yet imported by Python code, lands here for Plan 02-02
    - pytest-cov>=5.0,<7.0    # dev — gates 100% branch coverage on parser
  patterns:
    - flat prefix-dispatch ladder (≤ 2-level nesting per CLAUDE.md rule 5)
    - Pydantic v2 frozen + extra=forbid base class (_Event mirroring core/types._FrozenModel)
    - PEP 695 ``type X = ...`` syntax for type aliases (ruff UP040)
    - Final[...] module-level constants citing protocol.h:NN line numbers
    - tiger-style fail-fast guards before any decode/parse work (T-02-04 DoS containment)
key_files:
  created:
    - path: pastor_tracker/src/pastor_tracker/io/arduino_protocol.py
      role: pure protocol layer — DTOs, ErrorCode, parse_line, constants
      loc: 457
    - path: pastor_tracker/tests/test_arduino_protocol.py
      role: parametrized parser tests, 100% line + branch coverage
      loc: 327
  modified:
    - path: pastor_tracker/pyproject.toml
      change: added pyserial>=3.5,<4.0 to [project.dependencies]; added pytest-cov>=5.0,<7.0 to [dependency-groups].dev
    - path: pastor_tracker/uv.lock
      change: regenerated with pyserial 3.5, pytest-cov 6.3.0, coverage 7.13.5
decisions:
  - id: D-02-01-01
    decision: ProtocolEvent uses PEP 695 ``type`` syntax instead of plan's literal ``TypeAlias`` annotation
    rationale: ruff UP040 (enabled via [tool.ruff.lint] select=["UP"]) rejects TypeAlias on Python 3.12+
    impact: zero — semantic export unchanged; downstream importers see the same closed union
  - id: D-02-01-02
    decision: removed unreachable try/except ValidationError in _parse_settings + _parse_diag
    rationale: the allowlist + _safe_float + regex-captured digits already exclude all ValidationError paths; keeping the dead handlers blocked the 100% branch-coverage gate (each added an uncovered except branch)
    impact: zero — dead-code-defense removal; no behavior change observable from outside
  - id: D-02-01-03
    decision: Error.code modelled as ``ErrorCode | int`` union, with parser explicitly coercing known values to enum
    rationale: forward-compat per Pitfall 9 (RESEARCH.md lines 859-867) — unknown codes preserved as raw int; pydantic's left-to-right union resolution honours the coercion done in parse_error
    impact: callers can branch on ``isinstance(err.code, ErrorCode)`` to detect unknown codes
  - id: D-02-01-04
    decision: parse_line carries ``noqa: PLR0911, PLR0912`` for too-many-returns / too-many-branches
    rationale: the flat prefix-dispatch ladder IS the design (CLAUDE.md rule 5 — guard clauses, ≤ 2-level nesting); folding the 10 arms into a lookup dict would lose static-type narrowing on the closed ProtocolEvent union or hide priority order (FB: vs FB_HEADER:)
    impact: zero — design choice documented inline + here
metrics:
  start: 2026-05-03T18:44:10Z
  end: 2026-05-03T18:53:47Z
  duration_seconds: 577
  task_count: 3
  file_count_created: 2
  file_count_modified: 2
  test_count: 72
  branch_coverage_pct: 100
  line_coverage_pct: 100
---

# Phase 02 Plan 01: Pure Arduino Protocol Parser Summary

Lands the pure half of Arduino I/O — protocol DTOs, ErrorCode IntEnum, line-format
constants, and a flat prefix-dispatch parser — at 100% line + 100% branch coverage,
with zero serial / threading / asyncio / os / time imports so Plans 02-02 (transport)
and 02-03 (orchestrator) can build on top without dragging I/O into the parser test
surface.

## Plan One-Liner

`parse_line(bytes) -> ProtocolEvent` plus 11 frozen Pydantic v2 DTOs and 12-member
`ErrorCode` IntEnum, all mirrored from `arduino/stepper_controller/include/protocol.h`
and validated via 72 parametrized tests reaching 100% branch coverage.

## Files Created

| File | LOC | Purpose |
|------|-----|---------|
| `pastor_tracker/src/pastor_tracker/io/arduino_protocol.py` | 457 | DTOs + ErrorCode + parse_line + ProtocolParseError + constants |
| `pastor_tracker/tests/test_arduino_protocol.py` | 327 | 72 parametrized parser tests; 100% line + branch coverage |

## Files Modified

| File | Change |
|------|--------|
| `pastor_tracker/pyproject.toml` | +`pyserial>=3.5,<4.0` to `[project.dependencies]`; +`pytest-cov>=5.0,<7.0` to `[dependency-groups].dev`; `mypy.overrides` for `serial.*` untouched |
| `pastor_tracker/uv.lock` | regenerated — pyserial 3.5, pytest-cov 6.3.0, coverage 7.13.5 |

## Public Symbols Exported (final spec for Plans 02-02 / 02-03)

```python
# Constants — every value cites protocol.h:NN in source.
PROTOCOL_VERSION_MAJOR: Final[int] = 2                   # protocol.h:12
FIRMWARE_INPUT_BUFFER_SIZE: Final[int] = 48              # protocol.h:39
FIRMWARE_INPUT_BUFFER_USABLE: Final[int] = 47            # 48 minus null terminator
FIRMWARE_HEARTBEAT_TIMEOUT_MS: Final[int] = 1_000        # protocol.h:33
FIRMWARE_FEEDBACK_INTERVAL_MS: Final[int] = 20           # protocol.h:36
SEQ_MODULUS: Final[int] = 1 << 32                        # uint32 rollover
FEEDBACK_SEQ_GAP_WARN_THRESHOLD: Final[int] = 5          # IO-ARD-04
MAX_RX_LINE_BYTES: Final[int] = 256                      # T-02-04 DoS guard

class ErrorCode(IntEnum):  # 12 members, values 0..11 — mirrors protocol.h:67-80
    NONE = 0
    EMPTY_COMMAND = 1
    UNKNOWN_COMMAND_TYPE = 2
    MOVE_MISSING_ARGUMENT = 3
    DIAGNOSTIC_MISSING_ARGUMENT = 4
    SETTINGS_MISSING_ARGUMENT = 5
    DRIVER_MISSING_ARGUMENT = 6
    DRIVER_INVALID_ARGUMENT = 7
    HOMING_FAILED = 8
    ANGLE_OUT_OF_BOUNDS = 9
    SETTINGS_OUT_OF_BOUNDS = 10
    HEARTBEAT_TIMEOUT = 11

# All inherit from private _Event(BaseModel) with ConfigDict(frozen=True, extra="forbid")
class Ready(_Event):           version: int = Field(ge=1, le=255)
class Feedback(_Event):        current_angle_deg: float; target_angle_deg: float;
                               speed_steps_per_sec: float; is_running: bool;
                               timestamp_micros: int = Field(ge=0);
                               sequence: int = Field(ge=0);
                               accel_phase: Literal[0, 1, 2, 3]
class FeedbackHeader(_Event):  pass
class Settings(_Event):        max_speed/max_accel/pid_p/pid_i/pid_d: float
class SettingsInfo(_Event):    message: str = Field(min_length=1)
class Limits(_Event):          min_deg: float; max_deg: float
                               # @model_validator: max_deg > min_deg (strict)
class Driver(_Event):          enabled: bool
class Reset(_Event):           pass
class Stop(_Event):            pass
class Diag(_Event):            steps: int
class Error(_Event):           code: ErrorCode | int; message: str = Field(min_length=1)

type ProtocolEvent = (Ready | Feedback | Settings | SettingsInfo | Limits |
                      Driver | Reset | Stop | Diag | Error | FeedbackHeader)

class ProtocolParseError(Exception): ...

def parse_line(line: bytes) -> ProtocolEvent: ...
```

## Test Coverage

```
Name                                        Stmts   Miss Branch BrPart  Cover
src\pastor_tracker\io\arduino_protocol.py     201      0     48      0   100%
TOTAL                                         201      0     48      0   100%
72 passed in 0.60s
```

Test structure:

| Group | Tests | Purpose |
|-------|-------|---------|
| A — well-formed table | 21 | one parametrize block per RX prefix (READY×3, FB×3, FB_HEADER, SETTINGS textual×3, SETTINGS structured, LIMITS, DRIVER×2, RESET, STOP, DIAG×3) |
| B — ErrorCode parametrize | 14 | all 12 ErrorCode members + forward-compat unknown int (99) + ERROR:0 sentinel acceptance (Pitfall 9) |
| C — malformed | 30 | every malformed shape raises ProtocolParseError (empty, CRLF-only, unknown prefix, FB×7 incl. NaN/inf/non-numeric/Literal-OOR, READY×3 incl. ge/le, ERROR×4, SETTINGS×4, LIMITS×4, DRIVER, DIAG×2, T-02-04 over-length, T-02-04c non-ASCII) |
| D — frozen + invariants | 4 | frozen=True, extra=forbid, Limits inverted-range, Limits equal-range |
| E — CRLF stripping | 3 | \n, \r, \r\n all decode equivalently |
| F — DoS boundary | 2 | 256 B accepted, 257 B rejected (T-02-04 boundary) |
| **Total** | **72** | **100% line + 100% branch coverage** |

Branch matrix coverage:
- All 10 prefix dispatch arms hit (FB / FB_HEADER / READY / ERROR / SETTINGS / LIMITS / DRIVER / RESET / STOP / DIAG)
- Empty-line guard, MAX_RX_LINE_BYTES guard, CR/LF-only-after-strip guard, UnicodeDecodeError guard all exercised
- All 4 SETTINGS sub-forms: 3 textual + structured 5-tuple
- All 12 ErrorCode known codes + 1 forward-compat unknown
- Every `_safe_float` ValueError + non-finite path; every `_safe_int` ValueError path
- Every reachable Pydantic ValidationError path (FB Literal-OOR, READY Field bounds, ERROR empty message, LIMITS @model_validator)
- Every fall-through ProtocolParseError path

## Acceptance Gate Results

| Gate | Result |
|------|--------|
| `pytest tests/test_arduino_protocol.py -x` | 72 passed |
| `pytest --cov ... --cov-fail-under=100 --cov-branch` | 100% / 100% |
| `pytest -ra` (full Phase 1+2 suite) | 111 passed |
| `ruff check src tests` | clean |
| `mypy --strict` | clean (14 source files) |
| Purity gate — no serial/threading/asyncio/os/time imports | passed (direct imports: `__future__`, `enum`, `math`, `pydantic`, `re`, `typing`) |

## Conventional Commits (3)

| Hash | Type | Subject |
|------|------|---------|
| `842d490` | chore(02-01) | add pyserial 3.5 + pytest-cov 5 deps |
| `308d573` | feat(02-01) | pure arduino protocol parser + DTOs (IO-ARD-04 part 1) |
| `aab2d0a` | test(02-01) | parser branch coverage 100% (IO-ARD-04 / TEST-04 part 1) |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 — Bug] PEP 695 type-alias syntax instead of `TypeAlias`**
- **Found during:** Task 2 ruff check after first write
- **Issue:** Plan's literal `ProtocolEvent: TypeAlias = ...` form fails `ruff UP040` (enabled via `[tool.ruff.lint] select=["UP"]`); the project lint policy mandates the modern PEP 695 `type X = ...` syntax on Python 3.12+.
- **Fix:** Switched both `AccelPhaseLiteral` and `ProtocolEvent` to `type X = ...`. Semantic export unchanged — downstream importers still see a closed-union `ProtocolEvent` and a `Literal[0,1,2,3]` accel-phase alias.
- **Files modified:** `pastor_tracker/src/pastor_tracker/io/arduino_protocol.py`
- **Commit:** `308d573`

**2. [Rule 1 — Bug] Removed unreachable `except ValidationError` blocks**
- **Found during:** Task 3 (achieving 100% branch coverage)
- **Issue:** The first draft of `_parse_settings` (textual + structured paths) and `_parse_diag` wrapped DTO construction in `try/except ValidationError` even though the upstream allowlist (`_SETTINGS_TEXTUAL_MESSAGES`), `_safe_float` finiteness gate, and regex-captured digits guarantee construction never raises. Each unreachable `except` added an uncovered branch, blocking the `--cov-fail-under=100 --cov-branch` gate.
- **Fix:** Replaced the dead `try/except` blocks with direct returns + a comment documenting why ValidationError is unreachable on those paths. Behavior unchanged.
- **Files modified:** `pastor_tracker/src/pastor_tracker/io/arduino_protocol.py`
- **Commit:** `aab2d0a` (folded into the test commit because the refactor exists solely to enable the coverage gate)

**3. [Rule 1 — Bug] Suppressed `PLR0911` / `PLR0912` on `parse_line`**
- **Found during:** Task 2 ruff check
- **Issue:** Ruff's pylint-refactor rules flagged `parse_line`'s 10-arm dispatch ladder for "too many returns / too many branches". The plan and CLAUDE.md rule 5 both mandate a flat ladder (≤ 2-level nesting + guard clauses), and folding the arms into a dict-lookup would either lose mypy type narrowing on the closed `ProtocolEvent` union or hide the prefix priority order (`FB:` must dispatch before any `FB_HEADER:` sibling).
- **Fix:** Added `# noqa: PLR0911, PLR0912` on the `parse_line` signature with an inline rationale paragraph in the docstring.
- **Files modified:** `pastor_tracker/src/pastor_tracker/io/arduino_protocol.py`
- **Commit:** `308d573`

### Other Deviations

None. The plan executed as written aside from the three auto-fixes above; all
acceptance criteria, including grep counts on DTO classes / ErrorCode members /
firmware citations, are met. The grep pattern `^ProtocolEvent: TypeAlias` from
the plan's acceptance_criteria list was the only literal that no longer applies
verbatim due to deviation #1; the equivalent `^type ProtocolEvent` regex
matches once.

## Authentication Gates

None. Plan 02-01 has no auth surface (pure parser, no network, no credentials).

## Self-Check: PASSED

Files created:
- FOUND: `pastor_tracker/src/pastor_tracker/io/arduino_protocol.py`
- FOUND: `pastor_tracker/tests/test_arduino_protocol.py`

Files modified:
- FOUND (modified, in HEAD): `pastor_tracker/pyproject.toml`
- FOUND (modified, in HEAD): `pastor_tracker/uv.lock`

Commits exist (`git log --oneline 842d490^..HEAD`):
- FOUND: `842d490 chore(02-01): add pyserial 3.5 + pytest-cov 5 deps`
- FOUND: `308d573 feat(02-01): pure arduino protocol parser + DTOs (IO-ARD-04 part 1)`
- FOUND: `aab2d0a test(02-01): parser branch coverage 100% (IO-ARD-04 / TEST-04 part 1)`

All 4 verification gates green:
- pytest -ra: 111 passed
- pytest --cov-fail-under=100 --cov-branch: 100% / 100%
- ruff check src tests: clean
- mypy --strict: clean (14 source files)

Purity gate green:
- `grep -E "^(import|from) (serial|threading|asyncio|os|time)" arduino_protocol.py` → 0 matches
- direct imports: `__future__`, `enum`, `math`, `pydantic`, `re`, `typing` only
- `dir(arduino_protocol)` contains zero forbidden-namespace references
