"""Resolution-fallback tests for pastor_tracker.io.obs_camera (IO-CAM-03).

Covers the warmup-window p95 detector: fallback fires once inside the
warmup window when ``p95 > 1.2 x target_interval`` persists past the
budget; never fires outside the warmup window; already-720p case logs
ERROR but continues capturing (CONTEXT.md Area 3 lock).

SHORT-FUSE PATTERN (mirrors Phase 2 PATTERNS.md): tests monkeypatch
the module-level Final constants to compressed values so the entire
Phase 3 suite stays inside the VALIDATION 10 s envelope. Per-test
budget < 2 s. Compressed values:

    _P95_WINDOW_SIZE        = 5    samples (was 30) -- decide faster
    _P95_INDEX              = 4    (was 28; index of p95 in 5-sample window)
    _WARMUP_WINDOW_SEC      = 1.0  s (was 2.0) -- enough room for window+persist
    _BUDGET_PERSIST_NS      = 10_000_000 ns (10 ms; was 1 s) -- decide after 1 sample
    _STALL_THRESHOLD_NS     = 300_000_000 ns (300 ms; was 200 ms) -- no false stall on 60 ms delays
    _REOPEN_BACKOFFS_MS     = (20, 50, 100) (was (200, 500, 1000))

The plan's original short-fuse table (PLAN lines 1000-1006) under-
specified _P95_WINDOW_SIZE; with the production 30-sample window and
realistic test delays, the warmup window expired before the detector
filled. Compressing window size + persist + warmup keeps the
fallback decision deterministic inside an 800 ms budget.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
import structlog

from pastor_tracker.config import Config
from pastor_tracker.io.obs_camera import (
    _FALLBACK_HEIGHT,
    _FALLBACK_WIDTH,
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
    """Compress obs_camera timing constants so each fallback test stays < 2 s."""
    monkeypatch.setattr(
        "pastor_tracker.io.obs_camera._P95_WINDOW_SIZE", 5
    )
    monkeypatch.setattr("pastor_tracker.io.obs_camera._P95_INDEX", 4)
    monkeypatch.setattr(
        "pastor_tracker.io.obs_camera._WARMUP_WINDOW_SEC", 1.0
    )
    monkeypatch.setattr(
        "pastor_tracker.io.obs_camera._BUDGET_PERSIST_NS", 10_000_000
    )
    monkeypatch.setattr(
        "pastor_tracker.io.obs_camera._STALL_THRESHOLD_NS", 300_000_000
    )
    monkeypatch.setattr(
        "pastor_tracker.io.obs_camera._REOPEN_BACKOFFS_MS", (20, 50, 100)
    )


def _slow_script(
    width: int, height: int, count: int, delay_sec: float
) -> list[_ScriptedFrame]:
    """Build ``count`` frames of size ``(width, height)`` with given delay."""
    shared = make_solid_bgr(width, height, (0, 0, 0))
    return [
        _ScriptedFrame(bgr=shared, ok=True, delay_sec=delay_sec)
        for _ in range(count)
    ]


def _build_camera_with_factory(
    valid_config_dict: dict[str, object],
    sources: list[FakeVideoSource],
    *,
    overrides: dict[str, object] | None = None,
) -> ObsCamera:
    """Build an ObsCamera whose factory returns sources[0], sources[1], ... in order.

    The orchestrator releases+reopens on fallback / stall; ``sources``
    must contain enough entries for every reopen the test expects.
    """
    cfg_kwargs = dict(valid_config_dict)
    if overrides is not None:
        cfg_kwargs.update(overrides)
    call_index = [0]

    def _factory(*_a: object, **_kw: object) -> FakeVideoSource:
        idx = call_index[0]
        call_index[0] += 1
        if idx >= len(sources):
            return sources[-1]
        return sources[idx]

    return ObsCamera(
        Config(**cfg_kwargs),
        video_source_factory=_factory,
        filter_graph_factory=lambda: SimpleNamespace(
            get_input_devices=lambda: ["OBS Virtual Camera"]
        ),
    )


async def test_warmup_breach_triggers_fallback(
    valid_config_dict: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _short_fuse(monkeypatch)
    # Budget at 30 fps is int(int(1e9/30) * 1.2) = ~40 ms. Compressed
    # stall threshold = 300 ms. Use 60 ms delay: > budget (breach) and
    # < stall (no false-positive stall trigger). Compressed window = 5
    # samples; 5 * 60 ms = 300 ms to fill, +10 ms persist = ~310 ms;
    # well inside 1 s compressed warmup window.
    initial_source = FakeVideoSource(
        script=_slow_script(1920, 1080, count=200, delay_sec=0.060),
        width=1920,
        height=1080,
    )
    fallback_source = FakeVideoSource(
        script=_slow_script(1280, 720, count=400, delay_sec=0.060),
        width=1280,
        height=720,
    )
    cam = _build_camera_with_factory(
        valid_config_dict, [initial_source, fallback_source]
    )
    await cam.start()
    try:
        with structlog.testing.capture_logs() as caplog:
            await asyncio.sleep(0.8)  # 5 samples @ 60 ms + persist + grace
            fallbacks = [
                r for r in caplog if r.get("event") == "camera_resolution_fallback"
            ]
        assert len(fallbacks) >= 1
        assert cam.current_resolution == (_FALLBACK_WIDTH, _FALLBACK_HEIGHT)
        assert initial_source.release_calls >= 1
    finally:
        await cam.stop()


async def test_post_warmup_breach_inhibited(
    valid_config_dict: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _short_fuse(monkeypatch)
    # During compressed 1 s warmup: fast frames (no breach). After
    # warmup expires: switch to slow frames (would breach if not
    # inhibited). Compressed window = 5 samples, persist = 10 ms.
    fast_then_slow: list[_ScriptedFrame] = []
    shared_1080 = make_solid_bgr(1920, 1080, (0, 0, 0))
    # ~110 fast frames at 10 ms = 1.1 s (just past warmup) -> no breach.
    for _ in range(110):
        fast_then_slow.append(
            _ScriptedFrame(bgr=shared_1080, ok=True, delay_sec=0.010)
        )
    # Then slow frames at 60 ms (breach budget but warmup is over).
    for _ in range(200):
        fast_then_slow.append(
            _ScriptedFrame(bgr=shared_1080, ok=True, delay_sec=0.060)
        )
    src = FakeVideoSource(script=fast_then_slow, width=1920, height=1080)
    cam = _build_camera_with_factory(valid_config_dict, [src])
    await cam.start()
    try:
        with structlog.testing.capture_logs() as caplog:
            # 1.1 s fast (warmup) + ~0.5 s slow (post-warmup window).
            await asyncio.sleep(1.8)
            fallbacks = [
                r for r in caplog if r.get("event") == "camera_resolution_fallback"
            ]
        assert len(fallbacks) == 0
        assert cam.current_resolution == (1920, 1080)
    finally:
        await cam.stop()


async def test_720p_breach_logs_error_continues(
    valid_config_dict: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _short_fuse(monkeypatch)
    # Config starts at 720p so ``_current_width / _current_height``
    # already equal _FALLBACK_*. Persistent breach at 720p logs ERROR.
    src = FakeVideoSource(
        script=_slow_script(1280, 720, count=200, delay_sec=0.060),
        width=1280,
        height=720,
    )
    cam = _build_camera_with_factory(
        valid_config_dict,
        [src],
        overrides={"capture_width": 1280, "capture_height": 720},
    )
    await cam.start()
    try:
        with structlog.testing.capture_logs() as caplog:
            await asyncio.sleep(0.8)
            errors_at_720p = [
                r
                for r in caplog
                if r.get("event") == "camera_resolution_breach_at_720p"
            ]
            fallbacks = [
                r for r in caplog if r.get("event") == "camera_resolution_fallback"
            ]
        assert len(errors_at_720p) >= 1
        assert len(fallbacks) == 0  # no actual fallback fires when already 720p
        assert cam.current_resolution == (1280, 720)
        assert cam.state is _CamState.RUNNING
    finally:
        await cam.stop()


async def test_fallback_factory_exception_latches_stall_error(
    valid_config_dict: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CR-01 regression: factory raising on the fallback transition MUST
    surface as a typed CameraStallError on cam.last_error rather than
    silently kill the daemon thread.

    Models the production path where the post-release 720p
    ``VideoCapture(device_index, CAP_DSHOW)`` open fails (driver-side
    error). The orchestrator must NOT leak self._source = None +
    state == RUNNING + last_error == None.
    """
    _short_fuse(monkeypatch)
    initial_source = FakeVideoSource(
        script=_slow_script(1920, 1080, count=200, delay_sec=0.060),
        width=1920,
        height=1080,
    )
    call_index = [0]

    def _factory(*_a: object, **_kw: object) -> FakeVideoSource:
        idx = call_index[0]
        call_index[0] += 1
        if idx == 0:
            return initial_source
        # Fallback open raises -- mirrors a cv2 / DirectShow failure
        # the moment the orchestrator releases the 1080p handle and
        # tries to reopen at 720p.
        msg = f"simulated cv2 open error during fallback attempt {idx}"
        raise RuntimeError(msg)

    cam = ObsCamera(
        Config(**valid_config_dict),
        video_source_factory=_factory,
        filter_graph_factory=lambda: SimpleNamespace(
            get_input_devices=lambda: ["OBS Virtual Camera"]
        ),
    )
    await cam.start()
    try:
        with structlog.testing.capture_logs() as caplog:
            await asyncio.sleep(0.8)
            factory_failed = [
                r
                for r in caplog
                if r.get("event") == "camera_fallback_factory_failed"
            ]
        assert len(factory_failed) >= 1
        assert isinstance(cam.last_error, CameraStallError)
        assert cam.state is _CamState.FAULTED
        # The error reason MUST cite the factory exception so the
        # operator can distinguish a fallback-open failure from a
        # steady-state stall.
        reasons = [a[2] for a in cam.last_error.attempts]
        assert any("simulated cv2 open error" in r for r in reasons)
        # Initial source MUST have been released before the failed
        # fallback factory call (T-03-03 -- no leaked handle).
        assert initial_source.release_calls >= 1
    finally:
        await cam.stop()


async def test_fallback_one_shot(
    valid_config_dict: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _short_fuse(monkeypatch)
    initial_source = FakeVideoSource(
        script=_slow_script(1920, 1080, count=200, delay_sec=0.060),
        width=1920,
        height=1080,
    )
    # Post-fallback source: ALSO slow (should NOT trigger second fallback
    # because _fallback_consumed flag is True).
    fallback_source = FakeVideoSource(
        script=_slow_script(1280, 720, count=400, delay_sec=0.060),
        width=1280,
        height=720,
    )
    cam = _build_camera_with_factory(
        valid_config_dict, [initial_source, fallback_source]
    )
    await cam.start()
    try:
        with structlog.testing.capture_logs() as caplog:
            await asyncio.sleep(1.5)
            fallbacks = [
                r for r in caplog if r.get("event") == "camera_resolution_fallback"
            ]
        # Exactly ONE fallback should fire -- _fallback_consumed prevents
        # re-fire (CONTEXT.md "never re-fallback").
        assert len(fallbacks) == 1
    finally:
        await cam.stop()
