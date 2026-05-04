"""Discovery tests for pastor_tracker.io.obs_camera (IO-CAM-01 / IO-CAM-02).

Covers the ``discover_obs_camera_index`` matrix (single match / multiple
matches / no match / empty-devices / case-sensitive). ``OpenCvVideoSource``
is intentionally NOT exercised here -- it requires a real cv2 device and
is deferred to Phase 8 / QA-04 stage smoke.

Mocking discipline: monkeypatch the FilterGraph factory closure (Pitfall
10 -- constructing a real FilterGraph triggers CoInitialize and races
pytest workers). SimpleNamespace stubs satisfy the ``get_input_devices``
contract -- never ``unittest.mock.MagicMock``.
"""
from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace

import pytest
import structlog

from pastor_tracker.io.obs_camera import (
    CameraError,
    CameraStallError,
    OBSCameraNotFoundError,
    VideoSource,
    _P95Detector,
    discover_obs_camera_index,
)
from tests.fixtures.camera_traces import FakeVideoSource, _ScriptedFrame, make_solid_bgr


def _fake_filter_graph(devices: list[str]) -> SimpleNamespace:
    """SimpleNamespace stub satisfying the FilterGraph.get_input_devices contract."""
    return SimpleNamespace(get_input_devices=lambda: list(devices))


def _factory_returning(devices: list[str]) -> Callable[[], SimpleNamespace]:
    return lambda: _fake_filter_graph(devices)


def _logger() -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(module="obs_camera")


# ---------------------------------------------------------------------------
# Group A -- discovery matrix (covers IO-CAM-01 + IO-CAM-02)
# ---------------------------------------------------------------------------


def test_discover_returns_index_for_exact_match():
    factory = _factory_returning(["FaceTime HD", "OBS Virtual Camera", "Logitech BRIO"])
    assert discover_obs_camera_index(
        "OBS Virtual Camera", factory=factory, logger=_logger()
    ) == 1


def test_discover_missing_raises_with_device_list():
    factory = _factory_returning(["FaceTime HD", "Logitech BRIO"])
    with pytest.raises(OBSCameraNotFoundError) as exc_info:
        discover_obs_camera_index(
            "OBS Virtual Camera", factory=factory, logger=_logger()
        )
    assert exc_info.value.expected == "OBS Virtual Camera"
    assert exc_info.value.available == ["FaceTime HD", "Logitech BRIO"]
    assert isinstance(exc_info.value, CameraError)   # parent-class catch path


def test_discover_missing_with_empty_devices():
    factory = _factory_returning([])
    with pytest.raises(OBSCameraNotFoundError) as exc_info:
        discover_obs_camera_index(
            "OBS Virtual Camera", factory=factory, logger=_logger()
        )
    assert exc_info.value.available == []


def test_discover_multi_match_picks_first_logs_all():
    factory = _factory_returning(
        ["OBS Virtual Camera", "Logitech BRIO", "OBS Virtual Camera"]
    )
    with structlog.testing.capture_logs() as caplog:
        result = discover_obs_camera_index(
            "OBS Virtual Camera", factory=factory, logger=_logger()
        )
    assert result == 0
    multi = [r for r in caplog if r.get("event") == "camera_multiple_matches"]
    assert len(multi) == 1
    assert multi[0]["chosen_index"] == 0
    assert multi[0]["all_indexes"] == [0, 2]
    assert multi[0]["name"] == "OBS Virtual Camera"
    # Negative-space assertion: single-match event MUST NOT have fired.
    assert not any(r.get("event") == "camera_discovered" for r in caplog)


def test_discover_logs_available_count_on_single_match():
    factory = _factory_returning(["A", "B", "OBS Virtual Camera", "D", "E"])
    with structlog.testing.capture_logs() as caplog:
        result = discover_obs_camera_index(
            "OBS Virtual Camera", factory=factory, logger=_logger()
        )
    assert result == 2
    single = [r for r in caplog if r.get("event") == "camera_discovered"]
    assert len(single) == 1
    assert single[0]["index"] == 2
    assert single[0]["available_count"] == 5
    assert single[0]["name"] == "OBS Virtual Camera"
    # Negative-space assertion: multi-match event MUST NOT have fired.
    assert not any(r.get("event") == "camera_multiple_matches" for r in caplog)


def test_discover_case_sensitive_match():
    factory = _factory_returning(["obs virtual camera"])
    with pytest.raises(OBSCameraNotFoundError):
        discover_obs_camera_index(
            "OBS Virtual Camera", factory=factory, logger=_logger()
        )


def test_discover_factory_invoked_per_call():
    """Pitfall 10 contract: factory is a callable, invoked once per discover call."""
    invocation_count = {"n": 0}

    def factory():
        invocation_count["n"] += 1
        return _fake_filter_graph(["OBS Virtual Camera"])

    discover_obs_camera_index(
        "OBS Virtual Camera", factory=factory, logger=_logger()
    )
    assert invocation_count["n"] == 1
    discover_obs_camera_index(
        "OBS Virtual Camera", factory=factory, logger=_logger()
    )
    assert invocation_count["n"] == 2


# ---------------------------------------------------------------------------
# Group B -- error class behaviour
# ---------------------------------------------------------------------------


def test_OBSCameraNotFoundError_carries_attributes():
    err = OBSCameraNotFoundError(expected="X", available=["A", "B"])
    assert err.expected == "X"
    assert err.available == ["A", "B"]
    assert "X" in str(err) and "A" in str(err)


def test_OBSCameraNotFoundError_is_CameraError():
    assert issubclass(OBSCameraNotFoundError, CameraError)


def test_CameraStallError_carries_attempts_attribute():
    attempts = [(1, 200, "first_frame_timeout"), (2, 500, "open_failed")]
    err = CameraStallError(attempts=attempts)
    assert err.attempts == attempts
    assert "2 attempts" in str(err)
    assert isinstance(err, CameraError)


# ---------------------------------------------------------------------------
# Group D -- _P95Detector behaviour (pure math; no I/O)
# ---------------------------------------------------------------------------


def test_P95Detector_budget_arithmetic_at_30fps():
    """Budget = int((1e9 / 30) * 1.2) = 39_999_999 ns ~= 40 ms."""
    detector = _P95Detector(target_fps=30)
    assert detector.budget_ns == int(int(1_000_000_000 / 30) * 1.2)


def test_P95Detector_returns_zero_p95_until_window_full():
    detector = _P95Detector(target_fps=30)
    for _ in range(29):
        detector.observe(50_000_000)
    assert detector.current_p95_ns == 0
    detector.observe(50_000_000)
    assert detector.current_p95_ns == 50_000_000


def test_P95Detector_short_window_never_breaches():
    """Decision rule: need a full 30-sample window before deciding."""
    detector = _P95Detector(target_fps=30)
    # Observing zero or fewer than 30 samples must always return False
    assert detector.budget_breached_persistent(now_ns=10**12) is False
    for _ in range(29):
        detector.observe(100_000_000)  # 100 ms -- well above budget
    # 29 samples, all breached -- still no decision
    assert detector.budget_breached_persistent(now_ns=10**12) is False


def test_P95Detector_no_breach_below_budget():
    detector = _P95Detector(target_fps=30)
    # 33 ms inter-grab @ 30 fps target = under budget
    for _ in range(30):
        detector.observe(33_000_000)
    assert detector.budget_breached_persistent(now_ns=10**12) is False


def test_P95Detector_breach_requires_persistence():
    detector = _P95Detector(target_fps=30)
    # 50 ms p95 -- above budget
    for _ in range(30):
        detector.observe(50_000_000)
    # First breach call: arms first_breach_ns; returns False (no persistence yet)
    first_call_ns = 1_000_000_000
    assert detector.budget_breached_persistent(now_ns=first_call_ns) is False
    # Same instant -> still no persistence
    assert detector.budget_breached_persistent(now_ns=first_call_ns) is False
    # 999 ms later -- still under 1 s persistence threshold
    assert detector.budget_breached_persistent(
        now_ns=first_call_ns + 999_000_000
    ) is False
    # 1.0 s later -- persistent breach detected
    assert detector.budget_breached_persistent(
        now_ns=first_call_ns + 1_000_000_000
    ) is True


def test_P95Detector_recovery_resets_first_breach():
    """Partial breaches do NOT accumulate -- detector is one-shot per breach."""
    detector = _P95Detector(target_fps=30)
    # Phase 1: arm a breach (50 ms p95 > 40 ms budget)
    for _ in range(30):
        detector.observe(50_000_000)
    detector.budget_breached_persistent(now_ns=1_000_000_000)  # arm first_breach
    # Phase 2: recovery -- 30 fast frames push out the slow ones
    for _ in range(30):
        detector.observe(20_000_000)
    # p95 now under budget -- first_breach must reset
    assert detector.budget_breached_persistent(now_ns=2_000_000_000) is False
    # Phase 3: new breach must require its own 1 s persistence
    for _ in range(30):
        detector.observe(50_000_000)
    second_breach_ns = 3_000_000_000
    assert detector.budget_breached_persistent(now_ns=second_breach_ns) is False
    # Without independent persistence accumulation, the recovery should have
    # cleared the breach -- so 0.5 s later the breach is NOT yet persistent.
    assert detector.budget_breached_persistent(
        now_ns=second_breach_ns + 500_000_000
    ) is False
    # 1 s after the new arming, persistence threshold reached
    assert detector.budget_breached_persistent(
        now_ns=second_breach_ns + 1_000_000_000
    ) is True


# ---------------------------------------------------------------------------
# Group C -- FakeVideoSource Protocol compliance
# ---------------------------------------------------------------------------


def test_FakeVideoSource_satisfies_protocol():
    fake = FakeVideoSource(script=[], width=1920, height=1080)
    assert isinstance(fake, VideoSource)


def test_FakeVideoSource_release_is_observable():
    fake = FakeVideoSource(script=[], width=1920, height=1080)
    assert fake.is_opened() is True
    assert fake.release_calls == 0
    fake.release()
    assert fake.release_calls == 1
    assert fake.is_opened() is False


def test_FakeVideoSource_read_returns_scripted_then_None():
    frames = [
        _ScriptedFrame(bgr=make_solid_bgr(1920, 1080, (0, 0, 0)), ok=True, delay_sec=0.0),
        _ScriptedFrame(bgr=make_solid_bgr(1920, 1080, (255, 0, 0)), ok=True, delay_sec=0.0),
    ]
    fake = FakeVideoSource(script=frames, width=1920, height=1080)
    ok1, bgr1 = fake.read()
    ok2, bgr2 = fake.read()
    ok3, bgr3 = fake.read()
    assert ok1 is True and bgr1 is not None and bgr1.shape == (1080, 1920, 3)
    assert ok2 is True and bgr2 is not None
    assert ok3 is False and bgr3 is None


def test_FakeVideoSource_set_resolution_is_observable():
    fake = FakeVideoSource(script=[], width=1920, height=1080)
    fake.set_resolution(1280, 720, 30)
    assert fake.set_resolution_calls == [(1280, 720, 30)]
