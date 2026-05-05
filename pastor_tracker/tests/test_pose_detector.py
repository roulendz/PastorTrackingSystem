"""Wave-1 + Wave-3 tests for pastor_tracker.perception.pose_detector.

Wave 1 (Plan 04-01) covers the seam (PoseEngine Protocol, error hierarchy,
state enum, shared-memory roundtrip, FakePoseEngine scripted-detection
contract). Wave 3 (Plan 04-03) covers the orchestrator: start/stop
lifecycle, double-start guard, drop-oldest at ingress, async-iterator
drain in submission order, and FAULTED preservation across stop().
"""
from __future__ import annotations

import asyncio
from multiprocessing import shared_memory
from typing import Any

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
async def test_pose_detector_start_stop_clean(valid_config_dict: dict[str, Any]) -> None:
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
async def test_pose_detector_double_start_raises(valid_config_dict: dict[str, Any]) -> None:
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
async def test_drop_oldest_when_inference_lags(valid_config_dict: dict[str, Any]) -> None:
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
async def test_detections_iterator_drains_in_order(valid_config_dict: dict[str, Any]) -> None:
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
async def test_faulted_preserved_across_stop(valid_config_dict: dict[str, Any]) -> None:
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
