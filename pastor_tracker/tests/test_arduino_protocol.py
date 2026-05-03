"""Parser tests for ``pastor_tracker.io.arduino_protocol`` (IO-ARD-04 / TEST-04).

100 % branch + line coverage gate per `.planning/phases/02-arduino-i-o/02-VALIDATION.md`.
The parser is a pure ``bytes -> ProtocolEvent`` transform, so these tests need
no fakes, no fixtures, no mocks — just direct table-driven asserts.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from pastor_tracker.io.arduino_protocol import (
    MAX_RX_LINE_BYTES,
    Diag,
    Driver,
    Error,
    ErrorCode,
    Feedback,
    FeedbackHeader,
    Limits,
    ProtocolParseError,
    Ready,
    Reset,
    Settings,
    SettingsInfo,
    Stop,
    parse_line,
)

# ---------------------------------------------------------------------------
# Group A — well-formed table tests, one parametrize block per prefix.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line, expected",
    [
        (b"READY:v2", Ready(version=2)),
        (b"READY:v3", Ready(version=3)),
        (b"READY:v255", Ready(version=255)),
    ],
)
def test_parse_ready(line: bytes, expected: Ready) -> None:
    """IO-ARD-04: READY:v<N> with N in [1, 255] decodes to Ready(version=N)."""
    assert parse_line(line) == expected


@pytest.mark.parametrize(
    "line, expected",
    [
        (
            b"FB:1.50,2.00,1234.5,1,123456,42,2",
            Feedback(
                current_angle_deg=1.5,
                target_angle_deg=2.0,
                speed_steps_per_sec=1234.5,
                is_running=True,
                timestamp_micros=123456,
                sequence=42,
                accel_phase=2,
            ),
        ),
        (
            b"FB:-90.00,-90.00,0.00,0,0,0,0",
            Feedback(
                current_angle_deg=-90.0,
                target_angle_deg=-90.0,
                speed_steps_per_sec=0.0,
                is_running=False,
                timestamp_micros=0,
                sequence=0,
                accel_phase=0,
            ),
        ),
        (
            b"FB:0.00,5.00,9999.99,1,1000000,4294967295,3",
            Feedback(
                current_angle_deg=0.0,
                target_angle_deg=5.0,
                speed_steps_per_sec=9999.99,
                is_running=True,
                timestamp_micros=1_000_000,
                sequence=4_294_967_295,
                accel_phase=3,
            ),
        ),
    ],
)
def test_parse_fb(line: bytes, expected: Feedback) -> None:
    """IO-ARD-04: FB:<7-tuple> decodes; covers negative angles + accel_phase=3."""
    assert parse_line(line) == expected


def test_parse_fb_header() -> None:
    """IO-ARD-04: FB_HEADER:... boot/recovery banner decodes to FeedbackHeader()."""
    line = (
        b"FB_HEADER:currentAngle,targetAngle,speed,"
        b"isRunning,timestampMicros,sequence,accelState"
    )
    assert parse_line(line) == FeedbackHeader()


@pytest.mark.parametrize(
    "line, expected_message",
    [
        (b"SETTINGS: defaults (no valid EEPROM)", "defaults (no valid EEPROM)"),
        (b"SETTINGS: loaded from EEPROM", "loaded from EEPROM"),
        (b"SETTINGS: saved to EEPROM", "saved to EEPROM"),
    ],
)
def test_parse_settings_textual(line: bytes, expected_message: str) -> None:
    """IO-ARD-04: 3 textual SETTINGS forms decode to SettingsInfo."""
    assert parse_line(line) == SettingsInfo(message=expected_message)


def test_parse_settings_structured() -> None:
    """IO-ARD-04: structured 5-tuple SETTINGS decodes to Settings."""
    line = b"SETTINGS:25000.00,12500.00,1.00,0.00,0.10"
    assert parse_line(line) == Settings(
        max_speed=25000.0, max_accel=12500.0, pid_p=1.0, pid_i=0.0, pid_d=0.1
    )


def test_parse_limits() -> None:
    """IO-ARD-04: LIMITS:<min>,<max> decodes to Limits."""
    assert parse_line(b"LIMITS:-90.00,90.00") == Limits(min_deg=-90.0, max_deg=90.0)


@pytest.mark.parametrize(
    "line, enabled",
    [
        (b"DRIVER:ENABLED", True),
        (b"DRIVER:DISABLED", False),
    ],
)
def test_parse_driver(line: bytes, enabled: bool) -> None:
    """IO-ARD-04: DRIVER:ENABLED / DRIVER:DISABLED decode to Driver."""
    assert parse_line(line) == Driver(enabled=enabled)


def test_parse_reset() -> None:
    """IO-ARD-04: RESET:OK decodes to Reset() ack of host R command."""
    assert parse_line(b"RESET:OK") == Reset()


def test_parse_stop() -> None:
    """IO-ARD-04: STOP:OK decodes to Stop() ack of host E command."""
    assert parse_line(b"STOP:OK") == Stop()


@pytest.mark.parametrize(
    "line, steps",
    [
        (b"DIAG: moving 100 steps", 100),
        (b"DIAG: moving -200 steps", -200),
        (b"DIAG: moving 0 steps", 0),
    ],
)
def test_parse_diag(line: bytes, steps: int) -> None:
    """IO-ARD-04: DIAG: moving <N> steps decodes to Diag(steps=N)."""
    assert parse_line(line) == Diag(steps=steps)


# ---------------------------------------------------------------------------
# Group B — ErrorCode parametrize. Hits all 12 known codes + one unknown int
# (forward-compat per Pitfall 9 in 02-RESEARCH.md).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code, name",
    [(c.value, c.name) for c in ErrorCode],
    ids=[c.name for c in ErrorCode],
)
def test_parse_error_known_codes(code: int, name: str) -> None:
    """IO-ARD-04: every ErrorCode 0..11 decodes to Error(code=ErrorCode(N))."""
    line = f"ERROR:{code} - {name}".encode("ascii")
    event = parse_line(line)
    assert isinstance(event, Error)
    assert event.code == ErrorCode(code)
    assert isinstance(event.code, ErrorCode)
    assert event.message == name


def test_parse_error_unknown_code_forward_compat() -> None:
    """Pitfall 9: unknown ERROR code preserved as raw int (no parser crash)."""
    event = parse_line(b"ERROR:99 - future code")
    assert isinstance(event, Error)
    assert event.code == 99
    assert not isinstance(event.code, ErrorCode)
    assert event.message == "future code"


def test_parse_error_zero_sentinel_accepted() -> None:
    """Pitfall 9: ERROR:0 must be accepted even though firmware never emits it."""
    event = parse_line(b"ERROR:0 - sentinel")
    assert isinstance(event, Error)
    assert event.code is ErrorCode.NONE
    assert event.message == "sentinel"


# ---------------------------------------------------------------------------
# Group C — malformed lines raise ProtocolParseError.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "malformed",
    [
        # Empty / unknown shapes.
        b"",
        b"\r\n",  # CR/LF only — empties after rstrip
        b"GIBBERISH",
        # FB malformed paths.
        b"FB:1.5,2.0",  # too few fields
        b"FB:nan,2.0,3.0,1,4,5,0",  # NaN — T-02-04
        b"FB:1.0,2.0,inf,1,4,5,0",  # +inf
        b"FB:1.0,2.0,-inf,1,4,5,0",  # -inf
        b"FB:abc,2.0,3.0,1,4,5,0",  # non-numeric float
        b"FB:1.0,2.0,3.0,xyz,4,5,0",  # non-int int field
        b"FB:1.0,2.0,3.0,1,4,5,99",  # accel_phase out of Literal[0..3]
        # READY malformed paths.
        b"READY:vXX",  # non-numeric version
        b"READY:v0",  # version below ge=1
        b"READY:v256",  # version above le=255
        # ERROR malformed paths.
        b"ERROR: missing code",  # int parse fails on " missing code"
        b"ERROR:11",  # no separator
        b"ERROR:abc - msg",  # non-numeric code
        b"ERROR:1 - ",  # empty message (min_length=1)
        # SETTINGS malformed paths.
        b"SETTINGS: bogus message",  # textual outside allowlist
        b"SETTINGS:1.0,2.0,3.0",  # structured wrong field count
        b"SETTINGS:abc,2.0,3.0,4.0,5.0",  # non-numeric structured float
        b"SETTINGS:nan,2.0,3.0,4.0,5.0",  # NaN structured float
        # LIMITS malformed paths.
        b"LIMITS:1.0",  # too few fields
        b"LIMITS:abc,2.0",  # non-numeric float
        b"LIMITS:90.0,-90.0",  # max <= min — model_validator
        b"LIMITS:0.0,0.0",  # equal — model_validator
        # DRIVER malformed.
        b"DRIVER:HALFON",  # neither ENABLED nor DISABLED
        # DIAG malformed.
        b"DIAG: moving abc steps",  # non-numeric
        b"DIAG: not a diag line",  # regex no match
        # T-02-04 DoS guard — over-long line.
        b"X" * (MAX_RX_LINE_BYTES + 1),
        # T-02-04c — non-ASCII bytes.
        b"READY:v\xff",
    ],
)
def test_parse_line_malformed_raises(malformed: bytes) -> None:
    """Every malformed shape must raise ProtocolParseError (T-02-04 containment)."""
    with pytest.raises(ProtocolParseError):
        parse_line(malformed)


# ---------------------------------------------------------------------------
# Group D — frozen + extra-forbid + cross-field invariants on the DTOs.
# ---------------------------------------------------------------------------


def test_dtos_are_frozen() -> None:
    """ConfigDict(frozen=True) — assigning a field after construction raises."""
    ready = Ready(version=2)
    with pytest.raises(ValidationError):
        ready.version = 3  # type: ignore[misc]


def test_dtos_reject_extra_fields() -> None:
    """ConfigDict(extra='forbid') — unknown kwargs raise ValidationError."""
    with pytest.raises(ValidationError):
        Ready(version=2, foo=1)  # type: ignore[call-arg]


def test_limits_rejects_inverted_range_via_construction() -> None:
    """Limits.@model_validator: max_deg must strictly exceed min_deg."""
    with pytest.raises(ValidationError, match="max_deg"):
        Limits(min_deg=90.0, max_deg=-90.0)


def test_limits_rejects_equal_range_via_construction() -> None:
    """Limits invariant is strict (>) not loose (>=) — equal values reject."""
    with pytest.raises(ValidationError, match="max_deg"):
        Limits(min_deg=0.0, max_deg=0.0)


# ---------------------------------------------------------------------------
# Group E — CRLF stripping.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        b"READY:v2\n",
        b"READY:v2\r",
        b"READY:v2\r\n",
    ],
)
def test_crlf_stripped_before_dispatch(line: bytes) -> None:
    """parse_line strips trailing CR/LF/CRLF so callers can pass raw read_until output."""
    assert parse_line(line) == Ready(version=2)


# ---------------------------------------------------------------------------
# Group F — DoS guard (T-02-04). Length is checked BEFORE decode to bound work.
# ---------------------------------------------------------------------------


def test_max_rx_line_bytes_boundary_accepts_at_limit() -> None:
    """A 256 B line is accepted (boundary on inclusive limit)."""
    # Build a line that is exactly MAX_RX_LINE_BYTES and parses to Reset.
    padding_len = MAX_RX_LINE_BYTES - len(b"RESET:OK")
    # Pad with trailing spaces *inside* a textual message would be parsed by
    # _parse_settings; instead we exploit CRLF stripping by trailing newlines,
    # which the parser tolerates. Use a CR/LF tail to fill bytes without
    # introducing a non-ASCII byte.
    padded = b"RESET:OK" + (b"\r" * padding_len)
    assert len(padded) == MAX_RX_LINE_BYTES
    assert parse_line(padded) == Reset()


def test_max_rx_line_bytes_rejects_over_limit() -> None:
    """A line of MAX_RX_LINE_BYTES + 1 is rejected before any decode work."""
    with pytest.raises(ProtocolParseError, match="MAX_RX_LINE_BYTES"):
        parse_line(b"X" * (MAX_RX_LINE_BYTES + 1))
