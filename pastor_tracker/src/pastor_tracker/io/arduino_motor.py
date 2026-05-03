"""Asyncio orchestrator for the Arduino stepper firmware (v2).

Bridges the pure parser (arduino_protocol.py) and the transport
(arduino_transport.py) into a working motor link. Owns the only
threading <-> asyncio bridge in the project: a daemon RX thread reads
bytes, parses to ProtocolEvent, then crosses into the asyncio loop
via ``loop.call_soon_threadsafe`` to enqueue a bounded queue.

Concurrency invariants (CLAUDE.md rule 1, RESEARCH.md "Architecture
& Concurrency"):
    * Single writer: only the asyncio loop calls ``transport.write``,
      always behind ``self._tx_lock``.
    * Single reader: only the RX daemon thread calls
      ``transport.read_line``.
    * Bounded RX queue: ``maxsize=256``, drop-oldest + WARN log on full.
    * Mid-session ``Ready`` event = MCU watchdog reset -> re-issue
      settings + limits from Config -> resume.
    * ``Error`` event from firmware halts dispatch; requires manual
      reset (CONTEXT.md Out-of-Scope: no auto-recover).
    * Recovery ack timeout latches :class:`WatchdogResetError`; the
      ``_recover`` task does NOT re-raise (deterministic surface =
      next ``send_*`` call).
    * USB-disconnect / RX-thread serial exception latches
      :class:`LinkLostError` (NOT :class:`FirmwareErrorReceived` with
      :attr:`ErrorCode.NONE` -- that sentinel is the firmware's
      "no error" code per ``protocol.h:68`` and must not be reused
      for host-side events).
"""
from __future__ import annotations

import asyncio
import contextlib
import enum
import threading
from collections.abc import AsyncIterator
from typing import Final

import structlog

from pastor_tracker.config import Config
from pastor_tracker.core.types import MotorCommand
from pastor_tracker.io.arduino_protocol import (
    FEEDBACK_SEQ_GAP_WARN_THRESHOLD,
    FIRMWARE_INPUT_BUFFER_USABLE,
    PROTOCOL_VERSION_MAJOR,
    SEQ_MODULUS,
    Error,
    ErrorCode,
    Feedback,
    FeedbackHeader,
    Limits,
    ProtocolEvent,
    ProtocolParseError,
    Ready,
    Settings,
    SettingsInfo,
    parse_line,
)
from pastor_tracker.io.arduino_transport import (
    ArduinoPortNotFoundError,
    SerialTransport,
)

# Re-export so callers can `from pastor_tracker.io.arduino_motor import
# ArduinoPortNotFoundError` and have one orchestrator-import surface for
# Phase 5 / Phase 6 consumers.
__all__ = [
    "ArduinoError",
    "ArduinoMotor",
    "ArduinoPortNotFoundError",
    "FirmwareErrorReceived",
    "HandshakeTimeoutError",
    "LinkLostError",
    "ProtocolVersionMismatchError",
    "WatchdogResetError",
    "_MotorState",
]

# ---------------------------------------------------------------------------
# Module-level Final constants -- every literal cited (CLAUDE.md rule 6).
# ---------------------------------------------------------------------------

_TX_NEWLINE: Final[bytes] = b"\n"
_MS_PER_SEC: Final[float] = 1_000.0
_RX_QUEUE_MAX_SIZE: Final[int] = 256                 # CONTEXT.md drop-oldest bound
_RX_JOIN_TIMEOUT_SEC: Final[float] = 1.0             # close() RX-thread join budget
_RX_THREAD_READ_TIMEOUT_SEC: Final[float] = 0.1      # short tick -> responsive stop_event
_DECIMAL_DEG_PRECISION: Final[int] = 3               # Pitfall 5 -- keep TX line <= 47 bytes
# WARN 6 -- drain trailing "SETTINGS: saved to EEPROM" SettingsInfo line.
_RECOVERY_TRAILING_DRAIN_SEC: Final[float] = 0.1


class _MotorState(enum.Enum):
    """Internal state machine for :class:`ArduinoMotor`."""

    DISCONNECTED = "disconnected"
    HANDSHAKING = "handshaking"
    RUNNING = "running"
    RECOVERING = "recovering"
    FAULTED = "faulted"
    CLOSED = "closed"


# ---------------------------------------------------------------------------
# Exception hierarchy: Root ArduinoError + 5 subclasses = 6 class declarations.
# ---------------------------------------------------------------------------


class ArduinoError(Exception):
    """Root for all arduino_motor-originated errors."""


class HandshakeTimeoutError(ArduinoError):
    """READY:v<N> not received within arduino_ready_timeout_sec. -- IO-ARD-02."""


class ProtocolVersionMismatchError(ArduinoError):
    """READY:v<N> received but N != arduino_protocol_version. -- IO-ARD-02 / CFG-04."""


class WatchdogResetError(ArduinoError):
    """Mid-session READY:v2 recovery failed (settings/limits ack timeout). -- IO-ARD-06."""


class FirmwareErrorReceived(ArduinoError):
    """ERROR:<code> from firmware -- halts tracking. -- IO-ARD-07."""

    def __init__(self, code: ErrorCode | int, message: str) -> None:
        super().__init__(f"firmware ERROR:{int(code)} -- {message}")
        self.code: ErrorCode | int = code
        self.message: str = message


class LinkLostError(ArduinoError):
    """USB disconnect mid-session. -- IO-ARD-07 (ERROR-class halt).

    Distinct surface from :class:`FirmwareErrorReceived`: the firmware did
    not emit an ERROR; the host's serial link itself died. Do NOT reuse
    ``FirmwareErrorReceived(ErrorCode.NONE, ...)`` for this --
    :attr:`ErrorCode.NONE` is the firmware's "no error" sentinel
    (``protocol.h:68``) and must not appear on host-originated faults.
    """


# ---------------------------------------------------------------------------
# Orchestrator.
# ---------------------------------------------------------------------------


class ArduinoMotor:
    """Asyncio orchestrator for the Arduino stepper firmware v2.

    Public surface (consumed by Plan 5 ``command_dispatcher.py`` and
    Phase 6 ``pipeline.py``):

    * :meth:`start` -- run boot handshake, spawn RX thread + heartbeat task.
    * :meth:`close` -- cancel heartbeat, join RX thread, close transport.
    * Nine ``send_*`` methods -- typed TX wrappers per firmware command tag.
    * :meth:`events` -- async iterator over the bounded RX queue.
    * :attr:`state`, :attr:`is_dispatch_paused` -- read-only inspection.
    """

    def __init__(self, transport: SerialTransport, config: Config) -> None:
        self._transport: SerialTransport = transport
        self._config: Config = config
        self._tx_lock = asyncio.Lock()
        self._rx_queue: asyncio.Queue[ProtocolEvent] = asyncio.Queue(
            maxsize=_RX_QUEUE_MAX_SIZE
        )
        self._stop_event: threading.Event = threading.Event()
        self._dispatch_paused: bool = False
        self._state: _MotorState = _MotorState.DISCONNECTED
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._rx_thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._last_seq: int | None = None
        # _latched_error may be FirmwareErrorReceived OR LinkLostError
        # OR WatchdogResetError. The next public send_* call (except
        # send_emergency_stop) re-raises it in its concrete subclass form.
        self._latched_error: ArduinoError | None = None
        self._recover_task: asyncio.Task[None] | None = None
        self._logger = structlog.get_logger(module="arduino_motor")

    # -----------------------------------------------------------------------
    # Read-only properties.
    # -----------------------------------------------------------------------

    @property
    def state(self) -> _MotorState:
        """Current internal motor state. Read-only."""
        return self._state

    @property
    def is_dispatch_paused(self) -> bool:
        """``True`` while :meth:`send_motor_angle` is silenced (recovery)."""
        return self._dispatch_paused

    # -----------------------------------------------------------------------
    # Lifecycle.
    # -----------------------------------------------------------------------

    async def start(self) -> None:
        """Run boot handshake then spawn RX thread + heartbeat task."""
        if self._state is not _MotorState.DISCONNECTED:
            raise ArduinoError(
                f"start() called twice (state={self._state.value})"
            )
        self._loop = asyncio.get_running_loop()
        self._state = _MotorState.HANDSHAKING
        self._logger.info(
            "handshake_started",
            baud=self._config.arduino_baud,
            expected_version=PROTOCOL_VERSION_MAJOR,
        )
        start_time = self._loop.time()
        await self._wait_for_ready()
        elapsed_ms = (self._loop.time() - start_time) * _MS_PER_SEC
        self._logger.info(
            "handshake_complete",
            version=PROTOCOL_VERSION_MAJOR,
            elapsed_ms=round(elapsed_ms, 2),
        )
        self._state = _MotorState.RUNNING
        self._dispatch_paused = False
        self._rx_thread = threading.Thread(
            target=self._rx_loop, name="arduino-rx", daemon=True
        )
        self._rx_thread.start()
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        self._logger.info(
            "heartbeat_started",
            interval_ms=self._config.arduino_heartbeat_interval_ms,
        )

    async def close(self) -> None:
        """Cancel heartbeat + recover task, signal RX thread, close transport.

        Order matters: cancel any in-flight ``_recover`` task FIRST so it
        cannot race the transport close with a write. Mirrors the
        heartbeat-task shutdown pattern. Without this, an orphan
        ``_recover`` running concurrently would write to a closed
        transport and surface as ``Task exception was never retrieved``,
        which under ``filterwarnings=["error"]`` is a non-deterministic
        test failure (C-01).
        """
        if self._recover_task is not None:
            self._recover_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._recover_task
            self._recover_task = None
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._heartbeat_task
            self._heartbeat_task = None
        self._stop_event.set()
        rx_thread = self._rx_thread
        if rx_thread is not None:
            await asyncio.to_thread(rx_thread.join, _RX_JOIN_TIMEOUT_SEC)
            self._logger.info(
                "rx_thread_exited", clean=not rx_thread.is_alive()
            )
            self._rx_thread = None
        self._transport.close()
        self._state = _MotorState.CLOSED

    # -----------------------------------------------------------------------
    # Boot handshake.
    # -----------------------------------------------------------------------

    async def _wait_for_ready(self) -> None:
        """Read until ``READY:v<expected>`` or raise.

        Skips :class:`SettingsInfo` and :class:`FeedbackHeader` (Pitfall 1
        -- the 3-line preamble).
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._config.arduino_ready_timeout_sec
        expected = PROTOCOL_VERSION_MAJOR
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0.0:
                self._logger.error(
                    "handshake_timeout",
                    timeout_sec=self._config.arduino_ready_timeout_sec,
                )
                raise HandshakeTimeoutError(
                    f"no READY:v{expected} within "
                    f"{self._config.arduino_ready_timeout_sec}s"
                )
            line = await asyncio.wait_for(
                asyncio.to_thread(self._transport.read_line, remaining),
                timeout=remaining + 0.05,
            )
            if line is None:
                continue
            try:
                event = parse_line(line)
            except ProtocolParseError as exc:
                self._logger.warning(
                    "malformed_line",
                    line=line[:32].decode("ascii", errors="replace"),
                    error=str(exc),
                )
                continue
            if isinstance(event, (FeedbackHeader, SettingsInfo)):
                continue  # benign preamble
            if isinstance(event, Ready):
                if event.version != expected:
                    self._logger.error(
                        "handshake_version_mismatch",
                        expected=f"READY:v{expected}",
                        received=f"READY:v{event.version}",
                    )
                    raise ProtocolVersionMismatchError(
                        f"expected READY:v{expected}, got READY:v{event.version}"
                    )
                return
            self._logger.warning(
                "handshake_unexpected_line", event_type=type(event).__name__
            )

    # -----------------------------------------------------------------------
    # RX thread + loop-side bridge.
    # -----------------------------------------------------------------------

    def _rx_loop(self) -> None:
        """Daemon-thread RX loop. Translates serial errors to LinkLostError.

        The single bare-Exception catch below (``noqa: BLE001``) is
        justified: ``serial.SerialException`` from ``read_line`` on USB
        unplug or OS handle close MUST translate to a typed orchestrator-
        level error rather than crash the daemon thread silently.
        """
        while not self._stop_event.is_set():
            try:
                line = self._transport.read_line(_RX_THREAD_READ_TIMEOUT_SEC)
            except Exception as exc:  # noqa: BLE001 -- documented translator
                # RX thread bridge: serial errors (USB unplug, OS handle
                # close) translate to LinkLostError on the loop thread;
                # never silently swallow.
                if self._loop is not None and not self._stop_event.is_set():
                    self._loop.call_soon_threadsafe(self._on_link_lost, exc)
                return
            if line is None:
                continue
            try:
                event = parse_line(line)
            except ProtocolParseError as exc:
                self._logger.warning(
                    "malformed_line",
                    line=line[:32].decode("ascii", errors="replace"),
                    error=str(exc),
                )
                continue
            if self._loop is not None:
                self._loop.call_soon_threadsafe(self._on_rx_event, event)

    def _on_rx_event(self, event: ProtocolEvent) -> None:
        """Loop-thread handler for parsed events."""
        if isinstance(event, Feedback):
            self._check_seq_gap(event)
            self._enqueue(event)
            return
        if isinstance(event, Ready) and self._state is _MotorState.RUNNING:
            # Mid-session Ready = MCU watchdog reset signal. Don't enqueue
            # this Ready into the public events queue -- it is a control
            # signal, not a payload event.
            self._logger.warning("watchdog_reset_detected")
            self._recover_task = asyncio.create_task(self._recover())
            return
        if isinstance(event, Error):
            code_name = (
                event.code.name
                if isinstance(event.code, ErrorCode)
                else "Unknown"
            )
            self._latched_error = FirmwareErrorReceived(
                event.code, event.message
            )
            self._dispatch_paused = True
            self._state = _MotorState.FAULTED
            self._logger.error(
                "error_received",
                code=int(event.code),
                code_name=code_name,
                message=event.message,
            )
            self._enqueue(event)
            return
        self._enqueue(event)

    def _enqueue(self, event: ProtocolEvent) -> None:
        """Drop-oldest semantics on a full bounded queue."""
        if self._rx_queue.full():
            with contextlib.suppress(asyncio.QueueEmpty):
                self._rx_queue.get_nowait()
            self._logger.warning(
                "rx_queue_full", dropped_event_type=type(event).__name__
            )
        self._rx_queue.put_nowait(event)

    def _check_seq_gap(self, fb: Feedback) -> None:
        """Mod-2^32 rollover-safe gap detection (Pitfall 7)."""
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

    def _on_link_lost(self, exc: Exception) -> None:
        """Loop-thread latcher for RX-thread serial errors.

        Uses :class:`LinkLostError`, NOT
        ``FirmwareErrorReceived(ErrorCode.NONE, ...)`` --
        :attr:`ErrorCode.NONE` is the firmware's "no error" sentinel
        (``protocol.h:68``); reusing it for a host-side link-lost event
        would corrupt the typed contract that IO-ARD-07 enforces
        (``FirmwareErrorReceived`` means "firmware emitted an ERROR
        line"; ``LinkLostError`` means "the serial link itself died").
        These are DISTINCT failure surfaces.
        """
        self._state = _MotorState.FAULTED
        self._dispatch_paused = True
        self._latched_error = LinkLostError(f"link_lost: {exc}")
        self._logger.error("link_lost", reason=str(exc))

    # -----------------------------------------------------------------------
    # Heartbeat.
    # -----------------------------------------------------------------------

    async def _heartbeat_loop(self) -> None:
        """Emit ``b"Q\\n"`` every ``arduino_heartbeat_interval_ms``.

        Exits gracefully when the link has faulted (latched
        :class:`ArduinoError` from a firmware ERROR or USB unplug) -- we
        cannot keep the firmware watchdog satisfied if the link is dead,
        and continuing to call :meth:`send_query` would raise the latched
        error every tick.
        """
        interval_sec = (
            self._config.arduino_heartbeat_interval_ms / _MS_PER_SEC
        )
        try:
            while True:
                try:
                    await self.send_query()
                except ArduinoError as exc:
                    self._logger.info("heartbeat_halted", reason=str(exc))
                    return
                await asyncio.sleep(interval_sec)
        except asyncio.CancelledError:
            self._logger.info("heartbeat_stopped")
            raise

    # -----------------------------------------------------------------------
    # Watchdog recovery.
    # -----------------------------------------------------------------------

    async def _recover(self) -> None:
        """Re-issue settings + limits after a mid-session Ready event.

        Pinned timeout-error class is :class:`WatchdogResetError`; on
        ack timeout we LATCH the error, set ``_state = FAULTED``, log
        ``watchdog_recovery_failed``, and RETURN. We do NOT re-raise --
        re-raising leaves the exception un-awaited at GC time, which
        under ``filterwarnings = ["error"]`` becomes a non-deterministic
        test failure. Tiger-style requires a deterministic surface =
        the next ``send_*`` call raises the latched error.
        """
        self._state = _MotorState.RECOVERING
        self._dispatch_paused = True
        self._logger.warning(
            "watchdog_reset_detected",
            action="re-issuing settings+limits",
        )
        timeout = self._config.arduino_ready_timeout_sec
        try:
            await self.send_settings(
                self._config.motor_max_speed_steps_per_sec,
                self._config.motor_max_accel_steps_per_sec2,
                0.0,
                0.0,
                0.0,
            )
            await self._wait_for_event(
                Settings, timeout=timeout, stage="settings"
            )
            await self.send_limits(
                self._config.motor_angle_min_deg,
                self._config.motor_angle_max_deg,
            )
            await self._wait_for_event(
                Limits, timeout=timeout, stage="limits"
            )
        except (asyncio.TimeoutError, asyncio.QueueEmpty) as exc:  # noqa: UP041
            self._latched_error = WatchdogResetError(
                f"recovery ack timeout: {exc}"
            )
            self._state = _MotorState.FAULTED
            self._logger.error(
                "watchdog_recovery_failed",
                reason="ack_timeout",
                stage_exc=str(exc),
            )
            return
        except LinkLostError as exc:
            # USB unplug mid-recovery: preserve the typed surface so the
            # next send_* raises LinkLostError (not WatchdogResetError).
            self._latched_error = exc
            self._state = _MotorState.FAULTED
            self._logger.error(
                "watchdog_recovery_failed",
                reason="link_lost",
                stage_exc=str(exc),
            )
            return
        except ValueError as exc:
            # 47-byte TX buffer guard tripped while re-issuing settings/limits
            # (e.g., config drift). Latch deterministically.
            self._latched_error = WatchdogResetError(
                f"recovery write error: {exc}"
            )
            self._state = _MotorState.FAULTED
            self._logger.error(
                "watchdog_recovery_failed",
                reason="invalid_payload",
                stage_exc=str(exc),
            )
            return
        except asyncio.CancelledError:
            # close() awaits the cancellation via gather(return_exceptions=True);
            # propagate so cancellation semantics stay correct (C-01 + C-02).
            raise
        # WARN 6 -- drain ONE trailing "SETTINGS: saved to EEPROM"
        # SettingsInfo so the public events queue does NOT receive
        # recovery noise (RESEARCH line 506).
        with contextlib.suppress(asyncio.TimeoutError):
            trailing = await self._wait_for_event(
                SettingsInfo,
                timeout=_RECOVERY_TRAILING_DRAIN_SEC,
                stage="trailing_settings_info",
            )
            if isinstance(trailing, SettingsInfo):
                self._logger.debug(
                    "recovery_drained_trailing_settings_info",
                    message=trailing.message,
                )
        self._dispatch_paused = False
        self._state = _MotorState.RUNNING
        self._logger.warning("watchdog_recovery_complete")

    async def _wait_for_event(
        self,
        event_type: type[ProtocolEvent],
        *,
        timeout: float,
        stage: str,
    ) -> ProtocolEvent:
        """Drain queue until an event of ``event_type`` arrives or timeout.

        Discriminates :class:`Settings` (5-tuple) from
        :class:`SettingsInfo` (textual) -- the recovery ack-watcher must
        wait for the structured form per RESEARCH lines 504-506.
        Non-matching events (e.g. stale FBs) are discarded.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0.0:
                raise TimeoutError(
                    f"timeout waiting for {event_type.__name__} "
                    f"(stage={stage})"
                )
            ev = await asyncio.wait_for(
                self._rx_queue.get(), timeout=remaining
            )
            if isinstance(ev, event_type):
                return ev
            self._logger.debug(
                "recovery_discarded_event",
                stage=stage,
                event_type=type(ev).__name__,
            )

    # -----------------------------------------------------------------------
    # TX core -- single asyncio writer.
    # -----------------------------------------------------------------------

    async def _send_raw(self, line: bytes) -> None:
        """Single TX entry point. Pre-send buffer-size guard (Pitfall 5)."""
        if len(line) + 1 > FIRMWARE_INPUT_BUFFER_USABLE:
            raise ValueError(
                f"TX line {len(line)} > {FIRMWARE_INPUT_BUFFER_USABLE}"
            )
        loop = asyncio.get_running_loop()
        async with self._tx_lock:
            await loop.run_in_executor(
                None, self._transport.write, line + _TX_NEWLINE
            )

    def _raise_if_latched(self) -> None:
        """Re-raise any latched error in its concrete subclass form.

        Single point that surfaces all three latched-error types --
        :class:`FirmwareErrorReceived`, :class:`LinkLostError`,
        :class:`WatchdogResetError` -- so callers can ``except`` them
        discriminately (no flattening to a generic
        :class:`ArduinoError`).
        """
        if self._latched_error is not None:
            raise self._latched_error

    # -----------------------------------------------------------------------
    # Public TX wrappers (one per firmware command tag).
    # -----------------------------------------------------------------------

    async def send_motor_angle(self, command: MotorCommand) -> None:
        """``M:<deg>`` -- the hot-path.

        Latched-error gate is checked BEFORE the pause gate so that a fault
        that happened to also pause dispatch (FAULTED state sets both)
        surfaces the typed exception rather than silently returning. A
        plain RECOVERING-state pause (no latched error) still silences
        cleanly.
        """
        self._raise_if_latched()
        if self._dispatch_paused:
            return
        payload = (
            f"M:{command.target_angle_deg:.{_DECIMAL_DEG_PRECISION}f}"
        ).encode("ascii")
        await self._send_raw(payload)

    async def send_settings(
        self,
        max_speed: float,
        max_accel: float,
        pid_p: float,
        pid_i: float,
        pid_d: float,
    ) -> None:
        """``S:<spd>,<acc>,<p>,<i>,<d>`` -- structured settings command."""
        # Internal call from _recover bypasses pause; external callers
        # respect the latched-error gate.
        self._raise_if_latched()
        payload = (
            f"S:{max_speed:.3f},{max_accel:.3f},"
            f"{pid_p:.3f},{pid_i:.3f},{pid_d:.3f}"
        ).encode("ascii")
        await self._send_raw(payload)

    async def send_limits(self, min_deg: float, max_deg: float) -> None:
        """``L:<min>,<max>`` -- software angle limits."""
        self._raise_if_latched()
        payload = f"L:{min_deg:.3f},{max_deg:.3f}".encode("ascii")
        await self._send_raw(payload)

    async def send_reset(self) -> None:
        """``R`` -- soft reset command."""
        self._raise_if_latched()
        await self._send_raw(b"R")

    async def send_query(self) -> None:
        """``Q`` -- heartbeat / status query.

        Pause-respect=False (heartbeat must always work). Latched-error
        gate is honoured to surface faults to the heartbeat task too.
        """
        self._raise_if_latched()
        await self._send_raw(b"Q")

    async def send_emergency_stop(self) -> None:
        """``E`` -- emergency stop. ALWAYS bypasses pause and latched error.

        Safety: the e-stop command must work even after a fault.
        """
        await self._send_raw(b"E")

    async def send_home(self) -> None:
        """``H`` -- home command."""
        self._raise_if_latched()
        await self._send_raw(b"H")

    async def send_driver(self, enabled: bool) -> None:
        """``X:1`` (enable) / ``X:0`` (disable) -- driver enable command."""
        self._raise_if_latched()
        payload = b"X:1" if enabled else b"X:0"
        await self._send_raw(payload)

    async def send_diagnostic_steps(self, steps: int) -> None:
        """``D:<steps>`` -- diagnostic move command."""
        self._raise_if_latched()
        payload = f"D:{steps}".encode("ascii")
        await self._send_raw(payload)

    # -----------------------------------------------------------------------
    # RX surface.
    # -----------------------------------------------------------------------

    async def events(self) -> AsyncIterator[ProtocolEvent]:
        """Async-iterable view of the bounded RX queue.

        Caller breaks the loop on whatever sentinel they want (e.g.
        seeing an :class:`Error` event).
        """
        while True:
            ev = await self._rx_queue.get()
            yield ev
