"""Property tests on the real filterpy KalmanFilter (CLAUDE.md hard rule:
test real implementations, no mocks). This file's full suite lands in Plan
02 (Phase 4 Wave 2). Wave 1 (this plan) lands ONLY the smoke-import guard
to fail-fast on filterpy / numpy-2.4 incompatibility (RESEARCH 04 A1).

Plan 01 deviation note (Rule 3 — blocking issue): filterpy 1.4.5 ships a
docstring containing the literal sequence ``\\Sum`` which Python 3.12 treats
as ``SyntaxWarning: invalid escape sequence '\\S'``. Under the project-wide
``filterwarnings = ["error"]`` in pyproject.toml that warning is promoted
to a hard SyntaxError at filterpy import time -- BEFORE any numpy 2.4
interop is exercised. The cosmetic SyntaxWarning is unrelated to RESEARCH
04 Pitfall A1 (which targets numpy DeprecationWarning under filterpy's
math). We ignore the upstream cosmetic warning at the test scope so the
test actually validates the intended A1 contract: that filterpy's
``KalmanFilter.predict()`` runs cleanly on numpy 2.4.x without numpy
DeprecationWarning. Any genuine numpy deprecation will still escalate to
a hard failure here -- exactly the fail-fast guard A1 demands.
"""
from __future__ import annotations

import numpy as np
import pytest


@pytest.mark.filterwarnings("ignore::SyntaxWarning")
def test_filterpy_smoke_import() -> None:
    """RESEARCH 04 Pitfall A1: filterpy 1.4.5 (Oct 2018) under numpy 2.4.x.

    A numpy deprecation-as-error here MUST block Plan 02 (we'd need to either
    pin numpy<2 or vendor a Kalman class). The test-scoped ``ignore::SyntaxWarning``
    silences the unrelated upstream ``\\S`` docstring cosmetic; numpy
    DeprecationWarning is NOT silenced and will still hard-fail the test.
    """
    from filterpy.kalman import KalmanFilter

    kf = KalmanFilter(dim_x=4, dim_z=2)
    kf.F = np.eye(4, dtype=np.float64)
    kf.H = np.zeros((2, 4), dtype=np.float64)
    kf.H[0, 0] = 1.0
    kf.H[1, 1] = 1.0
    kf.x = np.zeros(4, dtype=np.float64)
    kf.predict()
    assert kf.x.shape == (4,)
