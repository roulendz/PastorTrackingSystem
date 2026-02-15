# Stack Research

**Domain:** Real-time camera tracking system with motor synchronization
**Researched:** 2026-02-15
**Confidence:** MEDIUM-HIGH (verified via PyPI, official docs, and multiple sources)

## Critical Compatibility Note: Python 3.10

The project currently targets Python 3.10. This creates a hard constraint:

- **NumPy >= 2.3** requires Python >= 3.11 (NumPy 2.2.x is the last line supporting 3.10)
- **SciPy >= 1.16** requires Python >= 3.11 (SciPy 1.15.x is the last line supporting 3.10)
- **InterpolatePy** requires Python >= 3.11 (unusable on 3.10)
- **Python 3.10 EOL:** October 2026

**Recommendation:** Upgrade to Python 3.12 early in the roadmap. Python 3.12 is 15-60% faster than 3.10, MediaPipe 0.10.32 supports 3.12, and it extends your support window to October 2028. All version recommendations below target Python 3.12. Where a 3.10-compatible fallback exists, it is noted.

---

## Recommended Stack

### Core Technologies (Already In Use -- Upgrade Versions)

| Technology | Current | Recommended | Purpose | Why Upgrade |
|------------|---------|-------------|---------|-------------|
| Python | 3.10 | 3.12 | Runtime | 15-60% faster, extends library compatibility window to 2028, required by modern NumPy/SciPy |
| opencv-python | 4.8.1.78 | 4.13.0.92 | Camera capture, image processing | Latest stable (Feb 2026), GIF support, improved JPEG-turbo performance on Windows |
| mediapipe | 0.10.8 | 0.10.32 | Pose detection | Latest stable (Jan 2026), supports Python 3.12, continued bug fixes |
| numpy | 1.24.3 | 2.2.3 | Numerical computing | Last line supporting 3.10 if upgrade delayed; use 2.4.2 on 3.12. Massive performance improvements |
| pyserial | 3.5 | 3.5 | Arduino serial communication | Stable, no updates needed. Last release Oct 2020 but fully functional |
| dearpygui | 1.11.1 | 1.11.1 | Settings GUI | Check latest version on 3.12 compatibility; may need update |

**Confidence:** HIGH -- all versions verified on PyPI with publication dates.

### New Core Libraries: Time Synchronization

| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| scipy (signal) | 1.15.x (3.10) / 1.17.0 (3.12) | Low-pass filtering, signal processing | `scipy.signal.sosfiltfilt` for zero-phase-offset filtering of motor position data. Already the standard for scientific Python signal processing. Do NOT add as separate dependency -- it comes in via InterpolatePy or python-control |

The time synchronization problem in this system is **not a library problem -- it is an architecture problem**. The core bug (virtual center line drift) stems from:

1. `time.perf_counter()` is called AFTER `VideoCapture.read()` returns, but `read()` blocks for the entire frame exposure + transfer time (~33ms at 30 FPS). The timestamp reflects "when Python got the frame" not "when the frame was captured."
2. Motor feedback timestamps use Arduino `micros()` mapped to `perf_counter` via an exponential moving average offset -- but with 0.98/0.02 weighting, this takes ~50 samples (1.7s at 30Hz) to converge after any clock drift event.
3. The `get_estimated_motor_angle_degrees()` interpolation is linear between history samples, which is correct for constant-velocity segments but wrong during acceleration/deceleration.

**What to add:**

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| (built-in) `time.perf_counter_ns()` | stdlib | Nanosecond-resolution timestamps | Eliminates float precision loss in timestamp arithmetic. Available since Python 3.7 |

**What NOT to add for time sync:** No external library solves this. The fix is architectural:
- Use `VideoCapture.grab()` then `time.perf_counter_ns()` then `retrieve()` to get the timestamp closer to actual frame capture moment
- Use a proper clock offset estimator (Cristian's algorithm or linear regression on recent Arduino timestamp pairs) instead of the current EMA
- Add measured camera latency as a constant offset to frame timestamps

**Confidence:** HIGH -- verified by reading the actual codebase, cross-referenced with OpenCV documentation on `grab()`/`retrieve()` semantics and multiple forum discussions on webcam latency.

### New Core Libraries: Motion Smoothing / Easing

| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| ruckig | 0.15.3 | Jerk-limited real-time trajectory generation | MIT licensed. The standard for online trajectory generation in robotics. Calculates time-optimal, jerk-constrained paths from ANY initial state to target. C++ core with Python bindings = fast. Supports real-time replanning every control cycle. Handles the "human-like motion" requirement directly |
| OneEuroFilter | 0.2.1 | Adaptive noise filter for pose detection output | Zero dependencies. Speed-adaptive cutoff: low jitter at low speeds, low lag at high speeds. Perfect for filtering MediaPipe pose center jitter without adding tracking lag. Well-proven in interactive systems (CHI 2012 paper) |

**Confidence for ruckig:** MEDIUM-HIGH -- verified on PyPI (v0.15.3, May 2025), MIT license confirmed on GitHub, but not tested on this specific hardware/system combination. The community version calculates waypoint trajectories via cloud API (non-real-time), but single-target state-to-state trajectories work fully offline, which is all this project needs.

**Confidence for OneEuroFilter:** MEDIUM -- verified on PyPI (v0.2.1, Aug 2024), zero dependencies confirmed. The filter is well-documented in academic literature but the PyPI package itself is lightly maintained. Fallback: implement the ~30-line algorithm directly (it is trivially simple).

**Why NOT InterpolatePy:** Requires Python >= 3.11 and pulls in NumPy >= 2.0 + SciPy >= 1.15 + Matplotlib >= 3.10. Overkill for this use case. Ruckig does online trajectory generation (what we actually need for real-time motor control). InterpolatePy is for offline trajectory planning.

### New Core Libraries: Deterministic Simulation Testing

| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| pytest | 9.0.2 | Test framework | The standard. No test suite exists yet -- this is the foundation |
| hypothesis | 6.151.6 | Property-based testing for control algorithms | Generates edge cases automatically. Each PBT finds ~50x as many mutations as a unit test (OOPSLA 2025). Perfect for testing PID/velocity controllers across parameter ranges |
| python-control | 0.10.2 | Control system simulation and analysis | Transfer functions, step response simulation, Bode/Nyquist plots, discrete-time systems. Built by Caltech. Use for modeling the motor plant and validating controller tuning offline |

**Confidence for pytest:** HIGH -- verified on PyPI, universal standard.

**Confidence for hypothesis:** HIGH -- verified on PyPI (v6.151.6, Feb 2026), supports Python 3.10+, actively maintained. Well-proven for this exact use case.

**Confidence for python-control:** MEDIUM -- verified on PyPI (v0.10.2, Jul 2025), requires Python >= 3.10. Not commonly used for testing per se, but provides the mathematical plant model needed for deterministic simulation. The alternative (writing your own discrete-time simulation) works too but is error-prone.

### Development Tools

| Tool | Purpose | Notes |
|------|---------|-------|
| pytest | Test runner | Use with `--tb=short` for compact output |
| pytest-cov | Coverage reporting | Target 80%+ on control algorithms |
| hypothesis | Property-based test generation | Configure `max_examples=200` for CI, `1000` for nightly |
| matplotlib | Visualization of test results | Already a transitive dependency. Use for plotting simulated vs actual trajectories |

---

## Simulation Test Harness Architecture (Not a Library -- Custom Build)

There is no off-the-shelf "TIGER-style deterministic simulation framework for camera tracking systems." The test harness must be custom-built, but it is straightforward using the recommended stack:

**Core idea:** Replace hardware interfaces with deterministic simulators:

```python
# SimulatedMotor: deterministic stepper motor model
# - Accepts angle commands
# - Reports position based on AccelStepper kinematic model
#   (trapezoidal velocity profile with configurable max speed/accel)
# - Returns position at any queried timestamp
# - NO randomness, NO real time delays

# SimulatedCamera: scripted pose sequence
# - Reads from a JSON file of (timestamp, person_x, person_y, confidence) tuples
# - Returns frames at deterministic timestamps
# - Supports scripted scenarios: "pastor walks left 2m over 5s", "pastor stands still 10s"

# SimulatedClock: controllable time source
# - Replaces time.perf_counter() calls
# - Advances in deterministic steps
# - Allows running 60 minutes of tracking in < 1 second
```

**What Hypothesis adds:** Property-based tests for the control algorithms themselves:
```python
@given(
    flErrorDegrees=st.floats(min_value=-45, max_value=45),
    flGain=st.floats(min_value=0.01, max_value=10.0)
)
def test_proportional_correction_same_sign_as_error(flErrorDegrees, flGain):
    """Control correction must always move toward the error, never away."""
    controller = ProportionalController(flGain)
    correction = controller.calculate_correction_from_error(flErrorDegrees)
    if flErrorDegrees != 0:
        assert (correction > 0) == (flErrorDegrees > 0)
```

---

## Installation

```bash
# Upgrade Python to 3.12 first, then:

# Core (already in use, upgrade versions)
pip install opencv-python==4.13.0.92
pip install mediapipe==0.10.32
pip install numpy==2.2.3       # or 2.4.2 on Python 3.12
pip install pyserial==3.5
pip install dataclasses-json==0.6.3
pip install dearpygui==1.11.1

# New: Motion smoothing
pip install ruckig==0.15.3
pip install OneEuroFilter==0.2.1

# New: Simulation and testing
pip install pytest==9.0.2
pip install pytest-cov
pip install hypothesis==6.151.6
pip install control==0.10.2

# New: Signal processing (transitive via control, but pin explicitly)
pip install scipy==1.15.2      # 1.17.0 on Python 3.12
pip install matplotlib         # transitive, needed for debug plots
```

### If staying on Python 3.10 (not recommended)

```bash
pip install numpy==2.2.3       # Last 2.x to support 3.10
pip install scipy==1.15.2      # Last to support 3.10
# ruckig, OneEuroFilter, hypothesis, pytest all work on 3.10
# InterpolatePy will NOT work on 3.10
```

---

## Alternatives Considered

| Recommended | Alternative | When to Use Alternative |
|-------------|-------------|-------------------------|
| ruckig (jerk-limited trajectories) | InterpolatePy | Only if doing offline trajectory planning, not real-time. Requires Python >= 3.11 |
| ruckig (jerk-limited trajectories) | Hand-rolled S-curve with easing functions | If ruckig's C++ bindings cause build issues on Windows. Use Robert Penner's easing functions as fallback |
| OneEuroFilter (pose noise filtering) | scipy.signal Butterworth low-pass | If you need fixed-cutoff filtering. OneEuroFilter's adaptive cutoff is better for tracking because it reduces lag during fast motion |
| OneEuroFilter (pose noise filtering) | Kalman filter (via numpy) | If you need to estimate both position AND velocity of the person. More complex to tune but provides velocity estimate for free |
| python-control (plant modeling) | Hand-rolled discrete simulation | If python-control's scipy dependency causes version conflicts. The motor model is simple enough (trapezoidal velocity profile) to simulate with raw numpy |
| hypothesis (property-based testing) | Just pytest with parametrize | If team is unfamiliar with PBT. Start with parametrize, add hypothesis later. But hypothesis is strictly better for control algorithm testing |
| Custom simulation harness | pytest-temporal | If the system evolves to have distributed components. Overkill for single-process control loop |

---

## What NOT to Use

| Avoid | Why | Use Instead |
|-------|-----|-------------|
| filterpy (Kalman filters) | Last updated 2018, supports Python 2.7-3.6 only. Will break on modern Python | Raw numpy Kalman implementation (~30 lines) or scipy.linalg for matrix operations |
| pykalman | Abandoned, last release 2012. Uses deprecated numpy APIs | Same as above |
| simple-pid | Last release Jul 2024, low maintenance. More importantly, you already have PID implemented in control_algorithm.py | Keep existing implementation, improve it |
| OpenPose | Heavyweight, requires GPU, primarily for multi-person. MediaPipe is faster and sufficient for single-person tracking | Stay with MediaPipe |
| time.time() | Affected by system clock adjustments (NTP, daylight saving). Already correctly using perf_counter() | time.perf_counter() or time.perf_counter_ns() |
| cv2.CAP_PROP_POS_MSEC | Returns -1 or 0 for live webcam streams on most backends. Unreliable for real-time timestamp extraction | time.perf_counter_ns() immediately after grab() |
| asyncio for motor control loop | GIL prevents true parallelism. Threading with locks (current approach) is correct for I/O-bound serial communication | Keep current threading model |

---

## Stack Patterns by Variant

**If motor is AccelStepper (current hardware -- trapezoidal velocity profile):**
- Ruckig's jerk-limited output must be translated to position targets sent at each control cycle
- The motor will follow its own trapezoidal profile to reach each target
- Set motor max speed/acceleration high enough that it tracks the ruckig output closely
- Monitor motor feedback to detect when motor falls behind the trajectory

**If upgrading to servo motor (future):**
- Ruckig can generate velocity commands directly
- OneEuroFilter becomes less critical (servos have built-in position feedback)
- python-control becomes more important (servo dynamics are more complex)

**If camera is HDMI capture card (not USB webcam):**
- Frame timestamps may have different latency characteristics
- May need to calibrate camera-to-motor time offset separately
- Consider using `cv2.CAP_PROP_POS_MSEC` if the capture card backend supports it

---

## Version Compatibility Matrix

| Package | Python 3.10 | Python 3.12 | Notes |
|---------|-------------|-------------|-------|
| opencv-python 4.13.0.92 | Yes | Yes | Builds with NumPy 2.x on 3.12 |
| mediapipe 0.10.32 | Yes | Yes | Supports 3.9-3.12 |
| numpy 2.2.3 | Yes | Yes | Last 2.x line supporting 3.10 |
| numpy 2.4.2 | No | Yes | Requires >= 3.11 |
| scipy 1.15.2 | Yes | Yes | Last supporting 3.10 |
| scipy 1.17.0 | No | Yes | Requires >= 3.11 |
| ruckig 0.15.3 | Yes (likely) | Yes | C++ bindings, check Windows wheels |
| OneEuroFilter 0.2.1 | Yes | Yes | Pure Python, no constraints |
| hypothesis 6.151.6 | Yes | Yes | Requires >= 3.10 |
| pytest 9.0.2 | Yes | Yes | Requires >= 3.10 |
| python-control 0.10.2 | Yes | Yes | Requires >= 3.10, depends on scipy |
| InterpolatePy 2.0.1 | No | Yes | Requires >= 3.11 |
| dearpygui 1.11.1 | Yes | Check | May need version bump for 3.12 |

---

## Sources

- [NumPy 2.2.3 on PyPI](https://pypi.org/project/numpy/2.2.3/) -- Python 3.10 support confirmed (HIGH confidence)
- [SciPy Toolchain Roadmap](https://docs.scipy.org/doc/scipy/dev/toolchain.html) -- SciPy 1.15 is last to support Python 3.10 (HIGH confidence)
- [opencv-python on PyPI](https://pypi.org/project/opencv-python/) -- v4.13.0.92, Feb 2026 (HIGH confidence)
- [mediapipe on PyPI](https://pypi.org/project/mediapipe/) -- v0.10.32, Jan 2026, supports 3.9-3.12 (HIGH confidence)
- [ruckig on PyPI](https://pypi.org/project/ruckig/) -- v0.15.3, May 2025, MIT license (HIGH confidence)
- [ruckig on GitHub](https://github.com/pantor/ruckig) -- MIT license, community version limitations documented (HIGH confidence)
- [InterpolatePy on PyPI](https://pypi.org/project/InterpolatePy/) -- v2.0.1, requires Python >= 3.11 (HIGH confidence)
- [OneEuroFilter on PyPI](https://pypi.org/project/OneEuroFilter/) -- v0.2.1, Aug 2024 (MEDIUM confidence -- lightly maintained)
- [hypothesis on PyPI](https://pypi.org/project/hypothesis/) -- v6.151.6, Feb 2026 (HIGH confidence)
- [python-control on PyPI](https://pypi.org/project/control/) -- v0.10.2, Jul 2025 (HIGH confidence)
- [pytest on PyPI](https://pypi.org/project/pytest/) -- v9.0.2, Dec 2025 (HIGH confidence)
- [PEP 418](https://peps.python.org/pep-0418/) -- perf_counter monotonicity guarantees (HIGH confidence)
- [CPython issue #115637](https://github.com/python/cpython/issues/115637) -- perf_counter monotonicity discussion (HIGH confidence)
- [OpenCV Forum: grab/retrieve timing](https://answers.opencv.org/question/129827/reduce-processing-time-of-webcam-capture/) -- MEDIUM confidence, community forum
- [OpenCV Forum: webcam buffer latency](https://answers.opencv.org/question/91077/delay-when-grabbing-frames-from-webcam/) -- MEDIUM confidence, community forum
- [1 Euro Filter paper (CHI 2012)](https://inria.hal.science/hal-00670496v1/document) -- HIGH confidence, peer-reviewed
- [OOPSLA 2025 PBT evaluation](https://cseweb.ucsd.edu/~mcoblenz/assets/pdf/OOPSLA_2025_PBT.pdf) -- Hypothesis finds ~50x mutations vs unit tests (HIGH confidence)
- [Python endoflife.date](https://endoflife.date/python) -- Python 3.10 EOL October 2026 (HIGH confidence)

---
*Stack research for: Pastor Tracking System -- camera/motor time sync, motion smoothing, simulation testing*
*Researched: 2026-02-15*
