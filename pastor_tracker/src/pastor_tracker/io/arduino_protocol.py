"""Pure protocol layer between Python host and AccelStepper firmware v2.

Mirrors the wire-format constants from
``arduino/stepper_controller/include/protocol.h`` and the line shapes emitted by
``arduino/stepper_controller/src/main.cpp``. Provides:

* Module-level :data:`Final` constants citing the firmware source line for every
  hardware-coupled value (CLAUDE.md rule 6 — no magic numbers).
* :class:`ErrorCode` ``IntEnum`` mirroring ``protocol.h:67-80`` (12 members,
  values 0..11 inclusive of ``NONE = 0`` sentinel).
* Eleven frozen Pydantic v2 event DTOs (``Ready``, ``Feedback``, ``Settings``,
  ``SettingsInfo``, ``Limits``, ``Driver``, ``Reset``, ``Stop``, ``Diag``,
  ``Error``, ``FeedbackHeader``) and the closed :data:`ProtocolEvent` union.
* :func:`parse_line` — pure ``bytes -> ProtocolEvent`` transform with a flat
  prefix-dispatch ladder (≤ 2-level nesting per CLAUDE.md rule 5).
* :class:`ProtocolParseError` — raised on every malformed RX line; never on the
  hot-path inside the orchestrator.

Mutation pattern (CLAUDE.md rule 9): every DTO inherits from
:class:`_Event` which sets ``ConfigDict(frozen=True, extra="forbid")``; mutate
via ``event.model_copy(update={...})`` if needed downstream.

Tiger-style invariants:
  * Empty input or input longer than :data:`MAX_RX_LINE_BYTES` (256) is rejected
    *before* any decode/parse work as a Tampering/DoS guard (T-02-04).
  * Float fields are validated with ``math.isfinite()`` after conversion;
    NaN/inf raises :class:`ProtocolParseError` (T-02-04b — RESEARCH Security
    Domain row 2).
  * ASCII decode is strict; any non-ASCII byte raises
    :class:`ProtocolParseError` (T-02-04c).
  * ``ERROR:N`` with code outside the known 0..11 range is preserved as a raw
    ``int`` for forward-compat (Pitfall 9 in 02-RESEARCH.md).

Firmware citations (verbatim line numbers in the shipped firmware tree):
  * ``main.cpp:417-422`` — boot preamble emits SETTINGS / FB_HEADER / READY in
    that order.
  * ``main.cpp:149-162`` — ``FB:`` line emission.
  * ``main.cpp:63-66``  — ``ERROR:`` line emission.
  * ``main.cpp:235-244`` — ``SETTINGS:`` echo (structured 5-tuple form).
  * ``main.cpp:273-276`` — ``LIMITS:`` echo.

This module is **pure**: it imports no ``serial``, ``threading``, ``asyncio``,
``os`` or ``time`` modules. Plans 02-02 (transport) and 02-03 (orchestrator)
build on top of these symbols without dragging I/O dependencies into the
parser's test surface.
"""
from __future__ import annotations

import math
import re
from enum import IntEnum
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

# ---------------------------------------------------------------------------
# Module-level firmware constants — single source of truth on the Python side.
# Every value is a Final[...] and cites its protocol.h line so the firmware /
# host pairing is auditable in one grep.
# ---------------------------------------------------------------------------

PROTOCOL_VERSION_MAJOR: Final[int] = 2  # protocol.h:12
FIRMWARE_INPUT_BUFFER_SIZE: Final[int] = 48  # protocol.h:39
FIRMWARE_INPUT_BUFFER_USABLE: Final[int] = 47  # protocol.h:39 minus null terminator
FIRMWARE_HEARTBEAT_TIMEOUT_MS: Final[int] = 1_000  # protocol.h:33
FIRMWARE_FEEDBACK_INTERVAL_MS: Final[int] = 20  # protocol.h:36
SEQ_MODULUS: Final[int] = 1 << 32  # uint32 rollover (main.cpp:42 sequence type)
FEEDBACK_SEQ_GAP_WARN_THRESHOLD: Final[int] = 5  # IO-ARD-04
MAX_RX_LINE_BYTES: Final[int] = 256  # T-02-04 DoS guard (firmware never emits > ~80)

# Internal parser literals. Named to avoid PLR2004 magic-value warnings even
# though they are pure structural counts (CLAUDE.md rule 6).
_FB_FIELD_COUNT: Final[int] = 7
_SETTINGS_STRUCTURED_FIELD_COUNT: Final[int] = 5
_LIMITS_FIELD_COUNT: Final[int] = 2
_ERROR_SEPARATOR: Final[str] = " - "
_FB_PREFIX: Final[str] = "FB:"
_FB_HEADER_PREFIX: Final[str] = "FB_HEADER:"
_READY_PREFIX: Final[str] = "READY:v"
_ERROR_PREFIX: Final[str] = "ERROR:"
_SETTINGS_PREFIX: Final[str] = "SETTINGS:"
_LIMITS_PREFIX: Final[str] = "LIMITS:"
_DRIVER_PREFIX: Final[str] = "DRIVER:"
_RESET_OK: Final[str] = "RESET:OK"
_STOP_OK: Final[str] = "STOP:OK"
_DIAG_PREFIX: Final[str] = "DIAG:"
_DIAG_RE: Final[re.Pattern[str]] = re.compile(r"^DIAG: moving (-?\d+) steps$")
_SETTINGS_TEXTUAL_MESSAGES: Final[frozenset[str]] = frozenset(
    {
        "defaults (no valid EEPROM)",
        "loaded from EEPROM",
        "saved to EEPROM",
    }
)
_DRIVER_ENABLED_LITERAL: Final[str] = "ENABLED"
_DRIVER_DISABLED_LITERAL: Final[str] = "DISABLED"
_PREFIX_PREVIEW_BYTES: Final[int] = 16  # for ProtocolParseError context

# AccelPhase is uint8 in firmware (protocol.h:50-55); the wire literal is
# {0,1,2,3}. Expose a matching Literal type for the Feedback DTO's accel_phase.
type AccelPhaseLiteral = Literal[0, 1, 2, 3]


class ErrorCode(IntEnum):
    """Mirrors ``stepper_protocol::ErrorCode`` from ``protocol.h:67-80``.

    ``NONE = 0`` is the firmware's internal "no error" sentinel and is never
    expected on the wire as ``ERROR:0``, but :func:`parse_line` accepts it for
    forward-compatibility (Pitfall 9 in 02-RESEARCH.md).
    """

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
    """Base for all parsed protocol events — frozen + extra-forbidden."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class Ready(_Event):
    """Boot / post-watchdog handshake banner — ``READY:v<N>`` (main.cpp:420-421).

    The parser does NOT enforce protocol-version match; that contract belongs to
    the orchestrator (Plan 02-03). ``version`` is bounded ``[1, 255]`` because
    the firmware emits a ``uint8_t`` (protocol.h:12).
    """

    version: int = Field(ge=1, le=255)


class Feedback(_Event):
    """Periodic motor-state line — ``FB:`` (main.cpp:149-162).

    Field order on the wire: current_angle, target_angle, speed_steps_per_sec,
    is_running (0/1), timestamp_micros, sequence (uint32), accel_phase (0..3).
    """

    current_angle_deg: float
    target_angle_deg: float
    speed_steps_per_sec: float
    is_running: bool
    timestamp_micros: int = Field(ge=0)
    sequence: int = Field(ge=0)
    accel_phase: AccelPhaseLiteral


class FeedbackHeader(_Event):
    """Boot/recovery banner ``FB_HEADER:...`` (main.cpp:419) — informational."""


class Settings(_Event):
    """Structured settings echo — ``SETTINGS:<spd>,<acc>,<p>,<i>,<d>``.

    Emitted by ``handle_settings`` after an ``S:`` command (main.cpp:235-244).
    """

    max_speed: float
    max_accel: float
    pid_p: float
    pid_i: float
    pid_d: float


class SettingsInfo(_Event):
    """Textual ``SETTINGS:`` line — boot defaults / EEPROM load / EEPROM save."""

    message: str = Field(min_length=1)


class Limits(_Event):
    """Echo of software angle limits — ``LIMITS:<min>,<max>`` (main.cpp:273-276)."""

    min_deg: float
    max_deg: float

    @model_validator(mode="after")
    def _max_strictly_greater_than_min(self) -> Limits:
        if self.max_deg <= self.min_deg:
            raise ValueError(
                f"max_deg ({self.max_deg}) must strictly exceed min_deg ({self.min_deg})"
            )
        return self


class Driver(_Event):
    """``DRIVER:ENABLED`` / ``DRIVER:DISABLED`` (main.cpp:318, 323)."""

    enabled: bool


class Reset(_Event):
    """``RESET:OK`` (main.cpp:284) — ack of host ``R`` command.

    NOT a watchdog-reset signal; see Pitfall 2 in 02-RESEARCH.md.
    """


class Stop(_Event):
    """``STOP:OK`` (main.cpp:295) — ack of host ``E`` command."""


class Diag(_Event):
    """``DIAG: moving <N> steps`` (main.cpp:196-198)."""

    steps: int


class Error(_Event):
    """``ERROR:<code> - <message>`` (main.cpp:63-66).

    ``code`` is an :class:`ErrorCode` enum if it matches a known value, else a
    raw ``int`` (forward-compat — Pitfall 9). The parser never crashes on an
    unknown numeric code.
    """

    code: ErrorCode | int
    message: str = Field(min_length=1)


type ProtocolEvent = (
    Ready
    | Feedback
    | Settings
    | SettingsInfo
    | Limits
    | Driver
    | Reset
    | Stop
    | Diag
    | Error
    | FeedbackHeader
)


class ProtocolParseError(Exception):
    """RX line did not match any known prefix or had bad fields. — IO-ARD-04."""


# ---------------------------------------------------------------------------
# Internal parsers — each enforces field count, calls ``_safe_float`` on every
# float (NaN/inf rejected), wraps ValueError/ValidationError in ProtocolParseError
# with ``raise ... from exc`` (ruff B904), ≤ 2-level nesting (CLAUDE.md rule 5).
# ---------------------------------------------------------------------------


def _safe_float(token: str, *, field_name: str) -> float:
    try:
        value = float(token)
    except ValueError as exc:
        raise ProtocolParseError(
            f"{field_name}: not a float: {token!r}"
        ) from exc
    if not math.isfinite(value):
        raise ProtocolParseError(
            f"{field_name}: non-finite float ({token!r}) rejected (T-02-04b)"
        )
    return value


def _safe_int(token: str, *, field_name: str) -> int:
    try:
        return int(token)
    except ValueError as exc:
        raise ProtocolParseError(
            f"{field_name}: not an int: {token!r}"
        ) from exc


def _parse_fb(text: str) -> Feedback:
    payload = text[len(_FB_PREFIX):]
    parts = payload.split(",")
    if len(parts) != _FB_FIELD_COUNT:
        raise ProtocolParseError(
            f"FB: expected {_FB_FIELD_COUNT} fields, got {len(parts)}: {text!r}"
        )
    current_angle = _safe_float(parts[0], field_name="current_angle_deg")
    target_angle = _safe_float(parts[1], field_name="target_angle_deg")
    speed = _safe_float(parts[2], field_name="speed_steps_per_sec")
    is_running_int = _safe_int(parts[3], field_name="is_running")
    timestamp = _safe_int(parts[4], field_name="timestamp_micros")
    sequence = _safe_int(parts[5], field_name="sequence")
    accel = _safe_int(parts[6], field_name="accel_phase")
    try:
        return Feedback(
            current_angle_deg=current_angle,
            target_angle_deg=target_angle,
            speed_steps_per_sec=speed,
            is_running=bool(is_running_int),
            timestamp_micros=timestamp,
            sequence=sequence,
            accel_phase=accel,  # type: ignore[arg-type]
        )
    except ValidationError as exc:
        raise ProtocolParseError(f"FB: validation failed: {exc}") from exc


def _parse_ready(text: str) -> Ready:
    payload = text[len(_READY_PREFIX):]
    version = _safe_int(payload, field_name="version")
    try:
        return Ready(version=version)
    except ValidationError as exc:
        raise ProtocolParseError(f"READY: validation failed: {exc}") from exc


def _parse_error(text: str) -> Error:
    remainder = text[len(_ERROR_PREFIX):]
    if _ERROR_SEPARATOR not in remainder:
        raise ProtocolParseError(
            f"ERROR: missing {_ERROR_SEPARATOR!r} separator: {text!r}"
        )
    code_str, message = remainder.split(_ERROR_SEPARATOR, 1)
    code_int = _safe_int(code_str, field_name="error_code")
    # Pitfall 9: known codes coerced to enum; unknown ints kept raw for forward-compat.
    code: ErrorCode | int = (
        ErrorCode(code_int) if code_int in ErrorCode._value2member_map_ else code_int
    )
    try:
        return Error(code=code, message=message)
    except ValidationError as exc:
        raise ProtocolParseError(f"ERROR: validation failed: {exc}") from exc


def _parse_settings(text: str) -> Settings | SettingsInfo:
    payload = text[len(_SETTINGS_PREFIX):]
    # Discriminator: textual forms have a leading space (" defaults...").
    # Structured form has no leading space ("25000.00,...").
    if payload.startswith(" "):
        message = payload[1:]
        if message not in _SETTINGS_TEXTUAL_MESSAGES:
            raise ProtocolParseError(
                f"SETTINGS: unknown textual message: {message!r}"
            )
        try:
            return SettingsInfo(message=message)
        except ValidationError as exc:
            raise ProtocolParseError(
                f"SETTINGS: SettingsInfo validation failed: {exc}"
            ) from exc
    parts = payload.split(",")
    if len(parts) != _SETTINGS_STRUCTURED_FIELD_COUNT:
        raise ProtocolParseError(
            f"SETTINGS: expected {_SETTINGS_STRUCTURED_FIELD_COUNT} structured "
            f"fields, got {len(parts)}: {text!r}"
        )
    max_speed = _safe_float(parts[0], field_name="max_speed")
    max_accel = _safe_float(parts[1], field_name="max_accel")
    pid_p = _safe_float(parts[2], field_name="pid_p")
    pid_i = _safe_float(parts[3], field_name="pid_i")
    pid_d = _safe_float(parts[4], field_name="pid_d")
    try:
        return Settings(
            max_speed=max_speed,
            max_accel=max_accel,
            pid_p=pid_p,
            pid_i=pid_i,
            pid_d=pid_d,
        )
    except ValidationError as exc:
        raise ProtocolParseError(f"SETTINGS: validation failed: {exc}") from exc


def _parse_limits(text: str) -> Limits:
    payload = text[len(_LIMITS_PREFIX):]
    parts = payload.split(",")
    if len(parts) != _LIMITS_FIELD_COUNT:
        raise ProtocolParseError(
            f"LIMITS: expected {_LIMITS_FIELD_COUNT} fields, got {len(parts)}: {text!r}"
        )
    min_deg = _safe_float(parts[0], field_name="min_deg")
    max_deg = _safe_float(parts[1], field_name="max_deg")
    try:
        return Limits(min_deg=min_deg, max_deg=max_deg)
    except ValidationError as exc:
        raise ProtocolParseError(f"LIMITS: validation failed: {exc}") from exc


def _parse_driver(text: str) -> Driver:
    payload = text[len(_DRIVER_PREFIX):]
    if payload == _DRIVER_ENABLED_LITERAL:
        return Driver(enabled=True)
    if payload == _DRIVER_DISABLED_LITERAL:
        return Driver(enabled=False)
    raise ProtocolParseError(f"DRIVER: unknown literal: {payload!r}")


def _parse_diag(text: str) -> Diag:
    match = _DIAG_RE.match(text)
    if match is None:
        raise ProtocolParseError(f"DIAG: malformed: {text!r}")
    steps = _safe_int(match.group(1), field_name="diag_steps")
    try:
        return Diag(steps=steps)
    except ValidationError as exc:
        raise ProtocolParseError(f"DIAG: validation failed: {exc}") from exc


def parse_line(line: bytes) -> ProtocolEvent:  # noqa: PLR0911, PLR0912
    """Parse one RX line (no trailing newline) into a typed :data:`ProtocolEvent`.

    PLR0911 / PLR0912 are silenced here on purpose: this function is the
    advertised flat prefix-dispatch ladder (CLAUDE.md rule 5 — guard clauses,
    ≤ 2-level nesting). Folding the arms into a lookup dict would either lose
    static-type narrowing on the closed ``ProtocolEvent`` union or hide the
    prefix priority order (e.g. ``FB:`` vs ``FB_HEADER:``).

    Trailing ``\\r``/``\\n``/``\\r\\n`` is stripped before dispatch so callers can
    pass either raw firmware lines or pyserial ``read_until`` output.

    Raises :class:`ProtocolParseError` on:
      * empty input,
      * input longer than :data:`MAX_RX_LINE_BYTES` (T-02-04 DoS guard),
      * non-ASCII bytes,
      * unknown prefixes,
      * any field-count, numeric, finiteness, or DTO-validation failure.
    """
    if not line:
        raise ProtocolParseError("empty line")
    if len(line) > MAX_RX_LINE_BYTES:
        raise ProtocolParseError(
            f"line length {len(line)} exceeds MAX_RX_LINE_BYTES={MAX_RX_LINE_BYTES} "
            f"(T-02-04 DoS guard)"
        )
    stripped = line.rstrip(b"\r\n")
    if not stripped:
        raise ProtocolParseError("line contained only CR/LF")
    try:
        text = stripped.decode("ascii", errors="strict")
    except UnicodeDecodeError as exc:
        raise ProtocolParseError(
            f"non-ASCII bytes in line: {stripped[:_PREFIX_PREVIEW_BYTES]!r}"
        ) from exc

    # Flat prefix-dispatch ladder. Order matters where prefixes share substrings:
    # check FB: before any other "F*" prefix; FB_HEADER: never starts with "FB:".
    if text.startswith(_FB_PREFIX):
        return _parse_fb(text)
    if text.startswith(_FB_HEADER_PREFIX):
        return FeedbackHeader()
    if text.startswith(_READY_PREFIX):
        return _parse_ready(text)
    if text.startswith(_ERROR_PREFIX):
        return _parse_error(text)
    if text.startswith(_SETTINGS_PREFIX):
        return _parse_settings(text)
    if text.startswith(_LIMITS_PREFIX):
        return _parse_limits(text)
    if text.startswith(_DRIVER_PREFIX):
        return _parse_driver(text)
    if text == _RESET_OK:
        return Reset()
    if text == _STOP_OK:
        return Stop()
    if text.startswith(_DIAG_PREFIX):
        return _parse_diag(text)
    raise ProtocolParseError(f"unknown prefix: {text[:_PREFIX_PREVIEW_BYTES]!r}")
