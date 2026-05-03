---
phase: 02-arduino-i-o
plan: 02
subsystem: arduino-transport
tags:
  - arduino
  - transport
  - serial
  - vid-pid
  - io-ard-01
requirements:
  - IO-ARD-01
dependency_graph:
  requires:
    - serial.tools.list_ports.comports     # pyserial 3.5 (added in 02-01)
    - serial.Serial                        # pyserial 3.5
    - structlog                            # Phase 1 logging stack
  provides:
    - pastor_tracker.io.arduino_transport.SerialTransport       # Protocol DI seam
    - pastor_tracker.io.arduino_transport.PySerialTransport     # production impl
    - pastor_tracker.io.arduino_transport.FakeSerialTransport   # in-memory test impl
    - pastor_tracker.io.arduino_transport.discover_arduino_port # IO-ARD-01 entry
    - pastor_tracker.io.arduino_transport.SUPPORTED_VID_PIDS    # frozenset(4)
    - pastor_tracker.io.arduino_transport.ArduinoPortNotFoundError
    - pastor_tracker.io.arduino_transport.FakeSerialClosedError
  affects:
    - Plan 02-03 (orchestrator) — imports SerialTransport, ArduinoPortNotFoundError; will subclass error under unified ArduinoError root
tech_stack:
  added: []                                # all deps already shipped via 02-01
  patterns:
    - typing.Protocol + @runtime_checkable for DI seam
    - SimpleNamespace stubs (no unittest.mock) for ListPortInfo fakes
    - monkeypatch.setattr on module-level comports binding (no pyserial.Serial mocking)
    - structlog.testing.capture_logs for log-event-name assertions
    - Final[...] module-level constants citing PROJECT.md / firmware sources
key_files:
  created:
    - path: pastor_tracker/src/pastor_tracker/io/arduino_transport.py
      role: dirty-edge transport — Protocol DI seam, PySerial wrapper, in-memory fake, VID:PID discovery
      loc: 233
    - path: pastor_tracker/tests/test_arduino_transport.py
      role: discover_arduino_port matrix + FakeSerialTransport behaviour tests
      loc: 226
  modified: []
decisions:
  - id: D-02-02-01
    decision: "PySerialTransport.write annotated with explicit ``written: int = ...`` intermediate to satisfy mypy --strict's no-any-return on pyserial-typed Any"
    rationale: pyproject.toml ``[[tool.mypy.overrides]]`` silences serial.* import resolution, leaving Serial.write inferred as Any; mypy --strict's no-any-return forbids returning that Any directly. Coercing through a typed local makes the public contract honest.
    impact: zero behaviour change; identical bytecode after CPython peephole
  - id: D-02-02-02
    decision: "discover_arduino_port returns matches[0].device through a typed local (chosen_device: str) for the same no-any-return reason"
    rationale: ListPortInfo.device is Any-typed via the override; explicit str annotation closes the type hole
    impact: zero behaviour change
  - id: D-02-02-03
    decision: "Added test_fake_transport_close_idempotent (6th fake test, plan called for 5)"
    rationale: SerialTransport contract states ``close() releases the underlying handle and is idempotent (calling twice does not crash)``. Without an explicit test, regression is silent. Cost is one assertion-free call pair; Rule 2 (auto-add missing critical functionality) — this is a contract-completeness gap.
    impact: +1 test (16 total in this file); fully passing
metrics:
  start: 2026-05-03T19:01:30Z
  end: 2026-05-03T19:11:30Z
  duration_seconds: 600
  task_count: 2
  file_count_created: 2
  file_count_modified: 0
  test_count: 16
  test_pass_count: 16
  full_suite_pass_count: 127
---

# Phase 02 Plan 02: Arduino Transport Layer Summary

Lands the dirty-edge transport for Arduino serial I/O — a `SerialTransport`
runtime-checkable Protocol (DI seam), a `PySerialTransport` production wrapper
around `pyserial`, a `FakeSerialTransport` in-memory bidirectional test fake
with FIFO read order and threading.Event-backed timeouts, and the
`discover_arduino_port` VID:PID auto-detect entry point. Closes IO-ARD-01.

## Plan One-Liner

`SerialTransport` Protocol + `PySerialTransport`/`FakeSerialTransport` impls +
`discover_arduino_port` (4 supported VID:PIDs with manual-override
fail-loud-on-miss), all behind 16 tests asserting both behavioural correctness
and pinned structlog event-name discrimination (`port_discovered` for
single-match vs `port_multiple_matches` for multi-match — WARN 5).

## Files Created

| File | LOC | Purpose |
|------|-----|---------|
| `pastor_tracker/src/pastor_tracker/io/arduino_transport.py` | 233 | SerialTransport Protocol, PySerialTransport, FakeSerialTransport, discover_arduino_port, SUPPORTED_VID_PIDS frozenset, ArduinoPortNotFoundError, FakeSerialClosedError |
| `pastor_tracker/tests/test_arduino_transport.py` | 226 | 6 discover-matrix tests + 6 fake-transport behaviour tests + 1 SUPPORTED_VID_PIDS invariant test (16 incl. 4 parametrize variants) |

## Files Modified

None — all dependencies (`pyserial>=3.5,<4.0`, `structlog`) were already
shipped via Plan 02-01 / Phase 1.

## Public Symbols Exported (final spec for Plan 02-03)

```python
# Module-level constants
SUPPORTED_VID_PIDS: Final[frozenset[tuple[int, int]]] = frozenset({
    (0x2341, 0x0043),   # Arduino Uno R3
    (0x2341, 0x0069),   # Arduino Uno R4
    (0x1A86, 0x7523),   # CH340 clone
    (0x0403, 0x6001),   # FTDI clone
})

# Errors (plain Exception subclasses; Plan 02-03 will reunify under ArduinoError)
class ArduinoPortNotFoundError(Exception): ...
class FakeSerialClosedError(Exception): ...

# DI seam — the only contract orchestrator code depends on
@runtime_checkable
class SerialTransport(Protocol):
    def write(self, data: bytes) -> int: ...
    def read_line(self, timeout: float) -> bytes | None: ...   # None on timeout
    def close(self) -> None: ...

# Production impl — opens serial.Serial at __init__ (fail-loud on missing device)
class PySerialTransport:
    def __init__(self, port: str, baud: int) -> None: ...
    def write(self, data: bytes) -> int: ...
    def read_line(self, timeout: float) -> bytes | None: ...   # patches per-call timeout
    def close(self) -> None: ...

# Test impl — bidirectional in-memory fake; FIFO read order
class FakeSerialTransport:
    captured_writes: list[bytes]
    def __init__(self) -> None: ...
    def feed_rx(self, line: bytes) -> None: ...    # enqueue (no trailing \n)
    def write(self, data: bytes) -> int: ...       # raises FakeSerialClosedError after close
    def read_line(self, timeout: float) -> bytes | None: ...   # threading.Event-backed
    def close(self) -> None: ...                   # idempotent

# Discovery entry point
def discover_arduino_port(configured_port: str | None) -> str: ...
```

`read_line` contract: returns the line WITHOUT trailing `\r`/`\n` so Plan
02-01's `parse_line` accepts it directly. Returns `None` on timeout.

`discover_arduino_port` log-event contract:
- `port_manual_override` (INFO, port=...)               — configured override honoured
- `port_discovered` (INFO, port/vid/pid)                — single VID:PID match
- `port_multiple_matches` (INFO, chosen=, all=[...])    — multi-match; first picked
- raises `ArduinoPortNotFoundError`                     — no match OR configured-but-absent

## Test Coverage

```
tests\test_arduino_transport.py ................        16 passed in 0.38s
Full Phase 1 + Plan 02-01 + Plan 02-02 suite           127 passed in 1.43s
```

Test structure:

| Group | Tests | Purpose |
|-------|-------|---------|
| A — discover() matrix | 6 (incl. 4 parametrize variants) | first-match returned, no-match raises, multi-match picks first AND emits `port_multiple_matches` log, manual-override present + logs, manual-override absent raises, each of 4 supported VID:PIDs returns + emits `port_discovered` log |
| B — FakeSerialTransport behaviour | 6 | feed-and-read, read-timeout, write captures, write-after-close raises, FIFO multi-line, close-idempotent |
| C — module invariants | 1 | SUPPORTED_VID_PIDS is frozenset of exactly 4 expected pairs |
| **Total** | **16** | **all green** |

Pinned log-event-name assertions (WARN 5):
- `test_discover_multiple_matches_picks_first` asserts `event == "port_multiple_matches"` and explicitly asserts `port_discovered` did NOT fire on the multi-match path.
- `test_discover_each_supported_vid_pid` asserts `event == "port_discovered"` and explicitly asserts `port_multiple_matches` did NOT fire on the single-match path.
No event-name swap is possible without breaking a test.

## Acceptance Gate Results

| Gate | Result |
|------|--------|
| `pytest tests/test_arduino_transport.py -x` | 16 passed in 0.38s |
| `pytest -ra` (full Phase 1 + 02-01 + 02-02 suite) | 127 passed in 1.43s |
| `ruff check src/pastor_tracker/io/arduino_transport.py tests/test_arduino_transport.py` | All checks passed! |
| `mypy --strict` (full package) | Success: no issues found in 15 source files |
| Pure-import smoke (no real device needed) | imports ok |
| `grep -c "Only the RX thread calls read_line"` (WARN 4 pin) | 1 |
| `grep -c "\"port_discovered\""` in source | 1 |
| `grep -c "\"port_multiple_matches\""` in source | 1 |
| `grep -c "@runtime_checkable"` | 1 |
| `grep -c "from unittest.mock import"` in tests | 0 |

## Conventional Commits (2)

| Hash | Type | Subject |
|------|------|---------|
| `837fa03` | feat(02-02) | arduino transport layer + VID:PID discovery (IO-ARD-01) |
| `bf1c8ed` | test(02-02) | transport discovery + fake-serial behaviour (IO-ARD-01) |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 — Bug] Mypy --strict no-any-return on pyserial-typed methods**
- **Found during:** Task 1 mypy gate after first write
- **Issue:** `pyproject.toml` `[[tool.mypy.overrides]]` silences `serial.*` import resolution, but that leaves `Serial.write` and `ListPortInfo.device` as `Any`. Returning them directly violates mypy --strict's `no-any-return` rule (the project's `disallow_any_explicit = true` posture).
- **Fix:** Coerce both return paths through typed locals — `written: int = self._serial.write(data)` in `PySerialTransport.write` and `chosen_device: str = matches[0].device` in `discover_arduino_port`. Bytecode-equivalent after peephole; honest typed contract.
- **Files modified:** `pastor_tracker/src/pastor_tracker/io/arduino_transport.py`
- **Commit:** `837fa03`

**2. [Rule 1 — Bug] RUF002 ambiguous Unicode multiplication sign in test docstring**
- **Found during:** Task 2 ruff gate
- **Issue:** Test module docstring used `×` (U+00D7 MULTIPLICATION SIGN) for "matrix dimensions" prose, which RUF002 flags as ambiguous against ASCII `x`.
- **Fix:** Replaced both occurrences with ASCII `x`. Reads identically; passes lint.
- **Files modified:** `pastor_tracker/tests/test_arduino_transport.py`
- **Commit:** `bf1c8ed`

### Other Deviations

**3. [Rule 2 — Missing critical functionality] Added 6th FakeSerialTransport test (`test_fake_transport_close_idempotent`)**
- **Found during:** Task 2 test design
- **Issue:** Plan listed 5 fake-transport tests (`feed_and_read`, `read_timeout`, `write_captures`, `write_after_close_raises`, `fifo`). The `SerialTransport` contract in the plan's `<behavior>` section explicitly states "`close()` … is idempotent (calling twice does not crash)" — without a test, that contract clause is silently regressable.
- **Fix:** Added `test_fake_transport_close_idempotent` (1 line of body — `close(); close()`). Cost negligible; closes contract gap.
- **Files modified:** `pastor_tracker/tests/test_arduino_transport.py`
- **Commit:** `bf1c8ed`

No other deviations. Plan executed as written aside from the three auto-fixes
above; all named acceptance criteria — including grep counts on
`port_discovered` / `port_multiple_matches` / `Only the RX thread calls
read_line` / `@runtime_checkable` / `from unittest.mock import` — pass.

## Authentication Gates

None. Plan 02-02 has no auth surface (local USB serial transport, no
credentials, no network).

## Self-Check: PASSED

Files created:
- FOUND: `pastor_tracker/src/pastor_tracker/io/arduino_transport.py` (233 LOC)
- FOUND: `pastor_tracker/tests/test_arduino_transport.py` (226 LOC)

Commits exist (`git log --oneline 837fa03^..HEAD`):
- FOUND: `837fa03 feat(02-02): arduino transport layer + VID:PID discovery (IO-ARD-01)`
- FOUND: `bf1c8ed test(02-02): transport discovery + fake-serial behaviour (IO-ARD-01)`

All four verification gates green:
- pytest tests/test_arduino_transport.py -x: 16 passed
- pytest -ra (full suite): 127 passed
- ruff check (transport + tests): All checks passed
- mypy --strict (full package): 15 source files clean

WARN-pin acceptance gates green:
- single-reader-invariant comment present (WARN 4): 1 occurrence
- distinct log events emitted (WARN 5): port_discovered + port_multiple_matches both present
- log-event-name swap test fires both ways: tests assert NEGATIVE on the wrong-event-name in each path

## EXECUTION COMPLETE

**Plan:** 02-02
**Tasks:** 2/2
**SUMMARY:** `.planning/phases/02-arduino-i-o/02-02-SUMMARY.md`

**Commits:**
- `837fa03` — feat(02-02): arduino transport layer + VID:PID discovery (IO-ARD-01)
- `bf1c8ed` — test(02-02): transport discovery + fake-serial behaviour (IO-ARD-01)

**Duration:** ~10 min
