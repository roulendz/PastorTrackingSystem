"""Tests for ``pastor_tracker.io.arduino_transport`` (IO-ARD-01).

Covers the discover_arduino_port matrix (4 supported VID:PIDs x manual override
present/absent x multi-match x no-match) and FakeSerialTransport behaviour.
PySerialTransport is intentionally NOT exercised here — it requires a real
device and is deferred to Phase 8 / QA-04 stage smoke.

Mocking discipline: ``monkeypatch.setattr`` on the module-level ``comports``
binding (per 02-PATTERNS.md line 562). ``SimpleNamespace`` for ListPortInfo
stubs — never ``unittest.mock.MagicMock`` (02-PATTERNS.md line 542; RESEARCH
"Don't Hand-Roll" line 935 — no mocking ``serial.Serial``).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
import structlog

from pastor_tracker.io.arduino_transport import (
    SUPPORTED_VID_PIDS,
    ArduinoPortNotFoundError,
    FakeSerialClosedError,
    FakeSerialTransport,
    discover_arduino_port,
)

# ---------------------------------------------------------------------------
# Helper — ListPortInfo-shaped stubs (POD: .device / .vid / .pid only).
# ---------------------------------------------------------------------------


def _fake_port(device: str, vid: int | None, pid: int | None) -> SimpleNamespace:
    return SimpleNamespace(device=device, vid=vid, pid=pid)


_COMPORTS_TARGET = "pastor_tracker.io.arduino_transport.comports"


# ---------------------------------------------------------------------------
# Group A — discover_arduino_port matrix (IO-ARD-01).
# ---------------------------------------------------------------------------


def test_discover_returns_first_supported_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Single supported port (mixed with a None-VID legacy port) → returned."""
    fake_ports = [
        _fake_port("COM6", 0x2341, 0x0043),
        _fake_port("COM1", None, None),
    ]
    monkeypatch.setattr(_COMPORTS_TARGET, lambda: fake_ports)
    assert discover_arduino_port(configured_port=None) == "COM6"


def test_discover_no_match_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Auto-detect with only None-VID ports → ArduinoPortNotFoundError."""
    fake_ports = [
        _fake_port("COM1", None, None),
        _fake_port("COM2", None, None),
    ]
    monkeypatch.setattr(_COMPORTS_TARGET, lambda: fake_ports)
    with pytest.raises(ArduinoPortNotFoundError, match="no Arduino"):
        discover_arduino_port(configured_port=None)


def test_discover_multiple_matches_picks_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Multi-match path: returns first; logs ``port_multiple_matches`` (WARN 5).

    Pinned event name is ``port_multiple_matches`` — NOT ``port_discovered``.
    The discriminator between the two success paths is part of the contract:
    operator triage needs the multi-match list when ambiguity is real.
    """
    fake_ports = [
        _fake_port("COM6", 0x2341, 0x0043),  # Uno R3
        _fake_port("COM7", 0x2341, 0x0069),  # Uno R4
    ]
    monkeypatch.setattr(_COMPORTS_TARGET, lambda: fake_ports)
    with structlog.testing.capture_logs() as caplog:
        result = discover_arduino_port(configured_port=None)
    assert result == "COM6"
    multi_records = [r for r in caplog if r.get("event") == "port_multiple_matches"]
    assert len(multi_records) == 1, (
        f"expected exactly one 'port_multiple_matches' log, got {caplog!r}"
    )
    record = multi_records[0]
    assert record["chosen"] == "COM6"
    # ``all`` is a list of (device, vid_hex, pid_hex) tuples — both ports must appear.
    devices_in_all = [entry[0] for entry in record["all"]]
    assert devices_in_all == ["COM6", "COM7"]
    # Single-match event must NOT have fired on the multi-match path.
    assert not any(r.get("event") == "port_discovered" for r in caplog)


def test_discover_manual_override_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Configured port present in comports() → returned; logs port_manual_override."""
    fake_ports = [_fake_port("COM6", 0x2341, 0x0043)]
    monkeypatch.setattr(_COMPORTS_TARGET, lambda: fake_ports)
    with structlog.testing.capture_logs() as caplog:
        result = discover_arduino_port(configured_port="COM6")
    assert result == "COM6"
    override_records = [r for r in caplog if r.get("event") == "port_manual_override"]
    assert len(override_records) == 1
    assert override_records[0]["port"] == "COM6"


def test_discover_manual_override_absent_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Configured port absent → ArduinoPortNotFoundError (no silent fallback)."""
    # Even though COM6 is a supported VID:PID, the configured COMX is absent;
    # tiger-style says crash, do NOT auto-detect.
    fake_ports = [_fake_port("COM6", 0x2341, 0x0043)]
    monkeypatch.setattr(_COMPORTS_TARGET, lambda: fake_ports)
    with pytest.raises(ArduinoPortNotFoundError, match="COMX"):
        discover_arduino_port(configured_port="COMX")


@pytest.mark.parametrize("vid_pid", sorted(SUPPORTED_VID_PIDS))
def test_discover_each_supported_vid_pid(
    monkeypatch: pytest.MonkeyPatch,
    vid_pid: tuple[int, int],
) -> None:
    """Single-match path: each of the 4 supported VID:PIDs → ``port_discovered``.

    Pinned event name is ``port_discovered`` — NOT ``port_multiple_matches``.
    Single-match path MUST emit the discovery-specific event so the operator
    sees vid/pid hex in the log; multi-match uses a different event name.
    """
    vid, pid = vid_pid
    fake_ports = [_fake_port("COM_TEST", vid, pid)]
    monkeypatch.setattr(_COMPORTS_TARGET, lambda: fake_ports)
    with structlog.testing.capture_logs() as caplog:
        result = discover_arduino_port(configured_port=None)
    assert result == "COM_TEST"
    discovered_records = [r for r in caplog if r.get("event") == "port_discovered"]
    assert len(discovered_records) == 1, (
        f"expected exactly one 'port_discovered' log, got {caplog!r}"
    )
    record = discovered_records[0]
    assert record["port"] == "COM_TEST"
    assert record["vid"] == hex(vid)
    assert record["pid"] == hex(pid)
    # Multi-match event must NOT have fired on the single-match path.
    assert not any(r.get("event") == "port_multiple_matches" for r in caplog)


# ---------------------------------------------------------------------------
# Group B — FakeSerialTransport behaviour.
# ---------------------------------------------------------------------------


def test_fake_transport_feed_and_read() -> None:
    """feed_rx then read_line returns same bytes (no trailing newline)."""
    fake = FakeSerialTransport()
    fake.feed_rx(b"hello")
    assert fake.read_line(timeout=0.0) == b"hello"


def test_fake_transport_read_timeout() -> None:
    """Empty queue: read_line(0.05) returns None within ~100 ms wall clock."""
    import time

    fake = FakeSerialTransport()
    started = time.monotonic()
    result = fake.read_line(timeout=0.05)
    elapsed = time.monotonic() - started
    assert result is None
    # Generous upper bound to absorb scheduler jitter on Windows; lower bound
    # is implicit in threading.Event.wait honoring the timeout.
    assert elapsed < 1.0


def test_fake_transport_write_captures() -> None:
    """write() appends to captured_writes; returns len(data)."""
    fake = FakeSerialTransport()
    n1 = fake.write(b"X")
    n2 = fake.write(b"YZ")
    assert fake.captured_writes == [b"X", b"YZ"]
    assert n1 == 1
    assert n2 == 2


def test_fake_transport_write_after_close_raises() -> None:
    """close() then write() raises FakeSerialClosedError (tiger-style)."""
    fake = FakeSerialTransport()
    fake.close()
    with pytest.raises(FakeSerialClosedError):
        fake.write(b"X")


def test_fake_transport_fifo() -> None:
    """Two fed lines → FIFO order; subsequent read returns None on timeout."""
    fake = FakeSerialTransport()
    fake.feed_rx(b"A")
    fake.feed_rx(b"B")
    assert fake.read_line(timeout=0.0) == b"A"
    assert fake.read_line(timeout=0.0) == b"B"
    assert fake.read_line(timeout=0.05) is None


def test_fake_transport_close_idempotent() -> None:
    """close() twice does not crash (idempotent shutdown contract)."""
    fake = FakeSerialTransport()
    fake.close()
    fake.close()  # must not raise


# ---------------------------------------------------------------------------
# Group C — module-level invariants.
# ---------------------------------------------------------------------------


def test_supported_vid_pids_count() -> None:
    """SUPPORTED_VID_PIDS is a frozenset of exactly 4 (vid, pid) pairs."""
    assert isinstance(SUPPORTED_VID_PIDS, frozenset)
    assert len(SUPPORTED_VID_PIDS) == 4
    assert (0x2341, 0x0043) in SUPPORTED_VID_PIDS  # Uno R3
    assert (0x2341, 0x0069) in SUPPORTED_VID_PIDS  # Uno R4
    assert (0x1A86, 0x7523) in SUPPORTED_VID_PIDS  # CH340
    assert (0x0403, 0x6001) in SUPPORTED_VID_PIDS  # FTDI
