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
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import shared_memory
from typing import Final, Protocol, runtime_checkable

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
        """Plan 03 implements drop-oldest + executor.submit. Plan 01 NotImplemented."""
        raise NotImplementedError(
            "UltralyticsPoseEngine.detect lands in Plan 04-03 (Wave 3)"
        )

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
    """Placeholder. Plan 04-03 (Wave 3) implements the full orchestrator."""

    def __init__(self, *, config: Config, engine: PoseEngine) -> None:
        self._config = config
        self._engine = engine
        self._logger = structlog.get_logger(module="pose_detector_orchestrator")
