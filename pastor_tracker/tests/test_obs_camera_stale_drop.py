"""Stale-frame consumer-side drop tests for pastor_tracker.io.obs_camera (IO-CAM-04).

Stale-drop policy: frames with ``(now_ns - timestamp_ns) > 100 ms`` are
dropped at retrieval and logged as WARN ``frame_stale_dropped``. The
capture thread MUST NOT decide age; consumer-side check only.

Implementation note: tests park the capture thread inside ``read()`` via
a :class:`threading.Event` so the live producer cannot race the test's
manually-injected frames. Mirrors the Phase 2 ``_BlockingTransport`` idiom.
"""
from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace

import numpy as np
import numpy.typing as npt
import structlog

from pastor_tracker.config import Config
from pastor_tracker.core.types import Frame
from pastor_tracker.io.obs_camera import (
    _STALE_FRAME_MAX_AGE_NS,
    ObsCamera,
)
from tests.fixtures.camera_traces import make_solid_bgr


class _BlockingVideoSource:
    """Fake VideoSource whose ``read()`` blocks on an Event after first frame.

    The capture thread parks inside ``read()``; no live frames hit the
    queue while the test injects scripted Frames by hand. Mirrors the
    Phase 2 ``_BlockingTransport`` idiom.
    """

    def __init__(self) -> None:
        self._gate = threading.Event()
        self._opened = True
        self._first_frame_returned = False
        self.release_calls: int = 0
        self.set_resolution_calls: list[tuple[int, int, int]] = []

    def read(self) -> tuple[bool, npt.NDArray[np.uint8] | None]:
        # Return one frame so start() can complete, then block.
        if not self._first_frame_returned:
            self._first_frame_returned = True
            return True, make_solid_bgr(1920, 1080, (0, 0, 0))
        self._gate.wait()
        return False, None

    def set_resolution(self, width: int, height: int, fps: int) -> None:
        self.set_resolution_calls.append((width, height, fps))

    def is_opened(self) -> bool:
        return self._opened

    def release(self) -> None:
        self._gate.set()  # unblock any pending read()
        self._opened = False
        self.release_calls += 1


async def _make_parked_camera(
    valid_config_dict: dict[str, object],
) -> tuple[ObsCamera, _BlockingVideoSource]:
    """Build an ObsCamera with a parked capture thread so tests own the queue.

    After ``start()`` returns the capture thread is parked inside the
    second ``read()`` call. Tests are free to inject Frames into
    ``cam._frames_queue`` in deterministic order.
    """
    src = _BlockingVideoSource()
    cam = ObsCamera(
        Config(**valid_config_dict),
        video_source_factory=lambda *_a, **_kw: src,
        filter_graph_factory=lambda: SimpleNamespace(
            get_input_devices=lambda: ["OBS Virtual Camera"]
        ),
    )
    await cam.start()
    # Drain the bootstrap frame the parking source yielded so the queue
    # is empty before the test injects.
    while not cam._frames_queue.empty():
        try:
            cam._frames_queue.get_nowait()
        except asyncio.QueueEmpty:
            break
    return cam, src


async def test_consumer_drops_stale_frame(
    valid_config_dict: dict[str, object],
) -> None:
    cam, _src = await _make_parked_camera(valid_config_dict)
    try:
        stale_ts_ns = time.perf_counter_ns() - 200_000_000
        stale = Frame(
            image=make_solid_bgr(1920, 1080, (0, 0, 0)),
            width=1920,
            height=1080,
            timestamp_ns=stale_ts_ns,
        )
        fresh = Frame(
            image=make_solid_bgr(1920, 1080, (255, 255, 255)),
            width=1920,
            height=1080,
            timestamp_ns=time.perf_counter_ns(),
        )
        cam._frames_queue.put_nowait(stale)
        cam._frames_queue.put_nowait(fresh)
        with structlog.testing.capture_logs() as caplog:
            yielded: list[Frame] = []
            async for f in cam.frames():
                yielded.append(f)
                break
            drops = [
                r for r in caplog if r.get("event") == "frame_stale_dropped"
            ]
        assert len(drops) >= 1
        assert drops[0]["threshold_ms"] == 100.0
        assert drops[0]["age_ms"] >= 100.0
        assert yielded[0] is fresh
    finally:
        await cam.stop()


async def test_fresh_frame_passes_through(
    valid_config_dict: dict[str, object],
) -> None:
    cam, _src = await _make_parked_camera(valid_config_dict)
    try:
        fresh = Frame(
            image=make_solid_bgr(1920, 1080, (1, 2, 3)),
            width=1920,
            height=1080,
            timestamp_ns=time.perf_counter_ns(),
        )
        cam._frames_queue.put_nowait(fresh)
        with structlog.testing.capture_logs() as caplog:
            yielded: list[Frame] = []
            async for f in cam.frames():
                yielded.append(f)
                break
            drops = [
                r for r in caplog if r.get("event") == "frame_stale_dropped"
            ]
        assert len(drops) == 0
        assert yielded[0] is fresh
    finally:
        await cam.stop()


def test_stale_drop_threshold_constant() -> None:
    assert _STALE_FRAME_MAX_AGE_NS == 100_000_000


async def test_below_threshold_not_dropped(
    valid_config_dict: dict[str, object],
) -> None:
    cam, _src = await _make_parked_camera(valid_config_dict)
    try:
        ts = time.perf_counter_ns() - 50_000_000
        just_under = Frame(
            image=make_solid_bgr(1920, 1080, (4, 5, 6)),
            width=1920,
            height=1080,
            timestamp_ns=ts,
        )
        cam._frames_queue.put_nowait(just_under)
        with structlog.testing.capture_logs() as caplog:
            yielded: list[Frame] = []
            async for f in cam.frames():
                yielded.append(f)
                break
            drops = [
                r for r in caplog if r.get("event") == "frame_stale_dropped"
            ]
        assert len(drops) == 0
        assert yielded[0] is just_under
    finally:
        await cam.stop()
