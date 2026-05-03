# Phase 2: Arduino I/O - Research

**Researched:** 2026-05-03
**Domain:** async pyserial driver + ASCII protocol parser + watchdog-recovery state machine
**Confidence:** HIGH (firmware source authoritative; pyserial 3.5 + pytest-asyncio 1.3.0 verified on dev box; current Uno R3 enumerates as `2341:0043` on COM6)

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Locked by PROJECT.md / PROMPT.md / CLAUDE.md / REQUIREMENTS.md**
- 115 200 baud, protocol v2 only — version mismatch = abort
- Threading for blocking serial RX only (PROJECT.md); asyncio for TX/lifecycle
- VID:PID auto-detect mandatory; fixed COM port hardcoding forbidden (PROJECT.md)
- Boot handshake `READY:v2` with `arduino_ready_timeout_sec` ceiling; abort on mismatch
- 200 ms PC heartbeat (vs firmware 1000 ms watchdog); AVR `WDTO_500MS` hardware watchdog out of scope (firmware-side)
- Re-receipt of `READY:v2` mid-session = MCU reset → PC must re-issue settings + limits
- `ERROR:` halts tracking, surfaces to UI, requires manual reset; no auto-recover
- Tiger-style fail-fast: validation errors raised at startup, no silent fallback
- `structlog` JSON logging only; no `print()`; bare `except` forbidden
- TEST-04 = canned-line replay + heartbeat verification (no hardware-in-CI)
- All public functions annotated, no `Any`, no magic numbers (everything tunable via `Config`)
- ≤ 2-level conditional nesting; guard clauses + early returns
- Conventional Commits, one logical change per commit

**Async/Threading Architecture**
- pyserial wrapped via dedicated RX thread that pushes typed parsed events into `asyncio.Queue`; no third-party async-serial dep
- TX path = `asyncio.Lock` around blocking `serial.Serial.write()`; single writer; coalesces command-dispatcher and heartbeat producers
- Parser runs IN the RX thread before queueing — keeps raw bytes off the asyncio hot path; queue carries typed events (`Feedback`, `Ready`, `Settings`, `Limits`, `Driver`, `Reset`, `Stop`, `Diag`, `Error`)
- RX queue bounded N=256 with drop-oldest semantics + WARN log on drop

**Disconnect & Port Discovery**
- Mid-session USB drop → surface `MotorLinkLost`-style ERROR, halt tracking, require manual reconnect
- Multiple VID:PID matches at boot → pick first hit, log INFO listing all matches and chosen path
- Manual override — `config.arduino_port` set AND device present at that path → use it; otherwise auto-detect
- Port discovery cadence — at boot only (one shot); re-discovery is operator-driven

**Watchdog Reset Recovery**
- Re-issued settings/limits derived from `Config` (PC authoritative)
- Recovery order on mid-session `READY:v2`: settings → limits → resume `M:` dispatch
- Ack model — wait for echoed `SETTINGS:` and `LIMITS:` parsed lines with timeout (`arduino_ready_timeout_sec` reused); ERROR if absent
- Pause `M:` dispatch until both acks received; WARN log on resume; heartbeat continues

**Test Strategy (TEST-04)**
- Fake serial transport — custom in-memory bidirectional fake + DI via a `SerialTransport`-style protocol
- Real-device smoke test deferred to Phase 8 QA-04
- Heartbeat timing test uses asyncio event-loop time with short test interval (e.g., 20 ms) + monotonic-clock virtual stepping; no `freezegun`, no real wall-clock sleeps
- Coverage target — ≥ 90% line coverage on `arduino_motor.py`; 100% on parser branches

### Claude's Discretion
- Internal type names (`Feedback`, `Ready`, `Settings`, …) and union-type definition style
- File granularity inside the I/O module (single `arduino_motor.py` vs split parser/transport submodules)
- Logging key names (kept consistent with structlog conventions established in Phase 1)
- Error class hierarchy (single `ArduinoError` vs typed subclasses)

### Deferred Ideas (OUT OF SCOPE)
- Continuous USB-port-event monitoring (WMI / udev) for hot-plug auto-reconnect
- Settings/limits round-trip verification by reading firmware EEPROM via `SETTINGS:`/`LIMITS:` query
- Real-device pytest fixture (`pytest --hardware` marker) — Phase 8 QA-04
- `aioserial` adoption — deferred unless dedicated-thread approach proves unworkable
- Live reconfig of motor settings without a restart
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| IO-ARD-01 | VID:PID auto-detect over `serial.tools.list_ports.comports()` matching genuine Uno R3 (`2341:0043`), R4 (`2341:0069`), CH340 (`1A86:7523`), FTDI (`0403:6001`) | Discovery & Boot — verified `pyserial 3.5` enumeration on dev box; ListPortInfo `.vid`/`.pid` are ints, formatter snippet provided |
| IO-ARD-02 | Boot handshake — read up to `arduino_ready_timeout_sec`, expect `READY:v2`, abort on version mismatch | Discovery & Boot — exact byte preamble cited from `main.cpp:419–422`; abort path returns `ProtocolVersionMismatchError` |
| IO-ARD-03 | Async TX wrapper for `M:` `S:` `L:` `R` `Q` `E` `H` `X:` `D:` commands; rate-limited dispatcher | Architecture & Concurrency — `asyncio.Lock` + `loop.run_in_executor` blocking `write()` sketch; rate-limiting belongs in Phase 5 dispatcher (CTRL-04), Phase 2 exposes raw typed `send_*` methods only |
| IO-ARD-04 | Threaded RX with parser for all 9 RX line types; `seq` gap > 5 → WARN | Protocol Reference truth table; Architecture & Concurrency RX thread sketch; parser branch coverage spec in Testing Strategy |
| IO-ARD-05 | 200 ms heartbeat task while tracking — sends `Q` (or other) so firmware never hits 1000 ms PC-heartbeat timeout | Architecture & Concurrency heartbeat-task sketch; firmware constant `HEARTBEAT_TIMEOUT_MILLIS = 1000` (`protocol.h:33`); 200 ms gives 5× safety margin |
| IO-ARD-06 | Watchdog-reset recovery — re-receipt of `READY:v2` mid-session = MCU reset → re-issue settings + limits + WARN log | Watchdog Recovery state machine; ack model uses echoed `SETTINGS:` + `LIMITS:` lines (handler emits both per `main.cpp:235–246` and `:273–277`) |
| IO-ARD-07 | `ERROR:` from firmware halts tracking, surfaces to UI, requires manual reset; `ERROR:11` (heartbeat lost) logs ERROR | Protocol Reference error-code table 0–11; `ERROR:11` = `ErrorCode::HeartbeatTimeout` (`protocol.h:79`); halt path identical for all codes |
| TEST-04 | Integration test for parser using fake-serial replay of canned `FB:`/`READY:`/`ERROR:` + heartbeat verification | Testing Strategy — `SerialTransport` Protocol DI sketch + virtual-clock heartbeat test pattern; coverage target ≥ 90% line, 100% parser branches |
</phase_requirements>

## Summary

The shipped firmware (`arduino/stepper_controller/src/main.cpp` + `include/protocol.h`) is the single source of truth and is **read-only** for this phase. Phase 2 is a Python-only build that mirrors firmware constants into a Python module, wraps `pyserial` (3.5, already installed on the dev box) in a one-RX-thread / one-asyncio-Lock-on-TX shape, parses 9 RX line types into a closed union of frozen Pydantic DTOs, runs a 200 ms heartbeat task, and recovers from MCU resets. There are no exotic library choices; all complexity is in the asyncio↔thread bridge and the parser.

Two facts from primary sources sharpen the plan:

1. **Boot preamble is two lines, not one.** `main.cpp:417–422` emits `SETTINGS: loaded from EEPROM` (or `SETTINGS: defaults (no valid EEPROM)`) BEFORE `FB_HEADER:...` BEFORE `READY:v2`. The handshake reader must scan a stream and pick `READY:v<N>` out of preceding lines, not assume `READY` is the first byte. This is the single biggest correctness trap.
2. **Mid-session reset emits the SAME boot preamble.** `wdt_reset()` followed by AVR watchdog firing → MCU reboots → `setup()` runs again → same `FB_HEADER` + `READY:v2` reach the host. Detection signal is **re-receipt of `READY:v2` after `STATE = Running`**, not `RESET:` (that line is a `R` command ack only — `main.cpp:284`).

**Primary recommendation:** Split the module into three files inside `pastor_tracker/io/` — `arduino_protocol.py` (constants + parser + DTO union, pure), `arduino_transport.py` (`SerialTransport` Protocol + real `pyserial` impl + fake), `arduino_motor.py` (orchestrator: handshake, RX thread driver, heartbeat task, watchdog recovery). The pure-core/dirty-edges split is what enables 100% parser branch coverage without a serial port.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Protocol line parsing (`FB:`, `READY:`, `ERROR:`, …) | Pure core (`arduino_protocol.py`) | — | Side-effect-free string→DTO transform; CLAUDE.md "pure core" rule; trivially unit-testable |
| VID:PID enumeration | Dirty edge (`arduino_transport.py`) | — | OS-level call (`serial.tools.list_ports.comports()`); hidden behind a `discover_port()` function so tests can monkeypatch |
| Blocking `serial.Serial.write/read_until` | Dirty edge (`arduino_transport.py`) | — | Real I/O lives behind `SerialTransport` Protocol; fake transport for tests |
| RX thread + asyncio queue bridge | Orchestrator (`arduino_motor.py`) | — | The only place threading touches asyncio; isolates the tricky bit |
| Heartbeat task lifecycle | Orchestrator (`arduino_motor.py`) | — | Owns `asyncio.Task`, the TX `asyncio.Lock`, and the cancellation contract |
| Watchdog-reset state machine | Orchestrator (`arduino_motor.py`) | — | Reads `Config` to re-issue settings/limits; a dirty-edge concern |
| TX rate-limiting (Δ > 0.2°, ≥ 50 ms) | **Phase 5** (`command_dispatcher.py`) | — | CTRL-04 lives in Phase 5; Phase 2 exposes raw typed `send_*(...)` only |

## Protocol Reference

All constants below quoted from `arduino/stepper_controller/include/protocol.h` and behavior from `arduino/stepper_controller/src/main.cpp`. Mirror these in `pastor_tracker/io/arduino_protocol.py` as a single source.

### Wire format

- **Encoding:** ASCII, newline-terminated. Both `\n` and `\r` are accepted as terminators by firmware (`main.cpp:358`); host SHOULD send `\n` only.
- **Baud:** 115 200 (`main.cpp:406`).
- **Buffer:** firmware input buffer = `INPUT_BUFFER_SIZE = 48` bytes including null (`protocol.h:39`). Commands MUST be ≤ 47 chars before `\n`.

### TX command catalog (host → firmware)

| Tag | Format | Args | Handler | Notes |
|-----|--------|------|---------|-------|
| `M:` | `M:<deg>\n` | float degrees | `handle_move` | Range-checked against `g_angle_min/max_degrees`; `ERROR:9` (`AngleOutOfBounds`) on violation |
| `D:` | `D:<steps>\n` | int32 steps | `handle_diagnostic_move` | Diagnostic only; bypasses angle limits — DO NOT use in tracking pipeline |
| `S:` | `S:<spd>,<acc>,<p>,<i>,<d>\n` | 5 floats | `handle_settings` | Clamped to firmware ranges (`protocol.h:27–30`); echoes `SETTINGS:<spd>,<acc>,<p>,<i>,<d>` then `SETTINGS: saved to EEPROM` |
| `L:` | `L:<min>,<max>\n` | 2 floats | `handle_limits` | `ERROR:10` (`SettingsOutOfBounds`) if `min >= max`; echoes `LIMITS:<min>,<max>` then `SETTINGS: saved to EEPROM` |
| `R` | `R\n` | none | `handle_reset` | Sets current position to 0; echoes `RESET:OK` |
| `Q` | `Q\n` | none | `handle_query` | Forces immediate `FB:` emission; ideal heartbeat payload |
| `E` | `E\n` | none | `handle_emergency_stop` | Decelerates, disables driver, echoes `STOP:OK`; transitions firmware to `MotorState::Stopped` |
| `H` | `H\n` | none | `handle_home` | Moves to 0°; `ERROR:9` if 0 outside limits |
| `X:` | `X:0\n` or `X:1\n` | int 0/1 | `handle_driver` | Echoes `DRIVER:DISABLED` / `DRIVER:ENABLED`; `ERROR:7` (`DriverInvalidArgument`) on anything else; `ERROR:6` (`DriverMissingArgument`) on empty |

**TX safety:** any command resets `g_last_command_millis` (`main.cpp:332`) — i.e. **any** TX line satisfies the firmware heartbeat watchdog, not just `Q`. Heartbeat task can therefore use any benign TX; `Q` is preferred because its echoed `FB:` doubles as a liveness probe (per CONTEXT.md `### Specifics`).

### RX line catalog (firmware → host) — parser truth table

Newline-stripped. All numeric fields decimal ASCII. `seq` is uint32 (`main.cpp:160`).

| Tag prefix | Format | Field types | Source line in `main.cpp` | DTO |
|------------|--------|-------------|---------------------------|-----|
| `READY:v` | `READY:v<N>` | uint8 N | `:420–421` (boot AND post-WDT reset) | `Ready(version: int)` |
| `FB:` | `FB:<curAng>,<tgtAng>,<speed>,<isRun>,<microsTs>,<seq>,<accelState>` | float, float, float, int(0/1), uint32, uint32, uint8(0–3) | `:149–162` | `Feedback(...)` |
| `FB_HEADER:` | `FB_HEADER:currentAngle,targetAngle,speed,isRunning,timestampMicros,sequence,accelState` | literal header | `:419` (boot AND post-WDT reset) | `FeedbackHeader()` — non-fatal informational |
| `SETTINGS:` | Three forms — see below | varies | `:98`, `:110`, `:123`, `:235–244` | `Settings(...)` (parsed values) **or** `SettingsInfo(message: str)` (textual) |
| `LIMITS:` | `LIMITS:<min>,<max>` | float, float | `:273–276` | `Limits(min_deg, max_deg)` |
| `DRIVER:` | `DRIVER:ENABLED` or `DRIVER:DISABLED` | literal | `:318`, `:323` | `Driver(enabled: bool)` |
| `RESET:` | `RESET:OK` | literal | `:284` | `Reset()` — ack of `R` command, NOT a watchdog reset signal |
| `STOP:` | `STOP:OK` | literal | `:295` | `Stop()` — ack of `E` |
| `DIAG:` | `DIAG: moving <N> steps` | int32 N | `:196–198` | `Diag(steps: int)` |
| `ERROR:` | `ERROR:<code> - <message>` | uint8 code, free-text msg | `:63–66` | `Error(code: ErrorCode, message: str)` |

**Three forms of `SETTINGS:`** — parser must handle all three:
1. `SETTINGS: defaults (no valid EEPROM)` — boot, no EEPROM magic (`main.cpp:98`)
2. `SETTINGS: loaded from EEPROM` — boot, valid EEPROM (`main.cpp:110`)
3. `SETTINGS:<spd>,<acc>,<p>,<i>,<d>` — `S:` command echo (`main.cpp:235–244`)
4. `SETTINGS: saved to EEPROM` — emitted AFTER both `S:` and `L:` echoes (`main.cpp:123`)

Forms 1, 2, 4 are textual and parse to `SettingsInfo`. Form 3 is the structured ack and parses to `Settings(max_speed, max_accel, pid_p, pid_i, pid_d)`. **The watchdog-recovery ack-watcher MUST require form 3, not forms 1/2/4.**

### Error code table — `ErrorCode` enum (`protocol.h:67–80`)

| Code | Name | Meaning | Trigger |
|------|------|---------|---------|
| 0 | None | sentinel; never emitted as `ERROR:0` | — |
| 1 | EmptyCommand | command line was empty after strip | newline with no content |
| 2 | UnknownCommandType | first char not in `MDSLRQEHX` | typo in TX |
| 3 | MoveMissingArgument | `M:` with no degree value | malformed TX |
| 4 | DiagnosticMissingArgument | `D:` with no step count | malformed TX |
| 5 | SettingsMissingArgument | `S:` or `L:` missing args | malformed TX |
| 6 | DriverMissingArgument | `X:` with no 0/1 arg | malformed TX |
| 7 | DriverInvalidArgument | `X:` arg not 0 or 1 | malformed TX |
| 8 | HomingFailed | reserved for future use | (firmware code path not present in v2) |
| 9 | AngleOutOfBounds | `M:` or `H:` target outside `g_angle_min/max_degrees` | exceeds limits |
| 10 | SettingsOutOfBounds | `L:` `min >= max` | malformed limits |
| 11 | HeartbeatTimeout | PC heartbeat lost > 1000 ms while `Moving`/`Homing` | PC stall — `main.cpp:371–378` |

**Codes 1–7, 10:** indicate host bug; log ERROR, halt, surface to UI per IO-ARD-07.
**Code 9:** indicates Config drift vs firmware EEPROM; log ERROR, halt, surface (operator must verify limits).
**Code 11:** indicates PC failed its own heartbeat; log ERROR with extra hint; halt path identical (CONTEXT.md ### Specifics).
**Code 0:** never emitted as `ERROR:0` (firmware uses 0 as the "no error" sentinel only); parser MAY treat it as malformed if seen.
**Code 8:** reserved; never emitted in shipped v2 firmware; parser MUST still accept it (forward-compat).

### Parser branch matrix (target: 100% coverage)

```
  prefix dispatch (9 cases) × [well-formed | malformed-fields | out-of-range value]
+ SETTINGS: 4 sub-forms × [structured | textual]
+ ERROR: codes 0..11 + unknown-int + malformed-tail
+ FB: float-precision edge cases (negative angles, scientific notation refusal)
+ unknown-prefix line → MalformedLineError (does NOT crash; logs WARN, drops)
```

The "unknown-prefix line" path is critical — `main.cpp:419` emits `FB_HEADER:` which a naive parser would reject. Treat unknown prefixes as warn-and-drop, not fatal.

## Architecture & Concurrency

### File layout (recommended split inside `pastor_tracker/io/`)

```
src/pastor_tracker/io/
├── arduino_protocol.py    # Constants mirror + parse_line() + DTO union (PURE)
├── arduino_transport.py   # SerialTransport Protocol + PySerialTransport + FakeSerialTransport
└── arduino_motor.py       # ArduinoMotor orchestrator: handshake, RX thread, heartbeat, recovery
```

Rationale: SRP (rule 2) + pure-core/dirty-edges (rule 4). `arduino_protocol.py` has zero I/O imports, so the parser is unit-testable with no fixtures and no fake. Tests for the orchestrator inject `FakeSerialTransport`. Splitting also keeps each file ≤ ~300 LOC, which matches Phase 1's pattern (`config.py` 205 LOC, `damping.py`/`geometry.py` similar).

### Concurrency contract (one diagram)

```
┌──────────────────────────────────────────────────────────────────────┐
│                       Asyncio event loop (main thread)               │
│                                                                       │
│  HeartbeatTask ──┐                                                    │
│                   ├─► TX_LOCK ─► transport.write(bytes) (blocking)    │
│  send_motor()  ──┤    asyncio.Lock         │                          │
│  send_emergency()┘                         │                          │
│                                            ▼                          │
│                              ┌──── pyserial Serial ────┐              │
│                              │   115200 8N1, /dev/...  │              │
│                              └──────────┬──────────────┘              │
│                                         │ blocking I/O                │
│  events_consumer  ◄──── RX_QUEUE ◄──┐   │                             │
│  (async for ev:)   asyncio.Queue(256)│   │                             │
│                              ▲       │   │                             │
│                              │       │   ▼                             │
└──────────────────────────────┼───────┼───┼─────────────────────────────┘
                               │       │   │
                  loop.call_soon_threadsafe ── RX thread
                               │           ┌───────────────┐
                               └────────── │ read_until(b'\n')
                                           │ parse_line()
                                           │ enqueue typed DTO
                                           └───────────────┘
                                              (daemon)
```

Key invariants:
1. **Single writer** — only the asyncio loop calls `transport.write()`, always behind `TX_LOCK`. Heartbeat and command sender share the lock.
2. **Single reader** — only the RX thread calls `transport.read_until()`. The thread parses INTO a typed DTO, then bridges to the asyncio queue via `loop.call_soon_threadsafe(queue.put_nowait, event)`.
3. **No shared mutable state** — RX thread holds zero asyncio objects; main loop holds zero `serial.Serial` references after handover.
4. **Bounded queue** — `asyncio.Queue(maxsize=256)`. Full-queue path: pop oldest, log `rx_queue_full` WARN, enqueue new (drop-oldest semantics per CONTEXT.md).

### TX path sketch

```python
# arduino_motor.py — async TX wrapper
import asyncio
from typing import Final

_TX_NEWLINE: Final[bytes] = b"\n"

class ArduinoMotor:
    def __init__(self, transport: SerialTransport, config: Config) -> None:
        self._transport = transport
        self._config = config
        self._tx_lock = asyncio.Lock()
        self._dispatch_paused = False  # set True during watchdog recovery

    async def _send_raw(self, line: bytes) -> None:
        # Tiger-style: line MUST already include payload, we add newline.
        # No magic numbers — INPUT_BUFFER_SIZE - 1 = 47 mirrored from protocol.h.
        if len(line) + 1 > _FIRMWARE_INPUT_BUFFER_USABLE:
            raise ValueError(f"TX line {len(line)} > {_FIRMWARE_INPUT_BUFFER_USABLE}")
        loop = asyncio.get_running_loop()
        async with self._tx_lock:
            # Run blocking write in default executor — does NOT block the loop.
            await loop.run_in_executor(None, self._transport.write, line + _TX_NEWLINE)

    async def send_motor_angle(self, command: MotorCommand) -> None:
        if self._dispatch_paused:
            return  # WARN logged elsewhere; pause is a recovery signal
        payload = f"M:{command.target_angle_deg:.3f}".encode("ascii")
        await self._send_raw(payload)

    async def send_query(self) -> None:
        await self._send_raw(b"Q")

    async def send_emergency_stop(self) -> None:
        # E-stop bypasses dispatch_paused — safety always wins.
        await self._send_raw(b"E")
```

`run_in_executor` with `None` uses the default `ThreadPoolExecutor`. This is the textbook idiom for "I have a blocking C call and an asyncio loop" and is preferred over wrapping `write()` in a third-party async-serial lib (CONTEXT.md explicitly bans that). [VERIFIED: pyserial 3.5 `Serial.write()` is blocking and threadsafe — see Pitfall 3.]

### RX thread sketch

```python
# arduino_transport.py — RX thread driver
import threading
from typing import Callable

class _RxThread(threading.Thread):
    def __init__(
        self,
        transport: SerialTransport,
        on_event: Callable[[ProtocolEvent], None],
        on_malformed: Callable[[bytes, Exception], None],
        stop_event: threading.Event,
    ) -> None:
        super().__init__(name="arduino-rx", daemon=True)
        self._transport = transport
        self._on_event = on_event
        self._on_malformed = on_malformed
        self._stop = stop_event

    def run(self) -> None:
        while not self._stop.is_set():
            line = self._transport.read_line(timeout=_RX_READ_TIMEOUT_SEC)
            if line is None:
                continue  # timeout — loop and re-check stop
            try:
                event = parse_line(line)
            except ProtocolParseError as exc:
                self._on_malformed(line, exc)
                continue
            self._on_event(event)
```

The orchestrator wires `on_event` to `lambda ev: loop.call_soon_threadsafe(self._enqueue, ev)`. `_enqueue` runs on the loop and applies drop-oldest:

```python
def _enqueue(self, event: ProtocolEvent) -> None:
    queue = self._rx_queue
    if queue.full():
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            pass  # race; harmless
        self._logger.warning("rx_queue_full", dropped_event_type=type(event).__name__)
    queue.put_nowait(event)
```

### Heartbeat task sketch

```python
async def _heartbeat_loop(self) -> None:
    interval_sec = self._config.arduino_heartbeat_interval_ms / _MS_PER_SEC
    try:
        while True:
            await self.send_query()  # any TX resets firmware watchdog
            await asyncio.sleep(interval_sec)
    except asyncio.CancelledError:
        self._logger.info("heartbeat_stopped")
        raise
```

200 ms interval against firmware `HEARTBEAT_TIMEOUT_MILLIS = 1000` (`protocol.h:33`) gives 5× safety margin. Even if a single TX is delayed by GC / executor saturation, four more heartbeats are scheduled before the firmware fires `ERROR:11`.

### Clean shutdown

```python
async def close(self) -> None:
    self._heartbeat_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await self._heartbeat_task
    self._stop_event.set()                # signal RX thread
    await asyncio.to_thread(self._rx_thread.join, _RX_JOIN_TIMEOUT_SEC)
    self._transport.close()               # safe: RX thread has exited
```

Order matters: cancel heartbeat → set stop event → wait for RX thread to exit its read loop → close port. Closing the port while the RX thread is mid-`read_until` raises `serial.SerialException` on Linux/Windows; the daemon-thread fallback prevents deadlock at process exit but is not clean.

## Discovery & Boot

### VID:PID enumeration on Windows 10 — verified

Tested on the dev box (2026-05-03) with `pyserial 3.5`:
```
'COM6' vid=0x2341 pid=0x43 desc=USB Serial Device (COM6)  ← live Uno R3
'COM1' vid=None  pid=None desc=Communications Port (COM1)
```

`ListPortInfo.vid` and `.pid` are `int | None`. None for legacy serial ports. Compare as ints (not strings, not lowercased hex).

```python
# arduino_transport.py
import structlog
from serial.tools.list_ports import comports

# Mirror PROJECT.md ## Context — VID:PID pairs that MUST match.
SUPPORTED_VID_PIDS: Final[frozenset[tuple[int, int]]] = frozenset({
    (0x2341, 0x0043),  # Arduino Uno R3
    (0x2341, 0x0069),  # Arduino Uno R4
    (0x1A86, 0x7523),  # CH340 clone
    (0x0403, 0x6001),  # FTDI clone
})

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

Note: Arduino Uno R4 Minima/WiFi PIDs vary (`0069` for Minima per Arduino reference, but Uno R4 family has multiple PIDs). The four pairs in CONTEXT.md / PROJECT.md are the contract; if a future R4 board ships with a different PID, that's a config-extension story, not a Phase 2 bug.

### Boot handshake — exact byte sequence

From `main.cpp:417–422`, in order, after USB port opens:

```
SETTINGS: defaults (no valid EEPROM)\n        ← OR "SETTINGS: loaded from EEPROM\n"
FB_HEADER:currentAngle,targetAngle,speed,isRunning,timestampMicros,sequence,accelState\n
READY:v2\n
```

(Three lines, possibly with leading garbage on Windows after DTR-triggered MCU reset — defensive parser must skip until `READY:v\d+` matches.)

### Handshake reader pseudocode

```python
async def _wait_for_ready(self) -> None:
    deadline = asyncio.get_running_loop().time() + self._config.arduino_ready_timeout_sec
    expected = self._config.arduino_protocol_version
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise HandshakeTimeoutError(
                f"no READY:v{expected} within {self._config.arduino_ready_timeout_sec}s"
            )
        # read_line in executor with the remaining timeout
        line = await asyncio.wait_for(
            asyncio.to_thread(self._transport.read_line, remaining),
            timeout=remaining,
        )
        if line is None:
            continue
        event = parse_line(line)
        if isinstance(event, FeedbackHeader):
            continue  # benign preamble
        if isinstance(event, SettingsInfo):
            continue  # benign preamble
        if isinstance(event, Ready):
            if event.version != expected:
                raise ProtocolVersionMismatchError(
                    f"expected READY:v{expected}, got READY:v{event.version}"
                )
            return  # SUCCESS
        # Anything else during handshake is suspicious — log WARN, keep reading
        self._logger.warning("handshake_unexpected_line", event=type(event).__name__)
```

Run the handshake reader BEFORE the RX thread starts. Once `READY:v2` is observed, hand the transport to the RX thread.

**Failure-mode log line (per CONTEXT.md ### Specifics):**
```python
log.error(
    "handshake_version_mismatch",
    expected=f"READY:v{config.arduino_protocol_version}",
    received=raw_line,
)
```

## Watchdog Recovery

### Detection signal — re-receipt of `READY:v2` mid-session

After the initial handshake completes, the orchestrator transitions to `Running`. Any subsequent `Ready` event in the queue indicates the AVR `WDTO_500MS` hardware watchdog (`main.cpp:423`, `:427`) fired or the host PC heartbeat watchdog tripped at the firmware (`main.cpp:371–378` → also self-resets via WDT after `report_error` because `g_motor_state = Faulted` and the loop's `wdt_reset()` keeps running, BUT `report_error` does NOT halt the WDT — re-verify this in main.cpp).

**Re-verification of WDT path:** `main.cpp:60–67` `report_error` only sets state and prints; it does NOT call `wdt_disable()`. The main `loop()` (`main.cpp:426–432`) calls `wdt_reset()` first thing, every iteration. Therefore `ERROR:11` does NOT itself reset the MCU. The MCU only reboots if `loop()` blocks > 500 ms, which the firmware design avoids. So in practice the recovery path is triggered by **hard hangs (cosmic ray, brownout, reset button)**, not by routine errors. ✅ Fine — the path still must exist; it's just rarer than I'd assumed.

### Settings/limits ARE wiped on reboot — but only RAM ones

`load_settings_from_eeprom` (`main.cpp:94–111`) runs on every boot. If EEPROM has a valid `EEPROM_MAGIC_VALUE = 0x50545332` (`protocol.h:91`), the previous settings are restored. So in practice settings/limits **survive** a watchdog reset. **But the contract per CONTEXT.md is "PC is authoritative" — re-issue from `Config` regardless.** This is correct: it makes the recovery path deterministic and immune to EEPROM-corruption silent failures.

### Recovery state machine

```
state = Running
on Ready event:
    log.warning("watchdog_reset_detected", action="re-issuing settings+limits")
    state = Recovering
    self._dispatch_paused = True            # block M: emission
    # heartbeat task continues — keeps firmware happy

    await self.send_settings(Config-derived 5-tuple)
    await self._wait_for_event(Settings, timeout=arduino_ready_timeout_sec)
        # NB: must be the structured form (5 numbers), not "saved to EEPROM" text
        # If timeout → raise WatchdogResetError, halt, surface to UI

    await self.send_limits(Config-derived (min, max))
    await self._wait_for_event(Limits, timeout=arduino_ready_timeout_sec)

    self._dispatch_paused = False
    log.warning("watchdog_recovery_complete")
    state = Running
```

### Ack-watcher details

- Wait for the **structured** `Settings(5-tuple)` — discriminate against `SettingsInfo("saved to EEPROM")`. The firmware emits the structured form FIRST then the textual one, so a single-instance `wait_for_event(Settings)` will hit the structured form.
- After `L:` is dispatched, firmware also emits `SETTINGS: saved to EEPROM` (`main.cpp:277`). Drain it from the queue but ignore.

## Testing Strategy

### `SerialTransport` Protocol — minimum DI surface

```python
# arduino_transport.py
from typing import Protocol, runtime_checkable

@runtime_checkable
class SerialTransport(Protocol):
    def write(self, data: bytes) -> int: ...
    def read_line(self, timeout: float) -> bytes | None: ...   # None on timeout
    def close(self) -> None: ...
```

`read_line` returns the line WITHOUT trailing `\n` to keep parser tests clean.

### `PySerialTransport` (production)

```python
class PySerialTransport:
    def __init__(self, port: str, baud: int) -> None:
        self._serial = serial.Serial(port=port, baudrate=baud, timeout=0.0)

    def write(self, data: bytes) -> int:
        return self._serial.write(data)

    def read_line(self, timeout: float) -> bytes | None:
        # pyserial Serial.timeout is the per-read timeout. Patch it for this call.
        self._serial.timeout = timeout
        line = self._serial.read_until(b"\n")
        if not line.endswith(b"\n"):
            return None       # timed out
        return line.rstrip(b"\r\n")

    def close(self) -> None:
        self._serial.close()
```

### `FakeSerialTransport` (tests)

```python
class FakeSerialTransport:
    """Bidirectional in-memory fake. Tests feed_rx() to enqueue inbound lines
    (without trailing \\n) and assert against captured_writes."""

    def __init__(self) -> None:
        self.captured_writes: list[bytes] = []
        self._rx_lines: collections.deque[bytes] = collections.deque()
        self._rx_event = threading.Event()
        self._closed = False

    def feed_rx(self, line: bytes) -> None:
        self._rx_lines.append(line)
        self._rx_event.set()

    def write(self, data: bytes) -> int:
        if self._closed:
            raise SerialClosedError
        self.captured_writes.append(data)
        return len(data)

    def read_line(self, timeout: float) -> bytes | None:
        if self._rx_lines:
            return self._rx_lines.popleft()
        if self._rx_event.wait(timeout):
            self._rx_event.clear()
            if self._rx_lines:
                return self._rx_lines.popleft()
        return None

    def close(self) -> None:
        self._closed = True
        self._rx_event.set()  # unblock any waiter
```

### Heartbeat timing test — virtual clock pattern (NO `freezegun`)

The constraint from CONTEXT.md: "asyncio event-loop time + virtual stepping; no `freezegun`, no real wall-clock sleeps". Two viable patterns:

**Pattern A — short interval, real but tiny sleeps (preferred for simplicity):**
```python
async def test_heartbeat_emits_q_at_interval(monkeypatch):
    config = make_config(arduino_heartbeat_interval_ms=20)
    fake = FakeSerialTransport()
    fake.feed_rx(b"SETTINGS: defaults (no valid EEPROM)")
    fake.feed_rx(b"FB_HEADER:currentAngle,...,accelState")
    fake.feed_rx(b"READY:v2")

    motor = ArduinoMotor(fake, config)
    await motor.start()
    await asyncio.sleep(0.105)  # 5 intervals at 20ms = 100ms; 5ms cushion
    await motor.close()

    q_writes = [w for w in fake.captured_writes if w == b"Q\n"]
    assert 4 <= len(q_writes) <= 6   # tolerate ±1 for scheduler jitter
```

**Pattern B — fully deterministic via custom event loop time (use only if Pattern A flakes):**
```python
class _VirtualTimeLoop(asyncio.SelectorEventLoop):
    def __init__(self) -> None:
        super().__init__()
        self._fake_now = 0.0
    def time(self) -> float:
        return self._fake_now
    def advance(self, delta: float) -> None:
        self._fake_now += delta
```
Run scheduled callbacks deterministically by calling `loop._run_once()` after `advance()`. This is fragile (depends on private API) — escalate to Pattern B only if Pattern A proves flaky in CI.

### Parser unit tests — branch coverage matrix

`tests/test_arduino_protocol.py` should be a parameterized table:

```python
@pytest.mark.parametrize("line,expected", [
    (b"READY:v2", Ready(version=2)),
    (b"READY:v3", Ready(version=3)),
    (b"FB:1.50,2.00,1234.5,1,123456,42,2", Feedback(...)),
    (b"FB:-90.00,-90.00,0.00,0,0,0,0", Feedback(current_angle_deg=-90.0, ...)),
    (b"SETTINGS: defaults (no valid EEPROM)", SettingsInfo("defaults (no valid EEPROM)")),
    (b"SETTINGS: loaded from EEPROM", SettingsInfo("loaded from EEPROM")),
    (b"SETTINGS: saved to EEPROM", SettingsInfo("saved to EEPROM")),
    (b"SETTINGS:25000.00,12500.00,1.00,0.00,0.10",
        Settings(25000.0, 12500.0, 1.0, 0.0, 0.1)),
    (b"LIMITS:-90.00,90.00", Limits(-90.0, 90.0)),
    (b"DRIVER:ENABLED",  Driver(enabled=True)),
    (b"DRIVER:DISABLED", Driver(enabled=False)),
    (b"RESET:OK", Reset()),
    (b"STOP:OK",  Stop()),
    (b"DIAG: moving 100 steps", Diag(steps=100)),
    (b"DIAG: moving -200 steps", Diag(steps=-200)),
    (b"ERROR:11 - PC heartbeat lost",
        Error(code=ErrorCode.HeartbeatTimeout, message="PC heartbeat lost")),
    (b"FB_HEADER:currentAngle,targetAngle,...", FeedbackHeader()),
    # Malformed paths:
    (b"FB:1.5,2.0",                   raises ProtocolParseError),
    (b"ERROR:99 - future code",       Error(code=99, message="future code")),  # forward-compat
    (b"ERROR: missing code",          raises ProtocolParseError),
    (b"GIBBERISH",                    raises ProtocolParseError),
    (b"",                             raises ProtocolParseError),
])
def test_parse_line(line, expected): ...
```

The point of the table is that every prefix branch is hit, every numeric-field count is exercised, and forward-compat (unknown error codes) is explicit.

### Replay-test fixture

```python
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
```

Drive into `FakeSerialTransport.feed_rx()` line by line; assert the orchestrator transitions Running → Faulted on `ERROR:11`, the queue contains the expected DTOs in order, and `captured_writes` shows ≥ 1 `Q\n` (heartbeat fired).

### Test inventory (Wave 0 gaps)

- [ ] `tests/test_arduino_protocol.py` — pure parser table tests (covers IO-ARD-04 parser branches; 100% branch coverage gate)
- [ ] `tests/test_arduino_transport.py` — `discover_arduino_port` matrix with monkeypatched `comports()` (covers IO-ARD-01)
- [ ] `tests/test_arduino_motor_handshake.py` — handshake success / version-mismatch / timeout (covers IO-ARD-02)
- [ ] `tests/test_arduino_motor_heartbeat.py` — heartbeat-cadence test using FakeSerialTransport (covers IO-ARD-05)
- [ ] `tests/test_arduino_motor_recovery.py` — mid-session `READY:v2` recovery + ack-timeout failure (covers IO-ARD-06)
- [ ] `tests/test_arduino_motor_error.py` — `ERROR:N` halts dispatch (covers IO-ARD-07; one parametrize case per code 1–11)
- [ ] `tests/test_arduino_motor_replay.py` — golden trace replay (covers TEST-04)
- [ ] `tests/fixtures/arduino_traces.py` — canned byte sequences

Existing `tests/conftest.py` already provides `valid_config_dict`; reuse it.

## Logging & Errors

### structlog event names (consistent with Phase 1 conventions)

Per Phase 1, structlog is configured at module entry; bindings include `module`. Phase 2 should bind:

```python
log = structlog.get_logger(module="arduino_motor")
```

### Event catalog

| Event name | Level | Bound keys | When |
|------------|-------|------------|------|
| `port_discovered` | INFO | `port`, `vid`, `pid` | Successful auto-detect |
| `port_manual_override` | INFO | `port` | `config.arduino_port` honored |
| `port_multiple_matches` | INFO | `chosen`, `all` | >1 VID:PID match; pick first |
| `handshake_started` | INFO | `port`, `baud`, `expected_version` | Before handshake reader |
| `handshake_complete` | INFO | `version`, `elapsed_ms` | `READY:v2` received |
| `handshake_unexpected_line` | WARN | `line` | Non-Ready event during handshake (skipped) |
| `handshake_version_mismatch` | ERROR | `expected`, `received` | Version mismatch — pre-abort |
| `handshake_timeout` | ERROR | `timeout_sec` | No `READY:v<N>` within window |
| `heartbeat_started` | INFO | `interval_ms` | Heartbeat task launched |
| `heartbeat_stopped` | INFO | — | Task cancelled (clean shutdown) |
| `watchdog_reset_detected` | WARN | — | Mid-session `READY:v2` |
| `watchdog_recovery_complete` | WARN | `elapsed_ms` | Settings + limits re-acked |
| `watchdog_recovery_failed` | ERROR | `stage` (settings/limits), `timeout_sec` | Ack timeout |
| `error_received` | ERROR | `code`, `code_name`, `message` | `ERROR:N` from firmware |
| `feedback_seq_gap` | WARN | `expected_seq`, `received_seq`, `gap` | `seq` jump > 5 |
| `rx_queue_full` | WARN | `dropped_event_type` | Queue at 256, dropped oldest |
| `rx_thread_exited` | INFO | `clean` (bool) | Thread `run()` returned |
| `link_lost` | ERROR | `reason` | `serial.SerialException` mid-session |
| `malformed_line` | WARN | `line`, `error` | Parser returned `ProtocolParseError` |
| `tx_sent` | DEBUG | `tag`, `bytes` | After `_send_raw` |

### Error class hierarchy

```python
class ArduinoError(Exception):
    """Root for all arduino_motor-originated errors."""

class ArduinoPortNotFoundError(ArduinoError):
    """No VID:PID match and no usable configured port. — IO-ARD-01"""

class HandshakeTimeoutError(ArduinoError):
    """READY:v<N> not received within arduino_ready_timeout_sec. — IO-ARD-02"""

class ProtocolVersionMismatchError(ArduinoError):
    """READY:v<N> received but N != arduino_protocol_version. — IO-ARD-02 / CFG-04"""

class ProtocolParseError(ArduinoError):
    """RX line did not match any known prefix or had bad fields. — IO-ARD-04"""

class WatchdogResetError(ArduinoError):
    """Mid-session READY:v2 recovery failed (settings/limits ack timeout). — IO-ARD-06"""

class FirmwareErrorReceived(ArduinoError):
    """ERROR:<code> from firmware — halts tracking. — IO-ARD-07"""
    code: ErrorCode
    message: str

class LinkLostError(ArduinoError):
    """USB disconnect mid-session (serial.SerialException raised). — IO-ARD-07 (ERROR-class halt)"""

class RxQueueOverflowError(ArduinoError):
    """Reserved — currently we drop-oldest + WARN, not raise. Kept for symmetry."""
```

`FirmwareErrorReceived` carries the typed `ErrorCode` enum (mirrored from `protocol.h:67–80`) so callers can branch on `if exc.code is ErrorCode.HeartbeatTimeout` without string parsing.

## Reference Projects

[ASSUMED — not verified in this session, optional per research questions]

- `aioserial` (eerimoq/aioserial, MIT) — wraps pyserial in `asyncio.run_in_executor` exactly the way we plan to. Takeaway: confirms the executor-based bridge is the idiomatic answer; no need to adopt the dep.
- `pyserial-asyncio` (Sphinx-Doc-style transport/protocol pattern) — abandoned-ish; uses `loop.create_serial_connection`. Takeaway: the transport/protocol API is clunky for our case (we need a typed parser, not a byte-stream protocol).
- `pyfirmata2` — reference for VID:PID enumeration handling and "wait for boot ready string" pattern. Takeaway: confirms 2 s ready timeout is a common default.

These are pointers, not dependencies — the Phase still uses pyserial directly.

## File Layout Recommendation

**Recommended: 3-file split inside `pastor_tracker/io/`**

```
arduino_protocol.py    ~200 LOC   PURE     parser, DTOs, error-code enum, line constants
arduino_transport.py   ~150 LOC   DIRTY    SerialTransport Protocol, PySerial impl, FakeSerial impl, discover_arduino_port()
arduino_motor.py       ~250 LOC   DIRTY    ArduinoMotor orchestrator (handshake, RX thread, heartbeat, recovery, public TX API)
```

Rationale:
- **SRP (CLAUDE.md rule 2):** parsing, transport, orchestration each have one reason to change.
- **Pure-core/dirty-edges (rule 4):** `arduino_protocol.py` has zero side effects; trivially unit-testable.
- **Test isolation:** parser tests need no serial port and no fake; orchestrator tests inject `FakeSerialTransport`.
- **Phase 5 consumption:** `command_dispatcher.py` imports `ArduinoMotor` (or a typed subset Protocol). The 3-file split keeps the dispatcher's import surface narrow.

Reject single-file: parser + transport + orchestrator in one 600+ LOC file violates SRP and would require commenting "this section is pure" — exactly the smell rule 4 is meant to prevent.

Reject 2-file split (parser + everything else): the `everything else` file still mixes blocking-I/O wrappers with the asyncio orchestrator, making transport unit tests pull in `asyncio` boilerplate.

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 8.4 + pytest-asyncio 1.3.0 (verified installed in `pastor_tracker/.venv`) |
| Config file | `pastor_tracker/pyproject.toml` `[tool.pytest.ini_options]` (asyncio_mode="auto", filterwarnings=error) |
| Quick run command | `pastor_tracker\.venv\Scripts\pytest.exe tests/test_arduino_protocol.py -x` |
| Full suite command | `pastor_tracker\.venv\Scripts\pytest.exe -ra` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|--------------|
| IO-ARD-01 | VID:PID auto-detect, configured-port override, no-match crash | unit | `pytest tests/test_arduino_transport.py::test_discover -x` | ❌ Wave 0 |
| IO-ARD-02 | Boot handshake `READY:v2`; abort on mismatch; timeout error | unit (with FakeSerial) | `pytest tests/test_arduino_motor_handshake.py -x` | ❌ Wave 0 |
| IO-ARD-03 | TX wrapper produces correct ASCII bytes for each command tag; `asyncio.Lock` serializes writers | unit | `pytest tests/test_arduino_motor_tx.py -x` | ❌ Wave 0 |
| IO-ARD-04 | Parser handles all 9 prefixes + malformed; `seq` gap > 5 → WARN | unit | `pytest tests/test_arduino_protocol.py -x` | ❌ Wave 0 (BRANCH-COVERAGE GATE: 100%) |
| IO-ARD-05 | Heartbeat task emits `Q` every 200 ms; cancels cleanly | unit (FakeSerial + short interval) | `pytest tests/test_arduino_motor_heartbeat.py -x` | ❌ Wave 0 |
| IO-ARD-06 | Mid-session `READY:v2` triggers settings → limits re-issue; pause `M:` until both acks; ERROR on ack timeout | integration | `pytest tests/test_arduino_motor_recovery.py -x` | ❌ Wave 0 |
| IO-ARD-07 | Each `ERROR:N` (codes 1–11) halts dispatch + raises `FirmwareErrorReceived` | unit (parametrized) | `pytest tests/test_arduino_motor_error.py -x` | ❌ Wave 0 |
| TEST-04 | Golden trace replay through full pipeline (handshake → FB stream → ERROR halt) | integration | `pytest tests/test_arduino_motor_replay.py -x` | ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** `pytest -x` against the touched test file (≤ 5 s)
- **Per wave merge:** `pytest -ra` full suite (Phase 1 baseline ~ 2 s + Phase 2 estimated ≤ 10 s)
- **Phase gate:** Full suite green AND `coverage report --include="src/pastor_tracker/io/arduino_*.py"` shows ≥ 90% line, 100% branch on `arduino_protocol.py`

### Wave 0 Gaps
- [ ] `tests/test_arduino_protocol.py` — covers IO-ARD-04
- [ ] `tests/test_arduino_transport.py` — covers IO-ARD-01
- [ ] `tests/test_arduino_motor_handshake.py` — covers IO-ARD-02
- [ ] `tests/test_arduino_motor_tx.py` — covers IO-ARD-03
- [ ] `tests/test_arduino_motor_heartbeat.py` — covers IO-ARD-05
- [ ] `tests/test_arduino_motor_recovery.py` — covers IO-ARD-06
- [ ] `tests/test_arduino_motor_error.py` — covers IO-ARD-07
- [ ] `tests/test_arduino_motor_replay.py` — covers TEST-04
- [ ] `tests/fixtures/arduino_traces.py` — canned byte sequences shared between recovery + replay tests
- [ ] Add `pyserial>=3.5,<4.0` to `[project.dependencies]` in `pastor_tracker/pyproject.toml`
- [ ] Add `pytest-mock>=3.14,<4.0` (or use built-in `monkeypatch`) — likely use built-in
- [ ] Add `pytest-cov>=5,<7` to dev deps so the 90/100% coverage gate is automatable

### Intrinsically Untestable in CI (move to Phase 8 / QA-04)

- Real Uno R3/R4 USB enumeration on a different host (different VID:PID pairs in the wild)
- Real AVR `WDTO_500MS` watchdog firing under load (require physical lockup)
- Real serial signal integrity at 115 200 baud over a 3 m USB cable
- Real DTR-toggle-on-port-open MCU reset behavior
- Real multi-Arduino-attached host (two devices matching VID:PID pairs simultaneously)

These all degrade to "log INFO and skip" in CI; they are deferred to QA-04 stage smoke per CONTEXT.md.

## Security Domain

### Applicable ASVS Categories (Level 1)

| ASVS Category | Applies | Standard Control |
|---------------|---------|------------------|
| V2 Authentication | no | local USB serial; no auth surface |
| V3 Session Management | no | no sessions |
| V4 Access Control | no | desktop app, single user |
| V5 Input Validation | yes | every parsed RX line is validated against the protocol DTOs (Pydantic frozen + range-checked); parser rejects malformed lines with `ProtocolParseError` |
| V6 Cryptography | no | local USB serial, no crypto needed |
| V7 Error Handling & Logging | yes | `structlog` JSON, no `print()`; `ERROR:` lines logged with full context; never silently swallowed |
| V8 Data Protection | no | no PII; no persistence |
| V12 Files and Resources | no | no file upload surface |
| V13 API and Web Services | no | no HTTP |
| V14 Configuration | yes | `Config` is frozen, range-validated at startup; no live reconfig |

### Known Threat Patterns for `pyserial + asyncio`

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Buffer-overflow on parser via crafted long line | Tampering | Reject lines > some bound (recommend 256 bytes — firmware can never emit > ~80 bytes per line; we still bound) |
| Float-parse DoS via scientific notation / NaN / inf | Tampering | Use `float()` directly but validate `math.isfinite()` and reject; firmware never emits these |
| Malicious `ERROR:` injection via USB device spoofing | Spoofing | VID:PID match is the gate; if a hostile USB device matches, the user has bigger problems |
| RX queue exhaustion (slow consumer) | DoS | Bounded queue (256) + drop-oldest + WARN log; covered |
| TX deadlock from re-entrant lock acquisition | DoS | `asyncio.Lock` is non-reentrant; `_send_raw` is the single entry point; never call from inside an already-held lock |
| Thread-safety on `serial.Serial` | Tampering | Single writer (asyncio loop) + single reader (RX thread); pyserial 3.5 documents this is safe — see Pitfall 3 |

The threat surface is small because USB serial is a local-only contract and the firmware is read-only. The biggest real risk is a malformed RX line crashing the parser and taking down tracking — bounded buffer + `ProtocolParseError` containment handles it.

## Common Pitfalls

### Pitfall 1: Boot preamble has 3 lines, not 1
**What goes wrong:** Naive handshake reads one line, sees `SETTINGS: defaults...`, doesn't match `READY:v\d+`, errors out.
**Why it happens:** `setup()` emits SETTINGS+FB_HEADER+READY in that order (`main.cpp:417–422`).
**How to avoid:** Handshake reader loops until `Ready` event, ignoring `SettingsInfo` and `FeedbackHeader`. See sketch above.
**Warning sign:** First boot test fails with "expected READY:v2, got SETTINGS: defaults".

### Pitfall 2: `RESET:OK` is NOT a watchdog signal
**What goes wrong:** Treating `RESET:` line as a "MCU was reset" indicator.
**Why it happens:** Naming collision — `RESET:OK` is the ack for an `R` command (host-initiated zeroing of position), not a watchdog event.
**How to avoid:** Watchdog detection MUST gate on `Ready` events received AFTER initial handshake completed. `Reset` events are pure acks.
**Warning sign:** Tests for `R` command flag false-positive watchdog recoveries.

### Pitfall 3: pyserial thread-safety contract
**What goes wrong:** Two threads call `Serial.write()` simultaneously → garbled bytes on the wire.
**Why it happens:** pyserial's threading guarantees are weak — single reader and single writer is safe; concurrent writers are not.
**How to avoid:** All TX behind one `asyncio.Lock`. RX thread NEVER writes. Heartbeat task and command sender share the lock.
**Warning sign:** `ERROR:2 - unknown command type` on the firmware after a heartbeat-burst.

### Pitfall 4: Closing port while RX thread is mid-`read_until`
**What goes wrong:** `serial.SerialException` raised in the RX thread → uncaught, daemon dies, possibly logs "fatal".
**Why it happens:** Order of shutdown: if `transport.close()` runs before the RX thread is signaled and joined, the read raises.
**How to avoid:** Set `_stop_event` first, wait for RX thread to exit (it pops out via timeout), THEN close transport. See `close()` sketch.
**Warning sign:** Test teardown errors with "ClearCommError failed" or similar.

### Pitfall 5: `INPUT_BUFFER_SIZE = 48` truncation
**What goes wrong:** TX line > 47 chars (e.g. `S:50000.000,30000.000,1.234,0.567,0.890`) → firmware truncates → `ERROR:5` or worse, silently mis-parsed settings.
**Why it happens:** `protocol.h:39` fixes the firmware buffer at 48 bytes static.
**How to avoid:** Validate TX payload length before send. Use compact float formatting (`%.3f`, not `%.6f`).
**Warning sign:** `S:` echo line shows different values than what was sent.

### Pitfall 6: Re-using `arduino_ready_timeout_sec` for ack timeouts is fine, but Config naming is confusing
**What goes wrong:** Future contributor wonders why "ready timeout" applies to settings ack.
**Why it happens:** CONTEXT.md locked the reuse decision; no separate `ack_timeout` field.
**How to avoid:** Document at use site: `# Reuses arduino_ready_timeout_sec per CONTEXT.md decision (2026-05-03)`. Don't add a new Config field.
**Warning sign:** PR review comment "should this be a different timeout?".

### Pitfall 7: `seq` gap > 5 with rollover
**What goes wrong:** `g_feedback_sequence_number` is `uint32_t` (`main.cpp:42`); after 4 294 967 295 a rollover to 0 looks like a giant gap.
**Why it happens:** Integer rollover is real.
**How to avoid:** Compute gap as `(received_seq - last_seq) & 0xFFFFFFFF` (mod 2³²). Document this constant.
**Warning sign:** Long-running tests after ~50 days emit a single false WARN. (Acceptable, but document.)

### Pitfall 8: Windows DTR-on-port-open triggers MCU reset
**What goes wrong:** Opening the serial port on Windows toggles DTR → Uno R3 reboots. The handshake reader sees the boot preamble; this is correct but adds ~2 s to startup.
**Why it happens:** Default pyserial behavior; Uno auto-reset on DTR is by design.
**How to avoid:** Accept the 2 s cost — `arduino_ready_timeout_sec` defaults to 2.0. Document. (`pyserial.Serial(dsrdtr=False)` would suppress, but it'd break recovery semantics — leave default.)
**Warning sign:** Cold-start integration test taking longer than warm-start.

### Pitfall 9: `ERROR:0` — sentinel never emitted but parser must not crash
**What goes wrong:** Parser asserts `code != 0` and crashes if a future firmware bug emits `ERROR:0`.
**Why it happens:** Defensive coding overreach.
**How to avoid:** Accept code 0 as a valid `ErrorCode` enum value (already is — `ErrorCode::None = 0`). Treat as warning only. Forward-compat applies to unknown codes too.
**Warning sign:** Test of `ERROR:0` fails parse.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Async serial wrapping | Custom `select()` loop in asyncio | `loop.run_in_executor` over blocking pyserial | Standard library; no dep; CONTEXT.md locks no third-party async-serial |
| Line tokenization | Hand-rolled byte buffer + state machine | `serial.Serial.read_until(b"\n")` | Built into pyserial 3.5; tested for ~15 years |
| VID:PID enumeration | `subprocess` + `wmic` / `ioreg` | `serial.tools.list_ports.comports()` | Cross-platform; pyserial bundled |
| Float parsing | Custom ASCII float parser | `float()` + `math.isfinite()` validation | Stdlib; correct |
| Thread-safe queue bridge | Manual `Lock`+`deque` | `asyncio.Queue` + `loop.call_soon_threadsafe` | Documented async/thread-bridge pattern |
| Time-based test scheduling | `time.sleep()` polling | `asyncio.sleep` in async tests + small interval | Phase 1 already established this style |
| Mock libraries for serial | `unittest.mock` over `serial.Serial` | DI Protocol + `FakeSerialTransport` | CONTEXT.md locks DI fake; mocks fragile against pyserial internals |

**Key insight:** This phase is small because pyserial does the hard byte-level work. Phase 2's job is the *typed* layer above bytes — a parser and an orchestrator — both of which are pure Python and trivially testable when split correctly.

## Code Examples

### `parse_line()` — pure parser dispatch table

```python
# arduino_protocol.py
from typing import Final, Callable
from pydantic import BaseModel, ConfigDict, Field

# Mirror protocol.h. Single source of truth on Python side.
PROTOCOL_VERSION_MAJOR: Final[int] = 2
FIRMWARE_INPUT_BUFFER_SIZE: Final[int] = 48          # protocol.h:39
FIRMWARE_INPUT_BUFFER_USABLE: Final[int] = 47        # minus null terminator
FIRMWARE_HEARTBEAT_TIMEOUT_MS: Final[int] = 1_000    # protocol.h:33
FIRMWARE_FEEDBACK_INTERVAL_MS: Final[int] = 20       # protocol.h:36
SEQ_MODULUS: Final[int] = 1 << 32                    # uint32 rollover guard
FEEDBACK_SEQ_GAP_WARN_THRESHOLD: Final[int] = 5      # IO-ARD-04

class ErrorCode(IntEnum):                            # mirrors protocol.h:67–80
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

class _Event(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

class Ready(_Event):
    version: int = Field(ge=1, le=255)

class Feedback(_Event):
    current_angle_deg: float
    target_angle_deg: float
    speed_steps_per_sec: float
    is_running: bool
    timestamp_micros: int = Field(ge=0)
    sequence: int = Field(ge=0)
    accel_phase: Literal[0, 1, 2, 3]

# ... Settings, SettingsInfo, Limits, Driver, Reset, Stop, Diag, Error, FeedbackHeader

ProtocolEvent = Ready | Feedback | Settings | SettingsInfo | Limits | Driver | Reset | Stop | Diag | Error | FeedbackHeader

def parse_line(line: bytes) -> ProtocolEvent:
    if not line:
        raise ProtocolParseError("empty line")
    text = line.decode("ascii", errors="strict")
    # Dispatch on prefix. Order matters where prefixes share substrings.
    if text.startswith("FB:"):           return _parse_fb(text)
    if text.startswith("FB_HEADER:"):    return FeedbackHeader()
    if text.startswith("READY:v"):       return _parse_ready(text)
    if text.startswith("ERROR:"):        return _parse_error(text)
    if text.startswith("SETTINGS:"):     return _parse_settings(text)
    if text.startswith("LIMITS:"):       return _parse_limits(text)
    if text.startswith("DRIVER:"):       return _parse_driver(text)
    if text.startswith("RESET:"):        return Reset()
    if text.startswith("STOP:"):         return Stop()
    if text.startswith("DIAG:"):         return _parse_diag(text)
    raise ProtocolParseError(f"unknown prefix: {text[:16]!r}")
```

### `seq` gap detection

```python
def _check_seq_gap(self, fb: Feedback) -> None:
    if self._last_seq is None:
        self._last_seq = fb.sequence
        return
    gap = (fb.sequence - self._last_seq) % SEQ_MODULUS
    if gap > FEEDBACK_SEQ_GAP_WARN_THRESHOLD:
        self._logger.warning(
            "feedback_seq_gap",
            expected_seq=(self._last_seq + 1) % SEQ_MODULUS,
            received_seq=fb.sequence,
            gap=gap,
        )
    self._last_seq = fb.sequence
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `pyserial-asyncio` transport/protocol | Plain pyserial + executor + thread bridge | ~2022 (community shift) | Simpler; no extra dep; matches PROJECT.md "threading for blocking RX only" |
| `freezegun` for async timing | Real short-interval sleeps OR custom event-loop time | ~2023 | `freezegun` doesn't patch `loop.time()` cleanly; CONTEXT.md bans it |
| `unittest.mock` over `serial.Serial` | DI `Protocol` + in-memory fake | always preferred for SRP | Decouples test from pyserial internals |
| String split parsing of ASCII protocols | Pydantic frozen DTOs as parser output | Phase 1 set the pattern | Range-checked; immutable; mypy-friendly |

**Deprecated/outdated:**
- `aioserial` — usable but unnecessary given executor pattern; CONTEXT.md bans it as a dep.
- Hardcoding `COM6` — PROJECT.md / Phase 2 decisions ban it; VID:PID is mandatory.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3.12 | All | ✓ | 3.12.x via uv-managed `.venv` | — |
| pyserial | IO-ARD-01, transport | ✗ in venv (✓ system) | 3.5 (system) | Add to `[project.dependencies]` Wave 0 |
| pytest 8.4 | TEST-04 | ✓ | 8.4 | — |
| pytest-asyncio 1.3.0 | async tests | ✓ | 1.3.0 (verified) | — |
| structlog | logging | ✓ | per Phase 1 | — |
| pydantic v2 | DTOs | ✓ | 2.13+ | — |
| coverage / pytest-cov | 90%/100% gate | ✗ | — | Add to dev deps Wave 0 |
| Real Uno on USB | QA-04 only | ✓ | R3 on COM6 (`2341:0043`) verified | — (deferred to Phase 8) |

**Missing dependencies with no fallback:** none.

**Missing dependencies with fallback:**
- `pyserial>=3.5,<4.0` — add to `[project.dependencies]` (production runtime dep)
- `pytest-cov>=5.0,<7.0` — add to `[dependency-groups].dev` (CI gate)

## Project Constraints (from CLAUDE.md)

- Python 3.12 + `uv` + `ruff` + `mypy --strict` (`disallow_any_explicit`) — no `Any`
- Pydantic v2 `frozen=True` for DTOs; immutable mutate via `.model_copy(update=...)`
- `structlog` JSON only — `print()` forbidden by ruff `T20`
- Bare `except` / `except Exception: pass` forbidden by ruff `BLE001` / `E722`
- `time.sleep()` in main loop forbidden — use `await asyncio.sleep`
- No magic numbers — all tunables in `Config` or named module constants
- ≤ 2-level conditional nesting; guard clauses + early returns
- PEP8 strict naming; descriptive identifiers (no abbreviations like `cx`)
- Type hints everywhere; `Literal` / `NewType` / `TypeAlias` to sharpen contracts
- One Conventional Commit per logical change
- Forbidden: PID, MediaPipe, EMA, mocked Kalman/damping (none apply to Phase 2 directly but reinforce the no-mocked-math test ethic)
- `pyproject.toml` ruff selects `S` (bandit), `PLR2004` (magic-value), `ANN` (annotations) — Phase 2 must pass all

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `aioserial` / `pyserial-asyncio` / `pyfirmata2` references in Reference Projects section | Reference Projects | Low — section labeled `[ASSUMED]` and projects are not deps |
| A2 | Arduino Uno R4 PID `0x0069` is the Minima variant; R4 WiFi may use a different PID | Discovery & Boot | Low — only relevant when an R4 ships; if mismatch, add PID to `SUPPORTED_VID_PIDS` constant |
| A3 | DTR-on-port-open triggers MCU reset on Windows ~ 2 s settle | Pitfall 8 | Low — `arduino_ready_timeout_sec=2.0` accommodates; if larger, raise Config default |
| A4 | `coverage report --include="src/pastor_tracker/io/arduino_*.py"` invocation flag spelling | Validation Architecture | Low — verify exact pytest-cov flag at task time |

All other claims in this RESEARCH.md are `[VERIFIED]` from the dev box (pyserial 3.5, COM6 `2341:0043`, pytest-asyncio 1.3.0) or `[CITED]` from the firmware sources (`protocol.h`, `main.cpp`).

## Open Questions

1. **Should `discover_arduino_port` log all matching VID:PIDs or just the chosen one?**
   - What we know: CONTEXT.md says "log INFO listing all matches and the chosen path".
   - What's unclear: structlog format — single event with `all=[...]` list, or one event per match?
   - Recommendation: single `port_multiple_matches` event with `chosen` and `all` keys (sketched above). Easier to grep.

2. **Heartbeat payload — `Q` vs no-op TX (e.g., trailing newline)?**
   - What we know: any TX resets `g_last_command_millis`; `Q` adds `FB:` reply liveness probe (Specifics).
   - What's unclear: `Q` adds RX traffic; is the RX queue burden material?
   - Recommendation: use `Q` per CONTEXT.md Specifics. 5 Hz extra `FB:` is negligible vs the 50 Hz `FB:` already throttled at firmware (`FEEDBACK_INTERVAL_MILLIS = 20`).

3. **Should `WatchdogResetError` halt the orchestrator, or attempt one retry?**
   - What we know: CONTEXT.md says "ERROR if absent".
   - What's unclear: ambiguous whether "ERROR" means "raise and halt" or "log ERROR and try again".
   - Recommendation: raise `WatchdogResetError`, halt, surface to UI — matches IO-ARD-07 ERROR-class semantics. No auto-retry. Operator does manual reconnect (CONTEXT.md ### Disconnect & Port Discovery).

4. **Drop-oldest implementation under contention.**
   - What we know: `_enqueue` runs in the loop thread; full-queue path pops one, then puts.
   - What's unclear: between the `get_nowait()` and `put_nowait()`, can another `_enqueue` fire? Yes, in single-threaded asyncio it cannot — both calls are one synchronous block.
   - Recommendation: no lock needed; document `_enqueue` runs only via `call_soon_threadsafe` and is non-async. (Resolved.)

## Sources

### Primary (HIGH confidence)
- `D:\System\Documents\PastorTrackingSystem\arduino\stepper_controller\include\protocol.h` — all firmware constants, error code enum, EEPROM layout
- `D:\System\Documents\PastorTrackingSystem\arduino\stepper_controller\src\main.cpp` — wire-format emission, boot preamble order, watchdog setup, command handler echo strings
- `D:\System\Documents\PastorTrackingSystem\pastor_tracker\src\pastor_tracker\config.py` — verified `arduino_*` fields and ranges
- `D:\System\Documents\PastorTrackingSystem\pastor_tracker\pyproject.toml` — verified ruff/mypy/pytest config and existing deps
- pyserial 3.5 live enumeration on dev box — verified `ListPortInfo.vid`/`.pid` are `int | None`, COM6 = Uno R3 (`0x2341:0x0043`)
- `pyserial.serialutil.read_until(expected, size)` signature — verified via Python `help()`
- pytest-asyncio 1.3.0 verified installed in `pastor_tracker/.venv`

### Secondary (MEDIUM confidence)
- `.planning/phases/02-arduino-i-o/02-CONTEXT.md` — locked decisions (this Phase's user intent record)
- `.planning/REQUIREMENTS.md` — IO-ARD-01..07, TEST-04 wording
- `.planning/PROJECT.md` — VID:PID set, hardware spec, two-stage damping context
- Phase 1 conventions inferred from existing test files and `01-RESEARCH.md` references in `core/types.py` docstrings

### Tertiary (LOW confidence)
- Reference Projects section (Reference Projects) — `[ASSUMED]`, not used as load-bearing claim

## Metadata

**Confidence breakdown:**
- Protocol Reference: HIGH — direct citation from firmware sources (line numbers given)
- Architecture & Concurrency: HIGH — pattern is standard pyserial+asyncio idiom; sketched code follows established practice
- Discovery & Boot: HIGH — VID:PID enumeration verified live on dev box
- Watchdog Recovery: MEDIUM — recovery sequence is locked by CONTEXT.md; ack-watcher discrimination of `Settings` vs `SettingsInfo` is a design point worth scrutiny in plan-check
- Testing Strategy: HIGH — fake transport DI is locked; coverage tooling is standard
- Logging & Errors: HIGH — names and class hierarchy are conventional; map to requirements is explicit

**Research date:** 2026-05-03
**Valid until:** 2026-06-02 (30 days — pyserial 3.5 and AccelStepper firmware are stable; only the Phase 2 plan itself drives expiry)

## RESEARCH COMPLETE
