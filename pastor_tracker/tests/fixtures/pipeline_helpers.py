"""Composition helper for Phase 6 pipeline integration tests.

Builds a real :class:`pastor_tracker.pipeline.Pipeline` backed by the four
in-tree fakes (CLAUDE.md TEST-05 -- only the four hardware/perception edges
are mocked; every Phase 4-5 stage runs real code):

    * :class:`tests.fixtures.arduino_traces.FakeSerialTransport`
    * :class:`tests.fixtures.camera_traces.FakeVideoSource`
    * :class:`tests.fixtures.pose_traces.FakePoseEngine`
    * :mod:`tests.fixtures.trajectories` (consumed downstream of the helper)

Caller is responsible for ``await pipeline.quit()`` in a ``finally`` block --
the helper does not register a cleanup; tests own teardown.

Import path is ``tests.fixtures.pipeline_helpers`` -- pytest ``rootdir`` is
``pastor_tracker/`` (``testpaths=["tests"]``, packages=``["src/pastor_tracker"]``);
``pastor_tracker/tests/`` is the test tree, NOT a sub-package of the
``pastor_tracker`` package. Mirrors the arduino_traces / camera_traces /
pose_traces convention.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from types import SimpleNamespace
from typing import Final, cast

from pygrabber.dshow_graph import FilterGraph

from pastor_tracker.config import Config
from pastor_tracker.control.command_dispatcher import CommandDispatcher
from pastor_tracker.control.pan_controller import PanController
from pastor_tracker.core.types import Detection
from pastor_tracker.intent.framer import Framer
from pastor_tracker.intent.motion_analyzer import MotionAnalyzer
from pastor_tracker.io.arduino_motor import ArduinoMotor
from pastor_tracker.io.arduino_transport import FakeSerialTransport
from pastor_tracker.io.obs_camera import ObsCamera
from pastor_tracker.perception.pose_detector import PoseDetector, PoseEngine
from pastor_tracker.perception.subject_tracker import SubjectTracker
from pastor_tracker.pipeline import Pipeline
from tests.fixtures.arduino_traces import ARDUINO_TRACE_BOOT_ONLY
from tests.fixtures.camera_traces import (
    FakeVideoSource,
    _ScriptedFrame,
    make_solid_bgr,
)
from tests.fixtures.pose_traces import FakePoseEngine, make_detection

__all__ = [
    "_DEFAULT_FRAME_COUNT",
    "_DEFAULT_FRAME_DELAY_SEC",
    "_FakeFilterGraph",
    "_build_home_reject_config_namespace",
    "_default_detection_script",
    "_default_frame_script",
    "_make_valid_config_dict",
    "_moving_detection_script",
    "_pipeline_with_fakes",
]

# -----------------------------------------------------------------------------
# Module-level constants (CLAUDE.md rule 6 -- no magic numbers in code).
# -----------------------------------------------------------------------------

# 30 fps capture cadence -- matches the Phase 5 composition tests
# (test_intent_control_pipeline._DT_30HZ_SEC) so trajectories carry over.
_DEFAULT_FRAME_DELAY_SEC: Final[float] = 1.0 / 30.0
# 10 s of headroom at 30 fps -- enough for hysteresis (0.3 s) + framing tau
# (0.8 s) + pan tau (0.6 s) + dispatcher emission + pause/resume + watchdog
# recovery without script exhaustion.
_DEFAULT_FRAME_COUNT: Final[int] = 300
# Test-only capture geometry -- 320x240 keeps ndarray allocations cheap (under
# 100 KB per frame); the Pipeline never reads width/height directly so any
# valid Config-accepted geometry works.
_TEST_CAPTURE_WIDTH: Final[int] = 320
_TEST_CAPTURE_HEIGHT: Final[int] = 240
# Detection timestamp cadence (matches 30 fps frame timestamps).
_NS_PER_FRAME: Final[int] = int(_DEFAULT_FRAME_DELAY_SEC * 1_000_000_000)
# Centered subject for the default script -- trajectories module owns the
# moving / off-center patterns; this default keeps the tracker LOCKED on a
# stationary subject so the dispatcher gets to emit.
_DEFAULT_SUBJECT_CX: Final[float] = 0.5
_DEFAULT_SUBJECT_CY: Final[float] = 0.5
_DEFAULT_TRACK_ID: Final[int] = 1
_DEFAULT_DETECTION_CONF: Final[float] = 0.9
_DEFAULT_BGR_COLOR: Final[tuple[int, int, int]] = (0, 0, 0)
# Module path for the OBS Virtual Camera friendly name; ObsCamera does an
# exact-match lookup against this string, so the fake FilterGraph must surface
# it. Mirrors camera_traces._started_camera default.
_OBS_VCAM_FRIENDLY_NAME: Final[str] = "OBS Virtual Camera"


def _make_valid_config_dict(**overrides: object) -> dict[str, object]:
    """Return a valid Config kwargs dict, with ``overrides`` spread on top.

    Mirrors ``conftest.valid_config_dict`` (which uses ``dict[str, object]``
    for the same reason) but keeps the helper self-contained so tests that
    don't take the fixture can still build a Pipeline. Only the 25 Config
    fields that diverge from defaults are listed; everything else rides
    Config defaults.
    """
    base: dict[str, object] = {
        "arduino_port": "COM_FAKE",  # not actually opened -- FakeSerialTransport
        "arduino_baud": 115_200,
        "arduino_protocol_version": 2,
        "arduino_ready_timeout_sec": 2.0,
        "arduino_heartbeat_interval_ms": 200,
        "obs_camera_name": _OBS_VCAM_FRIENDLY_NAME,
        "capture_width": _TEST_CAPTURE_WIDTH,
        "capture_height": _TEST_CAPTURE_HEIGHT,
        "capture_fps": 30,
        "camera_horizontal_fov_deg": 70.0,
        "detection_confidence_min": 0.55,
        "motion_threshold_norm_per_sec": 0.08,
        "motion_hysteresis_sec": 0.3,
        "dwell_threshold_norm_per_sec": 0.03,
        "dwell_duration_sec": 1.5,
        "framing_time_constant_sec": 0.8,
        "pan_time_constant_sec": 0.6,
        "pan_deadband_deg": 0.4,
        "pan_max_velocity_deg_per_sec": 30.0,
        "motor_max_speed_steps_per_sec": 25_000.0,
        "motor_max_accel_steps_per_sec2": 12_500.0,
        "motor_angle_min_deg": -90.0,
        "motor_angle_max_deg": 90.0,
        "command_min_delta_deg": 0.2,
        "command_min_interval_ms": 50,
    }
    base.update(overrides)
    return base


class _FakeFilterGraph:
    """Minimal pygrabber.FilterGraph stand-in for ObsCamera enumeration.

    ObsCamera.start() invokes ``filter_graph_factory().get_input_devices()``
    -- both are the only surface ObsCamera depends on. Returning a list with
    the OBS friendly name in it satisfies discover_obs_camera_index without
    pulling in pygrabber / DirectShow.
    """

    def __init__(self, devices: list[str]) -> None:
        self._devices: list[str] = devices

    def get_input_devices(self) -> list[str]:
        return list(self._devices)


def _default_frame_script(width: int, height: int) -> list[_ScriptedFrame]:
    """Build the default 3-second @ 30 fps black-frame script.

    A single ndarray is allocated and shared across all 90 entries (the
    capture thread treats Frame.image as read-only per core/types.py
    contract -- aliasing is safe and ~270x cheaper than allocating 90
    distinct buffers).
    """
    shared_bgr = make_solid_bgr(width, height, _DEFAULT_BGR_COLOR)
    return [
        _ScriptedFrame(bgr=shared_bgr, ok=True, delay_sec=_DEFAULT_FRAME_DELAY_SEC)
        for _ in range(_DEFAULT_FRAME_COUNT)
    ]


def _default_detection_script(count: int) -> list[list[Detection]]:
    """One centered, locked subject per frame at 30 fps timestamps.

    Centered + high confidence + stable track_id keeps SubjectTracker in
    LOCKED for the entire run, so MotionAnalyzer / Framer / PanController /
    CommandDispatcher all receive non-None upstream. The subject does NOT
    move -- intent stays "indeterminate" / "dwelling" so the dispatcher
    only emits if motion is introduced upstream. Use
    :func:`_moving_detection_script` for end-to-end tests that need an
    M: line on the wire.
    """
    return [
        [
            make_detection(
                cx=_DEFAULT_SUBJECT_CX,
                cy=_DEFAULT_SUBJECT_CY,
                track_id=_DEFAULT_TRACK_ID,
                conf=_DEFAULT_DETECTION_CONF,
                timestamp_ns=k * _NS_PER_FRAME,
            )
        ]
        for k in range(count)
    ]


def _moving_detection_script(
    count: int,
    *,
    cx_start: float = 0.3,
    cx_per_frame: float = 0.005,
    cy: float = _DEFAULT_SUBJECT_CY,
) -> list[list[Detection]]:
    """One locked subject moving rightward at a steady velocity.

    Rightward step = ``cx_per_frame`` per 1/30 s tick = 0.15 norm/sec at the
    default of 0.005 -- comfortably above the default
    ``motion_threshold_norm_per_sec`` of 0.08, so MotionAnalyzer flips to
    ``moving_right`` after the hysteresis window (0.3 s default = 9 frames
    at 30 fps), Framer drives toward the left third, PanController computes
    a non-zero angle, and CommandDispatcher emits at least one M: line.

    Position is clamped to [0.05, 0.95] so the subject never reaches the
    bbox edges and the Detection cross-field validator (bbox_x2 > bbox_x1)
    holds.
    """
    detections: list[list[Detection]] = []
    for k in range(count):
        cx_raw = cx_start + cx_per_frame * k
        cx_clamped = min(0.95, max(0.05, cx_raw))
        detections.append(
            [
                make_detection(
                    cx=cx_clamped,
                    cy=cy,
                    track_id=_DEFAULT_TRACK_ID,
                    conf=_DEFAULT_DETECTION_CONF,
                    timestamp_ns=k * _NS_PER_FRAME,
                )
            ]
        )
    return detections


def _make_video_source_factory(
    fake: FakeVideoSource,
) -> Callable[[int, int, int, int], FakeVideoSource]:
    """Return a video_source_factory that always returns the supplied fake.

    Top-level helper (not a lambda) so the closure-free call signature is
    explicit -- mirrors ``__main__._build_video_source`` discipline.
    """

    def _factory(_idx: int, _width: int, _height: int, _fps: int) -> FakeVideoSource:
        return fake

    return _factory


def _make_filter_graph_factory(
    devices: list[str],
) -> Callable[[], FilterGraph]:
    """Return a filter_graph_factory that returns ``_FakeFilterGraph(devices)``.

    The fake is structurally compatible (only ``get_input_devices()`` is
    called by ObsCamera's discover step), but the real production type is
    ``pygrabber.dshow_graph.FilterGraph``. ``cast`` is the documented bridge
    -- mirrors the ``# type: ignore[no-untyped-call]`` pattern used at
    ``__main__._build_filter_graph``.
    """

    def _factory() -> FilterGraph:
        return cast(FilterGraph, _FakeFilterGraph(devices))

    return _factory


async def _pipeline_with_fakes(
    config_overrides: dict[str, object] | None = None,
    *,
    detection_script: Iterable[list[Detection]] | None = None,
    frame_script: list[_ScriptedFrame] | None = None,
    pose_engine: PoseEngine | None = None,
    fake_serial: FakeSerialTransport | None = None,
    devices: list[str] | None = None,
    config_factory: Callable[[], Config] | None = None,
) -> tuple[Pipeline, FakeSerialTransport, FakeVideoSource, PoseEngine]:
    """Build a Pipeline backed by all four fakes.

    The boot preamble (``ARDUINO_TRACE_BOOT_ONLY``) is fed into the
    FakeSerialTransport BEFORE Pipeline.start() -- the motor handshake reads
    it during start.

    Caller MUST ``await pipeline.quit()`` (idempotent per D-09; safe even if
    start() raised partway).

    ``config_factory``: optional callable that returns a Config-shaped object.
    Used by tests that need to bypass Pydantic validation (e.g. the home-out-
    of-limits test, where motor_angle_max_deg=0 fails Config validation but
    is the precise condition the D-08 limit check exists for). Default: build
    via ``Config(**_make_valid_config_dict(**overrides))``.
    """
    overrides = config_overrides or {}
    if config_factory is not None:
        config = config_factory()
    else:
        config = Config(**_make_valid_config_dict(**overrides))
    fake_serial = fake_serial if fake_serial is not None else FakeSerialTransport()
    for line in ARDUINO_TRACE_BOOT_ONLY:
        fake_serial.feed_rx(line)
    motor = ArduinoMotor(fake_serial, config)
    fake_video = FakeVideoSource(
        frame_script
        or _default_frame_script(config.capture_width, config.capture_height),
        width=config.capture_width,
        height=config.capture_height,
    )
    camera = ObsCamera(
        config,
        video_source_factory=_make_video_source_factory(fake_video),
        filter_graph_factory=_make_filter_graph_factory(
            devices if devices is not None else [_OBS_VCAM_FRIENDLY_NAME]
        ),
    )
    if pose_engine is None:
        pose_engine = FakePoseEngine(
            list(detection_script)
            if detection_script is not None
            else _default_detection_script(_DEFAULT_FRAME_COUNT)
        )
    detector = PoseDetector(config=config, engine=pose_engine)
    pipeline = Pipeline(
        config,
        camera=camera,
        motor=motor,
        detector=detector,
        tracker=SubjectTracker(config),
        analyzer=MotionAnalyzer(config),
        framer=Framer(config),
        controller=PanController(config),
        dispatcher=CommandDispatcher(config),
    )
    return pipeline, fake_serial, fake_video, pose_engine


def _build_home_reject_config_namespace(
    *,
    motor_angle_min_deg: float,
    motor_angle_max_deg: float,
) -> SimpleNamespace:
    """Build a duck-typed Config substitute that violates the D-08 home guard.

    Pipeline.home() reads ONLY ``self._config.motor_angle_min_deg`` and
    ``self._config.motor_angle_max_deg`` (verified against pipeline.py:419-423
    on 2026-05-08). A SimpleNamespace with just those two attributes is
    sufficient AND the only way to bypass Config's ``motor_angle_min_deg <=
    0.0 <= motor_angle_max_deg`` validator (the validator is the structural
    reason the D-08 guard CANNOT trigger via a Pydantic-valid Config).

    The home test substitutes this namespace AFTER the Pipeline has already
    started with a real Config -- the substitution is scoped to the
    rejection-arm assertion only.
    """
    return SimpleNamespace(
        motor_angle_min_deg=motor_angle_min_deg,
        motor_angle_max_deg=motor_angle_max_deg,
    )
