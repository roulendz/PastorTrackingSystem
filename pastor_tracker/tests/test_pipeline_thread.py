"""Lifecycle tests for ``pastor_tracker.ui._pipeline_thread.PipelineThreadHost``.

Plan 07-02 Task 1. Verifies:
- start() blocks until the background loop is running (Pitfall 2: loop_ready race).
- submit(coro) before start() raises RuntimeError (explicit, not bare assert).
- submit(coro) after start() runs the coroutine and returns the result.
- start() twice raises RuntimeError (single-use; the second call is forbidden).
- stop() joins the thread deterministically.
"""
from __future__ import annotations

import pytest

from pastor_tracker.ui._pipeline_thread import PipelineThreadHost


async def _noop() -> int:
    return 42


def test_submit_runs_coroutine_and_returns_result() -> None:
    host = PipelineThreadHost()
    host.start()
    try:
        result = host.submit(_noop()).result(timeout=2.0)
        assert result == 42
    finally:
        host.stop()


def test_start_blocks_until_loop_ready() -> None:
    """Pitfall 2: a submit() immediately after start() must not race the loop.

    If start() returned before the background thread executed
    ``asyncio.set_event_loop`` + ``_loop_ready.set()``, the next submit()
    would raise on a None ``_loop`` attribute. We assert the absence of
    that race by submitting + reading the result with no sleep between
    start() and submit().
    """
    host = PipelineThreadHost()
    host.start()
    try:
        # No sleep -- if the race exists this fails immediately.
        future = host.submit(_noop())
        assert future.result(timeout=2.0) == 42
    finally:
        host.stop()


def test_double_start_raises() -> None:
    host = PipelineThreadHost()
    host.start()
    try:
        with pytest.raises(RuntimeError, match="may not be called twice"):
            host.start()
    finally:
        host.stop()


def test_submit_before_start_raises() -> None:
    host = PipelineThreadHost()
    coro = _noop()
    try:
        with pytest.raises(RuntimeError, match="before start"):
            host.submit(coro)
    finally:
        # Close the coroutine the submit refused so pytest's unraisable-
        # exception hook doesn't see an un-awaited coro at GC time.
        coro.close()


def test_stop_joins_background_thread() -> None:
    host = PipelineThreadHost()
    host.start()
    host.stop()
    assert host._thread is not None
    assert not host._thread.is_alive()


def test_stop_is_idempotent_when_never_started() -> None:
    host = PipelineThreadHost()
    # No raise; no thread to join.
    host.stop()
