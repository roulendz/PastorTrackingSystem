"""YOLO11-pose detection seam + production engine (Phase 4 perception).

Architecture (RESEARCH 04 + CONTEXT.md Area 1):
    * Production: ``UltralyticsPoseEngine`` owns a single-worker
      ``ProcessPoolExecutor`` + a long-lived ``SharedMemory`` block.
      Worker target lives in :mod:`._pose_worker` (Pitfall 4 -- spawn-safe).
    * Tests: ``FakePoseEngine`` (in ``tests/fixtures/pose_traces.py``)
      satisfies the ``PoseEngine`` Protocol with zero ML deps.

Drop-oldest semantics (RESEARCH 04 Pattern 4) and the ``consume() ->
detections()`` orchestrator surface land in Plan 03 (Wave 3); this module
in Plan 01 ships only the seam, the lifecycle (start/stop), and the typed
error hierarchy.
"""
from __future__ import annotations

import asyncio
import enum
from collections.abc import AsyncIterator
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import shared_memory
from typing import Final, Protocol, runtime_checkable

import numpy as np
import structlog

from pastor_tracker.config import Config
from pastor_tracker.core.types import Detection, Frame
from pastor_tracker.perception import _pose_worker

# --- Module-level Final constants (CLAUDE.md rule 6 -- no magic numbers) ---
_INFLIGHT_SLOTS: Final[int] = 1
_EXECUTOR_SHUTDOWN_TIMEOUT_SEC: Final[float] = 5.0
_WORKER_WARMUP_TIMEOUT_SEC: Final[float] = 30.0  # Pitfall 10 -- CUDA JIT
_NS_PER_SEC: Final[int] = 1_000_000_000
_BYTES_PER_PIXEL: Final[int] = 3  # BGR uint8
_OUT_QUEUE_MAX: Final[int] = 256  # Phase 2/3 bounded-queue precedent
_DETECTIONS_POLL_TIMEOUT_SEC: Final[float] = 0.5  # detections() get() poll cadence

__all__ = [
    "PerceptionError",
    "PoseDetector",
    "PoseEngine",
    "PoseEngineUnavailableError",
    "UltralyticsPoseEngine",
    "_DetectorState",
]


class PerceptionError(Exception):
    """Root for all perception-originated errors."""


class PoseEngineUnavailableError(PerceptionError):
    """Worker process failed to load YOLO model OR CUDA requested but missing."""

    def __init__(self, *, expected: str, available: list[str]) -> None:
        super().__init__(f"expected={expected!r}, available={available}")
        self.expected: str = expected
        self.available: list[str] = available


class _DetectorState(enum.Enum):
    DISCONNECTED = "disconnected"
    STARTING = "starting"
    RUNNING = "running"
    FAULTED = "faulted"
    CLOSED = "closed"


@runtime_checkable
class PoseEngine(Protocol):
    """Minimal DI surface -- production = UltralyticsPoseEngine, tests = FakePoseEngine."""

    async def detect(self, frame: Frame) -> list[Detection]: ...

    async def close(self) -> None: ...


def _resolve_device(requested: str) -> str:
    """RESEARCH 04 Open Question 4: resolve 'auto' to 'cuda' or 'cpu' at start.

    Fail-fast loud if the user explicitly asked for 'cuda' but no CUDA is
    available. 'auto' silently falls to 'cpu' with an INFO log emitted by
    the caller (PoseDetector.start logs the resolution).
    """
    if requested == "cuda":
        try:
            import torch  # heavy import deferred
        except ImportError as exc:  # pragma: no cover -- torch is a hard dep via ultralytics
            raise PoseEngineUnavailableError(expected="cuda", available=["cpu"]) from exc
        if not torch.cuda.is_available():
            raise PoseEngineUnavailableError(expected="cuda", available=["cpu"])
        return "cuda"
    if requested == "cpu":
        return "cpu"
    # "auto"
    try:
        import torch
    except ImportError:  # pragma: no cover
        return "cpu"
    return "cuda" if torch.cuda.is_available() else "cpu"


class UltralyticsPoseEngine:
    """Production PoseEngine. Owns ProcessPoolExecutor + SharedMemory block.

    Lifecycle (single-shot per Phase 2/3 precedent):
        ``__init__`` -- store config + bind logger.
        ``start()`` -- allocate shm, spawn executor, run warmup.
        ``detect(frame)`` -- (Plan 03) drop-oldest + executor.submit + Future.
        ``close()`` -- cancel inflight, shutdown executor, unlink shm.
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        self._logger = structlog.get_logger(module="pose_detector")
        self._state: _DetectorState = _DetectorState.DISCONNECTED
        self._loop: asyncio.AbstractEventLoop | None = None
        self._executor: ProcessPoolExecutor | None = None
        self._shm: shared_memory.SharedMemory | None = None
        self._resolved_device: str | None = None
        self._latched_error: PerceptionError | None = None

    @property
    def state(self) -> _DetectorState:
        return self._state

    @property
    def is_running(self) -> bool:
        return self._state is _DetectorState.RUNNING

    @property
    def last_error(self) -> PerceptionError | None:
        return self._latched_error

    async def start(self) -> None:
        if self._state is not _DetectorState.DISCONNECTED:
            raise PerceptionError(
                f"start() called twice (state={self._state.value})"
            )
        self._loop = asyncio.get_running_loop()
        self._state = _DetectorState.STARTING
        try:
            self._resolved_device = _resolve_device(self._config.yolo_device)
            self._logger.info(
                "pose_engine_device_resolved",
                requested=self._config.yolo_device,
                resolved=self._resolved_device,
            )
            shm_size = (
                self._config.capture_width
                * self._config.capture_height
                * _BYTES_PER_PIXEL
            )
            self._shm = shared_memory.SharedMemory(create=True, size=shm_size)
            self._executor = ProcessPoolExecutor(max_workers=_INFLIGHT_SLOTS)
            await asyncio.wait_for(
                self._loop.run_in_executor(
                    self._executor,
                    _pose_worker.warmup,
                    self._resolved_device,
                    str(self._config.yolo_model_path),
                ),
                timeout=_WORKER_WARMUP_TIMEOUT_SEC,
            )
            self._logger.info("pose_engine_warmup_complete")
        except PoseEngineUnavailableError as exc:
            self._state = _DetectorState.FAULTED
            self._latched_error = exc
            await self._teardown_partial()
            raise
        except Exception as exc:  # documented translator (cross-process boundary)
            self._state = _DetectorState.FAULTED
            err = PoseEngineUnavailableError(
                expected=self._config.yolo_device,
                available=[self._resolved_device or "unknown"],
            )
            self._latched_error = err
            await self._teardown_partial()
            raise err from exc
        self._state = _DetectorState.RUNNING

    async def detect(self, frame: Frame) -> list[Detection]:
        """Production inference path: copy frame into shm, submit to worker, await result.

        BL-02 fix (2026-05-05 review): the previous implementation raised
        NotImplementedError, which would have latched FAULTED on the very
        first frame for any orchestrator wired to this production engine
        (PoseDetector.consume() -> _infer_one() -> engine.detect() ->
        NotImplementedError). The runtime path now mirrors RESEARCH 04
        Pattern 4 and the Plan 04-03 PoseDetector orchestrator-side wiring:

            1. Validate state == RUNNING (fail-fast outside lifecycle).
            2. Copy frame.image bytes into the long-lived SharedMemory
               block (the only copy in the pipeline; the IPC handoff to
               the worker is zero-copy from this side onward).
            3. Submit ``_pose_worker.infer`` with (shm_name, shape,
               timestamp_ns, device, model_path, botsort_yaml_path) and
               await the Future via ``loop.run_in_executor``.
            4. Translate the picklable ``PoseEngineResult`` into the
               public ``Detection`` DTOs (re-validates field invariants
               at the trust boundary).

        Test coverage policy: there is NO CI test for this method --
        it requires real torch + ultralytics + model weights and is
        deferred to the Phase 8 QA-04 on-stage hardware run per
        CONTEXT.md Area 1 + 04-03-SUMMARY.md "Out-of-Scope Deferrals".
        The seam (PoseEngine Protocol + FakePoseEngine) covers every
        orchestrator-side path in CI; this method is the production
        plumbing that closes the loop.
        """
        if self._state is not _DetectorState.RUNNING:
            raise PerceptionError(
                f"detect() while state={self._state.value}; expected RUNNING"
            )
        assert self._shm is not None, "shm allocated in start()"
        assert self._executor is not None, "executor allocated in start()"
        assert self._loop is not None, "loop captured in start()"
        assert self._resolved_device is not None, "device resolved in start()"
        # WR-05 mirror: refuse to copy a frame whose bytes overrun the long-lived
        # shm block (e.g. camera reconfigured to a higher resolution mid-run).
        # The matching worker-side guard lives in _pose_worker.infer.
        if frame.image.nbytes > self._shm.size:
            raise PerceptionError(
                f"frame {frame.image.shape} requires {frame.image.nbytes} bytes; "
                f"shm block has only {self._shm.size}"
            )
        view = np.ndarray(
            frame.image.shape, dtype=np.uint8, buffer=self._shm.buf,
        )
        view[:] = frame.image
        botsort_arg = (
            str(self._config.botsort_yaml_path)
            if self._config.botsort_yaml_path is not None
            else None
        )
        result = await self._loop.run_in_executor(
            self._executor,
            _pose_worker.infer,
            self._shm.name,
            frame.image.shape,
            frame.timestamp_ns,
            self._resolved_device,
            str(self._config.yolo_model_path),
            botsort_arg,
        )
        return [Detection.model_validate(d.model_dump()) for d in result.detections]

    async def close(self) -> None:
        await self._teardown_partial()
        if self._state is not _DetectorState.FAULTED:
            self._state = _DetectorState.CLOSED

    async def _teardown_partial(self) -> None:
        """Close-order mirror of obs_camera.py:581-622 (Pitfall 7 discipline)."""
        if self._executor is not None:
            await asyncio.to_thread(
                self._executor.shutdown, wait=True, cancel_futures=True
            )
            self._executor = None
        if self._shm is not None:
            try:
                self._shm.close()
                self._shm.unlink()  # no-op on Windows but cheap insurance
            finally:
                self._shm = None


class PoseDetector:
    """Orchestrator: holds a ``PoseEngine``, drop-oldest at ingress, async iterator surface.

    Architecture (RESEARCH 04 Pattern 4 + Open Question 1):
        * In-flight slot = 1: ``_inflight: asyncio.Task | None`` IS the queue.
          New ``consume()`` while an inference is still running cancels the
          old task and emits ``inference_drop_oldest`` WARN.
        * Output queue: ``asyncio.Queue[list[Detection]](maxsize=256)`` --
          bounded per Phase 2/3 precedent. Consumer = downstream
          ``SubjectTracker.consume`` via the orchestrator (Phase 6).
        * Lifecycle (single-shot per Phase 2/3 precedent): ``start()`` opens
          the engine; ``stop()`` cancels in-flight + closes engine + preserves
          FAULTED (mirror obs_camera.py:581-622).

    Public surface mirrors Phase 2 ``motor.events()`` and Phase 3
    ``camera.frames()``: ``async def detections() -> AsyncIterator[list[Detection]]``.
    """

    def __init__(self, *, config: Config, engine: PoseEngine) -> None:
        self._config = config
        self._engine = engine
        self._logger = structlog.get_logger(module="pose_detector_orchestrator")
        self._state: _DetectorState = _DetectorState.DISCONNECTED
        self._inflight: asyncio.Task[None] | None = None
        self._out_queue: asyncio.Queue[list[Detection]] = asyncio.Queue(maxsize=_OUT_QUEUE_MAX)
        self._latched_error: PerceptionError | None = None

    # ---------- read-only status surface ----------
    @property
    def state(self) -> _DetectorState:
        return self._state

    @property
    def is_running(self) -> bool:
        return self._state is _DetectorState.RUNNING

    @property
    def last_error(self) -> PerceptionError | None:
        return self._latched_error

    # ---------- lifecycle ----------
    async def start(self) -> None:
        if self._state is not _DetectorState.DISCONNECTED:
            raise PerceptionError(
                f"start() called twice (state={self._state.value})"
            )
        self._state = _DetectorState.STARTING
        # Production engine (UltralyticsPoseEngine) has its own start();
        # FakePoseEngine has no start(). Honor whichever is available.
        engine_start = getattr(self._engine, "start", None)
        if callable(engine_start):
            try:
                maybe_coro = engine_start()
                if asyncio.iscoroutine(maybe_coro):
                    await maybe_coro
            except PerceptionError as exc:
                self._state = _DetectorState.FAULTED
                self._latched_error = exc
                raise
            except Exception as exc:
                self._state = _DetectorState.FAULTED
                err = PerceptionError(f"engine start failed: {exc}")
                self._latched_error = err
                raise err from exc
        self._state = _DetectorState.RUNNING
        self._logger.info("pose_detector_started")

    async def stop(self) -> None:
        """Pitfall 7 close-order: cancel inflight first, then close engine.

        Mirror obs_camera.py:581-622 -- preserve FAULTED across stop().
        """
        if self._inflight is not None and not self._inflight.done():
            self._inflight.cancel()
            try:
                await self._inflight
            except (asyncio.CancelledError, PerceptionError):
                pass  # cancellation or fault is the expected path here
            except Exception as exc:  # noqa: BLE001 -- documented translator (inflight swallow on stop)
                self._logger.warning(
                    "pose_detector_inflight_swallow_on_stop",
                    error=str(exc),
                )
        self._inflight = None
        try:
            await self._engine.close()
        except Exception as exc:  # noqa: BLE001 -- documented translator (engine boundary; W5 fix)
            self._logger.warning(
                "pose_engine_close_failed",
                error=str(exc),
            )
            # W5 fix: a failed engine close is a fault. Latch FAULTED + typed
            # PerceptionError so downstream callers / tests cannot mistake the
            # state for a clean shutdown.
            self._state = _DetectorState.FAULTED
            self._latched_error = PerceptionError(
                f"engine close failed: {exc}"
            )
        if self._state is not _DetectorState.FAULTED:
            self._state = _DetectorState.CLOSED
        self._logger.info("pose_detector_stopped", state=self._state.value)

    # ---------- ingress ----------
    async def consume(self, frame: Frame) -> None:
        """RESEARCH 04 Pattern 4: drop-oldest at ingress (in-flight slot = 1).

        New frame while inference still running:
          * Log ``inference_drop_oldest`` WARN (Phase 2/3 drop-oldest precedent).
          * Cancel the previous task; await its cancellation.
          * Submit a new inference task tied to the new frame.

        ``consume`` returns immediately; the result lands in ``_out_queue``
        when the inference task completes.
        """
        self._raise_if_latched()
        if self._state is not _DetectorState.RUNNING:
            raise PerceptionError(
                f"consume() while state={self._state.value}; expected RUNNING"
            )
        if self._inflight is not None and not self._inflight.done():
            self._logger.warning(
                "inference_drop_oldest",
                reason="previous_inference_still_running",
                dropped_timestamp_ns=frame.timestamp_ns,
            )
            self._inflight.cancel()
            try:
                await self._inflight
            except (asyncio.CancelledError, PerceptionError):
                pass
            except Exception as exc:  # noqa: BLE001 -- documented translator (drop-oldest swallow); see comment block below
                # The cause is already routed through ``_infer_one`` (latched
                # in ``_latched_error`` + ``pose_engine_fault`` ERROR log).
                # Here we only log at DEBUG so cancellation noise doesn't
                # shadow the real fault on the orchestrator surface.
                self._logger.debug(
                    "pose_detector_drop_oldest_swallow",
                    error=str(exc),
                )
        self._inflight = asyncio.create_task(self._infer_one(frame))

    async def _infer_one(self, frame: Frame) -> None:
        """Single-frame worker: call engine.detect, route result or fault."""
        try:
            detections = await self._engine.detect(frame)
        except asyncio.CancelledError:
            raise
        except PerceptionError as exc:
            self._latched_error = exc
            self._state = _DetectorState.FAULTED
            self._logger.error(
                "pose_engine_fault",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return
        except Exception as exc:  # noqa: BLE001 -- documented translator (engine boundary)
            err = PerceptionError(f"engine detect failed: {exc}")
            self._latched_error = err
            self._state = _DetectorState.FAULTED
            self._logger.error(
                "pose_engine_fault",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return
        # Route result. If queue is full, drop-oldest there too (mirror Phase 3).
        if self._out_queue.full():
            try:
                _ = self._out_queue.get_nowait()
                self._logger.warning(
                    "pose_detector_output_drop_oldest",
                    queue_size=self._out_queue.maxsize,
                )
            except asyncio.QueueEmpty:
                pass
        await self._out_queue.put(detections)

    # ---------- egress ----------
    async def detections(self) -> AsyncIterator[list[Detection]]:
        """Async iterator over per-frame detection lists.

        Mirrors Phase 2 ``motor.events()`` and Phase 3 ``camera.frames()``.
        Exits cleanly via ``StopAsyncIteration`` after stop() drains the queue.
        """
        while True:
            if self._state in (_DetectorState.CLOSED, _DetectorState.FAULTED):
                if self._latched_error is not None and self._out_queue.empty():
                    raise self._latched_error
                if self._out_queue.empty():
                    return  # clean StopAsyncIteration
            try:
                dets = await asyncio.wait_for(
                    self._out_queue.get(), timeout=_DETECTIONS_POLL_TIMEOUT_SEC,
                )
            except TimeoutError:
                continue
            yield dets

    # ---------- helpers ----------
    def _raise_if_latched(self) -> None:
        if self._latched_error is not None:
            raise self._latched_error
