"""Real-OBS smoke for Phase 3. Run with PTS_CAPTURE_FPS=25 for PAL hosts."""
from __future__ import annotations

import asyncio
import sys
import time

import structlog
from pygrabber.dshow_graph import FilterGraph

from pastor_tracker.config import Config
from pastor_tracker.io.obs_camera import (
    OBSCameraNotFoundError,
    ObsCamera,
    OpenCvVideoSource,
    discover_obs_camera_index,
)


def _build_camera(cfg: Config) -> ObsCamera:
    return ObsCamera(
        cfg,
        video_source_factory=lambda idx, w, h, fps: OpenCvVideoSource(idx, w, h, fps),
        filter_graph_factory=lambda: FilterGraph(),
    )


async def smoke_1_e2e(duration_sec: float = 30.0) -> None:
    cfg = Config()
    cam = _build_camera(cfg)
    await cam.start()
    print(
        f"[smoke 1] started state={cam.state.name} "
        f"resolution={cam.current_resolution} target_fps={cfg.capture_fps}"
    )
    started = time.perf_counter()
    count = 0
    last_ts = 0
    async for frame in cam.frames():
        if frame.timestamp_ns <= last_ts:
            raise AssertionError(
                f"non-monotonic timestamp: prev={last_ts} now={frame.timestamp_ns}"
            )
        last_ts = frame.timestamp_ns
        count += 1
        if time.perf_counter() - started >= duration_sec:
            break
    await cam.stop()
    elapsed = time.perf_counter() - started
    print(
        f"[smoke 1] frames={count} elapsed={elapsed:.2f}s "
        f"fps={count/elapsed:.2f} final_resolution={cam.current_resolution} "
        f"final_state={cam.state.name} last_error={cam.last_error}"
    )


def smoke_2_no_obs() -> None:
    cfg = Config()
    log = structlog.get_logger("obs_camera")
    print(f"[smoke 2] expecting OBSCameraNotFoundError for '{cfg.obs_camera_name}'")
    try:
        idx = discover_obs_camera_index(
            cfg.obs_camera_name, factory=lambda: FilterGraph(), logger=log
        )
        print(f"[smoke 2] FAIL — discovery unexpectedly returned idx={idx}")
        sys.exit(2)
    except OBSCameraNotFoundError as exc:
        print(f"[smoke 2] OK — {type(exc).__name__}")
        print(f"           expected={exc.expected!r}")
        print(f"           available={exc.available!r}")


async def smoke_3_fallback_under_load(duration_sec: float = 6.0) -> None:
    cfg = Config()
    cam = _build_camera(cfg)
    await cam.start()
    print(
        f"[smoke 3] started state={cam.state.name} "
        f"resolution={cam.current_resolution}"
    )
    started = time.perf_counter()
    count = 0
    async for _ in cam.frames():
        count += 1
        if time.perf_counter() - started >= duration_sec:
            break
    await cam.stop()
    elapsed = time.perf_counter() - started
    print(
        f"[smoke 3] frames={count} elapsed={elapsed:.2f}s "
        f"fps={count/elapsed:.2f} final_resolution={cam.current_resolution}"
    )


async def main() -> None:
    structlog.configure()
    target = sys.argv[1] if len(sys.argv) > 1 else "1"
    if target == "1":
        await smoke_1_e2e()
    elif target == "2":
        smoke_2_no_obs()
    elif target == "3":
        await smoke_3_fallback_under_load()
    else:
        print(f"unknown smoke target: {target!r}")
        sys.exit(2)


if __name__ == "__main__":
    asyncio.run(main())
