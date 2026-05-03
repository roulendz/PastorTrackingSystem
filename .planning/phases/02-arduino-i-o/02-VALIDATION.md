---
phase: 2
slug: arduino-i-o
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-05-03
---

# Phase 2 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution. Source: `02-RESEARCH.md` § Validation Architecture.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.4 + pytest-asyncio 1.3.0 (verified installed in `pastor_tracker/.venv`) |
| **Config file** | `pastor_tracker/pyproject.toml` `[tool.pytest.ini_options]` (`asyncio_mode="auto"`, `filterwarnings=error`) |
| **Quick run command** | `pastor_tracker\.venv\Scripts\pytest.exe tests/test_arduino_protocol.py -x` |
| **Full suite command** | `pastor_tracker\.venv\Scripts\pytest.exe -ra` |
| **Coverage gate command** | `pastor_tracker\.venv\Scripts\coverage.exe report --include="src/pastor_tracker/io/arduino_*.py"` ≥ 90 % line, 100 % branch on `arduino_protocol.py` |
| **Estimated runtime** | ~10 s (Phase 2 tests) on top of ~2 s Phase 1 baseline |

---

## Sampling Rate

- **After every task commit:** Run `pytest -x` against the touched test file (≤ 5 s)
- **After every plan wave:** Run `pytest -ra` full suite
- **Before `/gsd-verify-work`:** Full suite must be green AND coverage gate met
- **Max feedback latency:** ≤ 10 s

---

## Per-Task Verification Map

| Req ID | Plan | Wave | Behavior | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|--------|------|------|----------|------------|-----------------|-----------|-------------------|-------------|--------|
| IO-ARD-01 | 02-02 | 1 | VID:PID auto-detect, configured-port override, no-match crash | T-02-01 (USB spoof) | VID:PID match is sole gate; ConfigPortAbsent / NoArduinoFound on miss | unit | `pytest tests/test_arduino_transport.py::test_discover -x` | ❌ W0 | ⬜ pending |
| IO-ARD-02 | 02-03 | 2 | Boot handshake `READY:v2`; abort on mismatch; timeout error | T-02-02 (preamble parse) | Skip `SettingsInfo`/`FeedbackHeader`; `ProtocolVersionMismatchError` / `HandshakeTimeoutError` on miss | unit (FakeSerial) | `pytest tests/test_arduino_motor_handshake.py -x` | ❌ W0 | ⬜ pending |
| IO-ARD-03 | 02-03 | 2 | TX wrapper produces correct ASCII bytes per command tag; `asyncio.Lock` serialises writers | T-02-03 (interleaved bytes) | Single `asyncio.Lock` over `loop.run_in_executor`; INPUT_BUFFER_SIZE=48 enforced pre-send | unit | `pytest tests/test_arduino_motor_tx.py -x` | ❌ W0 | ⬜ pending |
| IO-ARD-04 | 02-01 | 1 | Parser handles all 9 RX prefixes + malformed; `seq` gap > 5 → WARN | T-02-04 (parser DoS) | Reject lines > 256 B; `ProtocolParseError` containment; `math.isfinite()` on floats | unit | `pytest tests/test_arduino_protocol.py -x` | ❌ W0 (BRANCH-COVERAGE GATE: 100 %) | ⬜ pending |
| IO-ARD-05 | 02-03 | 2 | Heartbeat emits `Q` every `arduino_heartbeat_interval_sec`; cancels cleanly | T-02-05 (TX deadlock) | Lock non-reentrant; heartbeat shares lock with command sender | unit (FakeSerial + virtual clock) | `pytest tests/test_arduino_motor_heartbeat.py -x` | ❌ W0 | ⬜ pending |
| IO-ARD-06 | 02-03 | 3 | Mid-session `READY:v2` triggers settings → limits re-issue; pause `M:` until both echoed; ERROR on ack timeout | T-02-06 (false reset) | Watchdog gate uses `Ready` events post-handshake only; `RESET:` lines are acks not signals | integration (FakeSerial replay) | `pytest tests/test_arduino_motor_recovery.py -x` | ❌ W0 | ⬜ pending |
| IO-ARD-07 | 02-03 | 2 | `ERROR:N` (codes 1–11) halts dispatch; raises `FirmwareErrorReceived`; manual reset required | T-02-07 (silent error) | No `except Exception: pass`; ERROR halts at orchestrator boundary | unit (parametrized over codes) | `pytest tests/test_arduino_motor_error.py -x` | ❌ W0 | ⬜ pending |
| TEST-04 | 02-03 | 3 | Golden trace replay through full pipeline (handshake → FB stream → ERROR halt) | T-02-08 (e2e drift) | Canned bytes mirror firmware exactly; deterministic | integration | `pytest tests/test_arduino_motor_replay.py -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

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
- [ ] Add `pytest-cov>=5,<7` to dev deps so the 90 / 100 % coverage gate is automatable

*If none: "Existing infrastructure covers all phase requirements." — N/A here, Wave 0 is non-empty.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Real Uno R3/R4 USB enumeration on host | IO-ARD-01 (real-hw subset) | Different hosts expose different VID:PID pairs in the wild | Phase 8 / QA-04 stage smoke — plug Uno, run `python -m pastor_tracker --probe-arduino` |
| Real AVR `WDTO_500MS` watchdog firing under load | IO-ARD-06 (real-hw subset) | Requires physical lockup conditions | Phase 8 / QA-04 — load test with command burst until firmware watchdog triggers |
| Real serial signal integrity at 115 200 baud over a 3 m USB cable | IO-ARD-03 (real-hw subset) | Cable / EMI conditions vary by venue | Phase 8 / QA-04 — on-stage smoke with production cable |
| Real DTR-toggle-on-port-open MCU reset behavior | IO-ARD-02 (real-hw subset) | Requires real MCU + real driver chip | Phase 8 / QA-04 — open port, observe `READY:v2` within `arduino_ready_timeout_sec` |
| Real multi-Arduino-attached host (two devices matching VID:PID simultaneously) | IO-ARD-01 (corner case) | Requires two physical devices | Phase 8 / QA-04 — plug two boards, confirm INFO log lists both, first wins |

These all degrade to "log INFO and skip" in CI; deferred to QA-04 stage smoke per CONTEXT.md.

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 10 s
- [ ] `nyquist_compliant: true` set in frontmatter once planner fills `Plan` column

**Approval:** pending
