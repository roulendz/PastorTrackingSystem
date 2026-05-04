"""Stall + reopen tests for pastor_tracker.io.obs_camera (IO-CAM-04).

Covers the 3-attempt linear-backoff reopen state machine: a stall
triggers ``_attempt_reopen`` with backoffs; 3 failed reopens latches
:class:`CameraStallError` on the loop side and surfaces via
``cam.last_error`` / ``cam.state == FAULTED``.

SHORT-FUSE PATTERN: monkeypatches ``_STALL_THRESHOLD_NS`` to 50 ms and
``_REOPEN_BACKOFFS_MS`` to ``(20, 50, 100)`` so per-test runtime stays
< 1 s. The locked-constants regression guard
(``test_reopen_backoffs_match_constant``) runs WITHOUT ``_short_fuse``
so it asserts the production values from CONTEXT.md.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import numpy as np
import numpy.typing as npt
import pytest
import structlog

from pastor_tracker.config import Config
from pastor_tracker.io.obs_camera import (
    _REOPEN_BACKOFFS_MS,
    CameraStallError,
    ObsCamera,
    _CamState,
)
from tests.fixtures.camera_traces import (
    FakeVideoSource,
    _ScriptedFrame,
    make_solid_bgr,
)


def _short_fuse(monkeypatch: pytest.MonkeyPatch) -> None:
    """Compress stall / reopen timing constants so each test stays < 1 s."""
    monkeypatch.setattr(
        "pastor_tracker.io.obs_camera._STALL_THRESHOLD_NS", 50_000_000
    )
    monkeypatch.setattr(
        "pastor_tracker.io.obs_camera._REOPEN_BACKOFFS_MS", (20, 50, 100)
    )


class _BrokenSource:
    """A source whose ``read()`` always returns ``(False, None)`` -- forces reopen failure."""

    def __init__(self) -> None:
        self.release_calls = 0

    def read(self) -> tuple[bool, npt.NDArray[np.uint8] | None]:
        return False, None

    def set_resolution(self, width: int, height: int, fps: int) -> None:
        pass

    def is_opened(self) -> bool:
        return True

    def release(self) -> None:
        self.release_calls += 1


def _make_camera(
    valid_config_dict: dict[str, object],
    sources: list[object],
) -> ObsCamera:
    """Build an ObsCamera whose factory returns ``sources[i]`` in order."""
    call_index = [0]

    def _factory(*_a: object, **_kw: object) -> object:
        idx = call_index[0]
        call_index[0] += 1
        if idx >= len(sources):
            return sources[-1]
        return sources[idx]

    return ObsCamera(
        Config(**valid_config_dict),
        video_source_factory=_factory,  # type: ignore[arg-type]
        filter_graph_factory=lambda: SimpleNamespace(
            get_input_devices=lambda: ["OBS Virtual Camera"]
        ),
    )


def test_reopen_backoffs_match_constant() -> None:
    """Regression guard for CONTEXT.md Area 4 lock (no _short_fuse)."""
    assert _REOPEN_BACKOFFS_MS == (200, 500, 1000)


async def test_stall_reopen_succeeds_attempt_1(
    valid_config_dict: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _short_fuse(monkeypatch)
    shared_bgr = make_solid_bgr(1920, 1080, (0, 0, 0))
    # Initial: 5 fast frames, then a stall frame (60 ms > 50 ms threshold).
    initial_script: list[_ScriptedFrame] = [
        _ScriptedFrame(bgr=shared_bgr, ok=True, delay_sec=0.005)
        for _ in range(5)
    ]
    initial_script.append(
        _ScriptedFrame(bgr=shared_bgr, ok=True, delay_sec=0.080)
    )
    # Plus more frames so the post-stall iteration gets (True, frame).
    initial_script.extend(
        _ScriptedFrame(bgr=shared_bgr, ok=True, delay_sec=0.005)
        for _ in range(20)
    )
    initial_source = FakeVideoSource(
        script=initial_script, width=1920, height=1080
    )
    # Recovered source: just fast frames.
    recovered_script: list[_ScriptedFrame] = [
        _ScriptedFrame(bgr=shared_bgr, ok=True, delay_sec=0.005)
        for _ in range(50)
    ]
    recovered_source = FakeVideoSource(
        script=recovered_script, width=1920, height=1080
    )
    cam = _make_camera(
        valid_config_dict, [initial_source, recovered_source]
    )
    await cam.start()
    try:
        with structlog.testing.capture_logs() as caplog:
            await asyncio.sleep(0.5)
            stall_detected = [
                r for r in caplog if r.get("event") == "camera_stall_detected"
            ]
            reopen_succeeded = [
                r for r in caplog if r.get("event") == "camera_reopen_succeeded"
            ]
        assert len(stall_detected) >= 1
        assert len(reopen_succeeded) >= 1
        assert reopen_succeeded[0]["attempt"] == 1
        assert cam.state in (_CamState.RUNNING, _CamState.REOPENING)
        assert cam.last_error is None
    finally:
        await cam.stop()


async def test_stall_3_failures_raises_camera_stall_error(
    valid_config_dict: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _short_fuse(monkeypatch)
    shared_bgr = make_solid_bgr(1920, 1080, (0, 0, 0))
    # Initial: 5 fast + 1 stall frame.
    initial_script = [
        _ScriptedFrame(bgr=shared_bgr, ok=True, delay_sec=0.005)
        for _ in range(5)
    ]
    initial_script.append(
        _ScriptedFrame(bgr=shared_bgr, ok=True, delay_sec=0.080)
    )
    initial_source = FakeVideoSource(
        script=initial_script, width=1920, height=1080
    )
    # Three broken sources for the 3 reopen attempts.
    broken_sources = [_BrokenSource() for _ in range(5)]
    cam = _make_camera(
        valid_config_dict, [initial_source, *broken_sources]
    )
    await cam.start()
    try:
        with structlog.testing.capture_logs() as caplog:
            await asyncio.sleep(0.6)
            unrecoverable = [
                r
                for r in caplog
                if r.get("event") == "camera_stall_unrecoverable"
            ]
        assert len(unrecoverable) >= 1
        assert isinstance(cam.last_error, CameraStallError)
        assert cam.state is _CamState.FAULTED
        assert len(cam.last_error.attempts) >= 3
        # Compressed _REOPEN_BACKOFFS_MS = (20, 50, 100) -- those are the
        # values the orchestrator records in attempts.
        backoffs_in_history = [a[1] for a in cam.last_error.attempts]
        for expected_backoff in (20, 50, 100):
            assert expected_backoff in backoffs_in_history
    finally:
        await cam.stop()


async def test_reopen_history_records_attempts(
    valid_config_dict: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _short_fuse(monkeypatch)
    shared_bgr = make_solid_bgr(1920, 1080, (0, 0, 0))
    initial_script = [
        _ScriptedFrame(bgr=shared_bgr, ok=True, delay_sec=0.005)
        for _ in range(5)
    ]
    initial_script.append(
        _ScriptedFrame(bgr=shared_bgr, ok=True, delay_sec=0.080)
    )
    initial_source = FakeVideoSource(
        script=initial_script, width=1920, height=1080
    )
    broken_sources = [_BrokenSource() for _ in range(5)]
    cam = _make_camera(
        valid_config_dict, [initial_source, *broken_sources]
    )
    await cam.start()
    try:
        await asyncio.sleep(0.6)
        assert isinstance(cam.last_error, CameraStallError)
        for attempt in cam.last_error.attempts:
            assert isinstance(attempt, tuple)
            assert len(attempt) == 3
            assert isinstance(attempt[0], int)
            assert attempt[0] >= 1
            assert isinstance(attempt[1], int)
            assert isinstance(attempt[2], str)
    finally:
        await cam.stop()


class _RaisingAfterFirstReadSource:
    """First read() returns a frame; second raises -- covers the BLE001 translator."""

    def __init__(self) -> None:
        self._first_done = False
        self.release_calls = 0

    def read(self) -> tuple[bool, npt.NDArray[np.uint8] | None]:
        if not self._first_done:
            self._first_done = True
            return True, make_solid_bgr(1920, 1080, (0, 0, 0))
        msg = "simulated DirectShow driver error"
        raise RuntimeError(msg)

    def set_resolution(self, width: int, height: int, fps: int) -> None:
        pass

    def is_opened(self) -> bool:
        return True

    def release(self) -> None:
        self.release_calls += 1


async def test_capture_thread_exception_latches_stall_error(
    valid_config_dict: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Coverage: ``read()`` raising forces the capture-loop noqa BLE001 path."""
    _short_fuse(monkeypatch)
    raising_source = _RaisingAfterFirstReadSource()
    cam = _make_camera(valid_config_dict, [raising_source])
    await cam.start()
    try:
        await asyncio.sleep(0.5)
        assert isinstance(cam.last_error, CameraStallError)
        assert cam.state is _CamState.FAULTED
        # _fault_with_stall(reason) appends a "capture_thread_exception:"
        # entry to the history.
        reasons = [a[2] for a in cam.last_error.attempts]
        assert any("capture_thread_exception" in r for r in reasons)
    finally:
        await cam.stop()


async def test_reopen_factory_exception_recorded_in_history(
    valid_config_dict: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Coverage: factory raising during reopen forces the BLE001 translator."""
    _short_fuse(monkeypatch)
    shared_bgr = make_solid_bgr(1920, 1080, (0, 0, 0))
    # Initial: 5 fast + 1 stall trigger.
    initial_script = [
        _ScriptedFrame(bgr=shared_bgr, ok=True, delay_sec=0.005)
        for _ in range(5)
    ]
    initial_script.append(
        _ScriptedFrame(bgr=shared_bgr, ok=True, delay_sec=0.080)
    )
    initial_source = FakeVideoSource(
        script=initial_script, width=1920, height=1080
    )
    call_index = [0]

    def _factory(*_a: object, **_kw: object) -> object:
        idx = call_index[0]
        call_index[0] += 1
        if idx == 0:
            return initial_source
        # Reopen attempts always raise.
        msg = f"simulated cv2 open error attempt {idx}"
        raise RuntimeError(msg)

    cam = ObsCamera(
        Config(**valid_config_dict),
        video_source_factory=_factory,  # type: ignore[arg-type]
        filter_graph_factory=lambda: SimpleNamespace(
            get_input_devices=lambda: ["OBS Virtual Camera"]
        ),
    )
    await cam.start()
    try:
        await asyncio.sleep(0.6)
        assert isinstance(cam.last_error, CameraStallError)
        # Every attempt should have its factory exception captured.
        reasons = [a[2] for a in cam.last_error.attempts]
        assert any("simulated cv2 open error" in r for r in reasons)
    finally:
        await cam.stop()


class _ShapeMismatchSource:
    """First read() returns 1080p; subsequent return 720p (wrong shape -> ValueError)."""

    def __init__(self) -> None:
        self._call_count = 0
        self.release_calls = 0

    def read(self) -> tuple[bool, npt.NDArray[np.uint8] | None]:
        self._call_count += 1
        if self._call_count == 1:
            return True, make_solid_bgr(1920, 1080, (0, 0, 0))
        # Wrong shape -- triggers Frame.__post_init__ ValueError ->
        # "frame_shape_mismatch" -> stall path.
        return True, make_solid_bgr(1280, 720, (0, 0, 0))

    def set_resolution(self, width: int, height: int, fps: int) -> None:
        pass

    def is_opened(self) -> bool:
        return True

    def release(self) -> None:
        self.release_calls += 1


async def test_frame_shape_mismatch_treated_as_stall(
    valid_config_dict: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Coverage: Frame __post_init__ ValueError logs frame_shape_mismatch + reopens."""
    _short_fuse(monkeypatch)
    cam = _make_camera(
        valid_config_dict,
        [_ShapeMismatchSource(), *[_BrokenSource() for _ in range(5)]],
    )
    await cam.start()
    try:
        with structlog.testing.capture_logs() as caplog:
            await asyncio.sleep(0.6)
            shape_mismatches = [
                r for r in caplog if r.get("event") == "frame_shape_mismatch"
            ]
        assert len(shape_mismatches) >= 1
        assert isinstance(cam.last_error, CameraStallError)
    finally:
        await cam.stop()
