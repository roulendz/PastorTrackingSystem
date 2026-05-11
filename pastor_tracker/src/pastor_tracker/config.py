"""Frozen Pydantic v2 settings for the Pastor Tracking System.

Sources, in precedence order (highest first):
    init kwargs > PTS_* env vars > .env file > config.json > defaults.

Range validation is enforced on every numeric field. Unknown fields are
rejected (``extra="forbid"``). Cross-field rule: ``motor_angle_max_deg``
must exceed ``motor_angle_min_deg``.

This module exists so every downstream stage consumes a single, validated,
frozen object. Tiger-style: invalid config crashes at startup. No silent
fallbacks. No defaults that hide errors.

Field count: 27 — every field in PROMPT.md ``## Config`` block plus the
Phase 7 UI preview drawlist dimensions (``preview_width_px``,
``preview_height_px``). See ``arduino_ready_timeout_sec`` description for
traceability to PROMPT.md ``## Failure Modes`` (boot ``READY:v2`` 2 s
timeout).
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import (
    BaseSettings,
    JsonConfigSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

# Module-level constants (CLAUDE.md rule 6 — no magic numbers in code).
# Values mirror PROMPT.md / firmware contract; document the source of each.
CONFIG_JSON_PATH: Path = Path("config.json")  # CWD-relative; override via env

_BAUD_MIN: int = 9_600
_BAUD_MAX: int = 921_600

# Firmware halts on > 1000 ms heartbeat silence (PROMPT.md ## Arduino Protocol).
_FIRMWARE_PC_HEARTBEAT_TIMEOUT_MS: int = 1_000
_HEARTBEAT_MIN_MS: int = 50
_HEARTBEAT_SAFETY_MARGIN_MS: int = 100
_HEARTBEAT_MAX_MS: int = _FIRMWARE_PC_HEARTBEAT_TIMEOUT_MS - _HEARTBEAT_SAFETY_MARGIN_MS

_FOV_MIN_DEG: float = 0.0
_FOV_MAX_DEG: float = 180.0

_PROBABILITY_MIN: float = 0.0
_PROBABILITY_MAX: float = 1.0

_TIME_CONSTANT_MAX_SEC: float = 10.0
# Normalized-frame velocity ceiling. 1.0 norm/sec = subject crosses the full
# frame width in one second; well above any plausible pastor motion (PROMPT.md
# motion thresholds default 0.08 norm/sec). Distinct constant prevents the
# unit-confusion bug of reusing the time-domain ceiling for velocity fields
# (CLAUDE.md rule 6 — name what you mean).
_VELOCITY_THRESHOLD_MAX_NORM_PER_SEC: float = 1.0
_PAN_VELOCITY_MAX_DEG_PER_SEC: float = 360.0

# Firmware clamps from PROMPT.md ## Settings clamps — host MUST mirror.
_MOTOR_SPEED_FLOOR_STEPS_PER_SEC: float = 100.0
_MOTOR_SPEED_CEILING_STEPS_PER_SEC: float = 50_000.0
_MOTOR_ACCEL_FLOOR_STEPS_PER_SEC2: float = 50.0
_MOTOR_ACCEL_CEILING_STEPS_PER_SEC2: float = 30_000.0

_MOTOR_ANGLE_LIMIT_DEG: float = 180.0
_DELTA_DEG_MAX: float = 10.0
_INTERVAL_MS_MAX: int = 10_000
_READY_TIMEOUT_MAX_SEC: float = 30.0

_CAPTURE_WIDTH_MIN: int = 320
_CAPTURE_WIDTH_MAX: int = 7_680
_CAPTURE_HEIGHT_MIN: int = 240
_CAPTURE_HEIGHT_MAX: int = 4_320
_CAPTURE_FPS_MIN: int = 5
_CAPTURE_FPS_MAX: int = 240

# Preview drawlist dimensions (Phase 7 D-13 / D-05 / Claude's Discretion).
# Defaults 960x540 per RESEARCH §"Claude's Discretion". Lower bound matches
# capture floor (320x240); upper bound = 4K (3840x2160) so 4K displays can
# render native preview without cv2.resize downsample if operator chooses.
_PREVIEW_WIDTH_PX_MIN: int = 320
_PREVIEW_WIDTH_PX_MAX: int = 3_840
_PREVIEW_HEIGHT_PX_MIN: int = 240
_PREVIEW_HEIGHT_PX_MAX: int = 2_160


class Config(BaseSettings):
    """Frozen application configuration. 27 fields. All ranges validated."""

    model_config = SettingsConfigDict(
        env_prefix="PTS_",
        env_file=".env",
        env_file_encoding="utf-8",
        json_file=CONFIG_JSON_PATH,
        json_file_encoding="utf-8",
        frozen=True,
        extra="forbid",
        case_sensitive=False,
    )

    # --- Arduino transport ---
    arduino_port: str | None = Field(
        default=None,
        description="None = auto-detect by VID:PID; override e.g. 'COM6'.",
    )
    arduino_baud: int = Field(default=115_200, ge=_BAUD_MIN, le=_BAUD_MAX)
    arduino_protocol_version: Literal[2] = 2  # CFG-04: pinned to v2 by Literal
    arduino_ready_timeout_sec: float = Field(
        default=2.0,
        gt=0.0,
        le=_READY_TIMEOUT_MAX_SEC,
        description=(
            "Boot handshake READY:v2 wait window per PROMPT.md ## Failure Modes "
            "(2 s default; documented in RESEARCH.md A1)."
        ),
    )
    arduino_heartbeat_interval_ms: int = Field(
        default=200,
        ge=_HEARTBEAT_MIN_MS,
        le=_HEARTBEAT_MAX_MS,
    )

    # --- Camera ---
    obs_camera_name: str = Field(default="OBS Virtual Camera", min_length=1)
    capture_width: int = Field(
        default=1_920, ge=_CAPTURE_WIDTH_MIN, le=_CAPTURE_WIDTH_MAX
    )
    capture_height: int = Field(
        default=1_080, ge=_CAPTURE_HEIGHT_MIN, le=_CAPTURE_HEIGHT_MAX
    )
    capture_fps: int = Field(default=30, ge=_CAPTURE_FPS_MIN, le=_CAPTURE_FPS_MAX)
    camera_horizontal_fov_deg: float = Field(
        default=70.0, gt=_FOV_MIN_DEG, lt=_FOV_MAX_DEG
    )

    # --- UI preview drawlist (Phase 7 D-05, D-13) ---
    preview_width_px: int = Field(
        default=960,
        ge=_PREVIEW_WIDTH_PX_MIN,
        le=_PREVIEW_WIDTH_PX_MAX,
        description=(
            "Width of the DearPyGui preview drawlist in pixels (D-05). "
            "Default 960 per RESEARCH §'Claude's Discretion'. cv2.resize "
            "downsamples larger capture sizes to this width per D-06."
        ),
    )
    preview_height_px: int = Field(
        default=540,
        ge=_PREVIEW_HEIGHT_PX_MIN,
        le=_PREVIEW_HEIGHT_PX_MAX,
        description=(
            "Height of the DearPyGui preview drawlist in pixels (D-05). "
            "Default 540 per RESEARCH §'Claude's Discretion'."
        ),
    )

    # --- Perception (YOLO11-pose + BoT-SORT) ---
    yolo_model_path: Path = Field(
        default=Path("yolo11n-pose.pt"),
        description=(
            "Path to YOLO11-pose weights file. Default resolves to ultralytics' "
            "auto-download cache (~/.cache/Ultralytics) on first use. ASVS V5: "
            "operator MUST verify SHA256 of weights file before deployment "
            "(see README — pinned hash for yolo11n-pose.pt)."
        ),
    )
    yolo_device: Literal["auto", "cuda", "cpu"] = Field(
        default="auto",
        description=(
            "Inference device selection. 'auto' resolves to 'cuda' if "
            "torch.cuda.is_available() else 'cpu' at PoseDetector.start(). "
            "Explicit 'cuda' fail-fasts loud if no CUDA device is present "
            "(RESEARCH 04 Open Question 4)."
        ),
    )
    botsort_yaml_path: Path | None = Field(
        default=None,
        description=(
            "Optional override for ultralytics' bundled botsort.yaml. None "
            "uses the bundled default (track_high_thresh=0.25). Set when "
            "stage tuning requires custom thresholds (RESEARCH 04 Pitfall 5; "
            "Open Question 3)."
        ),
    )

    # --- Detection / motion ---
    detection_confidence_min: float = Field(
        default=0.55, ge=_PROBABILITY_MIN, le=_PROBABILITY_MAX
    )
    motion_threshold_norm_per_sec: float = Field(
        default=0.08, gt=0.0, le=_VELOCITY_THRESHOLD_MAX_NORM_PER_SEC
    )
    motion_hysteresis_sec: float = Field(default=0.3, gt=0.0, le=_TIME_CONSTANT_MAX_SEC)
    dwell_threshold_norm_per_sec: float = Field(
        default=0.03, gt=0.0, le=_VELOCITY_THRESHOLD_MAX_NORM_PER_SEC
    )
    dwell_duration_sec: float = Field(default=1.5, gt=0.0, le=_TIME_CONSTANT_MAX_SEC)

    # --- Damping (two-stage) ---
    framing_time_constant_sec: float = Field(
        default=0.8, gt=0.0, le=_TIME_CONSTANT_MAX_SEC
    )
    pan_time_constant_sec: float = Field(
        default=0.6, gt=0.0, le=_TIME_CONSTANT_MAX_SEC
    )

    # --- Pan limits ---
    pan_deadband_deg: float = Field(default=0.4, ge=0.0, le=_DELTA_DEG_MAX)
    pan_max_velocity_deg_per_sec: float = Field(
        default=30.0, gt=0.0, le=_PAN_VELOCITY_MAX_DEG_PER_SEC
    )

    # --- Motor (mirror firmware clamps from PROMPT.md ## Settings clamps) ---
    motor_max_speed_steps_per_sec: float = Field(
        default=25_000.0,
        ge=_MOTOR_SPEED_FLOOR_STEPS_PER_SEC,
        le=_MOTOR_SPEED_CEILING_STEPS_PER_SEC,
    )
    motor_max_accel_steps_per_sec2: float = Field(
        default=12_500.0,
        ge=_MOTOR_ACCEL_FLOOR_STEPS_PER_SEC2,
        le=_MOTOR_ACCEL_CEILING_STEPS_PER_SEC2,
    )
    motor_angle_min_deg: float = Field(
        default=-90.0, ge=-_MOTOR_ANGLE_LIMIT_DEG, le=0.0
    )
    motor_angle_max_deg: float = Field(
        default=90.0, ge=0.0, le=_MOTOR_ANGLE_LIMIT_DEG
    )

    # --- Dispatcher rate limit ---
    # Anti-jitter throttles (PROMPT.md ## Anti-jitter). Both MUST be > 0;
    # zero here defeats the deliberate dispatcher rate limit that pairs with
    # deadband / vel-clamp / staleness-drop as the four jitter mitigations.
    command_min_delta_deg: float = Field(default=0.2, gt=0.0, le=_DELTA_DEG_MAX)
    command_min_interval_ms: int = Field(default=50, gt=0, le=_INTERVAL_MS_MAX)

    @model_validator(mode="after")
    def _max_above_min(self) -> Config:
        if self.motor_angle_max_deg <= self.motor_angle_min_deg:
            raise ValueError(
                f"motor_angle_max_deg ({self.motor_angle_max_deg}) must exceed "
                f"motor_angle_min_deg ({self.motor_angle_min_deg})"
            )
        return self

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Source precedence (highest first): init > env > .env > config.json > defaults."""
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            JsonConfigSettingsSource(settings_cls),
            file_secret_settings,
        )
