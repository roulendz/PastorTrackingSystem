# Phase 2: Arduino I/O - Context

**Gathered:** 2026-05-03
**Status:** Ready for planning
**Mode:** Smart-discuss (4 grey areas, all recommendations accepted)

<domain>
## Phase Boundary

A standalone, fully-tested async Arduino serial driver that auto-detects the Uno, completes the v2 boot handshake, runs the heartbeat, parses every RX line type, and recovers from MCU watchdog resets — proven against canned `FB:`/`READY:`/`ERROR:` replay before any other I/O exists.

**In scope:**
- `pastor_tracker/io/arduino_motor.py` — async TX wrapper + threaded RX + parser + heartbeat task + watchdog-reset recovery
- VID:PID auto-detect over `serial.tools.list_ports.comports()` (Uno R3 `2341:0043`, R4 `2341:0069`, CH340 `1A86:7523`, FTDI `0403:6001`); fallback to `config.arduino_port`
- Boot handshake — read up to `arduino_ready_timeout_sec`, expect `READY:v2`, abort on version mismatch
- Async TX for `M:` `S:` `L:` `R` `Q` `E` `H` `X:` `D:` commands
- Threaded RX parser for `FB:` `READY:` `SETTINGS:` `LIMITS:` `DRIVER:` `RESET:` `STOP:` `DIAG:` `ERROR:`; `seq` gap > 5 → WARN
- 200 ms heartbeat task while tracking
- Watchdog-reset recovery — re-receipt of `READY:v2` mid-session → re-issue settings + limits (Config-authoritative) + WARN log
- `ERROR:` from firmware halts tracking, surfaces to UI, requires manual reset; `ERROR:11` (heartbeat lost) logs ERROR
- Integration test against canned line replay + heartbeat verification (TEST-04)

**Out of scope (later phases):**
- Camera I/O (Phase 3)
- Perception / Kalman / BoT-SORT (Phase 4)
- Motion intent + pan controller + dispatcher (Phase 5; `arduino_motor.py` exposes the dispatcher's TX target only)
- Pipeline orchestrator (Phase 6)
- DearPyGui dashboard (Phase 7)
- Real-device on-stage smoke (Phase 8 / QA-04)

**Requirements covered:** IO-ARD-01..07, TEST-04.

</domain>

<decisions>
## Implementation Decisions

### Locked by PROJECT.md / PROMPT.md / CLAUDE.md / REQUIREMENTS.md
- 115 200 baud, protocol v2 only — version mismatch = abort
- Threading for blocking serial RX only (PROJECT.md); asyncio for TX/lifecycle
- VID:PID auto-detect mandatory; fixed COM port hardcoding forbidden (PROJECT.md)
- Boot handshake `READY:v2` with `arduino_ready_timeout_sec` ceiling; abort on mismatch
- 200 ms PC heartbeat (vs firmware 1000 ms watchdog); AVR WDTO_500 ms hardware watchdog out of scope (firmware-side)
- Re-receipt of `READY:v2` mid-session = MCU reset → PC must re-issue settings + limits
- `ERROR:` halts tracking, surfaces to UI, requires manual reset; no auto-recover (REQUIREMENTS Out-of-Scope)
- Tiger-style fail-fast: validation errors raised at startup, no silent fallback
- `structlog` JSON logging only; no `print()`; bare `except` forbidden
- TEST-04 integration test = canned-line replay + heartbeat verification (no hardware-in-CI)
- All public functions annotated, no `Any`, no magic numbers (everything tunable via `Config`)
- ≤ 2-level conditional nesting; guard clauses + early returns
- Conventional Commits, one logical change per commit

### Async/Threading Architecture
- pyserial wrapped via dedicated RX thread that pushes typed parsed events into `asyncio.Queue` (idiomatic; matches PROJECT.md "threading for blocking serial RX only"; no third-party async-serial dep)
- TX path = `asyncio.Lock` around blocking `serial.Serial.write()` on the loop (single writer, simple; coalesces command-dispatcher and heartbeat producers)
- Parser runs in RX thread before queueing — keeps raw bytes off the asyncio hot path; queue carries typed events (`Feedback`, `Ready`, `Settings`, `Limits`, `Driver`, `Reset`, `Stop`, `Diag`, `Error`)
- RX queue is bounded N=256 with drop-oldest semantics + WARN log on drop (fail-loud on backpressure; no unbounded memory growth)

### Disconnect & Port Discovery
- Mid-session USB drop → surface `MotorLinkLost` (or equivalent) ERROR, halt tracking, require manual reconnect (matches "no auto-recover from `ERROR:`" + tiger-style fail-loud)
- Multiple VID:PID matches at boot → pick first hit, log INFO listing all matches and the chosen path
- Manual override — if `config.arduino_port` is set AND a device is present at that path, use it; otherwise auto-detect (matches PROJECT.md fallback rule)
- Port discovery cadence — at boot only (one shot); re-discovery is an explicit operator-driven reconnect flow, not a background poll

### Watchdog Reset Recovery
- Re-issued settings/limits derived from `Config` (PC authoritative; deterministic; survives EEPROM corruption; no round-trip dependency on firmware EEPROM read)
- Recovery order on mid-session `READY:v2`: settings → limits → resume `M:` dispatch (limits depend on calibrated settings being applied first)
- Ack model — wait for echoed `SETTINGS:` and `LIMITS:` parsed lines with timeout (`arduino_ready_timeout_sec` reused); ERROR if absent
- Pause `M:` dispatch until both acks received; WARN log on resume; heartbeat continues throughout to keep firmware watchdog satisfied

### Test Strategy (TEST-04)
- Fake serial transport — custom in-memory bidirectional fake + DI via a `SerialTransport`-style protocol (zero deps; full control over canned RX bytes and TX capture)
- Real-device smoke test deferred to Phase 8 QA-04 (on-stage); Phase 2 = canned-line replay + heartbeat verification only, per TEST-04 wording
- Heartbeat timing test uses asyncio event-loop time with short test interval (e.g., 20 ms) + monotonic-clock virtual stepping; no `freezegun`, no real wall-clock sleeps
- Coverage target — ≥ 90% line coverage on `arduino_motor.py`; 100% on parser branches (parser correctness is safety-critical for `ERROR:` and `READY:v2` re-emission detection)

### Claude's Discretion
All implementation choices not pinned above are at Claude's discretion. Reasonable defaults expected:
- Internal type names (`Feedback`, `Ready`, `Settings`, …) and union-type definition style
- File granularity inside the I/O module (single `arduino_motor.py` vs split parser/transport submodules)
- Logging key names (kept consistent with structlog conventions established in Phase 1)
- Error class hierarchy (single `ArduinoError` vs typed subclasses for handshake/version/reset/error-line)

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `pastor_tracker/config.py` — frozen `Config` already exposes the 23 PROMPT.md fields including `arduino_port`, `arduino_baud`, `arduino_ready_timeout_sec`, `arduino_protocol_version`, `arduino_heartbeat_interval_sec`, motor speed/accel clamps, software angle limits — Phase 2 consumes these directly, no new config fields expected
- `pastor_tracker/core/types.py` — frozen DTOs include `MotorCommand` (consumed by the TX side); Phase 2 may add small RX event DTOs if not already present (parser output)
- `arduino/stepper_controller/include/protocol.h` — authoritative protocol v2 line-format and error-code definitions; mirror these constants in Python (no parallel protocol drift)
- `structlog` JSON logger configured in Phase 1 — reuse with `module="arduino_motor"` binding

### Established Patterns (Phase 1)
- Pydantic v2 `frozen=True` for DTOs; immutable mutate via `.model_copy(update=...)`
- `pytest` + `hypothesis` for property tests; mock-free for math; canned fixtures permitted for protocol parser
- Lint policy rejects `print(`, bare `except:`, `except Exception: pass` (`ruff` rules T20/BLE001/E722)
- Pure-core/dirty-edges layering — Phase 2 is squarely a "dirty edge" (serial I/O), so all impure code lives under `pastor_tracker/io/`

### Integration Points
- TX target consumed later by `pastor_tracker/control/command_dispatcher.py` (Phase 5) — public TX surface should expose typed methods (`send_motor_angle`, `send_emergency_stop`, `send_home`, `send_query`, etc.) keyed off `MotorCommand` fields
- RX events consumed later by `pipeline.py` (Phase 6) status panel and the dashboard (Phase 7) — public RX surface = `async for event in motor.events()` style or a typed callback registry
- `Config` is read once at construction; no live reconfig (the dashboard's "Save Config" reloads + restarts pipeline, not Phase 2's concern)

</code_context>

<specifics>
## Specific Ideas

- Mirror `arduino/stepper_controller/include/protocol.h` constants into a Python `protocol.py` (or top-of-module constants) so error codes 0–11 and line tags are single-sourced — version-bump aware
- The `seq` gap > 5 WARN log (IO-ARD-04) compares parsed `FB:` `seq` field against last seen; missing `seq` field on a `FB:` line → ERROR (malformed protocol)
- Heartbeat task chooses `Q` (status query) per IO-ARD-05 hint — its echo `FB:` line doubles as a liveness probe
- `ERROR:11` (heartbeat lost) is logged at ERROR level (REQUIREMENTS IO-ARD-07) but the same halt-and-surface flow as any other `ERROR:` applies — no special path
- Boot-handshake mismatch log format: `expected READY:v{config.arduino_protocol_version}, got {received_line}` so the operator sees both sides

</specifics>

<deferred>
## Deferred Ideas

- Continuous USB-port-event monitoring (WMI / udev) for hot-plug auto-reconnect — deferred; manual reconnect is sufficient for the on-stage operator workflow
- Settings/limits round-trip verification by reading firmware EEPROM via `SETTINGS:`/`LIMITS:` query — deferred; Config is authoritative
- Real-device pytest fixture (`pytest --hardware` marker) — deferred to Phase 8 QA-04 stage smoke
- `aioserial` adoption — deferred unless dedicated-thread approach proves unworkable (no current evidence it will)
- Live reconfig of motor settings without a restart — out of scope for v1 entirely

</deferred>
