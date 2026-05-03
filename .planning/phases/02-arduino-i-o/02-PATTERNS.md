# Phase 2: Arduino I/O - Pattern Map

**Mapped:** 2026-05-03
**Files analyzed:** 14 (4 source + 8 test + 1 fixture + 1 build config)
**Analogs found:** 13 / 14 (one file — `arduino_motor.py` orchestrator — has no exact analog because Phase 1 shipped no asyncio/threaded I/O; closest partial match cited below.)

## File Classification

| New / Modified File | Role | Data Flow | Closest Analog | Match Quality |
|---------------------|------|-----------|----------------|---------------|
| `pastor_tracker/src/pastor_tracker/io/__init__.py` | package marker | n/a | `pastor_tracker/src/pastor_tracker/core/__init__.py` | exact |
| `pastor_tracker/src/pastor_tracker/io/arduino_protocol.py` | model (DTOs) + utility (pure parser) | transform (bytes→DTO) | `pastor_tracker/src/pastor_tracker/core/types.py` (DTOs) + `pastor_tracker/src/pastor_tracker/core/geometry.py` (pure transform) | exact (DTO patterns) + role-match (transform) |
| `pastor_tracker/src/pastor_tracker/io/arduino_transport.py` | service (transport layer) + utility (`discover_arduino_port`) | request-response (bytes I/O) | `pastor_tracker/src/pastor_tracker/logging_config.py` (single-shot bootstrap) — NO direct analog for blocking I/O wrapping; partial Phase 1 match | partial |
| `pastor_tracker/src/pastor_tracker/io/arduino_motor.py` | service (orchestrator) | event-driven (async TX + RX queue) | `pastor_tracker/src/pastor_tracker/core/damping.py` (Config-field consumption + frozen-state shape) | partial (Config + naming patterns only — concurrency is novel) |
| `pastor_tracker/tests/test_arduino_protocol.py` | test (pure parser) | n/a | `pastor_tracker/tests/test_geometry.py` + `pastor_tracker/tests/test_types.py` | exact |
| `pastor_tracker/tests/test_arduino_transport.py` | test (port discovery, monkeypatched) | n/a | `pastor_tracker/tests/test_config.py` (monkeypatch + tmp_path style) | role-match |
| `pastor_tracker/tests/test_arduino_motor_handshake.py` | test (async, FakeSerial-driven) | n/a | `pastor_tracker/tests/test_logging.py` (capsys) + `tests/test_damping.py` (deterministic step-by-step) | partial |
| `pastor_tracker/tests/test_arduino_motor_tx.py` | test (async TX assertion) | n/a | `pastor_tracker/tests/test_damping.py` | partial |
| `pastor_tracker/tests/test_arduino_motor_heartbeat.py` | test (async, virtual-clock cadence) | n/a | `pastor_tracker/tests/test_damping.py` (timing-aware deterministic loop) | partial |
| `pastor_tracker/tests/test_arduino_motor_recovery.py` | test (state-machine integration) | n/a | `pastor_tracker/tests/test_damping.py` (multi-step state evolution) | partial |
| `pastor_tracker/tests/test_arduino_motor_error.py` | test (parametrized halt-paths) | n/a | `pastor_tracker/tests/test_types.py` (parametrized rejection cases) | role-match |
| `pastor_tracker/tests/test_arduino_motor_replay.py` | test (golden-trace integration) | n/a | NONE in Phase 1 — closest is `tests/test_logging.py` (end-to-end capsys assertion) | partial |
| `pastor_tracker/tests/fixtures/arduino_traces.py` | fixture (canned bytes) | n/a | `pastor_tracker/tests/fixtures/__init__.py` (empty marker) — module exists, no content analog | none (new shape) |
| `pastor_tracker/pyproject.toml` (modify) | config | n/a | `pastor_tracker/pyproject.toml` (extend `[project.dependencies]` + `[dependency-groups].dev`) | exact (self) |

---

## Pattern Assignments

### `pastor_tracker/src/pastor_tracker/io/__init__.py` (package marker)

**Analog:** `pastor_tracker/src/pastor_tracker/core/__init__.py` (already-shipped sibling) and the existing `pastor_tracker/src/pastor_tracker/io/__init__.py` (line 1) which already has the docstring `"""I/O edge — Arduino serial driver and OBS Virtual Camera frame source. Phase 2/3."""`.

**Action:** No change needed. Keep the existing one-line module docstring. Optionally append a `from __future__ import annotations` line for symmetry with siblings, but Phase 1's `core/__init__.py` is also a single docstring — match that style.

---

### `pastor_tracker/src/pastor_tracker/io/arduino_protocol.py` (model + pure parser)

**Analogs:**
- DTOs / frozen-Pydantic shape → `pastor_tracker/src/pastor_tracker/core/types.py`
- Pure transform with named constants and tiger-style `ValueError` → `pastor_tracker/src/pastor_tracker/core/geometry.py`

**Imports pattern** (from `core/types.py` lines 17–24):
```python
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
```
Add for this module: `from enum import IntEnum` (for `ErrorCode`) and `from typing import Final` (for protocol constants — see RESEARCH.md `Code Examples`).

**Module-docstring pattern** (from `core/types.py` lines 1–16):
```python
"""Frozen DTOs flowing between pipeline stages.

… mutation pattern … note on … invariants enforced in __post_init__ / model_validator …
"""
```
Apply: open with one-paragraph "what flows where" summary, document the parser-output union, cite firmware sources by line number (`main.cpp:417–422`, `protocol.h:67–80`).

**Named-constants pattern** (from `core/types.py` lines 33–41):
```python
# Frame.image contract: BGR uint8 of shape (H, W, 3). Named constants per
# CLAUDE.md rule 6 — no magic numbers in validation paths.
_IMAGE_NDIM_EXPECTED: int = 3  # H, W, channels
_IMAGE_CHANNELS_EXPECTED: int = 3  # B, G, R
_TIMESTAMP_NS_MIN: int = 0
```
Apply (verbatim names from RESEARCH.md `Code Examples`):
```python
PROTOCOL_VERSION_MAJOR: Final[int] = 2
FIRMWARE_INPUT_BUFFER_SIZE: Final[int] = 48          # protocol.h:39
FIRMWARE_INPUT_BUFFER_USABLE: Final[int] = 47        # minus null terminator
FIRMWARE_HEARTBEAT_TIMEOUT_MS: Final[int] = 1_000    # protocol.h:33
FIRMWARE_FEEDBACK_INTERVAL_MS: Final[int] = 20       # protocol.h:36
SEQ_MODULUS: Final[int] = 1 << 32                    # uint32 rollover
FEEDBACK_SEQ_GAP_WARN_THRESHOLD: Final[int] = 5      # IO-ARD-04
```
Each constant cites its firmware line — same style as `core/types.py` comments and Phase 1's `_FIRMWARE_PC_HEARTBEAT_TIMEOUT_MS` in `config.py:39`.

**Frozen-Pydantic base pattern** (from `core/types.py` lines 87–90):
```python
class _FrozenModel(BaseModel):
    """Base for all Pydantic DTOs in this module — frozen + extra-forbidden."""

    model_config = ConfigDict(frozen=True, extra="forbid")
```
Apply verbatim — name it `_Event` per RESEARCH.md and have every protocol DTO (`Ready`, `Feedback`, `Settings`, `SettingsInfo`, `Limits`, `Driver`, `Reset`, `Stop`, `Diag`, `Error`, `FeedbackHeader`) inherit from it.

**Field-validation pattern** (from `core/types.py` lines 100–122):
```python
class Detection(_FrozenModel):
    subject_center_x_normalized: float = Field(ge=0.0, le=1.0)
    …
    timestamp_ns: int = Field(ge=0)

    @model_validator(mode="after")
    def _bbox_well_ordered(self) -> Detection:
        if self.bbox_x2_normalized <= self.bbox_x1_normalized:
            raise ValueError(
                f"bbox_x2_normalized ({self.bbox_x2_normalized}) must exceed "
                f"bbox_x1_normalized ({self.bbox_x1_normalized})"
            )
        …
        return self
```
Apply to:
- `Ready(version: int = Field(ge=1, le=255))` — match RESEARCH.md `Code Examples`.
- `Feedback`: floats unconstrained except `timestamp_micros: int = Field(ge=0)` and `sequence: int = Field(ge=0)` and `accel_phase: Literal[0, 1, 2, 3]`.
- `Limits`: `@model_validator(mode="after")` enforcing `max_deg > min_deg` — exact mirror of the bbox validator.
- `Error`: `code: ErrorCode` (typed enum); accept unknown int codes via a separate `code_raw: int` for forward-compat per RESEARCH.md Pitfall 9.

**Pure-transform pattern** (from `core/geometry.py` lines 32–54):
```python
def normalized_x_to_angle_deg(
    normalized_x: float, horizontal_fov_deg: float
) -> float:
    if not NORMALIZED_X_MIN <= normalized_x <= NORMALIZED_X_MAX:
        raise ValueError(
            f"normalized_x out of [{NORMALIZED_X_MIN}, {NORMALIZED_X_MAX}]: "
            f"{normalized_x}"
        )
    if not FOV_DEG_MIN_EXCLUSIVE < horizontal_fov_deg < FOV_DEG_MAX_EXCLUSIVE:
        raise ValueError(…)
    half_fov_rad = math.radians(horizontal_fov_deg * HALF)
    offset = (TWO * normalized_x) - NORMALIZED_RANGE
    return math.degrees(math.atan(offset * math.tan(half_fov_rad)))
```
Key lessons for `parse_line(line: bytes) -> ProtocolEvent`:
1. **Module-level function, no class**, exactly like `normalized_x_to_angle_deg`.
2. **Guard clauses + early return** — exactly the prefix-dispatch ladder shown in RESEARCH.md `Code Examples` lines 990–1005. ≤ 2 levels of nesting (CLAUDE.md rule 5). No `if/elif` chains nested inside loops.
3. **Always raise `ValueError`-derived errors with informative messages** — `ProtocolParseError(ArduinoError)` is the local equivalent.
4. **No `Any`**, no `cast()`. Return type is the closed union `ProtocolEvent`.

**Error-class pattern** (no Phase 1 analog — derive from CLAUDE.md tiger-style):
RESEARCH.md `Logging & Errors` defines the hierarchy. Mirror it in `arduino_protocol.py` for `ProtocolParseError`, with the rest (`ArduinoError`, `HandshakeTimeoutError`, …) in `arduino_motor.py` (where they're raised from). `ProtocolParseError` stays in the pure module since it is raised by the parser.

---

### `pastor_tracker/src/pastor_tracker/io/arduino_transport.py` (Protocol + PySerial impl + Fake + discover)

**Analog:** None in Phase 1 for blocking I/O, but the **single-shot bootstrap pattern** in `pastor_tracker/src/pastor_tracker/logging_config.py` (lines 13–40) sets the precedent for a small dirty-edge module that gets called once. Discovery and transport construction follow that "one bootstrap call, fail loud" shape.

**Imports pattern** (from `logging_config.py` lines 6–10):
```python
from __future__ import annotations

import logging

import structlog
```
Apply (per RESEARCH.md `Discovery & Boot`):
```python
from __future__ import annotations

import collections
import threading
from typing import Final, Protocol, runtime_checkable

import serial                                           # pyserial
import structlog
from serial.tools.list_ports import comports
```

**Named-constants pattern** (from `config.py` lines 31–43):
```python
# Module-level constants (CLAUDE.md rule 6 — no magic numbers in code).
# Values mirror PROMPT.md / firmware contract; document the source of each.
_BAUD_MIN: int = 9_600
_FIRMWARE_PC_HEARTBEAT_TIMEOUT_MS: int = 1_000
```
Apply:
```python
SUPPORTED_VID_PIDS: Final[frozenset[tuple[int, int]]] = frozenset({
    (0x2341, 0x0043),  # Arduino Uno R3
    (0x2341, 0x0069),  # Arduino Uno R4
    (0x1A86, 0x7523),  # CH340 clone
    (0x0403, 0x6001),  # FTDI clone
})
_RX_READ_TIMEOUT_SEC: Final[float] = 0.1   # RX thread loop tick — short so stop_event is responsive
```
Verbatim from RESEARCH.md `Discovery & Boot` — those four pairs are project-locked (PROJECT.md ## Context).

**Protocol pattern** (from `core/damping.py` lines 27–34, frozen-dataclass shape; for the Protocol itself, follow RESEARCH.md `Testing Strategy`):
```python
@runtime_checkable
class SerialTransport(Protocol):
    def write(self, data: bytes) -> int: ...
    def read_line(self, timeout: float) -> bytes | None: ...   # None on timeout
    def close(self) -> None: ...
```
The Phase 1 `dataclass(frozen=True, slots=True)` style does not directly apply (Protocols are classes, not data); however the **`runtime_checkable` + minimal-method-set + return-type-annotated** discipline matches CLAUDE.md rule 8 ("type hints everywhere, no `Any`").

**Fail-loud guard-clause pattern** (from `core/damping.py` lines 47–51):
```python
def __post_init__(self) -> None:
    if self.time_constant_sec <= 0.0:
        raise ValueError(
            f"time_constant_sec must be > 0, got {self.time_constant_sec}"
        )
```
Apply identically in `discover_arduino_port`:
```python
def discover_arduino_port(configured_port: str | None) -> str:
    log = structlog.get_logger(module="arduino_transport")
    if configured_port is not None:
        if any(p.device == configured_port for p in comports()):
            log.info("port_manual_override", port=configured_port)
            return configured_port
        # Tiger-style: configured port not present → crash, do not silently fall back.
        raise ArduinoPortNotFoundError(
            f"arduino_port={configured_port!r} not present in serial.tools.list_ports"
        )
    matches = [
        p for p in comports()
        if p.vid is not None and p.pid is not None
        and (p.vid, p.pid) in SUPPORTED_VID_PIDS
    ]
    if not matches:
        raise ArduinoPortNotFoundError("no Arduino USB device matched VID:PID set")
    if len(matches) > 1:
        log.info(
            "port_multiple_matches",
            chosen=matches[0].device,
            all=[(m.device, hex(m.vid), hex(m.pid)) for m in matches],
        )
    return matches[0].device
```
Source: RESEARCH.md lines 383–406 (verbatim copy permissible — flagged `[VERIFIED]` against pyserial 3.5 on dev box).

**`PySerialTransport` impl pattern** (from RESEARCH.md `Testing Strategy`, no Phase 1 analog — the module is dirty-edge by definition):
```python
class PySerialTransport:
    def __init__(self, port: str, baud: int) -> None:
        self._serial = serial.Serial(port=port, baudrate=baud, timeout=0.0)

    def write(self, data: bytes) -> int:
        return self._serial.write(data)

    def read_line(self, timeout: float) -> bytes | None:
        self._serial.timeout = timeout
        line = self._serial.read_until(b"\n")
        if not line.endswith(b"\n"):
            return None       # timed out
        return line.rstrip(b"\r\n")

    def close(self) -> None:
        self._serial.close()
```

**`FakeSerialTransport` impl pattern** (from RESEARCH.md `Testing Strategy`, lines 549–582 — verbatim suitable):
```python
class FakeSerialTransport:
    """Bidirectional in-memory fake. Tests feed_rx() inbound lines (no \\n)
    and assert against captured_writes."""

    def __init__(self) -> None:
        self.captured_writes: list[bytes] = []
        self._rx_lines: collections.deque[bytes] = collections.deque()
        self._rx_event = threading.Event()
        self._closed = False

    def feed_rx(self, line: bytes) -> None: …
    def write(self, data: bytes) -> int: …
    def read_line(self, timeout: float) -> bytes | None: …
    def close(self) -> None: …
```

---

### `pastor_tracker/src/pastor_tracker/io/arduino_motor.py` (orchestrator)

**Analog:** No Phase 1 file does asyncio + threading. The **closest partial match is `pastor_tracker/src/pastor_tracker/core/damping.py`** for two narrow patterns: (a) `Config` field consumption pattern at construction, (b) `dataclass(frozen=True)` for internal state shapes (e.g. handshake state). The novel concurrency machinery (RX thread + asyncio queue + lock) has no Phase 1 analog and must follow RESEARCH.md `Architecture & Concurrency` directly.

**Imports pattern** (from `core/damping.py` lines 16–19 + `__main__.py` lines 6–10):
```python
from __future__ import annotations

import asyncio
import contextlib
import threading
from dataclasses import dataclass
from typing import Final

import structlog

from pastor_tracker.config import Config
from pastor_tracker.core.types import MotorCommand
from pastor_tracker.io.arduino_protocol import (
    ErrorCode,
    Feedback,
    FeedbackHeader,
    Ready,
    Settings,
    SettingsInfo,
    Limits,
    Error,
    ProtocolEvent,
    ProtocolParseError,
    parse_line,
    FIRMWARE_INPUT_BUFFER_USABLE,
    FEEDBACK_SEQ_GAP_WARN_THRESHOLD,
    SEQ_MODULUS,
)
from pastor_tracker.io.arduino_transport import SerialTransport
```

**Module-level named-constants pattern** (from `core/damping.py` lines 21–24):
```python
LN2: float = math.log(2.0)
DAMPING_NUMERATOR: float = 4.0 * LN2
HALF: float = 0.5
```
Apply (per RESEARCH.md sketches):
```python
_TX_NEWLINE: Final[bytes] = b"\n"
_MS_PER_SEC: Final[float] = 1_000.0
_RX_QUEUE_MAX_SIZE: Final[int] = 256              # CONTEXT.md drop-oldest bound
_RX_JOIN_TIMEOUT_SEC: Final[float] = 1.0
```

**`structlog.get_logger(module=…)` binding pattern** (from `__main__.py` line 16):
```python
log = structlog.get_logger(__name__)
log.info("boot", phase=1, status="scaffold-only")
```
Apply with the Phase 2 binding convention from RESEARCH.md `Logging & Errors` lines 692–694:
```python
self._logger = structlog.get_logger(module="arduino_motor")
self._logger.info("handshake_started", port=port, baud=baud, expected_version=expected)
```

**Config-field consumption pattern** (from `core/damping.py` doc lines 39–42):
> ``time_constant_sec`` is the tau exposed in
> ``Config.framing_time_constant_sec`` and ``Config.pan_time_constant_sec``.

`damping.py` does **not** import `Config` — it accepts the scalar directly. The orchestrator must accept `Config` as a whole because it touches many fields. The pattern (CLAUDE.md rule 9 — immutable, frozen Pydantic):
```python
class ArduinoMotor:
    def __init__(self, transport: SerialTransport, config: Config) -> None:
        self._transport = transport
        self._config = config                              # frozen Pydantic — safe to hold
        self._tx_lock = asyncio.Lock()
        self._rx_queue: asyncio.Queue[ProtocolEvent] = asyncio.Queue(maxsize=_RX_QUEUE_MAX_SIZE)
        self._stop_event = threading.Event()
        self._dispatch_paused = False
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._rx_thread: threading.Thread | None = None
        self._last_seq: int | None = None
        self._logger = structlog.get_logger(module="arduino_motor")
```

**Frozen internal-state dataclass pattern** (from `core/damping.py` lines 27–33):
```python
@dataclass(frozen=True, slots=True)
class FollowerState:
    position: float
    velocity: float
```
Apply for the orchestrator's enum-style state machine (Running / Recovering / Faulted), preferably as `enum.Enum` for type-strictness:
```python
class _MotorState(enum.Enum):
    DISCONNECTED = "disconnected"
    HANDSHAKING = "handshaking"
    RUNNING = "running"
    RECOVERING = "recovering"
    FAULTED = "faulted"
    CLOSED = "closed"
```
(Use `enum.Enum` not `IntEnum` here — these are not wire-protocol values; mypy `--strict` will catch comparison typos.)

**Guard-clause + ≤ 2-level conditional pattern** (from `core/damping.py` lines 59–76 and `core/geometry.py` lines 32–54):
```python
def step(self, state: FollowerState, target: float, dt: float) -> FollowerState:
    if dt <= 0.0:
        raise ValueError(f"dt must be > 0, got {dt}")
    halflife = self.time_constant_sec * LN2
    …
    return replace(state, position=new_position, velocity=new_velocity)
```
Apply to every async method — single guard at top, single happy-path body, single return. RESEARCH.md `Architecture & Concurrency` already shows this style for `_send_raw`, `_heartbeat_loop`, `_enqueue`, `_wait_for_ready`, `close` — those sketches comply. Copy them, do not invent.

**TX-path skeleton** (verbatim from RESEARCH.md lines 246–276):
```python
async def _send_raw(self, line: bytes) -> None:
    if len(line) + 1 > FIRMWARE_INPUT_BUFFER_USABLE:
        raise ValueError(f"TX line {len(line)} > {FIRMWARE_INPUT_BUFFER_USABLE}")
    loop = asyncio.get_running_loop()
    async with self._tx_lock:
        await loop.run_in_executor(None, self._transport.write, line + _TX_NEWLINE)

async def send_motor_angle(self, command: MotorCommand) -> None:
    if self._dispatch_paused:
        return
    payload = f"M:{command.target_angle_deg:.3f}".encode("ascii")
    await self._send_raw(payload)
```

**RX-thread + queue-bridge skeleton** (verbatim from RESEARCH.md lines 282–326).

**Handshake-reader skeleton** (verbatim from RESEARCH.md lines 426–455).

**Heartbeat-task skeleton** (verbatim from RESEARCH.md lines 331–340).

**Recovery state-machine skeleton** (verbatim from RESEARCH.md lines 482–501).

**Clean-shutdown skeleton** (verbatim from RESEARCH.md lines 347–354).

**Error-class hierarchy** (verbatim from RESEARCH.md lines 723–752): `ArduinoError`, `ArduinoPortNotFoundError` (raise from `arduino_transport.py`), `HandshakeTimeoutError`, `ProtocolVersionMismatchError`, `WatchdogResetError`, `FirmwareErrorReceived`, `LinkLostError`. `ProtocolParseError` lives in `arduino_protocol.py` (raised by parser, imported here for `except` typing).

---

### `pastor_tracker/tests/test_arduino_protocol.py` (parser tests)

**Analogs:**
- Property/parametrized table style → `pastor_tracker/tests/test_geometry.py`
- Pydantic ValidationError + multi-form rejection cases → `pastor_tracker/tests/test_types.py`

**Imports pattern** (from `tests/test_geometry.py` lines 1–13):
```python
"""Property tests for ``pastor_tracker.core.geometry`` (CORE-02 + TEST-01)."""
from __future__ import annotations

import math

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from pastor_tracker.core.geometry import (
    angle_deg_to_normalized_x,
    normalized_x_to_angle_deg,
)
```
Apply:
```python
"""Parser tests for ``pastor_tracker.io.arduino_protocol`` (IO-ARD-04 / TEST-04).

100% branch coverage gate per .planning/phases/02-arduino-i-o/02-RESEARCH.md
Validation Architecture. No fakes, no fixtures — pure bytes→DTO transform.
"""
from __future__ import annotations

import pytest

from pastor_tracker.io.arduino_protocol import (
    Driver, Diag, Error, ErrorCode, Feedback, FeedbackHeader,
    Limits, ProtocolParseError, Ready, Reset, Settings, SettingsInfo, Stop,
    parse_line,
)
```

**Named-constants-in-tests pattern** (from `tests/test_geometry.py` lines 15–24 — even tests use named constants):
```python
ROUNDTRIP_TOL: float = 1e-9
DEFAULT_FOV_DEG: float = 70.0
HYP_MAX_EXAMPLES: int = 200
```
Phase 2 may relax this in tests per `pyproject.toml` lines 61–62 (`tests/**/*.py` ignores `PLR2004` magic-value rule), BUT keep named constants for any tolerance/threshold shared across tests for readability. Magic strings (line literals like `b"READY:v2"`) are fine.

**Parametrized table pattern** (verbatim from RESEARCH.md `Testing Strategy` lines 622–651 — this IS the pattern):
```python
@pytest.mark.parametrize("line,expected", [
    (b"READY:v2", Ready(version=2)),
    (b"FB:1.50,2.00,1234.5,1,123456,42,2", Feedback(...)),
    (b"SETTINGS: defaults (no valid EEPROM)", SettingsInfo(message="defaults (no valid EEPROM)")),
    (b"SETTINGS:25000.00,12500.00,1.00,0.00,0.10",
        Settings(max_speed=25000.0, max_accel=12500.0, pid_p=1.0, pid_i=0.0, pid_d=0.1)),
    (b"DRIVER:ENABLED", Driver(enabled=True)),
    (b"ERROR:11 - PC heartbeat lost",
        Error(code=ErrorCode.HEARTBEAT_TIMEOUT, message="PC heartbeat lost")),
    (b"FB_HEADER:currentAngle,...", FeedbackHeader()),
])
def test_parse_line_well_formed(line: bytes, expected) -> None:
    assert parse_line(line) == expected
```

**Error-rejection-test pattern** (from `tests/test_types.py` lines 95–122, the WR-03 tests for inverted/zero-area bbox):
```python
def test_detection_rejects_inverted_bbox_x() -> None:
    """WR-03: ``bbox_x2`` must strictly exceed ``bbox_x1``."""
    with pytest.raises(ValidationError, match="bbox_x2_normalized"):
        Detection(…)
```
Apply:
```python
@pytest.mark.parametrize("malformed", [
    b"FB:1.5,2.0",                     # too few fields
    b"ERROR: missing code",            # no numeric code
    b"GIBBERISH",                      # unknown prefix
    b"",                               # empty
])
def test_parse_line_malformed_raises(malformed: bytes) -> None:
    with pytest.raises(ProtocolParseError):
        parse_line(malformed)
```
Plus a forward-compat case (RESEARCH.md Pitfall 9):
```python
def test_parse_line_unknown_error_code_accepted() -> None:
    """ERROR:99 (future code) must NOT crash the parser — forward-compat."""
    event = parse_line(b"ERROR:99 - future code")
    # Either typed enum or fall-through int — pin in plan.
    assert isinstance(event, Error)
```

---

### `pastor_tracker/tests/test_arduino_transport.py` (port discovery)

**Analog:** `pastor_tracker/tests/test_config.py` (monkeypatch-heavy, env+fs manipulation).

**Imports pattern** (from `test_config.py` lines 1–16):
```python
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from pastor_tracker.config import Config
```
Apply:
```python
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock     # OK in tests for stub'ing ListPortInfo

import pytest

from pastor_tracker.io.arduino_transport import (
    ArduinoPortNotFoundError,
    SUPPORTED_VID_PIDS,
    discover_arduino_port,
)
```
**Important:** RESEARCH.md `Don't Hand-Roll` table forbids mocking `serial.Serial` itself, but stubbing `ListPortInfo`-shaped objects (which are POD-ish — just `.device`, `.vid`, `.pid`) is a different game. Prefer `monkeypatch.setattr("pastor_tracker.io.arduino_transport.comports", lambda: [...])` returning simple objects (e.g. `SimpleNamespace(device="COM6", vid=0x2341, pid=0x0043)`) over `MagicMock`.

**Monkeypatch-via-`pytest.MonkeyPatch` pattern** (from `test_config.py` lines 39–52):
```python
def test_env_overrides_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    valid_config_dict: dict[str, Any],
) -> None:
    monkeypatch.setenv("PTS_ARDUINO_PORT", "COM9")
    cfg = Config()
    assert cfg.arduino_port == "COM9"
```
Apply:
```python
def test_discover_returns_first_supported_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_ports = [
        SimpleNamespace(device="COM6", vid=0x2341, pid=0x0043),
        SimpleNamespace(device="COM1", vid=None,  pid=None),
    ]
    monkeypatch.setattr(
        "pastor_tracker.io.arduino_transport.comports", lambda: fake_ports
    )
    assert discover_arduino_port(configured_port=None) == "COM6"
```

**Test cases to cover (per IO-ARD-01)**:
1. Single VID:PID match → returned.
2. Multiple matches → first returned, INFO log emitted (assert via `caplog` or `structlog.testing.capture_logs`).
3. No matches → `ArduinoPortNotFoundError`.
4. Configured port present → returned, manual-override INFO log.
5. Configured port absent → `ArduinoPortNotFoundError` (tiger-style fail-loud per RESEARCH.md `Discovery & Boot` line 389).
6. Each of the 4 supported VID:PIDs in turn (parametrize).

---

### `pastor_tracker/tests/test_arduino_motor_handshake.py` (async, FakeSerial)

**Analogs:** `pastor_tracker/tests/test_logging.py` (capsys-based JSON assertion) + `pastor_tracker/tests/test_damping.py` (deterministic step-by-step state evolution).

**Imports pattern** (combining `test_logging.py` + `test_damping.py`):
```python
from __future__ import annotations

import asyncio
import json

import pytest

from pastor_tracker.config import Config
from pastor_tracker.io.arduino_motor import (
    ArduinoMotor,
    HandshakeTimeoutError,
    ProtocolVersionMismatchError,
)
from pastor_tracker.io.arduino_transport import FakeSerialTransport
```

**`asyncio_mode="auto"` pattern** (from `pyproject.toml:93`): every `async def test_…` is auto-collected. No `@pytest.mark.asyncio` decorator needed. Mirror this — Phase 1 already configured it.

**Test-fixture-construction pattern** (from `tests/conftest.py` lines 7–41 — `valid_config_dict` is shared):
Reuse `valid_config_dict` to build a `Config(**valid_config_dict)` in each test. Override individual fields per test (`{**valid_config_dict, "arduino_ready_timeout_sec": 0.05}`). Phase 1's pattern is already prepared for this.

**Three-line boot preamble feeding pattern** (verbatim from RESEARCH.md lines 590–599):
```python
async def test_handshake_succeeds_with_full_preamble(
    valid_config_dict: dict[str, Any],
) -> None:
    fake = FakeSerialTransport()
    fake.feed_rx(b"SETTINGS: defaults (no valid EEPROM)")
    fake.feed_rx(b"FB_HEADER:currentAngle,targetAngle,speed,isRunning,timestampMicros,sequence,accelState")
    fake.feed_rx(b"READY:v2")
    motor = ArduinoMotor(fake, Config(**valid_config_dict))
    await motor.start()
    await motor.close()
```
Critical: this test enforces **Pitfall 1** — preamble has 3 lines, not 1.

**Negative-path pattern** (from `tests/test_damping.py` lines 71–84):
```python
def test_zero_or_negative_time_constant_rejected() -> None:
    with pytest.raises(ValueError, match="time_constant_sec"):
        CriticallyDampedFollower(time_constant_sec=0.0)
```
Apply:
```python
async def test_handshake_rejects_version_mismatch(
    valid_config_dict: dict[str, Any],
) -> None:
    fake = FakeSerialTransport()
    fake.feed_rx(b"READY:v3")
    motor = ArduinoMotor(fake, Config(**valid_config_dict))
    with pytest.raises(ProtocolVersionMismatchError, match="expected READY:v2"):
        await motor.start()

async def test_handshake_times_out_when_no_ready(
    valid_config_dict: dict[str, Any],
) -> None:
    cfg = Config(**{**valid_config_dict, "arduino_ready_timeout_sec": 0.05})
    fake = FakeSerialTransport()
    motor = ArduinoMotor(fake, cfg)
    with pytest.raises(HandshakeTimeoutError):
        await motor.start()
```

---

### `pastor_tracker/tests/test_arduino_motor_tx.py` (async TX assertion)

**Analog:** `tests/test_damping.py` (deterministic single-call assertion).

**Approach:** complete handshake (factor a `_started_motor()` helper into `tests/fixtures/arduino_traces.py` per RESEARCH.md `Wave 0 Gaps`), then call each `send_…` method, assert against `fake.captured_writes`. Verify:
1. `send_motor_angle(MotorCommand(target_angle_deg=12.345, timestamp_ns=1))` → `b"M:12.345\n"` exactly (3-decimal format per RESEARCH.md `_send_raw` sketch + Pitfall 5 `INPUT_BUFFER_SIZE = 48` length check).
2. `send_query()` → `b"Q\n"`.
3. `send_emergency_stop()` → `b"E\n"`.
4. Each of `S:`, `L:`, `R`, `H`, `X:0`, `X:1`, `D:` → expected byte sequence.
5. TX line > 47 chars → `ValueError` raised (Pitfall 5 buffer-size guard).
6. TX serialization under contention — kick off two concurrent `send_motor_angle` tasks, assert `captured_writes` shows both bytes-complete (no interleave) — exercises the `asyncio.Lock`.

---

### `pastor_tracker/tests/test_arduino_motor_heartbeat.py` (cadence test)

**Analog:** `tests/test_damping.py` (deterministic for-loop with horizon).

**Pattern:** Pattern A from RESEARCH.md lines 588–604 (preferred — short interval, real but tiny `asyncio.sleep`):
```python
async def test_heartbeat_emits_q_at_interval(
    valid_config_dict: dict[str, Any],
) -> None:
    cfg = Config(**{**valid_config_dict, "arduino_heartbeat_interval_ms": 50})
    fake = FakeSerialTransport()
    fake.feed_rx(b"SETTINGS: defaults (no valid EEPROM)")
    fake.feed_rx(b"FB_HEADER:…")
    fake.feed_rx(b"READY:v2")
    motor = ArduinoMotor(fake, cfg)
    await motor.start()
    await asyncio.sleep(0.260)   # 5 intervals at 50ms = 250ms; 10ms cushion
    await motor.close()
    q_writes = [w for w in fake.captured_writes if w == b"Q\n"]
    assert 4 <= len(q_writes) <= 6
```
**Constants:** RESEARCH.md uses `arduino_heartbeat_interval_ms=20` for the example, but `Config` validates `_HEARTBEAT_MIN_MS = 50` (`config.py:40`). Use 50 ms — the lowest legal value — to keep the test fast (≤ 300 ms total) without violating Config bounds.

---

### `pastor_tracker/tests/test_arduino_motor_recovery.py` (state machine)

**Analog:** `tests/test_damping.py` (multi-step state evolution, e.g. lines 50–68 horizon loop).

**Test cases (per IO-ARD-06):**
1. Mid-session `Ready` event → `_dispatch_paused = True`, `S:` then `L:` written, `Settings(structured)` ack received → `_dispatch_paused = False`, WARN log.
2. `S:` ack timeout → `WatchdogResetError`, halt.
3. `L:` ack timeout → `WatchdogResetError`, halt.
4. `M:` calls during recovery silently no-op (RESEARCH.md `_send_raw` sketch line 266).
5. Heartbeat continues during recovery (RESEARCH.md state machine `# heartbeat task continues`).
6. Discriminate structured `Settings` from textual `SettingsInfo` (Pitfall — RESEARCH.md `Watchdog Recovery` lines 504–506): feed `SettingsInfo("saved to EEPROM")` first, then `Settings(...)`; recovery must wait for the structured one.

---

### `pastor_tracker/tests/test_arduino_motor_error.py` (parametrized halt-paths)

**Analog:** `tests/test_types.py` (parametrized rejection — see `test_detection_rejects_inverted_bbox_x` family).

**Pattern:**
```python
@pytest.mark.parametrize("error_code,error_name", [
    (1, "EmptyCommand"), (2, "UnknownCommandType"), (3, "MoveMissingArgument"),
    (4, "DiagnosticMissingArgument"), (5, "SettingsMissingArgument"),
    (6, "DriverMissingArgument"), (7, "DriverInvalidArgument"),
    (8, "HomingFailed"), (9, "AngleOutOfBounds"),
    (10, "SettingsOutOfBounds"), (11, "HeartbeatTimeout"),
])
async def test_error_line_halts_dispatch(
    error_code: int,
    error_name: str,
    valid_config_dict: dict[str, Any],
) -> None:
    motor = await _started_motor(valid_config_dict)
    fake = motor._transport     # FakeSerialTransport
    fake.feed_rx(f"ERROR:{error_code} - {error_name}".encode("ascii"))
    # Drain queue; orchestrator must transition to FAULTED and refuse subsequent send_motor_angle.
    …
```
Plus a special-case for `ERROR:11`: assert log level is `ERROR` (not WARN) and includes `code_name="HeartbeatTimeout"` per RESEARCH.md `Logging & Errors` `error_received` event row.

---

### `pastor_tracker/tests/test_arduino_motor_replay.py` (golden trace)

**Analog:** No exact Phase 1 analog. Closest is `tests/test_logging.py` for end-to-end-with-capture style.

**Pattern (verbatim from RESEARCH.md lines 658–671):**
```python
from pastor_tracker.tests.fixtures.arduino_traces import ARDUINO_TRACE_GOLDEN

async def test_golden_trace_replay(
    valid_config_dict: dict[str, Any],
) -> None:
    fake = FakeSerialTransport()
    for line in ARDUINO_TRACE_GOLDEN.split(b"\n"):
        if line:
            fake.feed_rx(line)
    motor = ArduinoMotor(fake, Config(**valid_config_dict))
    await motor.start()
    # Drain expected events in order: Ready, FB×3, Error
    events: list[ProtocolEvent] = []
    async for ev in motor.events():
        events.append(ev)
        if isinstance(ev, Error):
            break
    await motor.close()
    assert any(isinstance(e, Feedback) for e in events)
    assert events[-1].code is ErrorCode.HEARTBEAT_TIMEOUT
    assert any(w == b"Q\n" for w in fake.captured_writes)   # heartbeat fired
```

---

### `pastor_tracker/tests/fixtures/arduino_traces.py` (canned bytes)

**Analog:** None — `tests/fixtures/__init__.py` is currently empty (just exists for namespace) and `tests/fixtures/_lint_canary.py` is excluded from ruff (`pyproject.toml:39`). This is a brand-new fixture module.

**Pattern (verbatim from RESEARCH.md lines 658–668):**
```python
"""Canned byte traces for Arduino I/O integration tests.

Drive these via FakeSerialTransport.feed_rx(). Lines are split on b'\\n'
and fed one at a time (without trailing \\n, per the parser contract).
"""
from __future__ import annotations

from typing import Final

ARDUINO_TRACE_GOLDEN: Final[bytes] = b"\n".join([
    b"SETTINGS: defaults (no valid EEPROM)",
    b"FB_HEADER:currentAngle,targetAngle,speed,isRunning,timestampMicros,sequence,accelState",
    b"READY:v2",
    b"FB:0.00,0.00,0.00,0,1000,0,0",
    b"FB:0.50,1.00,500.00,1,21000,1,1",
    b"FB:1.00,1.00,0.00,0,41000,2,0",
    b"ERROR:11 - PC heartbeat lost",
    b"",
])

ARDUINO_TRACE_BOOT_ONLY: Final[bytes] = b"\n".join([
    b"SETTINGS: defaults (no valid EEPROM)",
    b"FB_HEADER:currentAngle,targetAngle,speed,isRunning,timestampMicros,sequence,accelState",
    b"READY:v2",
    b"",
])

ARDUINO_TRACE_WATCHDOG_RESET: Final[bytes] = b"\n".join([
    # Initial handshake
    b"SETTINGS: loaded from EEPROM",
    b"FB_HEADER:currentAngle,targetAngle,speed,isRunning,timestampMicros,sequence,accelState",
    b"READY:v2",
    # Several FBs while running
    b"FB:0.00,0.00,0.00,0,1000,0,0",
    # Mid-session re-boot
    b"SETTINGS: loaded from EEPROM",
    b"FB_HEADER:currentAngle,targetAngle,speed,isRunning,timestampMicros,sequence,accelState",
    b"READY:v2",
    # Recovery acks (structured)
    b"SETTINGS:25000.00,12500.00,1.00,0.00,0.10",
    b"SETTINGS: saved to EEPROM",
    b"LIMITS:-90.00,90.00",
    b"SETTINGS: saved to EEPROM",
    b"",
])
```
The `tests/fixtures/__init__.py` (already exists per glob results) needs no change; this new module sits alongside.

---

### `pastor_tracker/pyproject.toml` (modify)

**Analog:** itself (extend the existing arrays).

**Action:** Add `pyserial>=3.5,<4.0` to `[project.dependencies]` (lines 7–12) and `pytest-cov>=5.0,<7.0` to `[dependency-groups].dev` (lines 14–23). The `[[tool.mypy.overrides]]` block at lines 79–81 already includes `serial.*` in the `ignore_missing_imports` list — **do not modify it**, the override is already correct (pyserial does not ship type stubs for all modules).

**Diff sketch:**
```toml
dependencies = [
    "pydantic>=2.13,<3.0",
    "pydantic-settings>=2.14,<3.0",
    "structlog>=24.4,<26.0",
    "numpy>=2.4,<3.0",
+   "pyserial>=3.5,<4.0",
]

[dependency-groups]
dev = [
    "ruff>=0.15,<0.16",
    …
+   "pytest-cov>=5.0,<7.0",
]
```

---

## Shared Patterns

### Frozen Pydantic v2 DTO base class
**Source:** `pastor_tracker/src/pastor_tracker/core/types.py` lines 87–90
**Apply to:** every event class in `arduino_protocol.py` (`Ready`, `Feedback`, `Settings`, `SettingsInfo`, `Limits`, `Driver`, `Reset`, `Stop`, `Diag`, `Error`, `FeedbackHeader`)
```python
class _Event(BaseModel):
    """Base for all parsed protocol events — frozen + extra-forbidden."""
    model_config = ConfigDict(frozen=True, extra="forbid")
```
Use `.model_copy(update={...})` for any DTO mutation (CLAUDE.md rule 9).

### Tiger-style fail-fast guard with informative message
**Source:** `pastor_tracker/src/pastor_tracker/core/damping.py` lines 47–51 + `core/geometry.py` lines 41–51 + `core/types.py` lines 62–84
**Apply to:**
- `parse_line` → `ProtocolParseError(f"unknown prefix: {text[:16]!r}")` and per-field decode errors.
- `discover_arduino_port` → `ArduinoPortNotFoundError("no Arduino USB device matched VID:PID set")`.
- `_send_raw` → `ValueError(f"TX line {len(line)} > {FIRMWARE_INPUT_BUFFER_USABLE}")`.
- handshake → `HandshakeTimeoutError`, `ProtocolVersionMismatchError(f"expected READY:v{expected}, got READY:v{event.version}")`.
- recovery → `WatchdogResetError`.
**Format invariant:** every error message includes both the violated bound AND the offending value, exactly as `core/damping.py:50` does.

### Module-level named constants (`_FOO: int = ...`) — no magic numbers
**Source:** `pastor_tracker/src/pastor_tracker/config.py` lines 31–75 + `core/damping.py` lines 21–24 + `core/types.py` lines 36–41
**Apply to:** all three new source files. Every wire-format / firmware constant cites `protocol.h` line in a trailing comment, exactly as `config.py:39` cites `PROMPT.md ## Arduino Protocol`. Tests live under `[tool.ruff.lint.per-file-ignores]` `tests/**/*.py = ["PLR2004"]` so they're free to use literals — but production code (`arduino_protocol.py`, `arduino_transport.py`, `arduino_motor.py`) is bound by the rule.

### structlog logger binding with `module=...`
**Source:** `pastor_tracker/src/pastor_tracker/__main__.py` line 16 + RESEARCH.md `Logging & Errors`
**Apply to:** `arduino_transport.py` (`module="arduino_transport"`) and `arduino_motor.py` (`module="arduino_motor"`)
```python
log = structlog.get_logger(module="arduino_motor")
log.info("handshake_started", port=port, baud=baud, expected_version=expected)
```
Event names follow RESEARCH.md `Logging & Errors` event-catalog table verbatim — `port_discovered`, `handshake_started`, `heartbeat_started`, `watchdog_reset_detected`, `error_received`, `feedback_seq_gap`, `rx_queue_full`, `link_lost`, `malformed_line`, etc.

### `from __future__ import annotations` at top of every `.py`
**Source:** every Phase 1 `.py` file (e.g. `core/damping.py:16`, `config.py:18`, `core/types.py:17`, `__main__.py:6`)
**Apply to:** all four new source files and all eight new test files. Required by `mypy --strict` + lazy-eval annotations + `from typing import …` patterns.

### `≤ 2-level conditional nesting + guard clauses`
**Source:** `core/damping.py:59–76`, `core/geometry.py:32–54`, `core/types.py:62–84`, `config.py:179–186`
**Apply to:** every function in Phase 2. The RESEARCH.md sketches already comply — copy them, don't simplify, don't elaborate. Any planned helper that grows past 2 levels needs to be split per CLAUDE.md rule 5 before the code is written, not after.

### Pytest test layout: docstring → constants → `@given/@parametrize` → asserts
**Source:** `tests/test_geometry.py:1–24`, `tests/test_damping.py:1–30`
**Apply to:** all eight new test files. Every test function carries a one-line docstring naming the requirement ID it covers (per `tests/test_damping.py:42–47`):
```python
def test_handshake_succeeds_with_full_preamble(…) -> None:
    """IO-ARD-02: 3-line boot preamble (SETTINGS / FB_HEADER / READY:v2) must succeed."""
```

### `valid_config_dict` fixture reuse
**Source:** `tests/conftest.py:7–41`
**Apply to:** every async test that needs a `Config` instance. Override single fields via `{**valid_config_dict, "arduino_heartbeat_interval_ms": 50}`. Do NOT introduce a Phase-2-specific fixture — extending the global one would break Phase 1 tests.

### Pure-core / dirty-edges layering inside `io/`
**Source:** CLAUDE.md rule 4 + RESEARCH.md `Architectural Responsibility Map` table
**Apply:** `arduino_protocol.py` is **pure** (no `serial`, `threading`, `asyncio`, `os`, `time` imports — only `pydantic`, `enum`, `typing`, stdlib `re`/`math` if needed). `arduino_transport.py` and `arduino_motor.py` are dirty edges. The split is enforced by import discipline; mypy and ruff cannot directly enforce it, so the planner must verify in plan-check.

---

## No Analog Found

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| `pastor_tracker/src/pastor_tracker/io/arduino_motor.py` (orchestrator concurrency) | service | event-driven | Phase 1 had zero asyncio + threading — no analog for `asyncio.Queue` + `loop.call_soon_threadsafe` + `loop.run_in_executor` bridge. **Use RESEARCH.md `Architecture & Concurrency` sketches verbatim** — they are the spec. |
| `pastor_tracker/tests/fixtures/arduino_traces.py` | fixture | n/a | First non-trivial fixture module in the project (`fixtures/_lint_canary.py` is a ruff-excluded canary). Use RESEARCH.md `Replay-test fixture` block as the seed. |

For both files: the planner should reference RESEARCH.md sections by line number rather than searching for non-existent codebase analogs.

---

## Metadata

**Analog search scope:**
- `pastor_tracker/src/pastor_tracker/**/*.py` (4 source files: `__main__.py`, `config.py`, `logging_config.py`, plus `core/{types,geometry,damping}.py`)
- `pastor_tracker/tests/**/*.py` (5 test files + `conftest.py` + `fixtures/__init__.py`)
- `pastor_tracker/pyproject.toml`
- `arduino/stepper_controller/include/protocol.h` (firmware contract — referenced for constants, no Python analog needed)

**Files scanned:** 14 in-tree Python sources + 1 firmware header + 1 build config + 2 phase-2 planning docs (CONTEXT.md, RESEARCH.md) + PROJECT.md + CLAUDE.md.

**Pattern extraction date:** 2026-05-03

**Phase 1 conventions confirmed in scope:**
- Frozen Pydantic v2 (`ConfigDict(frozen=True, extra="forbid")`) for all DTOs
- `dataclass(frozen=True, slots=True)` for non-validating internal state (e.g., `FollowerState`)
- `from __future__ import annotations` everywhere
- structlog JSON, no `print()`, `module=` keyword binding
- `pytest` `asyncio_mode="auto"` + `valid_config_dict` shared fixture
- Module-level `_FOO: type = value  # citation` constants (no magic numbers)
- Tiger-style guard clauses: bound check → informative `ValueError` → happy path
- ≤ 2-level conditional nesting (no spaghetti)
- Type hints everywhere; mypy `--strict disallow_any_explicit` clean

## PATTERN MAPPING COMPLETE

**Phase:** 2 — Arduino I/O
**Files classified:** 14
**Analogs found:** 13 / 14 (orchestrator concurrency machinery has no Phase 1 analog — falls back to RESEARCH.md sketches as spec)

### Coverage
- Files with exact analog: 7 (`io/__init__.py`, `arduino_protocol.py`, all 5 parser/types-style tests reusing `test_geometry.py` / `test_types.py` / `test_config.py` shapes)
- Files with role-match analog: 5 (3 async motor tests using `test_damping.py` deterministic-loop shape; `test_arduino_transport.py` reusing `test_config.py` monkeypatch shape; `pyproject.toml` self-modify)
- Files with partial analog only: 2 (`arduino_transport.py` blocking-I/O wrappers; `arduino_motor.py` concurrency machinery — both lean on RESEARCH.md verbatim sketches)
- Files with no analog: 2 (`arduino_motor.py` async/threading core; `tests/fixtures/arduino_traces.py` — both fall back to RESEARCH.md `Code Examples` / `Testing Strategy`)

### Key Patterns Identified
- Every protocol DTO inherits from a private `_Event(BaseModel)` base with `ConfigDict(frozen=True, extra="forbid")` — direct mirror of `core/types.py:_FrozenModel`.
- Tiger-style `ValueError`/`<Domain>Error` raises always include both the violated bound AND the offending value (`core/damping.py:50`, `core/geometry.py:42–45`, `core/types.py:65–67`); RESEARCH.md error class hierarchy already follows this format.
- All firmware constants mirrored at module top with `Final[…]` types and trailing `# protocol.h:NN` citations — exact analog of `config.py:39`'s `_FIRMWARE_PC_HEARTBEAT_TIMEOUT_MS` style.
- `parse_line(line: bytes) -> ProtocolEvent` is a module-level pure function with a flat prefix-dispatch ladder (no nested ifs) — directly modeled on `core/geometry.py:normalized_x_to_angle_deg`'s guard-clause shape.
- `arduino_protocol.py` stays pure (no `serial`, `threading`, `asyncio` imports) so its tests need no fakes — mirrors how `tests/test_geometry.py` and `tests/test_damping.py` need no fixtures or mocks.
- All async tests reuse `valid_config_dict` from `tests/conftest.py` rather than constructing Config-init dicts inline; no Phase-2-specific config fixture.
- `arduino_motor.py` orchestration sketches in RESEARCH.md `Architecture & Concurrency` (TX path lines 246–276, RX thread lines 282–312, heartbeat lines 331–340, handshake reader lines 426–455, recovery lines 482–501, shutdown lines 347–354) are the spec — copy verbatim, do not redesign.

### File Created
`D:\System\Documents\PastorTrackingSystem\.planning\phases\02-arduino-i-o\02-PATTERNS.md`

### Ready for Planning
Pattern mapping complete. Planner can now reference Phase 1 analog patterns and RESEARCH.md sketches by line number when writing PLAN.md files.
