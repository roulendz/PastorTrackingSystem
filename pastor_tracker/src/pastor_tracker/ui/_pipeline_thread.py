"""Background asyncio event loop host for the Phase 7 dashboard.

``PipelineThreadHost`` owns a single ``threading.Thread`` that runs an
``asyncio`` event loop. The main thread (which owns the DearPyGui render
loop per D-01) submits coroutines via ``host.submit(coro)`` -- a
synchronous wrapper around ``asyncio.run_coroutine_threadsafe``.

Design (CONTEXT.md D-01, D-02, D-04 + RESEARCH §Pattern 1):
    * ``start()`` blocks until the background loop is actually running
      (``threading.Event`` sync barrier closes the loop_ready race --
      RESEARCH Pitfall 2).
    * ``submit(coro)`` raises ``RuntimeError`` if called before start.
      Tiger-style (CLAUDE.md §1): explicit raise, not ``assert`` -- the
      assert is stripped under ``python -O``.
    * ``stop()`` is idempotent. A WARN log fires if the join exceeds the
      budget (mirrors Phase 3 W-06 close-budget precedent).
    * ``start()`` may be called exactly once per instance. The Save
      Config restart sequence (D-11, Plan 07-03) discards the old host
      and constructs a fresh ``PipelineThreadHost`` per RESEARCH
      Pitfall 3.
"""
from __future__ import annotations

import asyncio
import threading
from collections.abc import Coroutine
from concurrent.futures import Future
from typing import Final, TypeVar

import structlog

# Loop-startup sync barrier budget. 5 s mirrors the Phase 6 D-09 quit-drain
# budget; sub-second is unrealistic on Windows due to OS thread-scheduling
# jitter. The bound is operator-facing -- a missed barrier means the host
# never reaches ``set_event_loop`` (probable bug, not a race we can win).
_LOOP_READY_TIMEOUT_SEC: Final[float] = 5.0

# Background-thread join budget at stop() time. 5 s matches D-04 (RESEARCH
# §"Threading & Quit-Sequence Concrete Walkthrough" + Pitfall 7). A timeout
# is logged WARN, not raised -- quit() must complete the rest of the
# teardown chain regardless.
_THREAD_JOIN_TIMEOUT_SEC: Final[float] = 5.0

_T = TypeVar("_T")


class PipelineThreadHost:
    """Owns an asyncio event loop running in a background thread.

    Lifecycle:
        host = PipelineThreadHost()
        host.start()                        # blocks on loop_ready barrier
        fut = host.submit(coroutine)        # cross-thread submit
        result = fut.result(timeout=...)    # main-thread blocking get
        host.stop()                         # idempotent; logs WARN on join timeout
    """

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._loop_ready = threading.Event()
        self._logger = structlog.get_logger(module="ui.pipeline_thread")

    def start(self) -> None:
        """Spawn the background thread and block until the loop is running.

        Raises ``RuntimeError`` if invoked twice on the same instance --
        the Save Config restart sequence (D-11) discards the old host
        and constructs a fresh one (RESEARCH Pitfall 3).
        """
        if self._thread is not None:
            raise RuntimeError(
                "PipelineThreadHost.start() may not be called twice on the same instance"
            )
        self._thread = threading.Thread(
            target=self._run,
            name="pipeline-loop",
            daemon=False,
        )
        self._thread.start()
        if not self._loop_ready.wait(timeout=_LOOP_READY_TIMEOUT_SEC):
            raise RuntimeError(
                f"PipelineThreadHost loop failed to start within "
                f"{_LOOP_READY_TIMEOUT_SEC}s"
            )

    def _run(self) -> None:
        """Background-thread entry. Owns loop creation, run, and drain."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._loop_ready.set()
        try:
            loop.run_forever()
        finally:
            # Drain pending tasks (cancel + gather) so the loop close call
            # does not warn about pending tasks. Mirrors Phase 6 quit-drain.
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            loop.run_until_complete(
                asyncio.gather(*pending, return_exceptions=True)
            )
            loop.close()

    def submit(self, coro: Coroutine[object, object, _T]) -> Future[_T]:
        """Submit a coroutine to the background loop; returns a concurrent.futures.Future.

        Raises ``RuntimeError`` when called before ``start()`` -- tiger-
        style explicit raise (not bare assert) so the contract holds
        under ``python -O`` (CLAUDE.md §1).
        """
        if self._loop is None:
            raise RuntimeError(
                "PipelineThreadHost.submit called before start()"
            )
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def stop(self) -> None:
        """Stop the background loop and join the thread. Idempotent."""
        if self._loop is None or self._thread is None:
            return
        # call_soon_threadsafe handles a not-yet-running loop gracefully;
        # the run_forever() call returns and the finally-block drains.
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=_THREAD_JOIN_TIMEOUT_SEC)
        if self._thread.is_alive():
            self._logger.warning(
                "pipeline_thread_join_timeout",
                budget_sec=_THREAD_JOIN_TIMEOUT_SEC,
            )


__all__ = ["PipelineThreadHost"]
