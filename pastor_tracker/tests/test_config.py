"""Unit tests for ``pastor_tracker.config.Config``.

Covers CFG-01 (load + env+JSON), CFG-02 (range validation),
CFG-03 (fail-fast on invalid), CFG-04 (Literal[2] protocol version).
Test names mirror .planning/phases/01-scaffold-config-core-math/01-VALIDATION.md.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from pastor_tracker.config import Config

# ---------- CFG-01: env + JSON loading ----------


def test_loads_from_json_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    valid_config_dict: dict[str, Any],
) -> None:
    """Config loads field values from config.json when no env/init kwargs override."""
    cfg_dict = dict(valid_config_dict)
    cfg_dict["camera_horizontal_fov_deg"] = 65.5
    cfg_dict["arduino_port"] = "COM7"
    json_path = tmp_path / "config.json"
    json_path.write_text(json.dumps(cfg_dict), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    cfg = Config()
    assert cfg.camera_horizontal_fov_deg == pytest.approx(65.5)
    assert cfg.arduino_port == "COM7"


def test_env_overrides_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    valid_config_dict: dict[str, Any],
) -> None:
    """PTS_* env vars take precedence over config.json (per settings_customise_sources)."""
    cfg_dict = dict(valid_config_dict)
    cfg_dict["arduino_port"] = "COM7"  # JSON says COM7
    json_path = tmp_path / "config.json"
    json_path.write_text(json.dumps(cfg_dict), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PTS_ARDUINO_PORT", "COM9")  # env says COM9 — must win
    cfg = Config()
    assert cfg.arduino_port == "COM9"


# ---------- CFG-02: range validation ----------


def test_negative_pan_velocity_rejected() -> None:
    """``pan_max_velocity_deg_per_sec`` must be > 0."""
    with pytest.raises(ValidationError):
        Config(pan_max_velocity_deg_per_sec=-1.0)


def test_motor_speed_out_of_range_rejected() -> None:
    """Below firmware floor (100 steps/s) → ValidationError."""
    with pytest.raises(ValidationError):
        Config(motor_max_speed_steps_per_sec=99.0)


def test_motor_speed_above_ceiling_rejected() -> None:
    """Above firmware ceiling (50_000 steps/s) → ValidationError."""
    with pytest.raises(ValidationError):
        Config(motor_max_speed_steps_per_sec=60_000.0)


def test_max_below_min_rejected() -> None:
    """``motor_angle_max_deg`` must exceed ``motor_angle_min_deg`` (cross-field)."""
    with pytest.raises(ValidationError):
        Config(motor_angle_min_deg=10.0, motor_angle_max_deg=5.0)


def test_command_min_interval_zero_rejected() -> None:
    """WR-05: zero interval defeats throttle — PROMPT.md ## Anti-jitter requires > 0."""
    with pytest.raises(ValidationError):
        Config(command_min_interval_ms=0)


def test_command_min_delta_zero_rejected() -> None:
    """WR-05: zero delta defeats dispatch throttle — strict gt=0 mirrors interval."""
    with pytest.raises(ValidationError):
        Config(command_min_delta_deg=0.0)


# ---------- CFG-03: fail-fast on invalid ----------


def test_unknown_field_rejected() -> None:
    """``extra='forbid'`` blocks unknown keys — no silent fallback."""
    with pytest.raises(ValidationError):
        Config(nonexistent_field=42)  # type: ignore[call-arg]


def test_default_construction_succeeds_and_is_frozen() -> None:
    """Defaults are valid, and the model is frozen (mutation raises)."""
    cfg = Config()
    with pytest.raises(ValidationError):
        cfg.arduino_baud = 9_600  # type: ignore[misc]


# ---------- CFG-04: protocol version pinned to 2 ----------


def test_protocol_version_must_be_2() -> None:
    """``arduino_protocol_version`` is ``Literal[2]`` — anything else fails."""
    with pytest.raises(ValidationError):
        Config(arduino_protocol_version=1)  # type: ignore[arg-type]


def test_protocol_version_default_is_2() -> None:
    """Default value is the only legal value."""
    cfg = Config()
    assert cfg.arduino_protocol_version == 2


# ---------- Phase 4 / Plan 01: Perception (YOLO11-pose + BoT-SORT) fields ----------


def test_yolo_fields_validation(valid_config_dict: dict[str, Any]) -> None:
    """yolo_device defaults to 'auto', yolo_model_path to 'yolo11n-pose.pt',
    botsort_yaml_path to None. Invalid yolo_device rejected; valid Literal accepted."""
    cfg = Config(**valid_config_dict)
    assert cfg.yolo_device == "auto"
    assert cfg.yolo_model_path == Path("yolo11n-pose.pt")
    assert cfg.botsort_yaml_path is None
    with pytest.raises(ValidationError):
        Config(**valid_config_dict, yolo_device="invalid")  # type: ignore[arg-type]
    cfg_cuda = Config(**valid_config_dict, yolo_device="cuda")
    assert cfg_cuda.yolo_device == "cuda"


def test_yolo_model_path_must_be_path_type(
    valid_config_dict: dict[str, Any],
) -> None:
    """A string is coerced to Path; an explicit Path is preserved."""
    cfg_str = Config(**valid_config_dict, yolo_model_path="not/a/path")  # type: ignore[arg-type]
    assert cfg_str.yolo_model_path == Path("not/a/path")
    cfg_path = Config(**valid_config_dict, yolo_model_path=Path("/abs/some.pt"))
    assert cfg_path.yolo_model_path == Path("/abs/some.pt")


def test_botsort_yaml_path_optional(valid_config_dict: dict[str, Any]) -> None:
    """Default is None; explicit Path accepted."""
    cfg_default = Config(**valid_config_dict)
    assert cfg_default.botsort_yaml_path is None
    cfg_explicit = Config(
        **valid_config_dict, botsort_yaml_path=Path("custom.yaml")
    )
    assert cfg_explicit.botsort_yaml_path == Path("custom.yaml")
