"""Lint canary — intentionally violates CLAUDE.md forbiddens.

Excluded from main `ruff check` via `extend-exclude` in pyproject.toml.
A dedicated lint command (`ruff check ... --select T201,E722,BLE001`) MUST
exit NON-ZERO against this file. If it exits zero, the lint policy is broken.
"""
from __future__ import annotations


def lint_canary_print() -> None:
    print("forbidden by T201")  # noqa: PTS-INTENTIONAL


def lint_canary_bare_except() -> None:
    try:
        x = 1 / 0
    except:  # noqa: PTS-INTENTIONAL — E722
        x = 0
    return None  # type: ignore[return-value]


def lint_canary_blind_except() -> None:
    try:
        x = 1 / 0
    except Exception:  # noqa: PTS-INTENTIONAL — BLE001
        pass
