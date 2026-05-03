"""Dirty-edge transport for the Arduino stepper firmware.

Owns the only direct ``pyserial`` imports in the project. Exposes a
:class:`SerialTransport` ``Protocol`` (DI seam) so orchestrator tests can inject
:class:`FakeSerialTransport` without touching real hardware.
:func:`discover_arduino_port` is the single entry point for VID:PID
auto-detect — fixed COM-port hardcoding is forbidden by PROJECT.md ## Context.

Design split (per :file:`.planning/phases/02-arduino-i-o/02-CONTEXT.md`):

* This module = transport (Plan 02-02 — IO-ARD-01).
* Plan 02-03 = orchestrator (handshake, RX thread, heartbeat, recovery) — it
  imports :class:`SerialTransport` from here and unifies error classes under an
  ``ArduinoError`` root. To avoid a circular import, :class:`ArduinoPortNotFoundError`
  here is a plain :class:`Exception` subclass; the orchestrator simply
  ``except (ArduinoPortNotFoundError, HandshakeTimeoutError, ...)`` — no
  inheritance trickery required.

Tiger-style invariants:
  * Configured port that is **absent** from ``serial.tools.list_ports.comports()``
    raises :class:`ArduinoPortNotFoundError` rather than silently falling back to
    auto-detect (CONTEXT.md `Disconnect & Port Discovery`).
  * Auto-detect with no VID:PID match raises :class:`ArduinoPortNotFoundError`
    (CLAUDE.md tiger-style — no silent failure).
  * :class:`PySerialTransport.read_line` writes the per-call ``timeout`` property
    on the underlying ``serial.Serial`` handle. Only the orchestrator's RX
    thread is permitted to call ``read_line`` (single-reader invariant — see
    threat register T-02-01d).
"""
from __future__ import annotations

import collections
import threading
from typing import Final, Protocol, runtime_checkable

import serial  # pyserial
import structlog
from serial.tools.list_ports import comports

# ---------------------------------------------------------------------------
# Module-level constants — every literal lives here (CLAUDE.md rule 6).
# ---------------------------------------------------------------------------

# Mirror PROJECT.md ## Context — VID:PID pairs that MUST match. Verified live
# on dev box 2026-05-03 against an Uno R3 reporting (0x2341, 0x0043) on COM6.
SUPPORTED_VID_PIDS: Final[frozenset[tuple[int, int]]] = frozenset(
    {
        (0x2341, 0x0043),  # Arduino Uno R3
        (0x2341, 0x0069),  # Arduino Uno R4
        (0x1A86, 0x7523),  # CH340 clone
        (0x0403, 0x6001),  # FTDI clone
    }
)

# Initial open timeout — non-blocking. Per-read timeout is patched at use site
# in PySerialTransport.read_line (single-reader invariant; see WARN 4).
_PYSERIAL_OPEN_TIMEOUT_SEC: Final[float] = 0.0
_DEFAULT_RX_BUF_TERMINATOR: Final[bytes] = b"\n"


class ArduinoPortNotFoundError(Exception):
    """No VID:PID match and no usable configured port. — IO-ARD-01.

    Plain :class:`Exception` subclass; Plan 02-03's orchestrator re-exports it
    under the unified ``ArduinoError`` family without inheritance changes.
    """


class FakeSerialClosedError(Exception):
    """Raised when calling ``write`` on a closed :class:`FakeSerialTransport`.

    Tiger-style fail-loud (CLAUDE.md) — closing the fake then writing is a test
    bug, never a runtime path; we crash instead of silently dropping bytes.
    """


@runtime_checkable
class SerialTransport(Protocol):
    """Minimal DI surface — the only contract orchestrator code depends on.

    ``read_line`` returns the line WITHOUT trailing ``\\r``/``\\n`` so Plan
    02-01's :func:`parse_line` accepts it directly. Returns ``None`` on timeout.
    """

    def write(self, data: bytes) -> int: ...

    def read_line(self, timeout: float) -> bytes | None: ...

    def close(self) -> None: ...


class PySerialTransport:
    """Real ``pyserial`` wrapper. Single owner of the OS-level serial handle.

    ``__init__`` opens the port immediately (so a missing device fails at
    construction, not at first read). Constructor failure surfaces
    :class:`serial.SerialException` un-swallowed — tiger-style.
    """

    def __init__(self, port: str, baud: int) -> None:
        self._serial = serial.Serial(
            port=port,
            baudrate=baud,
            timeout=_PYSERIAL_OPEN_TIMEOUT_SEC,
        )

    def write(self, data: bytes) -> int:
        written: int = self._serial.write(data)
        return written

    def read_line(self, timeout: float) -> bytes | None:
        # pyserial's ``Serial.timeout`` is the per-read timeout. Patch it for
        # this call.
        #
        # Only the RX thread calls read_line; this property write is single-
        # threaded by the orchestrator's invariant (Plan 02-03: "single OS
        # reader = the RX daemon thread; never the loop, never the heartbeat
        # task"). Do NOT refactor toward a multi-reader pattern — concurrent
        # writers to ``self._serial.timeout`` would race on the OS handle update
        # and corrupt timeout semantics (threat register T-02-01d).
        self._serial.timeout = timeout
        line: bytes = self._serial.read_until(_DEFAULT_RX_BUF_TERMINATOR)
        if not line.endswith(_DEFAULT_RX_BUF_TERMINATOR):
            return None  # timed out — partial line discarded by pyserial
        return line.rstrip(b"\r\n")

    def close(self) -> None:
        self._serial.close()


class FakeSerialTransport:
    """Bidirectional in-memory fake.

    Tests call :meth:`feed_rx` to enqueue inbound lines (without trailing
    ``\\n``) and assert against :attr:`captured_writes`. FIFO read order;
    :meth:`read_line` blocks on a :class:`threading.Event` until either a line
    is fed or the timeout elapses.
    """

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
            raise FakeSerialClosedError("write on closed FakeSerialTransport")
        self.captured_writes.append(data)
        return len(data)

    def read_line(self, timeout: float) -> bytes | None:
        if self._rx_lines:
            line = self._rx_lines.popleft()
            if not self._rx_lines:
                # Without this clear, a subsequent read_line on an empty queue
                # would return immediately because the event is still set —
                # silently breaking the timeout contract.
                self._rx_event.clear()
            return line
        if self._rx_event.wait(timeout):
            self._rx_event.clear()
            if self._rx_lines:
                return self._rx_lines.popleft()
        return None

    def close(self) -> None:
        self._closed = True
        self._rx_event.set()  # unblock any waiter so read_line returns None


def discover_arduino_port(configured_port: str | None) -> str:
    """VID:PID auto-detect with manual-override fallback. — IO-ARD-01.

    Resolution order:
        1. ``configured_port`` is not None AND present in ``comports()`` →
           use it (logs ``port_manual_override``).
        2. ``configured_port`` is not None AND absent → raise
           :class:`ArduinoPortNotFoundError`.
        3. ``configured_port`` is None → first ``comports()`` entry whose
           ``(vid, pid)`` is in :data:`SUPPORTED_VID_PIDS`:

           * exactly 1 match → return it; log INFO ``port_discovered``
             with ``port`` / ``vid`` / ``pid``.
           * multiple matches → return first; log INFO
             ``port_multiple_matches`` with ``chosen`` + full ``all`` list.
        4. None match → raise :class:`ArduinoPortNotFoundError`.

    Tiger-style: configured-but-absent raises rather than falling back to
    auto-detect — a silent fallback would mask developer/operator misconfig.

    The two distinct success log events (``port_discovered`` for the
    single-match path; ``port_multiple_matches`` for the multi-match path)
    are part of the contract (WARN 5 / threat T-02-01c) — operator triage
    needs the multi-match list when ambiguity is real.
    """
    log = structlog.get_logger(module="arduino_transport")
    ports = list(comports())
    if configured_port is not None:
        if any(p.device == configured_port for p in ports):
            log.info("port_manual_override", port=configured_port)
            return configured_port
        raise ArduinoPortNotFoundError(
            f"arduino_port={configured_port!r} not present in serial.tools.list_ports"
        )
    matches = [
        p
        for p in ports
        if p.vid is not None
        and p.pid is not None
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
    else:
        log.info(
            "port_discovered",
            port=matches[0].device,
            vid=hex(matches[0].vid),
            pid=hex(matches[0].pid),
        )
    chosen_device: str = matches[0].device
    return chosen_device
