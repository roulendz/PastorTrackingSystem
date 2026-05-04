"""Canned ndarray sequences and async helpers for camera I/O integration tests.

Drive the traces via :class:`FakeVideoSource` -- pre-load a script of
``_ScriptedFrame`` triples, hand the fake to ``ObsCamera`` via the
``video_source_factory`` injection seam (Plan 03-02), and assert against the
observable side effects (``set_resolution_calls``, ``release_calls``).

Import path is ``tests.fixtures.camera_traces`` -- pytest ``rootdir`` is
``pastor_tracker/`` (``testpaths=["tests"]``, packages=``["src/pastor_tracker"]``);
``pastor_tracker/tests/`` is the test tree, NOT a sub-package of the
``pastor_tracker`` package. Importing as
``from pastor_tracker.tests.fixtures.camera_traces`` raises
:class:`ModuleNotFoundError`.
"""
from __future__ import annotations

import asyncio
import collections
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass
from types import SimpleNamespace

import numpy as np
import numpy.typing as npt

from pastor_tracker.config import Config
from pastor_tracker.io.obs_camera import ObsCamera, _CamState


@dataclass(frozen=True)
class _ScriptedFrame:
    """One scripted output of FakeVideoSource.read()."""

    bgr: npt.NDArray[np.uint8] | None
    ok: bool
    delay_sec: float    # how long read() blocks before returning


class FakeVideoSource:
    """Bidirectional in-memory fake VideoSource.

    Tests pre-load a script of ``_ScriptedFrame`` triples; ``read()`` pops
    the next one, sleeps for ``delay_sec``, and returns. After exhaustion,
    returns ``(False, None)`` until close.

    Mirrors arduino_transport.FakeSerialTransport in shape and threading
    model: collections.deque + threading.Lock, fail-loud on closed-write
    contract (set ``release_calls`` / ``set_resolution_calls`` lists for
    observable assertions).
    """

    def __init__(
        self,
        script: Iterable[_ScriptedFrame],
        *,
        width: int,
        height: int,
    ) -> None:
        self._script: collections.deque[_ScriptedFrame] = collections.deque(script)
        self._opened: bool = True
        self._lock = threading.Lock()
        self._width = width
        self._height = height
        self.set_resolution_calls: list[tuple[int, int, int]] = []
        self.release_calls: int = 0

    def read(self) -> tuple[bool, npt.NDArray[np.uint8] | None]:
        with self._lock:
            if not self._script:
                return False, None
            scripted = self._script.popleft()
        if scripted.delay_sec > 0:
            time.sleep(scripted.delay_sec)   # OK in test code; never in app code
        return scripted.ok, scripted.bgr

    def set_resolution(self, width: int, height: int, fps: int) -> None:
        self._width, self._height = width, height
        self.set_resolution_calls.append((width, height, fps))

    def is_opened(self) -> bool:
        return self._opened

    def release(self) -> None:
        self._opened = False
        self.release_calls += 1


def make_solid_bgr(width: int, height: int, color: tuple[int, int, int]) -> npt.NDArray[np.uint8]:
    """Allocate a fresh BGR uint8 frame; matches cv2's per-read allocation contract."""
    frame: npt.NDArray[np.uint8] = np.zeros((height, width, 3), dtype=np.uint8)
    frame[:, :] = color
    return frame


# ---------------------------------------------------------------------------
# Plan 03-02: async lifecycle helpers (mirror arduino_traces.py:54-95).
# ---------------------------------------------------------------------------


async def _started_camera(
    valid_config_dict: dict[str, object],
    *,
    script: list[_ScriptedFrame] | None = None,
    devices: list[str] | None = None,
) -> tuple[ObsCamera, FakeVideoSource]:
    """Build a fake-backed :class:`ObsCamera`, start it, return ``(camera, fake)``.

    Caller is responsible for ``await cam.stop()``.

    Default script provides ~20 s of headroom (600 frames @ 33 ms == ~20 s
    @ 30 fps) -- prevents script-exhaustion stall during lifecycle tests.
    Override ``script=`` for stall / fallback cases that need scripted slow
    grabs or read failures.
    """
    if script is None:
        # Share a single ndarray across all scripted frames -- 600 distinct
        # 1920x1080x3 buffers is ~3.6 GB and dominates per-test runtime
        # via allocation + GC. The capture thread does NOT mutate the
        # frame buffer (Frame.image is documented read-only -- core/types.py),
        # so aliasing is safe.
        shared_bgr = make_solid_bgr(1920, 1080, (0, 0, 0))
        script = [
            _ScriptedFrame(bgr=shared_bgr, ok=True, delay_sec=0.033)
            for _ in range(600)
        ]
    fake = FakeVideoSource(script=script, width=1920, height=1080)
    devices_list = devices if devices is not None else ["OBS Virtual Camera"]
    cam = ObsCamera(
        Config(**valid_config_dict),
        video_source_factory=lambda *_args, **_kw: fake,
        filter_graph_factory=lambda: SimpleNamespace(
            get_input_devices=lambda: list(devices_list)
        ),
    )
    await cam.start()
    return cam, fake


async def wait_for_state(
    camera: ObsCamera,
    target_state: _CamState,
    timeout: float = 1.0,
) -> None:
    """Poll ``camera.state`` at 10 ms cadence until ``target_state`` or ``timeout``.

    Replaces brittle ``asyncio.sleep`` cross-thread bridge waits.
    10 ms is faster than the capture thread's tick cadence AND faster
    than typical scheduler jitter, so polling overhead is bounded by
    ``timeout``.

    Raises :class:`TimeoutError` if state not reached within ``timeout``.
    """
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if camera.state == target_state:
            return
        await asyncio.sleep(0.01)
    raise TimeoutError(
        f"camera did not reach state={target_state.value!r} within {timeout}s "
        f"(current state={camera.state.value!r})"
    )
