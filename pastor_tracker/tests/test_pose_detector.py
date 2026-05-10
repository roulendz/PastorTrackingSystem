"""Wave-1 + Wave-3 tests for pastor_tracker.perception.pose_detector.

Wave 1 (Plan 04-01) covers the seam (PoseEngine Protocol, error hierarchy,
state enum, shared-memory roundtrip, FakePoseEngine scripted-detection
contract). Wave 3 (Plan 04-03) covers the orchestrator: start/stop
lifecycle, double-start guard, drop-oldest at ingress, async-iterator
drain in submission order, and FAULTED preservation across stop().
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from multiprocessing import shared_memory

import numpy as np
import pytest
import structlog

from pastor_tracker.core.types import Detection, Frame
from pastor_tracker.perception import _pose_worker
from pastor_tracker.perception.pose_detector import (
    PerceptionError,
    PoseEngine,
    PoseEngineUnavailableError,
    _DetectorState,
)
from tests.fixtures.pose_traces import FakePoseEngine, make_detection


def test_pose_engine_protocol_compliance() -> None:
    """FakePoseEngine satisfies the runtime_checkable PoseEngine Protocol."""
    engine = FakePoseEngine(script=[])
    assert isinstance(engine, PoseEngine)


def test_shared_memory_ndarray_roundtrip() -> None:
    """Bytes written into a long-lived shm block are byte-identical when re-attached.

    RESEARCH 04 Pattern 3 invariant: parent allocates once, child attaches by name.
    """
    h, w, c = 128, 256, 3
    block = shared_memory.SharedMemory(create=True, size=h * w * c)
    second: shared_memory.SharedMemory | None = None
    try:
        original = np.random.randint(0, 256, size=(h, w, c), dtype=np.uint8)
        view = np.ndarray((h, w, c), dtype=np.uint8, buffer=block.buf)
        view[:] = original
        second = shared_memory.SharedMemory(name=block.name)
        roundtrip = np.ndarray((h, w, c), dtype=np.uint8, buffer=second.buf)
        assert np.array_equal(original, roundtrip)
    finally:
        if second is not None:
            second.close()
        block.close()
        block.unlink()


def test_perception_error_hierarchy() -> None:
    err = PoseEngineUnavailableError(expected="cuda", available=["cpu"])
    assert isinstance(err, PerceptionError)
    assert err.expected == "cuda"
    assert err.available == ["cpu"]
    assert "expected='cuda'" in str(err)
    assert "available=['cpu']" in str(err)


def test_detector_state_enum_values() -> None:
    assert _DetectorState.DISCONNECTED.value == "disconnected"
    assert _DetectorState.STARTING.value == "starting"
    assert _DetectorState.RUNNING.value == "running"
    assert _DetectorState.FAULTED.value == "faulted"
    assert _DetectorState.CLOSED.value == "closed"


def test_pose_worker_module_has_top_level_functions() -> None:
    """Pitfall 4: spawn'd children import the worker module by name; targets MUST be top-level."""
    assert callable(_pose_worker.warmup)
    assert callable(_pose_worker.infer)


@pytest.mark.asyncio
async def test_fake_pose_engine_yields_scripted_detections() -> None:
    d1 = make_detection(cx=0.5, cy=0.5, track_id=1, conf=0.9, timestamp_ns=1_000)
    d2 = make_detection(cx=0.4, cy=0.5, track_id=2, conf=0.85, timestamp_ns=1_000)
    d3 = make_detection(cx=0.6, cy=0.5, track_id=1, conf=0.88, timestamp_ns=3_000)
    engine = FakePoseEngine(script=[[d1, d2], [], [d3]])
    img = np.zeros((480, 640, 3), dtype=np.uint8)
    f1 = Frame(image=img, width=640, height=480, timestamp_ns=1_000)
    f2 = Frame(image=img, width=640, height=480, timestamp_ns=2_000)
    f3 = Frame(image=img, width=640, height=480, timestamp_ns=3_000)
    assert await engine.detect(f1) == [d1, d2]
    assert await engine.detect(f2) == []
    assert await engine.detect(f3) == [d3]
    assert engine.detect_calls == [1_000, 2_000, 3_000]


# --- Wave-3 (Plan 04-03) orchestrator tests --------------------------------


@pytest.mark.asyncio
async def test_pose_detector_start_stop_clean(valid_config_dict: dict[str, object]) -> None:
    """Clean lifecycle: DISCONNECTED -> RUNNING -> CLOSED with no FAULT, no WARN."""
    from pastor_tracker.config import Config
    from pastor_tracker.perception.pose_detector import PoseDetector

    detector = PoseDetector(
        config=Config(**valid_config_dict), engine=FakePoseEngine(script=[]),
    )
    await detector.start()
    assert detector.state is _DetectorState.RUNNING
    await detector.stop()
    assert detector.state is _DetectorState.CLOSED


@pytest.mark.asyncio
async def test_pose_detector_double_start_raises(valid_config_dict: dict[str, object]) -> None:
    """Single-shot guard: a second start() after success raises PerceptionError."""
    from pastor_tracker.config import Config
    from pastor_tracker.perception.pose_detector import PoseDetector

    detector = PoseDetector(
        config=Config(**valid_config_dict), engine=FakePoseEngine(script=[]),
    )
    await detector.start()
    try:
        with pytest.raises(PerceptionError, match=r"start\(\) called twice"):
            await detector.start()
    finally:
        await detector.stop()


@pytest.mark.asyncio
async def test_drop_oldest_when_inference_lags(valid_config_dict: dict[str, object]) -> None:
    """RESEARCH 04 Pattern 4: bursty consume() against slow engine -> drop-oldest WARN.

    W6 fix: hoist Config construction above the for-loop -- avoid rebuilding it 5+ times.
    """
    from pastor_tracker.config import Config
    from pastor_tracker.perception.pose_detector import PoseDetector
    from tests.fixtures.pose_traces import SlowFakePoseEngine

    cfg = Config(**valid_config_dict)  # W6: hoisted above loop
    engine = SlowFakePoseEngine(per_call_delay_sec=0.1, script=[[] for _ in range(5)])
    detector = PoseDetector(config=cfg, engine=engine)
    await detector.start()
    try:
        img = np.zeros((cfg.capture_height, cfg.capture_width, 3), dtype=np.uint8)
        with structlog.testing.capture_logs() as caplog:
            for k in range(5):
                f = Frame(
                    image=img,
                    width=cfg.capture_width,
                    height=cfg.capture_height,
                    timestamp_ns=k * 33_000_000,
                )
                await detector.consume(f)
                await asyncio.sleep(1 / 30)  # 30 fps cadence
            await asyncio.sleep(0.3)  # let final inference finish
        events = [e for e in caplog if e.get("event") == "inference_drop_oldest"]
        assert len(events) >= 4, (
            f"expected >= 4 drop-oldest WARNs, got {len(events)}"
        )
    finally:
        await detector.stop()


@pytest.mark.asyncio
async def test_detections_iterator_drains_in_order(valid_config_dict: dict[str, object]) -> None:
    """Async iterator drains processed-frame results in submission order."""
    from pastor_tracker.config import Config
    from pastor_tracker.perception.pose_detector import PoseDetector

    cfg = Config(**valid_config_dict)
    scripted: list[list[Detection]] = [
        [make_detection(cx=0.5, cy=0.5, track_id=k + 1, conf=0.9, timestamp_ns=k * 33_000_000)]
        for k in range(3)
    ]
    engine = FakePoseEngine(script=scripted)
    detector = PoseDetector(config=cfg, engine=engine)
    await detector.start()
    try:
        img = np.zeros((cfg.capture_height, cfg.capture_width, 3), dtype=np.uint8)
        for k in range(3):
            f = Frame(
                image=img,
                width=cfg.capture_width,
                height=cfg.capture_height,
                timestamp_ns=k * 33_000_000,
            )
            await detector.consume(f)
            await asyncio.sleep(0.02)  # let each inference finish
        received: list[list[Detection]] = []
        it = detector.detections()
        for _ in range(3):
            received.append(await asyncio.wait_for(anext(it), timeout=1.0))
        assert [r[0].track_id for r in received] == [1, 2, 3]
    finally:
        await detector.stop()


@pytest.mark.asyncio
async def test_faulted_preserved_across_stop(valid_config_dict: dict[str, object]) -> None:
    """Phase 3 obs_camera precedent: FAULTED state survives stop()."""
    from pastor_tracker.config import Config
    from pastor_tracker.perception.pose_detector import PoseDetector
    from tests.fixtures.pose_traces import FailingPoseEngine

    cfg = Config(**valid_config_dict)
    engine = FailingPoseEngine()
    detector = PoseDetector(config=cfg, engine=engine)
    await detector.start()
    img = np.zeros((cfg.capture_height, cfg.capture_width, 3), dtype=np.uint8)
    f = Frame(
        image=img,
        width=cfg.capture_width,
        height=cfg.capture_height,
        timestamp_ns=1_000,
    )
    await detector.consume(f)
    # Allow inference task to fail
    await asyncio.sleep(0.05)
    assert detector.state is _DetectorState.FAULTED
    await detector.stop()
    assert detector.state is _DetectorState.FAULTED  # NOT CLOSED


@pytest.mark.asyncio
async def test_pose_detector_properties_reflect_state(
    valid_config_dict: dict[str, object],
) -> None:
    """Read-only dashboard surface: is_running, last_error track _state correctly."""
    from pastor_tracker.config import Config
    from pastor_tracker.perception.pose_detector import PoseDetector

    detector = PoseDetector(
        config=Config(**valid_config_dict), engine=FakePoseEngine(script=[]),
    )
    assert detector.is_running is False
    assert detector.last_error is None
    await detector.start()
    assert detector.is_running is True
    assert detector.state is _DetectorState.RUNNING
    await detector.stop()
    assert detector.is_running is False


@pytest.mark.asyncio
async def test_consume_before_start_raises(valid_config_dict: dict[str, object]) -> None:
    """consume() while DISCONNECTED raises PerceptionError (state guard)."""
    from pastor_tracker.config import Config
    from pastor_tracker.perception.pose_detector import PoseDetector

    cfg = Config(**valid_config_dict)
    detector = PoseDetector(config=cfg, engine=FakePoseEngine(script=[]))
    img = np.zeros((cfg.capture_height, cfg.capture_width, 3), dtype=np.uint8)
    f = Frame(
        image=img,
        width=cfg.capture_width,
        height=cfg.capture_height,
        timestamp_ns=1_000,
    )
    with pytest.raises(PerceptionError, match=r"consume\(\) while state="):
        await detector.consume(f)


@pytest.mark.asyncio
async def test_engine_close_failure_latches_faulted(
    valid_config_dict: dict[str, object],
) -> None:
    """W5 fix: a failed engine.close() during stop() latches FAULTED + last_error.

    Cover the close-time translator that converts a generic engine close
    exception into a typed PerceptionError + FAULTED gate.
    """
    from pastor_tracker.config import Config
    from pastor_tracker.perception.pose_detector import PoseDetector

    class _CloseFailureEngine:
        def __init__(self) -> None:
            self._closed = False

        async def detect(self, frame: Frame) -> list[Detection]:
            return []

        async def close(self) -> None:
            raise RuntimeError("simulated close failure")

    cfg = Config(**valid_config_dict)
    detector = PoseDetector(config=cfg, engine=_CloseFailureEngine())
    await detector.start()
    await detector.stop()
    assert detector.state is _DetectorState.FAULTED
    err = detector.last_error
    assert err is not None
    assert "engine close failed" in str(err)


@pytest.mark.asyncio
async def test_non_perception_error_translates_to_perception_error(
    valid_config_dict: dict[str, object],
) -> None:
    """A non-PerceptionError exception from engine.detect() is translated.

    Covers the generic-Exception translator branch in ``_infer_one``: the
    error is wrapped in PerceptionError, latched, and state goes FAULTED.
    """
    from pastor_tracker.config import Config
    from pastor_tracker.perception.pose_detector import PoseDetector

    class _BareErrorEngine:
        async def detect(self, frame: Frame) -> list[Detection]:
            raise RuntimeError("bare engine fault")

        async def close(self) -> None:
            return None

    cfg = Config(**valid_config_dict)
    detector = PoseDetector(config=cfg, engine=_BareErrorEngine())
    await detector.start()
    img = np.zeros((cfg.capture_height, cfg.capture_width, 3), dtype=np.uint8)
    f = Frame(
        image=img,
        width=cfg.capture_width,
        height=cfg.capture_height,
        timestamp_ns=1_000,
    )
    await detector.consume(f)
    await asyncio.sleep(0.05)  # let _infer_one complete
    assert detector.state is _DetectorState.FAULTED
    err = detector.last_error
    assert err is not None
    assert "engine detect failed" in str(err)
    assert "bare engine fault" in str(err)
    await detector.stop()
    assert detector.state is _DetectorState.FAULTED


# --- Phase 6 Wave-0 (Plan 06-01): PoseDetector.stream() helper -------------


def _make_test_frame(cfg_w: int, cfg_h: int, timestamp_ns: int) -> Frame:
    """Build a synthetic Frame for stream() tests."""
    img = np.zeros((cfg_h, cfg_w, 3), dtype=np.uint8)
    return Frame(image=img, width=cfg_w, height=cfg_h, timestamp_ns=timestamp_ns)


@pytest.mark.asyncio
async def test_stream_yields_frame_detection_pairs(
    valid_config_dict: dict[str, object],
) -> None:
    """D-03/D-17: stream() yields (Frame, list[Detection]) tuples; identity preserved."""
    from pastor_tracker.config import Config
    from pastor_tracker.core.types import Frame as FrameT
    from pastor_tracker.perception.pose_detector import PoseDetector

    cfg = Config(**valid_config_dict)
    scripted: list[list[Detection]] = [
        [make_detection(cx=0.5, cy=0.5, track_id=k + 1, conf=0.9, timestamp_ns=k * 33_333_333)]
        for k in range(3)
    ]
    engine = FakePoseEngine(script=list(scripted))
    detector = PoseDetector(config=cfg, engine=engine)
    await detector.start()
    try:
        frames = [
            _make_test_frame(cfg.capture_width, cfg.capture_height, ts)
            for ts in (0, 33_333_333, 66_666_666)
        ]

        async def _frame_gen() -> AsyncIterator[FrameT]:
            for fr in frames:
                yield fr

        received: list[tuple[FrameT, list[Detection]]] = []
        async for pair in detector.stream(_frame_gen()):
            received.append(pair)
            if len(received) == 3:
                break
        assert len(received) == 3
        for idx, (frame_out, dets_out) in enumerate(received):
            assert frame_out is frames[idx], "Frame identity must be preserved"
            assert frame_out.timestamp_ns == frames[idx].timestamp_ns
            assert dets_out == scripted[idx]
    finally:
        await detector.stop()


@pytest.mark.asyncio
async def test_stream_exits_cleanly_when_frames_exhausted(
    valid_config_dict: dict[str, object],
) -> None:
    """stream() returns cleanly via StopAsyncIteration when upstream frames() ends."""
    from pastor_tracker.config import Config
    from pastor_tracker.core.types import Frame as FrameT
    from pastor_tracker.perception.pose_detector import PoseDetector

    cfg = Config(**valid_config_dict)
    scripted: list[list[Detection]] = [
        [make_detection(cx=0.5, cy=0.5, track_id=k + 1, conf=0.9, timestamp_ns=k * 33_000_000)]
        for k in range(2)
    ]
    engine = FakePoseEngine(script=list(scripted))
    detector = PoseDetector(config=cfg, engine=engine)
    await detector.start()
    try:
        frames = [
            _make_test_frame(cfg.capture_width, cfg.capture_height, k * 33_000_000)
            for k in range(2)
        ]

        async def _frame_gen() -> AsyncIterator[FrameT]:
            for fr in frames:
                yield fr

        count = 0
        async for _pair in detector.stream(_frame_gen()):
            count += 1
        assert count == 2, f"expected 2 yields, got {count}"
    finally:
        await detector.stop()


@pytest.mark.asyncio
async def test_stream_propagates_engine_fault(
    valid_config_dict: dict[str, object],
) -> None:
    """Engine fault inside stream() surfaces the latched PerceptionError."""
    from pastor_tracker.config import Config
    from pastor_tracker.core.types import Frame as FrameT
    from pastor_tracker.perception.pose_detector import PoseDetector
    from tests.fixtures.pose_traces import FailingPoseEngine

    cfg = Config(**valid_config_dict)
    detector = PoseDetector(config=cfg, engine=FailingPoseEngine())
    await detector.start()
    try:
        frames = [
            _make_test_frame(cfg.capture_width, cfg.capture_height, k * 33_000_000)
            for k in range(3)
        ]

        async def _frame_gen() -> AsyncIterator[FrameT]:
            for fr in frames:
                yield fr

        with pytest.raises(PerceptionError, match="simulated engine fault"):
            async for _pair in detector.stream(_frame_gen()):
                pass
    finally:
        await detector.stop()


@pytest.mark.asyncio
async def test_stream_drops_stale_internally(
    valid_config_dict: dict[str, object],
) -> None:
    """BL-01 still active when wired through stream(): drop-oldest WARNs emitted."""
    from pastor_tracker.config import Config
    from pastor_tracker.core.types import Frame as FrameT
    from pastor_tracker.perception.pose_detector import PoseDetector
    from tests.fixtures.pose_traces import SlowFakePoseEngine

    cfg = Config(**valid_config_dict)
    engine = SlowFakePoseEngine(per_call_delay_sec=0.1, script=[[] for _ in range(5)])
    detector = PoseDetector(config=cfg, engine=engine)
    await detector.start()
    try:
        frames = [
            _make_test_frame(cfg.capture_width, cfg.capture_height, k * 33_333_333)
            for k in range(5)
        ]

        async def _frame_gen() -> AsyncIterator[FrameT]:
            for fr in frames:
                yield fr
                # 30 fps cadence between yields drives drop-oldest
                await asyncio.sleep(1 / 30)

        yielded = 0
        with structlog.testing.capture_logs() as caplog:
            async for _pair in detector.stream(_frame_gen()):
                yielded += 1
        drop_events = [e for e in caplog if e.get("event") == "inference_drop_oldest"]
        assert len(drop_events) >= 1, (
            f"expected at least one drop-oldest WARN, got {len(drop_events)}"
        )
        assert yielded <= 5, f"yielded={yielded} cannot exceed input frame count"
    finally:
        await detector.stop()
