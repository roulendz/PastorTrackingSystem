"""Lifecycle tests for pastor_tracker.io.obs_camera (IO-CAM-01 / IO-CAM-04 + lifecycle).

Covers start/stop / first-frame timeout / queue drop-oldest / monotonic
timestamps / status properties / double-start guard / source release on
all paths. :class:`FakeVideoSource` is the only dependency seam.
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest
import structlog

from pastor_tracker.config import Config
from pastor_tracker.io.obs_camera import (
    CameraError,
    CameraOpenError,
    ObsCamera,
    _CamState,
)
from tests.fixtures.camera_traces import (
    FakeVideoSource,
    _ScriptedFrame,
    _started_camera,
    make_solid_bgr,
)


async def test_start_returns_after_first_frame(
    valid_config_dict: dict[str, object],
) -> None:
    cam, _fake = await _started_camera(valid_config_dict)
    try:
        assert cam.state == _CamState.RUNNING
        assert cam.is_running is True
        assert cam.current_resolution == (1920, 1080)
        assert cam.last_error is None
    finally:
        await cam.stop()


async def test_start_then_stop_clean(
    valid_config_dict: dict[str, object],
) -> None:
    cam, fake = await _started_camera(valid_config_dict)
    await cam.stop()
    assert cam.state == _CamState.CLOSED
    assert fake.release_calls >= 1


async def test_double_start_raises(
    valid_config_dict: dict[str, object],
) -> None:
    cam, _fake = await _started_camera(valid_config_dict)
    try:
        with pytest.raises(CameraError, match="called twice"):
            await cam.start()
    finally:
        await cam.stop()


async def test_first_frame_timeout_raises_camera_open_error(
    valid_config_dict: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "pastor_tracker.io.obs_camera._FIRST_FRAME_TIMEOUT_SEC", 0.05
    )
    # Script of 200 unsuccessful reads -- forces first-frame gate to time out.
    script = [
        _ScriptedFrame(bgr=None, ok=False, delay_sec=0.0) for _ in range(200)
    ]
    fake = FakeVideoSource(script=script, width=1920, height=1080)
    cam = ObsCamera(
        Config(**valid_config_dict),
        video_source_factory=lambda *_a, **_kw: fake,
        filter_graph_factory=lambda: SimpleNamespace(
            get_input_devices=lambda: ["OBS Virtual Camera"]
        ),
    )
    with pytest.raises(CameraOpenError, match="OBS"):
        await cam.start()
    assert cam.state == _CamState.FAULTED
    assert isinstance(cam.last_error, CameraOpenError)
    # T-03-03: source.release MUST have fired on the timeout path.
    assert fake.release_calls >= 1


async def test_stop_releases_source_on_normal_exit(
    valid_config_dict: dict[str, object],
) -> None:
    cam, fake = await _started_camera(valid_config_dict)
    await cam.stop()
    assert fake.release_calls == 1


async def test_frames_yields_in_order(
    valid_config_dict: dict[str, object],
) -> None:
    cam, _fake = await _started_camera(valid_config_dict)
    try:
        seen: list[int] = []
        async for frame in cam.frames():
            seen.append(frame.timestamp_ns)
            if len(seen) >= 3:
                break
        assert all(seen[i + 1] >= seen[i] for i in range(len(seen) - 1))
    finally:
        await cam.stop()


async def test_frames_emits_perf_counter_ns_timestamps(
    valid_config_dict: dict[str, object],
) -> None:
    cam, _fake = await _started_camera(valid_config_dict)
    try:
        async for frame in cam.frames():
            assert frame.timestamp_ns > 0
            assert frame.timestamp_ns <= time.perf_counter_ns()
            break
    finally:
        await cam.stop()


async def test_queue_drop_oldest_when_full(
    valid_config_dict: dict[str, object],
) -> None:
    # Burst of 200 fast frames -- producer outpaces idle consumer; the
    # bounded queue (maxsize=64) MUST drop oldest with a WARN log.
    script = [
        _ScriptedFrame(
            bgr=make_solid_bgr(1920, 1080, (i % 256, 0, 0)),
            ok=True,
            delay_sec=0.0,
        )
        for i in range(200)
    ]
    fake = FakeVideoSource(script=script, width=1920, height=1080)
    cam = ObsCamera(
        Config(**valid_config_dict),
        video_source_factory=lambda *_a, **_kw: fake,
        filter_graph_factory=lambda: SimpleNamespace(
            get_input_devices=lambda: ["OBS Virtual Camera"]
        ),
    )
    await cam.start()
    try:
        with structlog.testing.capture_logs() as caplog:
            # Idle consumer so queue saturates.
            await asyncio.sleep(0.5)
            drops = [r for r in caplog if r.get("event") == "frames_queue_full"]
        assert len(drops) >= 1
    finally:
        await cam.stop()


async def test_status_properties_during_running(
    valid_config_dict: dict[str, object],
) -> None:
    cam, _fake = await _started_camera(valid_config_dict)
    try:
        assert cam.state is _CamState.RUNNING
        assert cam.is_running is True
        assert cam.current_resolution == (1920, 1080)
        assert cam.last_error is None
    finally:
        await cam.stop()


async def test_start_translates_graph_factory_failure(
    valid_config_dict: dict[str, object],
) -> None:
    """B-04: filter-graph factory failure (DLL missing, COM init error)
    translates to a typed CameraOpenError + FAULTED, not a stuck OPENING.

    Previously the ``except OBSCameraNotFoundError`` block only handled
    the discovery-empty case; an OSError / pywintypes.error from the
    FilterGraph constructor escaped untyped and left state at OPENING,
    confusing the dashboard and Phase 6 orchestrator.
    """

    def _bad_factory() -> object:
        raise OSError("quartz.dll not found (simulated)")

    cam = ObsCamera(
        Config(**valid_config_dict),
        video_source_factory=lambda *_a, **_kw: pytest.fail(
            "video_source_factory should not be reached"
        ),
        filter_graph_factory=_bad_factory,  # type: ignore[arg-type]
    )
    with pytest.raises(CameraOpenError, match="DirectShow"):
        await cam.start()
    assert cam.state is _CamState.FAULTED
    assert isinstance(cam.last_error, CameraOpenError)
