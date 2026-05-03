---
phase: 01-scaffold-config-core-math
reviewed: 2026-05-03T00:00:00Z
depth: standard
files_reviewed: 19
files_reviewed_list:
  - pastor_tracker/src/pastor_tracker/__init__.py
  - pastor_tracker/src/pastor_tracker/__main__.py
  - pastor_tracker/src/pastor_tracker/logging_config.py
  - pastor_tracker/src/pastor_tracker/config.py
  - pastor_tracker/src/pastor_tracker/core/__init__.py
  - pastor_tracker/src/pastor_tracker/core/types.py
  - pastor_tracker/src/pastor_tracker/core/geometry.py
  - pastor_tracker/src/pastor_tracker/core/damping.py
  - pastor_tracker/src/pastor_tracker/io/__init__.py
  - pastor_tracker/src/pastor_tracker/perception/__init__.py
  - pastor_tracker/src/pastor_tracker/intent/__init__.py
  - pastor_tracker/src/pastor_tracker/control/__init__.py
  - pastor_tracker/src/pastor_tracker/ui/__init__.py
  - pastor_tracker/tests/__init__.py
  - pastor_tracker/tests/conftest.py
  - pastor_tracker/tests/test_logging.py
  - pastor_tracker/tests/test_config.py
  - pastor_tracker/tests/test_types.py
  - pastor_tracker/tests/test_geometry.py
  - pastor_tracker/tests/test_damping.py
  - pastor_tracker/tests/fixtures/__init__.py
  - pastor_tracker/tests/fixtures/_lint_canary.py
  - pastor_tracker/pyproject.toml
  - pastor_tracker/.pre-commit-config.yaml
  - pastor_tracker/.python-version
findings:
  critical: 1
  warning: 5
  info: 6
  total: 12
status: issues_found
---

# Phase 1: Code Review Report

**Reviewed:** 2026-05-03
**Depth:** standard
**Files Reviewed:** 19 source/test/config files (uv.lock and `_lint_canary.py` excluded from rigorous review per scope rules)
**Status:** issues_found

## Summary

The Phase 1 scaffold is functionally green (30/30 tests pass, ruff/mypy clean) and the heavy hitters land correctly: Holden's exact closed-form damper is mathematically sound and unconditionally stable; the pinhole geometry forward map is correct and round-trip-property-tested; the frozen-Config + frozen-DTO pattern is rigorously enforced; the lint canary fixture works as designed. The scaffold's bones are solid.

That said, this is a pure-math/contract phase whose entire value to downstream stages is **trustworthy fail-fast contracts**, and there are three real contract leaks the verification didn't catch because the existing tests only feed each function its happy-path inputs:

1. **`angle_deg_to_normalized_x` accepts any angle without validation** — out-of-domain angles produce silent garbage in `[-∞, +∞]`, violating tiger-style fail-fast and the documented `[0, 1]` postcondition. This is the only **BLOCKER**.
2. **A misnamed constant (`_TIME_CONSTANT_MAX_SEC`) is reused as the upper bound for three non-time velocity fields** in `Config`, exactly the unit-confusion bug CLAUDE.md rule 6 ("no magic numbers, name what you mean") was written to prevent.
3. **`Frame` carries `width`/`height` independent of `image.shape`**, with no validator tying them together — an ndarray of shape (480, 640, 3) constructed with `width=1920, height=1080` is silently accepted and will mis-frame every downstream stage that trusts those scalars.

The remaining warnings are the usual scaffold-phase polish (cross-field validation gaps, idempotency claim that doesn't quite hold, env-loading test ordering risk). Info items are style/clarity nits.

## Critical Issues

### CR-01: `angle_deg_to_normalized_x` performs no input validation on `angle_deg` — silently returns out-of-range values

**File:** `pastor_tracker/src/pastor_tracker/core/geometry.py:49-61`

**Issue:** The forward function `normalized_x_to_angle_deg` correctly tiger-style guards both `normalized_x ∈ [0, 1]` and `horizontal_fov_deg ∈ (0, 180)` *before* touching `math.tan`. The inverse function `angle_deg_to_normalized_x` only guards `horizontal_fov_deg`. There is no guard on `angle_deg`.

The function's documented domain is `angle_deg ∈ (-fov/2, +fov/2)` (it is the analytic inverse of a function whose codomain is exactly that interval), and its documented codomain is `[0, 1]`. With no input guard, a caller that passes any angle outside `±fov/2` gets a normalized-x value silently outside `[0, 1]`. Two concrete failure modes:

- `angle_deg_to_normalized_x(50.0, 70.0)` returns `~1.34` (no exception, no NaN, just garbage > 1).
- `angle_deg_to_normalized_x(90.0, 70.0)` calls `math.tan(math.radians(90))`, which returns `~1.633e16` (Python's `math.tan` does NOT raise at exactly π/2 because the float `radians(90)` is not exactly π/2). Result: `normalized_x ≈ 2.3e16`. Still no exception.
- `angle_deg_to_normalized_x(180.0, 70.0)` returns a finite negative value via `tan(π) ≈ -1.22e-16`. Caller assumes it's a valid frame coordinate.

This is a CLAUDE.md rule 1 violation ("fail fast, fail loud. Validate inputs at boundaries. Crash on contract violation. No silent excepts."). Phase 5's `pan_controller` will be the first consumer of this function (mapping deadband angle → normalized-x for hysteresis math), and it will trust the `[0, 1]` postcondition. The existing roundtrip test never catches this because it only feeds `angle_deg_to_normalized_x` angles produced by the forward function — i.e. the happy path.

The bug also makes the public contract asymmetric: forward validates two inputs, inverse validates one. That is the kind of inconsistency that bites downstream maintainers months later.

**Fix:**
```python
# core/geometry.py — add to angle_deg_to_normalized_x BEFORE the math.tan call.
def angle_deg_to_normalized_x(
    angle_deg: float, horizontal_fov_deg: float
) -> float:
    """Inverse of :func:`normalized_x_to_angle_deg`."""
    if not FOV_DEG_MIN_EXCLUSIVE < horizontal_fov_deg < FOV_DEG_MAX_EXCLUSIVE:
        raise ValueError(
            f"horizontal_fov_deg out of "
            f"({FOV_DEG_MIN_EXCLUSIVE}, {FOV_DEG_MAX_EXCLUSIVE}): "
            f"{horizontal_fov_deg}"
        )
    half_fov_deg = horizontal_fov_deg * HALF
    if not -half_fov_deg <= angle_deg <= half_fov_deg:
        raise ValueError(
            f"angle_deg out of [{-half_fov_deg}, {half_fov_deg}] for "
            f"fov={horizontal_fov_deg}: {angle_deg}"
        )
    half_fov_rad = math.radians(half_fov_deg)
    offset = math.tan(math.radians(angle_deg)) / math.tan(half_fov_rad)
    return (offset + NORMALIZED_RANGE) * HALF
```

Add a paired test in `tests/test_geometry.py`:
```python
def test_invalid_angle_raises() -> None:
    with pytest.raises(ValueError, match="angle_deg"):
        angle_deg_to_normalized_x(50.0, DEFAULT_FOV_DEG)  # |angle| > fov/2
    with pytest.raises(ValueError, match="angle_deg"):
        angle_deg_to_normalized_x(90.0, DEFAULT_FOV_DEG)
```

## Warnings

### WR-01: `Config` reuses `_TIME_CONSTANT_MAX_SEC` as the ceiling for three velocity fields — misnamed constant, unit confusion

**File:** `pastor_tracker/src/pastor_tracker/config.py:50, 126, 130`

**Issue:** `_TIME_CONSTANT_MAX_SEC: float = 10.0` is declared as a *time* ceiling (seconds, sensible upper bound for a damper time-constant), and is correctly used as `le=` for the four time-constant fields (`motion_hysteresis_sec`, `dwell_duration_sec`, `framing_time_constant_sec`, `pan_time_constant_sec`). It is also reused as the `le=` for three velocity-domain fields:

- `motion_threshold_norm_per_sec: float = Field(default=0.08, gt=0.0, le=_TIME_CONSTANT_MAX_SEC)` (line 126) — units: normalized-frame per second; default 0.08; ceiling expressed via a constant named "TIME_CONSTANT_MAX_SEC".
- `dwell_threshold_norm_per_sec: float = Field(default=0.03, gt=0.0, le=_TIME_CONSTANT_MAX_SEC)` (line 130) — same problem.

This is exactly the magic-number / unit-confusion bug CLAUDE.md rule 6 was written to prevent ("no magic numbers in code — everything tunable lives in Config" *and named for what it is*). A constant named `_TIME_CONSTANT_MAX_SEC` carrying the value `10.0` and applied to a velocity in `norm/sec` reads as an obviously copy-pasted bound. Worse, `10.0 norm/sec` means the speaker would cross 10 full frame widths per second to hit the ceiling — useless as a real guard. A reasonable normalized velocity ceiling is on the order of `2.0 norm/sec` (one full frame in 0.5 s).

**Fix:**
```python
# config.py — split the ceiling into properly named constants.
_TIME_CONSTANT_MAX_SEC: float = 10.0          # time domain only
_NORMALIZED_VELOCITY_MAX_PER_SEC: float = 2.0 # frame-widths/sec; PROMPT.md motion thresholds default 0.08

# ...

motion_threshold_norm_per_sec: float = Field(
    default=0.08, gt=0.0, le=_NORMALIZED_VELOCITY_MAX_PER_SEC
)
dwell_threshold_norm_per_sec: float = Field(
    default=0.03, gt=0.0, le=_NORMALIZED_VELOCITY_MAX_PER_SEC
)
```

### WR-02: `Frame.width` / `Frame.height` not validated against `image.shape` — silent dimension lie

**File:** `pastor_tracker/src/pastor_tracker/core/types.py:35-46`

**Issue:** `Frame` is a frozen dataclass with separate `width: int`, `height: int`, and `image: ImageArray` fields. Nothing ties them together. A construction like:

```python
Frame(image=np.zeros((480, 640, 3), dtype=np.uint8), width=1920, height=1080, timestamp_ns=t)
```

is silently accepted. Every downstream stage (perception, framing, motor angle conversion) trusts `frame.width` to convert a YOLO bbox pixel-x to a normalized-x. The wrong scalar means *every framing decision and every motor angle* is wrong by the ratio of the lie. This is the kind of bug that produces "the tracker is consistently off by 33%" — extremely hard to diagnose because no exception ever fires.

A frozen dataclass cannot use a Pydantic validator, but `__post_init__` works:

**Fix:**
```python
# core/types.py
@dataclass(frozen=True, slots=True)
class Frame:
    image: ImageArray
    width: int
    height: int
    timestamp_ns: int

    def __post_init__(self) -> None:
        if self.image.ndim != 3 or self.image.shape[2] != 3:
            raise ValueError(
                f"Frame.image must be HxWx3 BGR, got shape {self.image.shape}"
            )
        h, w = self.image.shape[0], self.image.shape[1]
        if w != self.width or h != self.height:
            raise ValueError(
                f"Frame width/height ({self.width}x{self.height}) "
                f"disagree with image.shape ({w}x{h})"
            )
        if self.timestamp_ns < 0:
            raise ValueError(f"timestamp_ns must be >= 0, got {self.timestamp_ns}")
```

(The `timestamp_ns >= 0` check matches the pattern used on the Pydantic DTOs in this same file — currently `Frame.timestamp_ns` accepts negatives where every other DTO rejects them.)

### WR-03: `Detection.bbox_x2 > bbox_x1` and `bbox_y2 > bbox_y1` not enforced — degenerate / inverted bboxes silently constructible

**File:** `pastor_tracker/src/pastor_tracker/core/types.py:55-65`

**Issue:** `Detection` validates each bbox edge independently in `[0, 1]` but never checks `x2 > x1` or `y2 > y1`. A YOLO post-processing bug or a swapped tuple unpack will produce inverted or zero-area bboxes. Phase 4's tracker would compute negative widths/heights downstream. The cross-field validator pattern is already established in `Config._max_above_min`; mirror it here:

**Fix:**
```python
# core/types.py
from pydantic import model_validator

class Detection(_FrozenModel):
    # ... fields unchanged ...

    @model_validator(mode="after")
    def _bbox_well_ordered(self) -> "Detection":
        if self.bbox_x2_normalized <= self.bbox_x1_normalized:
            raise ValueError(
                f"bbox_x2 ({self.bbox_x2_normalized}) must exceed "
                f"bbox_x1 ({self.bbox_x1_normalized})"
            )
        if self.bbox_y2_normalized <= self.bbox_y1_normalized:
            raise ValueError(
                f"bbox_y2 ({self.bbox_y2_normalized}) must exceed "
                f"bbox_y1 ({self.bbox_y1_normalized})"
            )
        return self
```

### WR-04: `configure_logging()` is not actually idempotent across differing levels

**File:** `pastor_tracker/src/pastor_tracker/logging_config.py:13-31`

**Issue:** The docstring states "Idempotent." but the function calls `logging.basicConfig(level=...)` which by design is a **no-op if the root logger already has handlers** (Python stdlib semantics). Two consequences:

1. Calling `configure_logging("INFO")` then `configure_logging("DEBUG")` does NOT change the stdlib root logger level — the second `basicConfig` silently does nothing. structlog's filtering wrapper *is* re-installed with the new level (because `structlog.configure` always replaces), so structlog now wants to emit DEBUG but stdlib still drops them. Mixed behaviour, dependent on which path a log line takes.
2. `cache_logger_on_first_use=True` (line 30) means every previously-bound `structlog.get_logger(...)` instance keeps the *first* call's filtering wrapper, regardless of subsequent reconfigure. Combined with #1, the second `configure_logging` call is mostly a lie.

For Phase 1 this is harmless because nothing calls it twice — but the docstring claim is wrong, and Phase 7 (UI) "tuning sliders" plus Phase 8 (E2E ship gates) will both want to flip log levels at runtime. Either drop the idempotency claim *and* document the one-shot constraint, or fix the implementation:

**Fix (option A — honest one-shot):**
```python
def configure_logging(level: str = "INFO") -> None:
    """Configure structlog + stdlib logging for JSON output. CALL ONCE at startup."""
    # ... body unchanged ...
```

**Fix (option B — actually idempotent):**
```python
def configure_logging(level: str = "INFO") -> None:
    """Configure structlog + stdlib logging for JSON output. Idempotent."""
    numeric_level = getattr(logging, level.upper())
    root = logging.getLogger()
    root.setLevel(numeric_level)
    for handler in root.handlers:
        handler.setLevel(numeric_level)
    if not root.handlers:
        logging.basicConfig(level=numeric_level, format="%(message)s")
    structlog.configure(
        # ... existing processors ...
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,  # honour reconfigure
    )
```

Either fix is acceptable; the current state (claim + behaviour mismatch) is not.

### WR-05: `command_min_interval_ms` allows zero — silently disables the dispatcher rate limiter

**File:** `pastor_tracker/src/pastor_tracker/config.py:168`

**Issue:** `command_min_interval_ms: int = Field(default=50, ge=0, le=_INTERVAL_MS_MAX)` allows zero. Setting it to zero means "emit every M:" — the deliberate 50 ms throttle that PROMPT.md "## Anti-jitter" calls out as one of the four jitter mitigations (deadband / vel-clamp / **command throttle** / staleness-drop) is silently defeatable from a config file. The contract per PROMPT.md is "≥ 50 ms since last command"; the field's lower bound should match.

The same is technically true for `command_min_delta_deg: float = Field(..., ge=0.0, ...)` (line 167), which would defeat the 0.2° throttle. Less acute because the deadband (`pan_deadband_deg`) backstops it.

**Fix:**
```python
# config.py
command_min_interval_ms: int = Field(default=50, gt=0, le=_INTERVAL_MS_MAX)
# command_min_delta_deg can stay ge=0 (deadband backstops) but consider gt=0 for symmetry.
```

Add a unit test in `tests/test_config.py`:
```python
def test_command_min_interval_zero_rejected() -> None:
    """Zero interval defeats the throttle — PROMPT.md ## Anti-jitter requires > 0."""
    with pytest.raises(ValidationError):
        Config(command_min_interval_ms=0)
```

## Info

### IN-01: `_FrozenModel` pattern duplicated by `Config` — share or document

**File:** `pastor_tracker/src/pastor_tracker/config.py:72-84` and `pastor_tracker/src/pastor_tracker/core/types.py:49-52`

**Issue:** `Config` declares `frozen=True` + `extra="forbid"` directly on `SettingsConfigDict`; `core/types.py` introduces a `_FrozenModel(BaseModel)` base for the same `frozen=True, extra="forbid"` pattern. Two near-identical conventions for the same intent (frozen + strict extras). Either lift `_FrozenModel` to a shared module, or add a one-line comment in `config.py` noting the deliberate divergence (Settings vs BaseModel API). Pure scaffold-hygiene; no runtime impact.

**Fix:** Move `_FrozenModel` to `core/types.py` only (already there) and add a comment in `config.py` near `model_config`:
```python
# Note: BaseSettings uses SettingsConfigDict (not _FrozenModel) — same intent
# (frozen, extra='forbid') expressed via the settings-specific config object.
```

### IN-02: `command_min_delta_deg` and `pan_deadband_deg` share a misnamed ceiling `_DELTA_DEG_MAX`

**File:** `pastor_tracker/src/pastor_tracker/config.py:60, 143, 167`

**Issue:** `_DELTA_DEG_MAX: float = 10.0` is used as ceiling for both `pan_deadband_deg` (default 0.4) and `command_min_delta_deg` (default 0.2). Both are deg-domain so the unit isn't wrong, but a 10° deadband or a 10° dispatch-delta would be absurd in this hardware (motor full range ±90°, pan velocity 30°/s). A tighter ceiling (say 5.0°) and a more descriptive name (`_PAN_DEADBAND_CEILING_DEG`) would express intent better.

**Fix:**
```python
_PAN_DEADBAND_CEILING_DEG: float = 5.0  # 5° on a ±90° axis is already absurd
# rename references accordingly
```

### IN-03: `core/damping.py` constant `HALF: float = 0.5` is duplicated with `geometry.py`

**File:** `pastor_tracker/src/pastor_tracker/core/damping.py:24` and `pastor_tracker/src/pastor_tracker/core/geometry.py:19`

**Issue:** Both modules define `HALF: float = 0.5` at module scope. CLAUDE.md rule 3 (DRY) says "shared math goes in `core/`" — but this is *inside* core. A `core/_constants.py` (or just inlining `0.5` since it's a mathematical constant, not a tunable) avoids the duplication. Lowest-cost fix: drop both and use the literal `0.5` inline (a literal `0.5` in a closed-form math expression is not a "magic number" in the CLAUDE.md sense — it's the mathematical constant ½). Either choice is fine; the current state is two declarations of the same trivial constant.

### IN-04: `tests/test_logging.py` has no test for invalid level — `getattr(logging, level.upper())` will leak `AttributeError`

**File:** `pastor_tracker/src/pastor_tracker/logging_config.py:26` and `pastor_tracker/tests/test_logging.py`

**Issue:** `getattr(logging, level.upper())` raises `AttributeError` on bogus levels like `"BANANA"`. That's tiger-style fail-fast (good!), but there's no test pinning the behaviour, and the function signature (`level: str = "INFO"`) doesn't constrain it to a `Literal`. Either:
- Tighten the signature to `level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"` (mypy catches typos), OR
- Add `if not hasattr(logging, level.upper()): raise ValueError(...)` and a one-line test.

Phase 1 nit; tighten before Phase 7 exposes it via a slider.

### IN-05: `tests/test_config.py::test_loads_from_json_file` does not unset `PTS_*` env vars from the developer's shell

**File:** `pastor_tracker/tests/test_config.py:21-35`

**Issue:** The test `monkeypatch.chdir(tmp_path)` and writes `config.json`, but does not `monkeypatch.delenv("PTS_CAMERA_HORIZONTAL_FOV_DEG", raising=False)` (and friends). If a developer happens to have `PTS_CAMERA_HORIZONTAL_FOV_DEG=80` set in their shell while running tests, this test will fail with a misleading "JSON didn't load" error — actually env precedence won. monkeypatch does isolate within the test once set, but doesn't pre-clear what's already in the inherited environment.

**Fix:**
```python
def test_loads_from_json_file(...):
    # Pre-clear any PTS_* env that could shadow JSON loading in dev shells.
    for key in list(os.environ):
        if key.startswith("PTS_"):
            monkeypatch.delenv(key, raising=False)
    # ... rest unchanged ...
```

### IN-06: `tests/fixtures/_lint_canary.py` declared returns mismatch (intentional fixture)

**File:** `pastor_tracker/tests/fixtures/_lint_canary.py:14-19, 22-26`

**Issue:** `lint_canary_bare_except() -> None` returns `None` from inside the function body but the `try` block assigns `x = 1/0` (unused), and the type ignore `# type: ignore[return-value]` on `return None` is misleading — the annotation is `-> None` so `return None` is fine. The unused `x` and the misleading `type: ignore` are stylistic confusion in a fixture file. Per review rules this file gets `info` only — flagged for awareness; do not gate on it.

**Fix (cosmetic, optional):** Drop the unused `x =` assignment (`pass` is enough to provide a body for the `except`), and drop the wrong `# type: ignore`. The lint canary continues to fire on T201/E722/BLE001 regardless.

---

_Reviewed: 2026-05-03_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
