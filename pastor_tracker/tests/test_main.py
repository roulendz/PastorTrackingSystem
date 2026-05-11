"""In-process tests for the Plan 07-04 ``--ui`` / ``--headless`` flag matrix.

Subprocess-based coverage of the exit-code translator ladder lives in
``tests/test_pipeline.py::test_main_invalid_config_exit_code`` and
``tests/test_pipeline.py::test_main_sigint_clean_shutdown``. The tests
here cover the in-process dispatch logic (which argparse branch runs)
without paying the subprocess cost.
"""
from __future__ import annotations

import asyncio
import sys
import types
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock

import pytest
import structlog


@pytest.fixture(autouse=True)
def _reset_structlog_after_test() -> Iterator[None]:
    """``configure_logging`` is non-idempotent; reset between tests."""
    yield
    structlog.reset_defaults()


def _stub_dashboard_module(
    run_returns: int = 0,
) -> tuple[types.ModuleType, MagicMock]:
    """Build a fake ``pastor_tracker.ui.dashboard`` module + Dashboard class.

    Returns ``(module, dashboard_class_mock)`` so the test can assert
    construction arguments without importing real DearPyGui.
    """
    fake_module = types.ModuleType("pastor_tracker.ui.dashboard")
    dashboard_cls = MagicMock(name="Dashboard")
    instance = MagicMock(name="dashboard_instance")
    instance.run.return_value = run_returns
    dashboard_cls.return_value = instance
    fake_module.Dashboard = dashboard_cls  # type: ignore[attr-defined]
    return fake_module, dashboard_cls


def test_main_headless_flag_runs_phase6_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--headless`` invokes ``asyncio.run(_amain(...))``, not Dashboard."""
    called: dict[str, bool] = {"asyncio_run": False, "dashboard_run": False}

    def _fake_asyncio_run(coro: Any) -> int:
        called["asyncio_run"] = True
        close = getattr(coro, "close", None)
        if callable(close):
            close()  # prevent "coroutine was never awaited" warning
        return 0

    monkeypatch.setattr(asyncio, "run", _fake_asyncio_run)

    # Stub Dashboard so an accidental import never tries to talk to DPG.
    fake_dash_mod, dashboard_cls = _stub_dashboard_module()
    monkeypatch.setitem(sys.modules, "pastor_tracker.ui.dashboard", fake_dash_mod)

    import pastor_tracker.__main__ as main_mod

    result = main_mod.main(["--headless"])

    assert called["asyncio_run"] is True
    assert result == 0
    dashboard_cls.assert_not_called()


def test_main_ui_default_runs_dashboard(monkeypatch: pytest.MonkeyPatch) -> None:
    """No flags -> UI mode (default). Dashboard.run is called; asyncio.run is not."""
    asyncio_run_called: dict[str, bool] = {"flag": False}

    def _fake_asyncio_run(coro: Any) -> int:
        asyncio_run_called["flag"] = True
        close = getattr(coro, "close", None)
        if callable(close):
            close()
        return 99  # distinct from 0 so an accidental UI->headless dispatch fails

    monkeypatch.setattr(asyncio, "run", _fake_asyncio_run)

    fake_dash_mod, dashboard_cls = _stub_dashboard_module(run_returns=0)
    monkeypatch.setitem(sys.modules, "pastor_tracker.ui.dashboard", fake_dash_mod)

    import pastor_tracker.__main__ as main_mod

    result = main_mod.main([])

    assert asyncio_run_called["flag"] is False
    assert result == 0
    dashboard_cls.assert_called_once()
    # event_bus kwarg must be passed (shared with structlog tap).
    _args, kwargs = dashboard_cls.call_args
    assert "event_bus" in kwargs


def test_main_ui_explicit_flag_runs_dashboard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit ``--ui`` also routes to Dashboard (equivalent to no flag)."""

    def _fake_asyncio_run(coro: Any) -> int:
        close = getattr(coro, "close", None)
        if callable(close):
            close()
        return 99

    monkeypatch.setattr(asyncio, "run", _fake_asyncio_run)
    fake_dash_mod, dashboard_cls = _stub_dashboard_module(run_returns=0)
    monkeypatch.setitem(sys.modules, "pastor_tracker.ui.dashboard", fake_dash_mod)

    import pastor_tracker.__main__ as main_mod

    result = main_mod.main(["--ui"])

    assert result == 0
    dashboard_cls.assert_called_once()


def test_main_ui_and_headless_mutually_exclusive() -> None:
    """argparse rejects both flags with a non-zero SystemExit."""
    import pastor_tracker.__main__ as main_mod

    with pytest.raises(SystemExit) as exc_info:
        main_mod.main(["--ui", "--headless"])
    # argparse uses code=2 for usage errors.
    assert exc_info.value.code != 0


def test_main_ui_passes_event_bus_to_dashboard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same event_bus deque flows to BOTH configure_logging and Dashboard."""
    fake_dash_mod, dashboard_cls = _stub_dashboard_module(run_returns=0)
    monkeypatch.setitem(sys.modules, "pastor_tracker.ui.dashboard", fake_dash_mod)

    seen_buffer: dict[str, object] = {"buf": None}

    import pastor_tracker.__main__ as main_mod
    from pastor_tracker import logging_config as _lc

    real_configure = _lc.configure_logging

    def _spy_configure_logging(
        level: str = "INFO", event_bus_buffer: object = None
    ) -> None:
        seen_buffer["buf"] = event_bus_buffer
        real_configure(level=level, event_bus_buffer=event_bus_buffer)  # type: ignore[arg-type]

    monkeypatch.setattr(main_mod, "configure_logging", _spy_configure_logging)

    result = main_mod.main([])

    assert result == 0
    # Dashboard received the SAME deque the structlog chain captured.
    dashboard_event_bus = dashboard_cls.call_args.kwargs["event_bus"]
    assert seen_buffer["buf"] is dashboard_event_bus
