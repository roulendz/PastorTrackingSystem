"""Asyncio orchestrator for the OBS Virtual Camera frame source.

Bridges the blocking ``cv2.VideoCapture`` (hidden behind the
:class:`VideoSource` Protocol DI seam) and the asyncio event loop into a
working camera link. Mirrors :mod:`pastor_tracker.io.arduino_motor` almost
line-for-line: a daemon capture thread runs the blocking ``read()`` loop,
constructs a typed :class:`pastor_tracker.core.types.Frame` DTO inside the
thread (timestamp + shape validation off the asyncio hot path), and bridges
to the loop via ``loop.call_soon_threadsafe(_enqueue_frame, frame)`` against
a bounded :class:`asyncio.Queue` with drop-oldest semantics.

Concurrency invariants (mirror Phase 2 -- arduino_motor.py:9-27):
    * Single producer: only the capture thread (lives in Plan 03-02) calls
      ``source.read()``.
    * Single consumer: only the asyncio loop drains ``frames_queue``.
    * Bounded queue: :data:`_FRAMES_QUEUE_MAX_SIZE` = 64; drop-oldest +
      ``frames_queue_full`` WARN on full.
    * Boot-only one-shot DirectShow enumeration (no background re-poll).
    * Stall detection at the capture thread; stale-drop at the consumer.
    * One-shot resolution fallback gated to a 2 s warmup window; never
      re-promote.
    * The two-factory injection pattern (``video_source_factory``,
      ``filter_graph_factory``) defends Pitfall 10 (FilterGraph
      ``CoInitialize`` race in pytest workers).

Sibling split (this plan vs. Plan 03-02):
    * Plan 03-01 (this file): VideoSource Protocol + OpenCvVideoSource real
      impl + discover_obs_camera_index + CameraError hierarchy + _CamState
      enum + _P95Detector helper. All seam + discovery + helpers.
    * Plan 03-02 (appended below the marker): ObsCamera orchestrator class
      (``start``/``stop``/``frames``/``_capture_loop``/``_attempt_reopen``/
      ``_maybe_fallback``/``_on_capture_failed``/``_enqueue_frame``).
"""
from __future__ import annotations

import asyncio
import collections
import enum
import threading
import time
from collections.abc import AsyncIterator, Callable
from typing import Final, Protocol, runtime_checkable

import cv2
import numpy as np
import numpy.typing as npt
import structlog
from pygrabber.dshow_graph import FilterGraph

from pastor_tracker.config import Config
from pastor_tracker.core.types import Frame

# ---------------------------------------------------------------------------
# Module-level Final constants -- every literal cited (CLAUDE.md rule 6).
# Matches RESEARCH.md Constants table verbatim.
# ---------------------------------------------------------------------------

# Bounded queue + memory ceiling -- 1080p frame ~= 6 MiB; 64 frames -> ~380 MiB.
# RESEARCH "Sizing the bounded queue".
_FRAMES_QUEUE_MAX_SIZE: Final[int] = 64
# CAP_DSHOW first-frame latency [CITED OpenCV forum -- "Slow camera initialization"].
_FIRST_FRAME_TIMEOUT_SEC: Final[float] = 3.0
# Mirrors arduino_motor _RX_JOIN_TIMEOUT_SEC.
_CAPTURE_JOIN_TIMEOUT_SEC: Final[float] = 1.0
# IO-CAM-04 spec -- 200 ms inter-grab ceiling.
_STALL_THRESHOLD_NS: Final[int] = 200_000_000
# IO-CAM-04 spec -- 100 ms consumer-side drop.
_STALE_FRAME_MAX_AGE_NS: Final[int] = 100_000_000
# CONTEXT.md Area 4 lock -- linear backoff 200 / 500 / 1000 ms.
_REOPEN_BACKOFFS_MS: Final[tuple[int, int, int]] = (200, 500, 1000)
# CONTEXT.md Area 3 lock -- 2 s warmup window for one-shot fallback decision.
_WARMUP_WINDOW_SEC: Final[float] = 2.0
# CONTEXT.md Area 3 lock -- p95 budget = 1.2 x target frame interval.
_BUDGET_MULTIPLIER: Final[float] = 1.2
# CONTEXT.md Area 3 -- 1 s sustained breach before fallback fires.
_BUDGET_PERSIST_NS: Final[int] = 1_000_000_000
# CONTEXT.md Area 3 -- 30-frame window @ 30 fps == 1 s of frames.
_P95_WINDOW_SIZE: Final[int] = 30
# int(0.95 * 30) - 1 -- index of the p95 element in a sorted 30-element list.
_P95_INDEX: Final[int] = 28
# IO-CAM-03 spec -- fallback resolution.
_FALLBACK_WIDTH: Final[int] = 1280
_FALLBACK_HEIGHT: Final[int] = 720
_NS_PER_MS: Final[int] = 1_000_000
_MS_PER_SEC: Final[float] = 1_000.0
# Mirror arduino_motor _RX_THREAD_READ_TIMEOUT_SEC -- responsive stop_event tick.
_CAPTURE_THREAD_TICK_SEC: Final[float] = 0.1
_NS_PER_SEC: Final[int] = 1_000_000_000

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

type _ImageArray = npt.NDArray[np.uint8]
type _FilterGraphFactory = Callable[[], FilterGraph]


# ---------------------------------------------------------------------------
# Public surface re-export -- mirror arduino_motor.py:74-84 shape.
# FakeVideoSource is intentionally not exported (lives in
# tests/fixtures/camera_traces.py per Phase 2 fixture discipline).
# ---------------------------------------------------------------------------

__all__ = [
    "CameraError",
    "CameraOpenError",
    "CameraStallError",
    "OBSCameraNotFoundError",
    "ObsCamera",
    "OpenCvVideoSource",
    "VideoSource",
    "_CamState",
    "discover_obs_camera_index",
]


# ---------------------------------------------------------------------------
# Error class hierarchy: Root CameraError + 3 typed subclasses.
# Mirror arduino_motor.py ArduinoError + 5 subclasses (Phase 2 line 116-149).
# ---------------------------------------------------------------------------


class CameraError(Exception):
    """Root for all obs_camera-originated errors."""


class OBSCameraNotFoundError(CameraError):
    """OBS VCam friendly-name not found in DirectShow enumeration. -- IO-CAM-02.

    Distinct from :class:`CameraOpenError`: the device is not even enumerated.
    Carries the full enumerated friendly-name list (``available``) for
    operator triage -- mirrors Phase 2's ``port_multiple_matches`` log shape.
    """

    def __init__(self, *, expected: str, available: list[str]) -> None:
        super().__init__(f"expected camera_name={expected!r}, available={available}")
        self.expected: str = expected
        self.available: list[str] = available


class CameraOpenError(CameraError):
    """VideoSource opened but no first frame within timeout. -- IO-CAM-01.

    Distinct from :class:`OBSCameraNotFoundError`: the device IS enumerated;
    the most likely cause is OBS running but with 'Start Virtual Camera' not
    toggled [CITED OBS Studio Issue #8057, OpenCV Issue #19746].
    """


class CameraStallError(CameraError):
    """Steady-state stall: > 200 ms inter-grab delta, 3 reopen attempts failed. -- IO-CAM-04.

    Carries restart-attempt history (``attempt_index``, ``backoff_ms``,
    ``reason``) for operator triage. The capture thread (Plan 03-02)
    populates ``attempts`` from its ``_reopen_history``.
    """

    def __init__(self, *, attempts: list[tuple[int, int, str]]) -> None:
        super().__init__(f"camera stall unrecoverable after {len(attempts)} attempts")
        self.attempts: list[tuple[int, int, str]] = attempts  # (attempt_idx, backoff_ms, reason)


# ---------------------------------------------------------------------------
# Internal state machine. Full ObsCamera lifecycle ships in Plan 03-02.
# ---------------------------------------------------------------------------


class _CamState(enum.Enum):
    """Internal state machine for ``ObsCamera`` (added in Plan 03-02)."""

    DISCONNECTED = "disconnected"
    OPENING = "opening"
    RUNNING = "running"
    REOPENING = "reopening"
    FAULTED = "faulted"
    CLOSED = "closed"


# ---------------------------------------------------------------------------
# Section 1: VideoSource Protocol DI seam + OpenCvVideoSource real impl.
# Mirror arduino_transport.py:77-128 (SerialTransport + PySerialTransport).
# ---------------------------------------------------------------------------


@runtime_checkable
class VideoSource(Protocol):
    """Minimal DI surface -- the only contract orchestrator code depends on.

    ``read`` returns ``(False, None)`` on driver-side failure; consumers MUST
    check ``ok`` before touching ``bgr`` (Pitfall 5 in 03-RESEARCH.md).
    """

    def read(self) -> tuple[bool, _ImageArray | None]: ...

    def set_resolution(self, width: int, height: int, fps: int) -> None: ...

    def is_opened(self) -> bool: ...

    def release(self) -> None: ...


class OpenCvVideoSource:
    """Real ``cv2.VideoCapture`` wrapper. Single owner of the OS-level handle.

    Constructor opens immediately + applies properties so a missing device
    fails at construction (mirrors PySerialTransport.__init__ idiom).
    ``set_resolution`` is provided for completeness but the orchestrator
    prefers release+reopen on fallback / recovery (cap.set() is flaky on
    CAP_DSHOW [CITED OpenCV Issue #23533]).

    Pitfall 1 mitigation: ``cv2.CAP_DSHOW`` is passed explicitly so both
    pygrabber's enumeration order AND cv2's open path use the same
    ``ICreateDevEnum`` ordering.
    """

    def __init__(self, device_index: int, width: int, height: int, fps: int) -> None:
        self._cap = cv2.VideoCapture(device_index, cv2.CAP_DSHOW)
        self._apply_props(width, height, fps)

    def _apply_props(self, width: int, height: int, fps: int) -> None:
        # Order per kurokesu.com guide [CITED]: width, height, FPS. FOURCC
        # skipped -- OBS VCam advertises a single stream and DirectShow
        # negotiates the format (Pitfall 3 -- cap.set is advisory; the actual
        # frame shape is read from bgr.shape at capture time).
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, float(width))
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, float(height))
        self._cap.set(cv2.CAP_PROP_FPS, float(fps))

    def read(self) -> tuple[bool, _ImageArray | None]:
        # cv2 returns BGR uint8 HxWx3 by default; None on failure (Pitfall 5).
        # cv2 stubs widen the dtype to int|float -- VideoCapture's runtime
        # guarantee is uint8, so we narrow at the seam. Downstream consumers
        # see a typed ``_ImageArray`` (uint8) and ``Frame.__post_init__``
        # validates the shape on construction.
        ok, frame = self._cap.read()
        if not ok or frame is None:
            return False, None
        return True, frame  # type: ignore[return-value]

    def set_resolution(self, width: int, height: int, fps: int) -> None:
        # Phase 3 fallback path: orchestrator prefers release+reopen over
        # in-place set; CAP_DSHOW in-place sets are flaky across vendors per
        # OpenCV Issue #23533 [CITED]. This method exists for tests / future
        # re-use; the orchestrator does NOT call it on the fallback path.
        self._apply_props(width, height, fps)

    def is_opened(self) -> bool:
        return bool(self._cap.isOpened())

    def release(self) -> None:
        self._cap.release()


# ---------------------------------------------------------------------------
# Section 2: Discovery helper -- exact-match by friendly name.
# Mirror arduino_transport.py:176-233 (discover_arduino_port).
# ---------------------------------------------------------------------------


def discover_obs_camera_index(
    expected_name: str,
    *,
    factory: _FilterGraphFactory,
    logger: structlog.stdlib.BoundLogger,
) -> int:
    """Enumerate DirectShow devices; return index whose friendly name matches. -- IO-CAM-01.

    Tiger-style: no exact match -> raise :class:`OBSCameraNotFoundError`
    carrying the full device list. Multiple matches with the same name ->
    first hit, log all (mirrors arduino_transport multi-match rule -- Phase
    2 WARN 5).

    The ``factory`` closure exists for Pitfall 10 (FilterGraph ctor calls
    ``CoInitialize`` -- tests monkeypatch the factory rather than constructing
    a real :class:`FilterGraph`, which avoids ``RPC_E_CHANGED_MODE`` in
    pytest workers). Production wires ``lambda: FilterGraph()``.

    Two distinct success log events (mirrors Phase 2 ``port_discovered`` vs
    ``port_multiple_matches``):

    * ``camera_discovered`` -- single match; keys: ``index``, ``name``,
      ``available_count``.
    * ``camera_multiple_matches`` -- multi-match; keys: ``chosen_index``,
      ``all_indexes``, ``name``.
    """
    graph = factory()
    # pygrabber.dshow_graph.FilterGraph is unstubbed (mypy override at
    # pyproject.toml:81-83). The runtime contract returns list[str].
    devices: list[str] = list(graph.get_input_devices())  # type: ignore[no-untyped-call]
    matches = [(idx, name) for idx, name in enumerate(devices) if name == expected_name]
    if not matches:
        raise OBSCameraNotFoundError(expected=expected_name, available=devices)
    if len(matches) > 1:
        logger.info(
            "camera_multiple_matches",
            chosen_index=matches[0][0],
            all_indexes=[m[0] for m in matches],
            name=expected_name,
        )
    else:
        logger.info(
            "camera_discovered",
            index=matches[0][0],
            name=expected_name,
            available_count=len(devices),
        )
    return matches[0][0]


# ---------------------------------------------------------------------------
# Section 3: Helpers (pure transforms, no I/O).
# ---------------------------------------------------------------------------


class _P95Detector:
    """Rolling p95 of inter-grab nanosecond deltas. Pure transform on a deque.

    No Phase 2 analog -- see 03-RESEARCH.md "Pattern 3: Rolling p95 sliding
    window". Sort cost on 30 ints is < 1 us; negligible vs ~33 ms grab.

    Decision rules (CONTEXT.md Area 3 lock):
        * Need a full window (30 samples) before deciding -- short windows
          return ``False`` so warmup doesn't false-trip.
        * p95 must exceed budget AND the breach must persist for at least
          :data:`_BUDGET_PERSIST_NS` (1 s) before returning ``True``.
        * Recovery (p95 returns to budget) resets ``_first_breach_ns`` to
          ``None`` -- partial breaches do NOT accumulate (one-shot detector).
    """

    def __init__(self, target_fps: int) -> None:
        target_interval_ns = int(_NS_PER_SEC / target_fps)
        self._budget_ns: int = int(target_interval_ns * _BUDGET_MULTIPLIER)
        self._window: collections.deque[int] = collections.deque(maxlen=_P95_WINDOW_SIZE)
        self._first_breach_ns: int | None = None

    def observe(self, delta_ns: int) -> None:
        self._window.append(delta_ns)

    def budget_breached_persistent(self, now_ns: int) -> bool:
        if len(self._window) < _P95_WINDOW_SIZE:
            return False
        sorted_window = sorted(self._window)
        p95 = sorted_window[_P95_INDEX]
        if p95 <= self._budget_ns:
            self._first_breach_ns = None
            return False
        if self._first_breach_ns is None:
            self._first_breach_ns = now_ns
            return False
        return (now_ns - self._first_breach_ns) >= _BUDGET_PERSIST_NS

    @property
    def current_p95_ns(self) -> int:
        if len(self._window) < _P95_WINDOW_SIZE:
            return 0
        return sorted(self._window)[_P95_INDEX]

    @property
    def budget_ns(self) -> int:
        return self._budget_ns


# ----------------------------------------------------------------------------
# Section 4: ObsCamera orchestrator (Plan 03-02).
#
# Owns the only threading <-> asyncio bridge in this module. Mirrors
# :class:`pastor_tracker.io.arduino_motor.ArduinoMotor`:
#   * Single producer (capture thread) -> bounded asyncio.Queue.
#   * Single consumer (asyncio loop) drains the queue.
#   * Cross-thread bridge via ``loop.call_soon_threadsafe`` -- never direct
#     queue access from the thread.
#   * Pitfall 7 close-order: ``_stop_event.set()`` -> ``thread.join`` ->
#     ``source.release()`` -> state CLOSED.
# ----------------------------------------------------------------------------


class ObsCamera:
    """Asyncio orchestrator for the OBS Virtual Camera frame source.

    Owns the only threading <-> asyncio bridge in this module: a daemon
    capture thread reads BGR frames via the injected :class:`VideoSource`,
    builds typed :class:`Frame` DTOs (timestamp + shape validation off the
    asyncio hot path), then crosses into the asyncio loop via
    ``loop.call_soon_threadsafe`` to enqueue a bounded
    :class:`asyncio.Queue` with drop-oldest semantics.

    Concurrency invariants (mirror :class:`ArduinoMotor`):
        * Single producer: only ``_capture_loop`` calls ``source.read()``.
        * Single consumer: only the asyncio loop drains ``_frames_queue``.
        * Bounded queue: ``maxsize=64``, drop-oldest + WARN log.
        * Communication via ``_stop_event`` (loop -> thread, signal-only)
          and ``call_soon_threadsafe`` (thread -> loop, frames + errors).

    Lifecycle (mirrors arduino_motor.start / close):
        * :meth:`start` is single-shot; calling twice raises
          :class:`CameraError`.
        * :meth:`start` blocks until the first valid :class:`Frame`
          arrives via the capture thread, OR raises
          :class:`CameraOpenError` after :data:`_FIRST_FRAME_TIMEOUT_SEC`.
          On timeout the source handle is released (T-03-03) before the
          error is raised -- no leaked VideoCapture handle.
        * :meth:`stop` close-order is ``_stop_event.set()`` -> thread join
          (timeout) -> ``source.release()`` -> state CLOSED (Pitfall 7).
        * :meth:`frames` yields :class:`Frame` instances; consumer-side
          stale-drop discards frames older than
          :data:`_STALE_FRAME_MAX_AGE_NS` (100 ms).

    Read-only status surface (Phase 7 dashboard):
        :attr:`state`, :attr:`is_running`, :attr:`current_resolution`,
        :attr:`last_error`.
    """

    def __init__(
        self,
        config: Config,
        *,
        video_source_factory: Callable[[int, int, int, int], VideoSource],
        filter_graph_factory: _FilterGraphFactory,
    ) -> None:
        self._config: Config = config
        self._video_source_factory = video_source_factory
        self._filter_graph_factory = filter_graph_factory
        self._frames_queue: asyncio.Queue[Frame] = asyncio.Queue(
            maxsize=_FRAMES_QUEUE_MAX_SIZE
        )
        self._stop_event: threading.Event = threading.Event()
        self._first_frame_event: threading.Event = threading.Event()
        self._state: _CamState = _CamState.DISCONNECTED
        self._capture_thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._source: VideoSource | None = None
        self._device_index: int | None = None
        self._current_width: int = config.capture_width
        self._current_height: int = config.capture_height
        self._reopen_history: list[tuple[int, int, str]] = []
        self._latched_error: CameraError | None = None
        self._fallback_consumed: bool = False
        self._logger = structlog.get_logger(module="obs_camera")

    # -----------------------------------------------------------------------
    # Read-only status surface.
    # -----------------------------------------------------------------------

    @property
    def state(self) -> _CamState:
        """Current internal camera state. Read-only."""
        return self._state

    @property
    def is_running(self) -> bool:
        """``True`` while ``state is _CamState.RUNNING``."""
        return self._state is _CamState.RUNNING

    @property
    def current_resolution(self) -> tuple[int, int]:
        """Currently-active capture resolution (flips on fallback)."""
        return self._current_width, self._current_height

    @property
    def last_error(self) -> CameraError | None:
        """Latched terminal error, or ``None`` while running."""
        return self._latched_error

    # -----------------------------------------------------------------------
    # Lifecycle.
    # -----------------------------------------------------------------------

    async def start(self) -> None:
        """Discover -> open -> spawn capture thread -> await first frame.

        IO-CAM-01. Single-shot: a second call raises :class:`CameraError`
        with the current state in the message. On first-frame timeout
        the source handle is released BEFORE the
        :class:`CameraOpenError` is raised (T-03-03 mitigation).

        Pre-thread errors (discovery / initial factory open) are
        translated to typed terminal states and latched on
        ``last_error`` before propagating, so the dashboard can
        distinguish "failed at discovery" from "still opening".
        """
        if self._state is not _CamState.DISCONNECTED:
            raise CameraError(
                f"start() called twice (state={self._state.value})"
            )
        self._loop = asyncio.get_running_loop()
        self._state = _CamState.OPENING
        try:
            self._device_index = discover_obs_camera_index(
                self._config.obs_camera_name,
                factory=self._filter_graph_factory,
                logger=self._logger,
            )
        except OBSCameraNotFoundError as exc:
            # CR-03: latch terminal discovery failure so dashboard can
            # observe FAULTED + last_error rather than a stuck OPENING.
            self._state = _CamState.FAULTED
            self._latched_error = exc
            raise
        except Exception as exc:
            # B-04: graph-factory failures (FilterGraph CoInitialize race,
            # missing quartz.dll / mfplat.dll, pythoncom.com_error) MUST
            # translate to a typed CameraOpenError on the lifecycle surface.
            # Without this catch, raw OSError / pywintypes.error escape and
            # state stays at OPENING forever (CR-03's typed-terminal-state
            # pattern was missing this branch). The catch-and-translate is
            # symmetric with the cv2 / DirectShow open path below; BLE001
            # is exempt because the typed re-raise IS the translation, no
            # silent swallow.
            self._state = _CamState.FAULTED
            self._latched_error = CameraOpenError(
                f"DirectShow enumeration failed: {exc!r}"
            )
            raise self._latched_error from exc
        try:
            self._source = self._video_source_factory(
                self._device_index,
                self._current_width,
                self._current_height,
                self._config.capture_fps,
            )
        except Exception as exc:
            # CR-03: cv2 / DirectShow open errors translate to a typed
            # CameraOpenError on the lifecycle surface. The catch-and-
            # re-raise (``raise ... from exc``) pattern is exempt from
            # BLE001 because the typed re-raise IS the translation; no
            # silent swallow.
            self._state = _CamState.FAULTED
            self._latched_error = CameraOpenError(
                f"VideoCapture open failed: {exc!r}"
            )
            raise self._latched_error from exc
        self._capture_thread = threading.Thread(
            target=self._capture_loop,
            name="obs-camera-capture",
            daemon=True,
        )
        self._capture_thread.start()
        first_frame_arrived = await asyncio.to_thread(
            self._first_frame_event.wait, _FIRST_FRAME_TIMEOUT_SEC
        )
        if not first_frame_arrived:
            self._stop_event.set()
            if self._capture_thread is not None:
                await asyncio.to_thread(
                    self._capture_thread.join, _CAPTURE_JOIN_TIMEOUT_SEC
                )
                # WR-03: null the handle so a subsequent stop() does
                # not log a redundant camera_thread_exited for an
                # already-joined thread. Mirrors the symmetric null
                # at stop()'s join-then-null block.
                self._capture_thread = None
            if self._source is not None:
                # T-03-03: release on timeout path -- no leaked handle.
                self._source.release()
                self._source = None
            self._logger.error(
                "camera_first_frame_timeout",
                timeout_sec=_FIRST_FRAME_TIMEOUT_SEC,
                device_index=self._device_index,
            )
            self._state = _CamState.FAULTED
            self._latched_error = CameraOpenError(
                "first frame timeout -- is OBS running and "
                "'Start Virtual Camera' toggled on?"
            )
            raise self._latched_error
        # CR-03: do not clobber a FAULTED state that the capture thread
        # raced ahead of us to latch (e.g. read() raises immediately
        # after the first frame). The fault path owns the terminal
        # transition; only promote to RUNNING from OPENING.
        if self._state is _CamState.OPENING:
            self._state = _CamState.RUNNING
        self._logger.info(
            "camera_started",
            device_index=self._device_index,
            width=self._current_width,
            height=self._current_height,
            fps=self._config.capture_fps,
        )

    async def stop(self) -> None:
        """Signal stop event, join capture thread (timeout), release source.

        Pitfall 7 close-order: ``_stop_event.set()`` BEFORE thread join
        BEFORE ``source.release()``. Releasing the source before joining
        the thread races the in-flight ``read()``.

        CR-03: terminal :class:`_CamState.FAULTED` is preserved across
        stop -- the dashboard distinguishes "stopped after fault" from
        "stopped cleanly" via ``last_error``, so clobbering FAULTED to
        CLOSED would erase the diagnostic and disagree with
        ``last_error``.
        """
        self._stop_event.set()
        capture_thread = self._capture_thread
        if capture_thread is not None:
            await asyncio.to_thread(
                capture_thread.join, _CAPTURE_JOIN_TIMEOUT_SEC
            )
            if capture_thread.is_alive():
                # W-06: structured WARN on join timeout. The capture thread
                # is most likely still inside ``_video_source_factory(...)``
                # (cv2.VideoCapture cold-open on CAP_DSHOW can take 2-3 s
                # [CITED OpenCV forum]); stop() proceeds to release() which
                # races the in-flight read(). The operator at least sees
                # the leak rather than diagnosing a "phantom RUNNING state"
                # downstream.
                self._logger.warning(
                    "camera_thread_join_timeout",
                    timeout_sec=_CAPTURE_JOIN_TIMEOUT_SEC,
                    note="release proceeding; potential handle race",
                )
            self._logger.info(
                "camera_thread_exited",
                clean=not capture_thread.is_alive(),
            )
            self._capture_thread = None
        if self._source is not None:
            self._source.release()
            self._source = None
        if self._state is not _CamState.FAULTED:
            self._state = _CamState.CLOSED

    # -----------------------------------------------------------------------
    # Capture thread (the only producer).
    # -----------------------------------------------------------------------

    def _capture_loop(self) -> None:
        """Daemon-thread capture loop.

        IO-CAM-04 (timestamp + stall) + IO-CAM-03 (fallback). The single
        bare-Exception catch below (``noqa: BLE001``) is justified: cv2
        / DirectShow exceptions on driver-side failure MUST translate to
        a typed orchestrator-level error rather than crash the daemon
        thread silently.
        """
        last_grab_ns: int | None = None
        p95_detector = _P95Detector(self._config.capture_fps)
        warmup_started_ns = time.perf_counter_ns()
        while not self._stop_event.is_set():
            try:
                if self._source is None:
                    # WR-01: defense-in-depth -- a future refactor that
                    # leaves self._source = None on a recovery branch
                    # must surface as a typed terminal error rather
                    # than a silent daemon exit. Today this guard is
                    # unreachable (CR-01 closed the only known path);
                    # tomorrow it MUST fail loud (CLAUDE.md rule 1).
                    self._fault_with_stall(
                        RuntimeError(
                            "capture loop observed source=None; "
                            "recovery path bug",
                        ),
                    )
                    return
                ok, bgr = self._source.read()
            except Exception as exc:  # noqa: BLE001 -- documented translator
                # Capture-thread bridge: cv2 errors translate to a
                # CameraStallError on the loop thread; never silently swallow.
                self._fault_with_stall(exc)
                return
            now_ns = time.perf_counter_ns()
            stalled = (
                last_grab_ns is not None
                and (now_ns - last_grab_ns) > _STALL_THRESHOLD_NS
            )
            if not ok or bgr is None or stalled:
                if stalled and last_grab_ns is not None:
                    self._logger.warning(
                        "camera_stall_detected",
                        delta_ms=(now_ns - last_grab_ns) / _NS_PER_MS,
                        threshold_ms=_STALL_THRESHOLD_NS / _NS_PER_MS,
                    )
                if not self._attempt_reopen():
                    self._fault_with_stall(None)
                    return
                last_grab_ns = None
                continue
            # Build typed Frame inside the thread; __post_init__ validates shape.
            try:
                frame = Frame(
                    image=bgr,
                    width=self._current_width,
                    height=self._current_height,
                    timestamp_ns=now_ns,
                )
            except ValueError as exc:
                self._logger.info(
                    "frame_shape_mismatch",
                    expected=f"{self._current_width}x{self._current_height}",
                    observed=str(getattr(bgr, "shape", "n/a")),
                    reason=str(exc),
                )
                if not self._attempt_reopen():
                    self._fault_with_stall(None)
                    return
                last_grab_ns = None
                continue
            if not self._first_frame_event.is_set():
                self._first_frame_event.set()
            if last_grab_ns is not None and not self._fallback_consumed:
                p95_detector.observe(now_ns - last_grab_ns)
                warmup_remaining_ns = (
                    int(_WARMUP_WINDOW_SEC * _NS_PER_SEC)
                    - (now_ns - warmup_started_ns)
                )
                # WR-05: half-open window [0, W). At the exact
                # boundary the warmup is over, so the fallback
                # decision MUST NOT fire. CONTEXT.md "2 s warmup
                # window" reads as "inside warmup" semantics.
                if warmup_remaining_ns > 0 and self._maybe_fallback(
                    p95_detector, now_ns
                ):
                    # CR-02: fallback released + reopened the source.
                    # The new 720p CAP_DSHOW first-frame latency is
                    # documented as up to _FIRST_FRAME_TIMEOUT_SEC (3 s);
                    # measuring (now_ns_next - last_grab_ns) against
                    # _STALL_THRESHOLD_NS (200 ms) would deterministically
                    # false-trip the stall detector and burn the reopen
                    # budget on a healthy fallback. Mirror the stall-
                    # recovery pattern (last_grab_ns = None; continue).
                    last_grab_ns = None
                    continue
            last_grab_ns = now_ns
            if self._loop is not None:
                self._loop.call_soon_threadsafe(self._enqueue_frame, frame)

    # -----------------------------------------------------------------------
    # Cross-thread bridge: thread -> loop.
    # -----------------------------------------------------------------------

    def _enqueue_frame(self, frame: Frame) -> None:
        """Loop-thread synchronous enqueuer. Drop-oldest semantics."""
        if self._frames_queue.full():
            # WR-07: full() guarantees the queue has at least one
            # element, so get_nowait() cannot raise QueueEmpty here.
            # The previous contextlib.suppress(asyncio.QueueEmpty)
            # was unreachable defensive code -- per CLAUDE.md rule
            # 1 (tiger-style: fail fast, fail loud), unreachable
            # error-suppression hides real bugs (e.g. a future
            # refactor that drops the full() guard). Let any
            # unexpected QueueEmpty propagate as a contract bug.
            self._frames_queue.get_nowait()
            self._logger.warning(
                "frames_queue_full",
                dropped_timestamp_ns=frame.timestamp_ns,
                queue_max=_FRAMES_QUEUE_MAX_SIZE,
            )
        self._frames_queue.put_nowait(frame)

    def _on_capture_failed(self, error: CameraStallError) -> None:
        """Loop-thread latcher for capture-thread terminal errors."""
        self._state = _CamState.FAULTED
        self._latched_error = error
        self._logger.error(
            "camera_stall_unrecoverable",
            attempts=len(error.attempts),
            reopen_history=error.attempts,
        )

    def _fault_with_stall(self, reason: Exception | None) -> None:
        """Latch a CameraStallError and cross to the loop thread.

        Single point that builds a :class:`CameraStallError` from the
        accumulated ``_reopen_history`` (plus, if present, a
        capture-thread exception that triggered the fault). Sets
        ``_stop_event`` so any subsequent iteration of
        ``_capture_loop`` (e.g. the WR-01 ``self._source is None``
        guard) exits cleanly without re-firing a duplicate fault that
        would clobber the original ``_latched_error``.
        """
        history = list(self._reopen_history)
        if reason is not None:
            history.append(
                (
                    len(history) + 1,
                    0,
                    f"capture_thread_exception: {reason!r}",
                )
            )
        if self._loop is not None and not self._stop_event.is_set():
            self._loop.call_soon_threadsafe(
                self._on_capture_failed,
                CameraStallError(attempts=history),
            )
        # Halt the daemon thread on its next loop iteration. Callers
        # that already ``return`` (the BLE001 translators in
        # _capture_loop and _attempt_reopen) are unaffected; callers
        # that fall through (the _maybe_fallback factory-exception
        # path) now terminate before WR-01's source=None guard re-
        # fires _fault_with_stall.
        self._stop_event.set()

    # -----------------------------------------------------------------------
    # Recovery state machine.
    # -----------------------------------------------------------------------

    def _attempt_reopen(self) -> bool:
        """Try up to 3 reopens with linear backoff. Returns ``True`` on success.

        Backoffs are :data:`_REOPEN_BACKOFFS_MS` (200, 500, 1000 ms)
        per CONTEXT.md Area 4 lock. The single bare-Exception catch
        (``noqa: BLE001``) is justified for the same reason as
        ``_capture_loop``: the orchestrator MUST translate cv2 /
        DirectShow open errors to a typed history entry rather than
        crash the daemon thread.

        WR-04: transitions to :class:`_CamState.REOPENING` on entry
        and back to :class:`_CamState.RUNNING` on success so the
        dashboard can render the recovery in flight. On failure
        (``return False``) we leave the state at REOPENING -- the
        caller (``_capture_loop``) immediately calls
        :meth:`_fault_with_stall`, which crosses to the loop thread
        and sets FAULTED via ``_on_capture_failed``. Python attribute
        writes are atomic so no lock is needed for the daemon-thread
        write, but readers see a consistent value.
        """
        prior_state = self._state
        if prior_state is _CamState.RUNNING:
            self._state = _CamState.REOPENING
        # W-05: ``_first_frame_event`` is single-shot after start(). The
        # capture loop only ever ``set()``s it (idempotent); no path waits
        # on it after start() returns. Clearing it here was a no-op that
        # mis-led readers into thinking a first-frame handshake re-runs on
        # reopen. start() is documented single-shot ("calling twice raises
        # CameraError"), so reopen-after-restart is not a real path.
        for attempt_index, backoff_ms in enumerate(_REOPEN_BACKOFFS_MS, start=1):
            if self._source is not None:
                self._source.release()
                self._source = None
            if self._stop_event.wait(backoff_ms / _MS_PER_SEC):
                return False
            if self._device_index is None:
                return False
            try:
                self._source = self._video_source_factory(
                    self._device_index,
                    self._current_width,
                    self._current_height,
                    self._config.capture_fps,
                )
            except Exception as exc:  # noqa: BLE001 -- documented translator
                # Reopen factory-call bridge: cv2 / DirectShow errors
                # translate into a typed reopen-history entry rather
                # than crash the daemon thread silently.
                self._reopen_history.append(
                    (attempt_index, backoff_ms, str(exc))
                )
                self._logger.warning(
                    "camera_reopen_attempt_failed",
                    attempt=attempt_index,
                    backoff_ms=backoff_ms,
                    reason=str(exc),
                )
                continue
            ok, _bgr = self._source.read()
            if ok:
                self._logger.warning(
                    "camera_reopen_succeeded",
                    attempt=attempt_index,
                    backoff_ms=backoff_ms,
                )
                # WR-04: only promote back to RUNNING if we entered
                # from RUNNING; never overwrite a terminal state that
                # raced ahead via _on_capture_failed.
                if self._state is _CamState.REOPENING:
                    self._state = _CamState.RUNNING
                return True
            self._reopen_history.append(
                (
                    attempt_index,
                    backoff_ms,
                    "first_frame_after_reopen_returned_False",
                )
            )
            self._logger.warning(
                "camera_reopen_attempt_failed",
                attempt=attempt_index,
                backoff_ms=backoff_ms,
                reason="first_frame_after_reopen_returned_False",
            )
            # WR-02: T-03-03 release-on-failure also applies to the
            # FINAL failed iteration. Without this release, the loop
            # falls through to ``return False`` carrying a live
            # VideoCapture handle on a 3rd-attempt failure -- the
            # _capture_loop caller calls _fault_with_stall and exits,
            # so the handle leaks until stop() runs. Earlier attempts
            # release the previous handle at the top of the next loop
            # iteration; the final attempt has no next iteration.
            self._source.release()
            self._source = None
        return False

    # -----------------------------------------------------------------------
    # Resolution-fallback decision.
    # -----------------------------------------------------------------------

    def _maybe_fallback(self, p95: _P95Detector, now_ns: int) -> bool:
        """One-shot 1080p -> 720p fallback inside warmup window.

        IO-CAM-03. CONTEXT.md Area 3 lock: never re-promote, never
        re-fallback; already-720p case logs ERROR and continues
        capturing (halting kills the only camera path).

        Returns ``True`` iff the source was actually swapped (the
        caller MUST then reset ``last_grab_ns`` so the new 720p
        CAP_DSHOW first-frame latency does not trip the 200 ms stall
        detector). Returns ``False`` for every no-op path (already-
        consumed / no breach / no device / already-720p / factory
        failure -- all of which leave the grab clock untouched OR
        terminate via _fault_with_stall).
        """
        if self._fallback_consumed:
            return False
        if not p95.budget_breached_persistent(now_ns):
            return False
        if self._device_index is None:
            return False
        p95_ms = p95.current_p95_ns / _NS_PER_MS
        budget_ms = p95.budget_ns / _NS_PER_MS
        already_at_fallback = (
            self._current_width == _FALLBACK_WIDTH
            and self._current_height == _FALLBACK_HEIGHT
        )
        if already_at_fallback:
            self._logger.error(
                "camera_resolution_breach_at_720p",
                p95_ms=p95_ms,
                budget_ms=budget_ms,
            )
            self._fallback_consumed = True
            return False
        if self._source is not None:
            self._source.release()
            self._source = None
        try:
            self._source = self._video_source_factory(
                self._device_index,
                _FALLBACK_WIDTH,
                _FALLBACK_HEIGHT,
                self._config.capture_fps,
            )
        except Exception as exc:  # noqa: BLE001 -- documented translator
            # CR-01: cv2 / DirectShow open errors during fallback MUST
            # translate to a typed terminal stall rather than escape
            # _capture_loop and silently kill the daemon thread.
            # Without this, self._source stays None, state stays
            # RUNNING, last_error stays None, and the consumer hangs
            # on _frames_queue.get() forever.
            self._logger.error(
                "camera_fallback_factory_failed",
                reason=str(exc),
                from_dim=f"{self._current_width}x{self._current_height}",
                to_dim=f"{_FALLBACK_WIDTH}x{_FALLBACK_HEIGHT}",
            )
            self._fault_with_stall(exc)
            self._fallback_consumed = True
            return False
        self._logger.warning(
            "camera_resolution_fallback",
            from_dim=f"{self._current_width}x{self._current_height}",
            to_dim=f"{_FALLBACK_WIDTH}x{_FALLBACK_HEIGHT}",
            p95_ms=p95_ms,
            budget_ms=budget_ms,
            device_index=self._device_index,
        )
        self._current_width = _FALLBACK_WIDTH
        self._current_height = _FALLBACK_HEIGHT
        self._fallback_consumed = True
        return True

    # -----------------------------------------------------------------------
    # Async iterator surface (consumer side).
    # -----------------------------------------------------------------------

    async def frames(self) -> AsyncIterator[Frame]:
        """Async-iterable view of fresh Frames.

        IO-CAM-04 consumer-side stale-drop: discards frames where
        ``(time.perf_counter_ns() - frame.timestamp_ns) >
        _STALE_FRAME_MAX_AGE_NS`` (100 ms) and logs WARN
        ``frame_stale_dropped``. Surfaces any latched terminal error
        once the queue drains (mirrors arduino_motor pattern).

        WR-06: producer-dead watchdog. ``_frames_queue.get()``
        blocks forever if the queue is empty and no producer is
        alive. CR-01/WR-01 closed every known path that exits the
        capture thread without latching an error, but a future
        regression could re-introduce the symptom. Wrap the get in
        ``asyncio.wait_for`` with a :data:`_FIRST_FRAME_TIMEOUT_SEC`
        budget; on timeout, check whether the capture thread is
        still alive. If the thread died without latching an error,
        synthesize a typed :class:`CameraStallError` so the consumer
        does NOT hang silently.
        """
        while True:
            if self._latched_error is not None and self._frames_queue.empty():
                raise self._latched_error
            try:
                frame = await asyncio.wait_for(
                    self._frames_queue.get(),
                    timeout=_FIRST_FRAME_TIMEOUT_SEC,
                )
            except TimeoutError:
                # B-02: clean-shutdown surface. After stop(), state is
                # CLOSED, the capture thread is None, and the queue
                # stops being filled -- the wait_for() naturally times
                # out within _FIRST_FRAME_TIMEOUT_SEC. Treat this as a
                # generator-clean exit so consumers iterating via
                # ``async for frame in cam.frames()`` see
                # StopAsyncIteration, NOT a synthetic CameraStallError
                # masquerading as "camera crashed".
                if self._state is _CamState.CLOSED:
                    return
                # WR-06: only treat the timeout as fatal if the
                # producer is gone AND no error is latched. If a
                # latched error exists (race with the loop-thread
                # latcher), the next iteration's empty-check raises
                # it. If the thread is alive, simply continue
                # waiting -- the camera is just idle.
                thread_dead = (
                    self._capture_thread is None
                    or not self._capture_thread.is_alive()
                )
                if thread_dead and self._latched_error is None:
                    raise CameraStallError(
                        attempts=[
                            (
                                0,
                                0,
                                "capture thread dead, no error latched",
                            ),
                        ],
                    ) from None
                continue
            now_ns = time.perf_counter_ns()
            age_ns = now_ns - frame.timestamp_ns
            if age_ns > _STALE_FRAME_MAX_AGE_NS:
                self._logger.warning(
                    "frame_stale_dropped",
                    age_ms=age_ns / _NS_PER_MS,
                    threshold_ms=_STALE_FRAME_MAX_AGE_NS / _NS_PER_MS,
                )
                continue
            yield frame
