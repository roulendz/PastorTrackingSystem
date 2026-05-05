"""Property tests for ``pastor_tracker.core.geometry`` (CORE-02 + TEST-01)."""
from __future__ import annotations

import math

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from pastor_tracker.core.geometry import (
    NORMALIZED_X_MAX,
    NORMALIZED_X_MIN,
    angle_deg_to_normalized_x,
    normalized_x_to_angle_deg,
)

ROUNDTRIP_TOL: float = 1e-9
EDGE_TOL: float = 1e-7
SPOT_CHECK_TOL: float = 1e-12
DEFAULT_FOV_DEG: float = 70.0
DEFAULT_HALF_FOV_DEG: float = 35.0
FOV_HYP_MIN: float = 1e-3
FOV_HYP_MAX: float = 180.0 - 1e-3
FOV_INNER_MIN: float = 1.0
FOV_INNER_MAX: float = 170.0
HYP_MAX_EXAMPLES: int = 200


@given(
    nx=st.floats(
        min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False
    ),
    fov=st.floats(
        min_value=FOV_HYP_MIN,
        max_value=FOV_HYP_MAX,
        allow_nan=False,
        allow_infinity=False,
    ),
)
@settings(
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
    max_examples=HYP_MAX_EXAMPLES,
)
def test_normalized_angle_roundtrip(nx: float, fov: float) -> None:
    """For any FOV in (0, 180) and any nx in [0, 1], the round-trip is identity."""
    angle = normalized_x_to_angle_deg(nx, fov)
    nx_back = angle_deg_to_normalized_x(angle, fov)
    assert abs(nx_back - nx) < ROUNDTRIP_TOL, (
        f"roundtrip drift fov={fov} nx={nx} -> angle={angle} -> nx_back={nx_back}"
    )


@given(
    fov=st.floats(
        min_value=FOV_INNER_MIN,
        max_value=FOV_INNER_MAX,
        allow_nan=False,
        allow_infinity=False,
    )
)
@settings(deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_centre_maps_to_zero(fov: float) -> None:
    """nx=0.5 (optical centre) -> 0 deg for any FOV."""
    assert abs(normalized_x_to_angle_deg(0.5, fov)) < ROUNDTRIP_TOL


@given(
    fov=st.floats(
        min_value=FOV_INNER_MIN,
        max_value=FOV_INNER_MAX,
        allow_nan=False,
        allow_infinity=False,
    )
)
@settings(deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_edges_map_to_half_fov(fov: float) -> None:
    """nx=0.0 -> -fov/2; nx=1.0 -> +fov/2 (within float tolerance)."""
    assert abs(normalized_x_to_angle_deg(0.0, fov) + fov / 2.0) < EDGE_TOL
    assert abs(normalized_x_to_angle_deg(1.0, fov) - fov / 2.0) < EDGE_TOL


def test_default_fov_70_known_values() -> None:
    """Spot-check fixed values against textbook pinhole formula at fov=70 deg."""
    fov = DEFAULT_FOV_DEG
    assert math.isclose(
        normalized_x_to_angle_deg(0.5, fov), 0.0, abs_tol=SPOT_CHECK_TOL
    )
    assert math.isclose(
        normalized_x_to_angle_deg(1.0, fov),
        DEFAULT_HALF_FOV_DEG,
        abs_tol=SPOT_CHECK_TOL,
    )
    assert math.isclose(
        normalized_x_to_angle_deg(0.0, fov),
        -DEFAULT_HALF_FOV_DEG,
        abs_tol=SPOT_CHECK_TOL,
    )


def test_invalid_normalized_x_raises() -> None:
    with pytest.raises(ValueError, match="normalized_x"):
        normalized_x_to_angle_deg(1.5, DEFAULT_FOV_DEG)


def test_invalid_fov_raises() -> None:
    with pytest.raises(ValueError, match="horizontal_fov_deg"):
        normalized_x_to_angle_deg(0.5, 0.0)
    with pytest.raises(ValueError, match="horizontal_fov_deg"):
        normalized_x_to_angle_deg(0.5, 180.0)


def test_invalid_angle_raises() -> None:
    """``angle_deg_to_normalized_x`` rejects ``|angle| > fov/2`` (CR-01)."""
    with pytest.raises(ValueError, match="angle_deg"):
        angle_deg_to_normalized_x(50.0, DEFAULT_FOV_DEG)  # |angle| > fov/2
    with pytest.raises(ValueError, match="angle_deg"):
        angle_deg_to_normalized_x(90.0, DEFAULT_FOV_DEG)
    with pytest.raises(ValueError, match="angle_deg"):
        angle_deg_to_normalized_x(180.0, DEFAULT_FOV_DEG)
    with pytest.raises(ValueError, match="angle_deg"):
        angle_deg_to_normalized_x(-90.0, DEFAULT_FOV_DEG)


@given(
    fov=st.floats(
        min_value=FOV_INNER_MIN,
        max_value=FOV_INNER_MAX,
        allow_nan=False,
        allow_infinity=False,
    ),
    nx=st.floats(
        min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False
    ),
)
@settings(
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
    max_examples=HYP_MAX_EXAMPLES,
)
def test_inverse_map_output_in_unit_interval(fov: float, nx: float) -> None:
    """W-01: ``angle_deg_to_normalized_x`` output MUST be in [0, 1].

    The forward map's ULP drift at the half-FOV boundary, plus the
    input-side ``_HALF_FOV_BOUNDARY_TOL_DEG`` tolerance, can produce
    ``normalized = 1 + epsilon`` without the output-side clamp.
    Downstream Pydantic DTOs (Detection / FramingTarget) reject with
    ValidationError. This property test asserts the clamp holds.
    """
    angle = normalized_x_to_angle_deg(nx, fov)
    nx_back = angle_deg_to_normalized_x(angle, fov)
    assert NORMALIZED_X_MIN <= nx_back <= NORMALIZED_X_MAX, (
        f"output out of [0, 1] for fov={fov}, nx={nx}, angle={angle}, "
        f"nx_back={nx_back}"
    )
