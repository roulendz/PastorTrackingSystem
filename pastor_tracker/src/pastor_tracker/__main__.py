"""Pastor Tracking System entry point.

Phase 1 contract: configure structlog and emit a single ``boot`` event.
The full pipeline lands in Phase 6.
"""
from __future__ import annotations

import structlog

from pastor_tracker.logging_config import configure_logging


def main() -> None:
    """Boot the tracker. Phase 1: log a single ``boot`` event and exit."""
    configure_logging()
    log = structlog.get_logger(__name__)
    log.info("boot", phase=1, status="scaffold-only")


if __name__ == "__main__":
    main()
