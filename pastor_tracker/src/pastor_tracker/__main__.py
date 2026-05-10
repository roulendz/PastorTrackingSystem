"""Pastor Tracking System process entry point (Phase 6).

Boot sequence:
    1. Parse CLI args (``--config-json`` optional override).
    2. ``configure_logging()`` -- structlog JSON sink (Phase 1 SCAF-04).
    3. Load ``Config`` (pydantic-settings; init > env > .env > config.json > defaults).
    4. Construct the 8 stages (D-05 ordering): ``ArduinoMotor``, ``ObsCamera``,
       ``PoseDetector``, ``SubjectTracker``, ``MotionAnalyzer``, ``Framer``,
       ``PanController``, ``CommandDispatcher``.
    5. Instantiate ``Pipeline`` (06-02 deliverable; lazy-imported -- see below).
    6. ``asyncio.run(_amain(config))``.
    7. ``_amain`` installs a platform-conditional SIGINT handler that sets a
       shutdown ``asyncio.Event`` (RESEARCH Pattern 5 + Pitfall 5), awaits
       ``pipeline.start()``, awaits the shutdown event, then calls
       ``pipeline.quit()`` in a ``finally`` block (D-09 idempotence makes
       this safe on every exit path).
    8. Process exits with a structured exit code (see ``EXIT_*`` constants).

Exit codes (sysexits.h-flavoured per RESEARCH Section "Pattern 5"):
    0   ``EXIT_OK``               -- clean shutdown via SIGINT or natural drain.
    64  ``EXIT_INVALID_CONFIG``   -- pydantic ``ValidationError`` on ``Config()``
                                     (T-06-08 mitigation: bad ``--config-json``).
    65  ``EXIT_HARDWARE_FAILED``  -- ``CameraError`` | ``ArduinoError`` |
                                     ``PerceptionError`` at boot (D-10/D-12/D-13).
    70  ``EXIT_CRASHED``          -- uncaught exception in pipeline tick task
                                     (D-14 fail-fast at orchestrator boundary).

Cross-platform SIGINT (RESEARCH Pattern 5 / Pitfall 5):
    Windows ``ProactorEventLoop`` does NOT support
    ``loop.add_signal_handler`` (cpython#137863). The portable pattern is
    ``signal.signal()`` on Windows and ``loop.add_signal_handler()`` on
    POSIX. The handler MUST NOT raise -- it bridges into the asyncio loop
    via ``loop.call_soon_threadsafe(shutdown_event.set)``. SIGTERM cannot
    be installed via ``signal.signal`` on Windows (raises ``ValueError``);
    the ``KeyboardInterrupt`` fallback in ``main()`` covers the legacy path.

Lazy ``Pipeline`` import (06-03/06-02 wave-merge contract):
    Plan 06-03 ships in parallel with 06-02 against the same 06-01 base.
    The 06-01 ``pipeline.py`` skeleton exports only ``OrchestratorRejected``;
    the full ``Pipeline`` class lands in 06-02. To keep this module
    importable in both branches (and to keep mypy strict), ``Pipeline`` is
    referenced via ``TYPE_CHECKING`` for static analysis and imported at
    runtime inside ``_amain()`` -- the only call site that needs it.
"""
from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Final

import structlog
from pydantic import ValidationError
from pygrabber.dshow_graph import FilterGraph

from pastor_tracker.config import Config
from pastor_tracker.control.command_dispatcher import CommandDispatcher
from pastor_tracker.control.pan_controller import PanController
from pastor_tracker.intent.framer import Framer
from pastor_tracker.intent.motion_analyzer import MotionAnalyzer
from pastor_tracker.io.arduino_motor import ArduinoError, ArduinoMotor
from pastor_tracker.io.arduino_transport import (
    PySerialTransport,
    discover_arduino_port,
)
from pastor_tracker.io.obs_camera import (
    CameraError,
    ObsCamera,
    OpenCvVideoSource,
    VideoSource,
)
from pastor_tracker.logging_config import configure_logging
from pastor_tracker.perception.pose_detector import (
    PerceptionError,
    PoseDetector,
    UltralyticsPoseEngine,
)
from pastor_tracker.perception.subject_tracker import SubjectTracker
from pastor_tracker.pipeline import OrchestratorRejected

if TYPE_CHECKING:
    # Pipeline lands in plan 06-02 (parallel wave). Import-for-type-only
    # keeps this module importable against the 06-01 skeleton (which exports
    # only OrchestratorRejected). The runtime import lives inside _amain().
    # The attr-defined ignore is bounded to the wave-merge interval: once
    # 06-02 lands, ``Pipeline`` resolves and the ignore becomes a no-op
    # (mypy's ``warn-unused-ignores`` is OFF in this repo's Phase-1 config).
    from pastor_tracker.pipeline import Pipeline  # type: ignore[attr-defined]


# Exit codes -- sysexits.h-flavoured (RESEARCH Section "Pattern 5: __main__
# Refactor"). Final[int] so mypy --strict treats them as immutable module
# constants and rejects accidental rebinding.
EXIT_OK: Final[int] = 0
EXIT_INVALID_CONFIG: Final[int] = 64
EXIT_HARDWARE_FAILED: Final[int] = 65
EXIT_CRASHED: Final[int] = 70

# Platform discriminator for SIGINT install path. Module-level constant so
# the win32 / POSIX branch is auditable in one place (CLAUDE.md rule 6).
_WIN32_PLATFORM: Final[str] = "win32"


def _build_video_source(
    index: int, width: int, height: int, fps: int
) -> VideoSource:
    """Factory wrapper for ``ObsCamera``'s injected ``video_source_factory``.

    Defined as a top-level function (not a lambda) so mypy --strict can
    annotate the closure-free signature explicitly.
    """
    return OpenCvVideoSource(index, width, height, fps)


def _build_filter_graph() -> FilterGraph:
    """Factory wrapper for ``ObsCamera``'s injected ``filter_graph_factory``.

    Same rationale as :func:`_build_video_source` -- explicit annotation
    keeps the strict-typed surface flat.
    """
    # ``pygrabber.dshow_graph`` ships no type stubs; the no-untyped-call
    # ignore is bounded to this single boundary call.
    return FilterGraph()  # type: ignore[no-untyped-call]


def main() -> int:
    """Process entry point. Returns int exit code consumed by ``SystemExit``.

    Three exit-translator branches (typed at the boundary):
        * ``ValidationError`` -> ``EXIT_INVALID_CONFIG`` (T-06-08 mitigation).
        * ``CameraError`` | ``ArduinoError`` | ``PerceptionError`` -> hardware.
        * Any other ``Exception`` -> ``EXIT_CRASHED`` (D-14 fail-fast).

    The two ``# noqa: BLE001`` translators are the documented exception
    boundary; they are the only broad ``except`` clauses in the module and
    each re-classifies into a structured exit code (no swallowing).
    """
    parser = argparse.ArgumentParser(prog="pastor_tracker")
    parser.add_argument(
        "--config-json",
        type=Path,
        default=None,
        help="Override config.json path (default: ./config.json from CWD).",
    )
    args = parser.parse_args()

    configure_logging()
    log = structlog.get_logger(module="__main__")

    # pydantic-settings 2.x: init kwarg with leading underscore (``_json_file``)
    # is the documented JSON-source override hook. Verified against
    # pydantic-settings docs at planning time (RESEARCH A6). The call-arg
    # ignore is bounded to the single override invocation.
    try:
        config = (
            Config(_json_file=args.config_json)  # type: ignore[call-arg]
            if args.config_json is not None
            else Config()
        )
    except ValidationError as exc:
        log.error("config_invalid", reason=str(exc))
        return EXIT_INVALID_CONFIG

    try:
        return asyncio.run(_amain(config))
    except KeyboardInterrupt:
        # Defense-in-depth: signal handler should have caught SIGINT and set
        # the shutdown event before this ever fires. If it does fire, the
        # legacy Ctrl-C path is still a clean operator exit.
        log.warning("pipeline_exit", reason="keyboard_interrupt")
        return EXIT_OK
    except (CameraError, ArduinoError, PerceptionError) as exc:
        log.error(
            "pipeline_exit",
            reason="hardware_failed",
            exc_type=type(exc).__name__,
            exc_msg=str(exc),
        )
        return EXIT_HARDWARE_FAILED
    except Exception as exc:  # noqa: BLE001 -- typed exit-code translator at process boundary
        log.error(
            "pipeline_exit",
            reason="crashed",
            exc_type=type(exc).__name__,
            exc_msg=str(exc),
        )
        return EXIT_CRASHED


async def _amain(config: Config) -> int:
    """Async entry: build stages -> install signal handler -> start -> wait -> quit.

    Lifecycle (D-05/D-09):
        1. Construct the 8 stages with the Config-derived discovery + factories.
        2. Instantiate ``Pipeline`` (lazy import; see module docstring).
        3. Install the platform-conditional SIGINT handler -- sets the
           ``shutdown_event`` (Pitfall 5: handler MUST NOT raise).
        4. ``await pipeline.start()`` -- ``CameraError`` | ``ArduinoError`` |
           ``PerceptionError`` | ``OrchestratorRejected`` translate to
           ``EXIT_HARDWARE_FAILED`` after a guaranteed ``pipeline.quit()``.
        5. ``await shutdown_event.wait()`` -- normal-run wait point.
        6. ``finally: await pipeline.quit()`` -- guaranteed handle release on
           every exit path (D-09 idempotence makes this safe).
    """
    # Lazy runtime import: Pipeline lands in 06-02 (parallel wave). See module
    # docstring for the wave-merge rationale. attr-defined ignore is bounded
    # to the wave-merge interval (mirrors the TYPE_CHECKING import above).
    from pastor_tracker.pipeline import Pipeline  # type: ignore[attr-defined]

    log = structlog.get_logger(module="__main__")

    # --- stage construction (D-05 ordering preserved) ---
    arduino_port = discover_arduino_port(config.arduino_port)
    transport = PySerialTransport(port=arduino_port, baud=config.arduino_baud)
    motor = ArduinoMotor(transport, config)
    camera = ObsCamera(
        config,
        video_source_factory=_build_video_source,
        filter_graph_factory=_build_filter_graph,
    )
    pose_engine = UltralyticsPoseEngine(config)
    detector = PoseDetector(config=config, engine=pose_engine)
    tracker = SubjectTracker(config)
    analyzer = MotionAnalyzer(config)
    framer = Framer(config)
    controller = PanController(config)
    dispatcher = CommandDispatcher(config)
    pipeline: Pipeline = Pipeline(
        config,
        camera=camera,
        motor=motor,
        detector=detector,
        tracker=tracker,
        analyzer=analyzer,
        framer=framer,
        controller=controller,
        dispatcher=dispatcher,
    )

    # --- shutdown event + signal install (RESEARCH Pitfall 5) ---
    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _on_signal(signum: int = 0, frame: object = None) -> None:
        """Signal handler -- MUST NOT raise. Bridges into the asyncio loop.

        ``signal.signal`` handlers (Windows path) receive
        ``(signum, frame)``; ``loop.add_signal_handler`` (POSIX path)
        receives no args. Defaulting both to optional + ignoring lets one
        callable serve both surfaces without a wrapper.
        """
        del signum, frame
        loop.call_soon_threadsafe(shutdown_event.set)

    if sys.platform == _WIN32_PLATFORM:
        # ProactorEventLoop does not support ``add_signal_handler``
        # (cpython#137863). ``signal.signal(SIGINT, ...)`` is the portable
        # pattern. SIGTERM cannot be installed via ``signal.signal`` on
        # Windows (raises ``ValueError``); the ``KeyboardInterrupt``
        # fallback in ``main()`` covers the legacy path.
        signal.signal(signal.SIGINT, _on_signal)
    else:
        loop.add_signal_handler(signal.SIGINT, _on_signal)
        loop.add_signal_handler(signal.SIGTERM, _on_signal)

    # --- start pipeline; translate hardware errors to EXIT_HARDWARE_FAILED ---
    try:
        await pipeline.start()
    except (
        CameraError,
        ArduinoError,
        PerceptionError,
        OrchestratorRejected,
    ) as exc:
        log.error(
            "pipeline_start_failed",
            exc_type=type(exc).__name__,
            exc_msg=str(exc),
        )
        # quit() is idempotent (D-09) -- safe even if start() partially succeeded.
        await pipeline.quit()
        return EXIT_HARDWARE_FAILED

    # --- main wait + guaranteed drain (T-06-10 mitigation) ---
    try:
        await shutdown_event.wait()
    finally:
        await pipeline.quit()

    log.info("pipeline_exit", reason="clean_shutdown")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
