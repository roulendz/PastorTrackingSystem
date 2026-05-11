# pastor-tracker

Cinematic auto-tracker for a single primary speaker — Python 3.12 + Arduino stepper pan.

## Overview

The `pastor-tracker` app follows one primary speaker on stage and drives an Arduino stepper-motor camera mount over USB serial, producing a jitter-free pan with rule-of-thirds lead-room framing. Input is the OBS Virtual Camera; output is a `M:<angle>` serial command stream consumed by the firmware in `arduino/stepper_controller/`. The cinematic constraint is non-negotiable: no overshoot, no oscillation, no lock-loss to audience or interpreter, no audible motor jerk. If pan smoothness or lock stability fails, nothing else matters.

## Requirements

- **Operating system:** Windows 10 or Windows 11.
- **Runtime:** Python 3.12 with the `uv` package manager installed.
- **Capture:** OBS Studio 28 or newer (Virtual Camera ships built-in since OBS 26 on Windows; no separate plugin needed).
- **Motor controller:** Arduino Uno R3 or R4 — or a CH340 / FTDI clone — flashed with the firmware in `arduino/stepper_controller/`. Boot banner must be `READY:v2`.
- **Optional GPU:** CUDA-capable NVIDIA GPU for YOLO11-pose inference. CPU works but at lower frame rates; `yolo_device` defaults to `auto`.

## Install

```powershell
git clone <repo-url>
cd pastor_tracker
uv sync
```

`uv sync` resolves the dependency lock and creates `.venv/` inside the `pastor_tracker/` directory. The console script `pastor-tracker` is registered via `pyproject.toml` and runs as `uv run pastor-tracker`.

### Verifying YOLO weights

The YOLO11-pose weights file (`yolo11n-pose.pt`) is downloaded by ultralytics on first use and lives under `~/.cache/Ultralytics/`. Before running on stage, verify its SHA256 hash matches the upstream-published value:

```powershell
# Windows PowerShell
Get-FileHash yolo11n-pose.pt -Algorithm SHA256
```

```bash
# Linux / WSL / macOS
sha256sum yolo11n-pose.pt
```

The pinned authoritative hash will be added to this README in v2 supply-chain hardening, once the weights file is committed to the repository. For v1, operators should record the hash produced by the command above on first install and re-check it before every stage rehearsal to detect tampering.

## OBS Virtual Camera setup

1. **Install OBS Studio 28 or newer.** Virtual Camera is built in; no separate plugin needed.
2. **Open OBS.** In the Scenes panel, click `+` and add a scene named `Stage`.
3. **Add the physical stage camera as a Source.** In Sources, click `+` → Video Capture Device → select the USB camera.
4. **Click Start Virtual Camera** in the Controls panel (bottom-right). The button label changes to "Stop Virtual Camera" when active.
5. **Leave OBS running.** `pastor-tracker` enumerates DirectShow devices, finds the device named `OBS Virtual Camera`, and opens it.

**Verification:** Run `uv run pastor-tracker --headless` from `pastor_tracker/`. If the camera is found, the structured log line **camera_started** appears on stdout. If not, see [Troubleshooting](#troubleshooting).

**Common gotchas:**
- OBS must be started **before** `pastor-tracker`. DirectShow enumeration runs once at boot.
- If you renamed the device (OBS → Settings → Virtual Camera → Output Type → Source), the new name must match the `obs_camera_name` field in `config.json` (default `"OBS Virtual Camera"`).
- Skype, Zoom, Teams, or a browser tab with webcam permission can hold the device exclusively and block OpenCV from opening it. Close those apps before launching.
- If **Start Virtual Camera** is greyed out, run OBS as administrator once to register the device.

## FOV calibration

The pipeline maps normalized-frame x ∈ [0, 1] to motor angle via the lens horizontal field of view. An incorrect FOV produces either over-pan (motor leads the subject) or under-pan (motor lags). v1 calibration is manual; auto-calibration is a v2 deferred item.

**Procedure:**

1. Place a 1-metre-wide reference (a bar, or two tape marks 1 m apart on a wall) at the **stage centre**, at the same distance from the lens that the speaker will stand.
2. Measure the **lens-to-bar distance** in metres (call this `d`).
3. Start OBS Virtual Camera and run `uv run pastor-tracker` (default UI mode). Aim the physical camera so the bar is centred horizontally.
4. In the preview window, note how much of the frame the 1 m bar fills.
5. If the bar fills the **full** visible frame width, FOV is:

   ```
   h_fov_deg = 2 * atan(0.5 / d) * (180 / π)
   ```

   Examples:
   - `d = 2.0 m` → 1 m fills full frame → `h_fov ≈ 28.07°`
   - `d = 1.0 m` → 1 m fills full frame → `h_fov ≈ 53.13°`
   - `d = 0.7 m` → 1 m fills full frame → `h_fov ≈ 71.08°` (typical webcam wide setting)

6. If the bar fills only a fraction `f ∈ (0, 1]` of the frame, scale: `h_fov_deg = 2 * atan(0.5 / (d * f)) * (180 / π)`. If you cannot see the bar at all (f → 0), re-aim the camera before recording a measurement; the formula is undefined at f = 0.
7. Write the result into `pastor_tracker/config.json` (default 70.0):

   ```json
   {
     "camera_horizontal_fov_deg": 70.0
   }
   ```

   Or set the live slider in the UI and click **Save Config** — the app restarts the pipeline with the new value.

**Verification:** With FOV set correctly, a 10° camera-mount pan should move the subject roughly `10 / h_fov` of the frame width (about 14% at 70° FOV, 19% at 53° FOV). Eyeball this on the preview.

**Pitfall:** OBS Virtual Camera does not change the FOV from the source camera. The FOV you measure is the **physical lens FOV** of the USB camera, unaffected by OBS scaling.

## Run

Run from the `pastor_tracker/` directory (the `config.json` source resolves from CWD when `--config-json` is not passed).

```powershell
cd pastor_tracker
uv run pastor-tracker                                          # default UI
uv run pastor-tracker --headless                               # Phase 6 path, SIGINT-driven
uv run pastor-tracker --config-json C:\path\to\config.json     # explicit config path
```

If neither flag is passed, UI mode is selected (DearPyGui dashboard with live preview, sliders, status panel, and hotkeys); this matches passing `--ui` explicitly. `--headless` is the SIGINT-driven Phase 6 path used by automation, regression scripts, and CI smoke runs. `--ui` and `--headless` are mutually exclusive; passing both is rejected at the argparse layer.

Exit codes (sysexits.h-flavoured):

| Code | Constant | Meaning |
|-|-|-|
| 0 | EXIT_OK | Clean shutdown via SIGINT or natural drain. |
| 64 | EXIT_INVALID_CONFIG | `config.json` failed Pydantic validation — unknown field, out-of-range value, or cross-field violation. |
| 65 | EXIT_HARDWARE_FAILED | Camera, Arduino port, or perception engine failed at boot. |
| 70 | EXIT_CRASHED | Uncaught exception in the pipeline tick task. |

## Hotkeys

| Key | Action | Trigger | Notes |
|-|-|-|-|
| `S` | Start tracking | On release | Begins pipeline; safe to spam |
| `P` | Pause / resume | On release | Toggles; motor holds last commanded angle |
| `H` | Home | On release | Commands motor to 0° |
| `E` | E-stop | On press (debounced) | Immediate; latches faulted state until app restart |
| `Q` | Quit | On release | Prompts to save unsaved slider changes if dirty |

E-stop fires on key-press for minimum latency; other hotkeys fire on key-release to prevent auto-repeat.

## Tuning sliders

The dashboard exposes four live sliders. Edits go into a pending-config buffer (an **unsaved changes** badge appears next to the row); the buffer is committed only when **Save Config** succeeds.

| Slider | Config field | Effect |
|-|-|-|
| Pan time-constant | `pan_time_constant_sec` | Larger = smoother but laggier pan |
| Deadband | `pan_deadband_deg` | Larger = less micro-jitter, looser tracking |
| Max velocity | `pan_max_velocity_deg_per_sec` | Larger = faster catch-up, louder motor |
| FOV | `camera_horizontal_fov_deg` | Must match physical lens (see FOV calibration) |

**Save Config restart semantics:** clicking **Save Config** runs `Config.model_validate(...)` on the buffered values. On success, the running pipeline is stopped and a **fresh** `PipelineThreadHost` plus `Pipeline` are constructed with the new config, then started; the badge clears. On validation failure (e.g. text-entry pushes a slider out of bounds) the red error banner shows the validation error and the buffer is preserved so you can edit further. **Cancel does not clear the buffer** — unsaved changes survive until Save Config succeeds or you choose **Quit Anyway** in the close-window modal. The modal offers three options: **Save & Quit**, **Quit Anyway**, **Cancel**.

## First run on stage

This is the QA-04 on-stage smoke procedure. Run it on every stage rehearsal before going live.

### Pre-flight

1. Stage camera USB-plugged into the host machine.
2. OBS Studio launched; **Stage** scene configured with the stage camera as Source; **Start Virtual Camera** clicked.
3. Uno (R3, R4, CH340, or FTDI) USB-plugged with `READY:v2` firmware already burned. Device visible in Device Manager → Ports (COM & LPT).
4. `config.json` has `camera_horizontal_fov_deg` set per the [FOV calibration](#fov-calibration) procedure above.
5. Speaker on stage at typical position.

### Run

6. From `pastor_tracker/`: `uv run pastor-tracker` (default UI).
7. Confirm the preview window opens and shows live frames from the OBS Virtual Camera.
8. Press `S` to start tracking. The framing-target vertical line is drawn, the subject bounding box overlays the speaker, and the motor begins tracking after a < 1 s damping ramp-up.
9. Speaker walks slowly stage-left, dwells 3 s, walks stage-right, dwells 3 s, repeats for **at least 5 minutes** of continuous tracking.

### Pass criteria

- [ ] Zero overshoot — motor never overshoots framing target and recoils.
- [ ] Zero audible motor jerk — pan is silent or smooth-hum; no clack, no chatter.
- [ ] Zero lock-loss to audience or interpreter — camera does not jump to a non-primary subject for > 2 s.
- [ ] Rule-of-thirds framing holds — moving-right subject sits in left third; moving-left subject sits in right third; dwelling subject sits at centre (INTENT-03).
- [ ] E-stop works — pressing `E` halts motor within one tick (< 50 ms perceived).
- [ ] 5+ minutes of continuous tracking — no crashes, no `ERROR:` log lines.

### Fail handling

Any unchecked box means re-run with the appropriate config tweak: raise `pan_deadband_deg` if jitter dominates, raise `pan_time_constant_sec` if overshoot dominates, re-run [FOV calibration](#fov-calibration) if framing mistracks. After the tweak, re-run the full 5-minute pattern. Log the failure mode plus remediation in `.planning/phases/08-end-to-end-and-ship-gates/08-VERIFICATION.md` before the next attempt.

## Troubleshooting

### OBS Virtual Camera not found

**Symptom:** App exits with EXIT_HARDWARE_FAILED (65); the `OBSCameraNotFoundError` message lists the available DirectShow devices (the `camera_discovered` / `camera_multiple_matches` success events do not fire on this path).

**Fixes:**
- Start OBS and click **Start Virtual Camera** in the Controls panel.
- Confirm the device name matches `obs_camera_name` in `config.json` (default `"OBS Virtual Camera"`).
- Close other apps holding the camera exclusively (Skype, Zoom, Teams, browser tabs with webcam permission).

### Arduino not detected

**Symptom:** App exits with EXIT_HARDWARE_FAILED (65); `ArduinoPortNotFoundError` is raised before any port log event fires (the `port_discovered` / `port_multiple_matches` / `port_manual_override` success events do not fire on this path).

**Fixes:**
- Confirm the Uno is plugged in via USB. Check Device Manager → Ports (COM & LPT) for a `COMn` entry.
- Confirm the board is flashed with the shipped firmware from `arduino/stepper_controller/`. The firmware **must** emit `READY:v2` on boot.
- Auto-detect matches these VID:PIDs:

  | Board | VID:PID |
  |-|-|
  | Genuine Uno R3 | `2341:0043` |
  | Genuine Uno R4 | `2341:0069` |
  | CH340 clone | `1A86:7523` |
  | FTDI clone | `0403:6001` |

- If your board uses a different USB-serial bridge, set `arduino_port` explicitly in `config.json` (e.g. `"COM7"`). Auto-detect is preferred — COM port numbering changes per USB jack.

### Heartbeat lost — ERROR:11

**Symptom:** Structured log line **error_received code=11 code_name=HEARTBEAT_TIMEOUT**. Motor stops. App requires restart.

**Fixes:**
- Re-seat the USB cable firmly. Cheap micro-USB cables under high load can drop packets.
- Close apps that hog the CPU (renderers, browsers with heavy tabs). The PC heartbeat is a 200 ms async task — sustained event-loop starvation > 800 ms triggers firmware `ERROR:11`.
- If the firmware watchdog is still asserting after restart, power-cycle the Uno (unplug USB for 5 seconds).

### Lock-loss to subject > 2 s

**Symptom:** Motor stops following the speaker; log lines **lock_loss** then **lock_acquired** (or **lock_reacquired** if re-acquire happens on a different track_id).

**Fixes:**
- Lighting: BoT-SORT relies on subject appearance; very dim or strongly backlit subjects lose ID. Raise stage lights or reduce backlight.
- Detection confidence threshold: `detection_confidence_min` defaults to 0.55. If the speaker wears low-contrast clothing, lower to 0.45 in `config.json`.
- Audience or interpreter walking through frame: the central-60% heuristic on re-acquire keeps the camera on stage centre. Brief lock-loss to interpreter is expected and self-corrects within ~2 s.

## Pipeline

```
OBS VCam → PoseDetector → SubjectTracker → MotionAnalyzer → Framer → PanController → CommandDispatcher → ArduinoMotor
```

## Project layout

The repository is split into two top-level trees: `arduino/stepper_controller/` (PlatformIO firmware, shipped) and the `pastor_tracker/` Python tracking app (this directory). See `.planning/PROJECT.md` for the full directory map and `CLAUDE.md` for the engineering-culture contract.

## Engineering culture

This codebase enforces tiger-style fail-fast, single-responsibility, immutable DTOs, and Conventional Commits. See `CLAUDE.md` for the full set of rules.
