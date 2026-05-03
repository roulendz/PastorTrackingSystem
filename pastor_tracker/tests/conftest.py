"""Shared pytest fixtures for the pastor_tracker test suite."""
from __future__ import annotations

import pytest


@pytest.fixture
def valid_config_dict() -> dict[str, object]:
    """Minimal kwargs that construct a valid Config.

    Use as a baseline; override per test (e.g. ``{**valid_config_dict, "arduino_baud": 9600}``).
    Mirrors the 25 fields shipped by ``pastor_tracker.config.Config`` so downstream
    phases can build valid Config objects without re-discovering every field.
    """
    return {
        "arduino_port": "COM6",
        "arduino_baud": 115_200,
        "arduino_protocol_version": 2,
        "arduino_ready_timeout_sec": 2.0,
        "arduino_heartbeat_interval_ms": 200,
        "obs_camera_name": "OBS Virtual Camera",
        "capture_width": 1_920,
        "capture_height": 1_080,
        "capture_fps": 30,
        "camera_horizontal_fov_deg": 70.0,
        "detection_confidence_min": 0.55,
        "motion_threshold_norm_per_sec": 0.08,
        "motion_hysteresis_sec": 0.3,
        "dwell_threshold_norm_per_sec": 0.03,
        "dwell_duration_sec": 1.5,
        "framing_time_constant_sec": 0.8,
        "pan_time_constant_sec": 0.6,
        "pan_deadband_deg": 0.4,
        "pan_max_velocity_deg_per_sec": 30.0,
        "motor_max_speed_steps_per_sec": 25_000.0,
        "motor_max_accel_steps_per_sec2": 12_500.0,
        "motor_angle_min_deg": -90.0,
        "motor_angle_max_deg": 90.0,
        "command_min_delta_deg": 0.2,
        "command_min_interval_ms": 50,
    }
