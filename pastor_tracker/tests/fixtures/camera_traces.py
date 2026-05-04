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

import collections
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt


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
