"""Wave-1 tests for pastor_tracker.perception.pose_detector.

Plan 03 (Wave 3) adds tests for orchestrator drop-oldest and end-to-end.
"""
from __future__ import annotations

from multiprocessing import shared_memory

import numpy as np
import pytest

from pastor_tracker.core.types import Frame
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
