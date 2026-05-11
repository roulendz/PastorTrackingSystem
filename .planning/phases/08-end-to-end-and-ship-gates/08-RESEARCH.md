# Phase 8: End-to-End and Ship Gates - Research

**Researched:** 2026-05-11
**Domain:** Operator documentation + ship-gate verification + deferred-smoke procedure
**Confidence:** HIGH

## Summary

Phase 8 ships v1 by producing **three artifacts**, **no new code**:

1. **DOC-01** — Replace the 11-line `pastor_tracker/README.md` stub with a full operator manual.
2. **Ship-gate re-verification** — Re-run `ruff check` + `mypy --strict` + `pytest`, sample `git log` for Conventional Commits compliance, and record results in VERIFICATION.md.
3. **QA-04 smoke procedure** — Write the on-stage smoke checklist into both the README ("First run on stage") and VERIFICATION.md `human_verification`. Phase 8 closes with `human_needed`, matching Phase 7's pattern.

Lint, types, and tests are already green as of 2026-05-11 (CONTEXT.md confirms ruff clean, mypy clean across 32 source files, suite passes 561/562 with 1 skipped). The risk surface is small: a careless README commit might trip a pre-commit hook, or the README might claim a field name or flag that does not exist. Verification is therefore "doc claims match reality" rather than runtime invariants.

**Primary recommendation:** Write README in field-name-faithful form (cross-check every `config.py` field reference and CLI flag against source), commit it, re-run gates, record verification.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

- **README is a full operator manual**, not a stub. Required sections, in order:
  1. Overview (what the system does + cinematic constraint)
  2. Requirements (Win 10/11, Python 3.12, uv, OBS Studio + Virtual Camera, Uno R3/R4 or CH340/FTDI clone with shipped firmware)
  3. Install (`git clone`, `cd pastor_tracker`, `uv sync`)
  4. OBS Virtual Camera setup (start OBS, add stage camera as source, click Start Virtual Camera; one optional screenshot)
  5. FOV calibration (measure horizontal FOV, set `camera_h_fov_deg` — see Open Question 1 below)
  6. Run (`uv run pastor-tracker` default UI, `--headless`, `--config path.json` — see Open Question 2)
  7. Hotkey table (S start, P pause, H home, E e-stop debounced, Q quit; matches UI-05)
  8. Tuning sliders (4 sliders: pan time-constant, deadband, max velocity, FOV; Save Config restart behavior)
  9. Troubleshooting (OBS VCam missing; Arduino not detected with VID:PID list; ERROR:11 heartbeat lost; lock-loss > 2 s)

- **QA-04 deferred to human verification.** Phase 8 writes the smoke procedure into the README's "First run on stage" subsection AND VERIFICATION.md `human_verification`. Phase 8 closes with `human_needed`, mirroring Phase 7.
- **Single plan: `08-01-PLAN.md`** covering README + ship-gate re-verify + VERIFICATION. One or two atomic commits (README + verification record). No new test files. No new source files.
- **Ship gates:** QA-01 ruff + QA-02 mypy --strict already clean — re-run during execution. QA-03 Conventional Commits already clean through Phase 7. If any gate regresses during README work, fail loud and fix before committing.

### Claude's Discretion

- README tone, formatting, exact wording.
- Exact ordering of troubleshooting entries.
- Whether to add a small ASCII pipeline diagram (only if it adds clarity).

### Deferred Ideas (OUT OF SCOPE)

- Screenshots of OBS VCam setup and dashboard — optional only, skip if cost > value.
- Arduino firmware flashing documentation — belongs in `arduino/README.md`, not v1 operator README.
- Auto FOV calibration (CAL-01) — v2.
- Service-mode / autostart documentation — v2.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| DOC-01 | `README.md` — install (`uv sync`), OBS VCam setup, FOV calibration procedure, run command, hotkey table | Section 2 README structure, Section 3 OBS setup, Section 4 FOV calibration, Section 5 hotkey table |
| QA-01 | `ruff check` clean | Section 7 ship-gate commands |
| QA-02 | `mypy --strict` clean — every function annotated, no `Any` | Section 7 ship-gate commands |
| QA-03 | One Conventional Commit per module per Order of Work | git log sampling (verified 25 recent commits all use `feat(NN-NN):` / `fix(NN):` / `docs(NN):` / `test(NN-NN):` scopes — clean) |
| QA-04 | End-to-end smoke test on stage — real Uno (auto-detected port), real OBS VCam, real speaker | Section 8 QA-04 smoke procedure; deferred to human |
</phase_requirements>

## Architectural Responsibility Map

Documentation phase — no new runtime tiers. Mapping shows which existing tier each README section describes.

| README Section | Owning Tier | Source of Truth |
|----------------|-------------|-----------------|
| Install | Build tooling | `pyproject.toml`, `uv lock` |
| OBS VCam setup | Edge / camera I/O | OBS Studio external + `io/obs_camera.py` `obs_camera_name` config field |
| FOV calibration | Edge / camera I/O + Config | `core/geometry.py` + `config.camera_horizontal_fov_deg` |
| Run command + flags | Process entry | `src/pastor_tracker/__main__.py` argparse block (lines 225-245) |
| Hotkey table | UI | `ui/dashboard.py` (Phase 7 P03 — E debounced, S/P/H/Q on release) |
| Tuning sliders | UI | `ui/dashboard.py` sliders + Save Config restart logic (Phase 7 P03/P04) |
| Troubleshooting | All tiers | Error code surface (PROMPT.md ## Failure Modes, ERROR:11 firmware contract) |

## Standard Stack

No external library research — the stack is locked from CLAUDE.md and Phases 1-7. Documentation only references what already exists.

### Documentation Conventions

| Item | Choice | Why |
|------|--------|-----|
| Format | Markdown (GitHub-flavored) | `pyproject.toml` already declares `readme = "README.md"` |
| Code fences | ```powershell for Windows commands, ```bash for cross-platform, ```json for config snippets | Windows is the deployment OS |
| Tables | GitHub-flavored markdown tables | Renders in GitHub web + most editors |
| Headers | `#` H1 once (title), `##` for sections, `###` for sub-sections | Standard README pattern |

## README Structure Recommendation

**Concrete section list (matches CONTEXT.md decision verbatim; rationale added):**

```
# pastor_tracker

[Tagline — one sentence: cinematic auto-tracker for a single speaker.]

## Overview                          (1 paragraph — what + cinematic constraint)
## Requirements                      (Win 10/11, Python 3.12, uv, OBS Studio + VCam, Uno + shipped firmware)
## Install                           (git clone, cd, uv sync)
## OBS Virtual Camera setup          (5 numbered steps)
## FOV calibration                   (procedure + formula + config.json snippet)
## Run                               (default UI command, --headless, --config flags)
## Hotkeys                           (table: S/P/H/E/Q)
## Tuning sliders                    (4 sliders + Save Config restart semantics)
## First run on stage                (QA-04 smoke checklist — 5 min, what counts as pass)
## Troubleshooting                   (4 entries: OBS missing, Arduino missing, ERROR:11, lock-loss)
## Project layout                    (1 paragraph or skip — defer to CLAUDE.md / PROJECT.md)
## Engineering culture               (1 sentence pointer to CLAUDE.md)
```

**Rationale for ordering:** A first-time operator reads top-to-bottom. They install before they configure, configure (FOV) before they run, run before they hit hotkeys, and only consult troubleshooting on failure. "First run on stage" sits before Troubleshooting because it is the success path; Troubleshooting is the failure path.

[CITED: GitHub Docs - About READMEs] Standard README ordering for desktop apps: name, description, install, usage, configuration, hotkeys, troubleshooting.

## OBS Virtual Camera Setup (Windows 10/11)

[VERIFIED: io/obs_camera.py enumerates via FilterGraph and matches `"OBS Virtual Camera"` by name — config field `obs_camera_name` default]

Steps to include in README:

1. **Install OBS Studio 28.0 or newer.** Virtual Camera is built in since OBS 26 on Windows; no separate plugin needed. [CITED: obsproject.com/kb/virtual-camera-guide]
2. **Open OBS.** In the Scenes panel, click `+` and add a scene named (e.g.) "Stage".
3. **Add the physical stage camera as a Source.** In Sources, click `+` → Video Capture Device → select the USB camera.
4. **Click Start Virtual Camera** in the Controls panel (bottom-right). The button label changes to "Stop Virtual Camera" when active.
5. **Leave OBS running.** `pastor_tracker` will enumerate DirectShow devices, find the device named `OBS Virtual Camera`, and open it via `cv2.VideoCapture(idx, CAP_DSHOW)`.

**Verification:** Run `uv run pastor-tracker --headless` from `pastor_tracker/`. If the camera is found, structured log line `obs_camera_opened` is emitted. If not, see Troubleshooting → "OBS VCam not found".

**Common gotchas to mention:**
- OBS must be started **before** `pastor-tracker`. The DirectShow enumeration happens once at boot.
- If the operator renamed the device (Settings → Virtual Camera → Output Type → Source), the new name must match `obs_camera_name` in config.json.
- Skype/Zoom/Teams holding the device exclusively prevents OpenCV from opening it. Close other apps that use the VCam.

[ASSUMED] Modern OBS (28+) does not require admin rights on Windows 10/11 to start the virtual camera, but the operator's machine policy may differ. Recommend mentioning "if Start Virtual Camera is greyed out, run OBS as administrator once."

## FOV Calibration Procedure

The pipeline maps normalized-frame x ∈ [0, 1] to motor angle via `core/geometry.normalized_x_to_angle_deg(x, h_fov_deg)`. An incorrect FOV produces either over-pan (motor leads subject) or under-pan (motor lags). The README must explain how to measure horizontal FOV from a known-distance reference.

[VERIFIED: config.py line 134 — actual field name is `camera_horizontal_fov_deg`, NOT `camera_h_fov_deg` as CONTEXT.md says. README MUST use the real field name.]

**Procedure (operator-facing wording):**

1. Place a reference object (a 1-meter-wide bar, or two tape marks 1 m apart on a wall) at the **stage center**, at the same distance from the lens that the speaker will stand.
2. Measure the **lens-to-bar distance** in meters (call this `d`).
3. Start OBS Virtual Camera and start `pastor-tracker --ui`. Aim the physical camera so the bar is centered horizontally.
4. In the preview window, note how much of the frame the 1 m bar fills. If the bar spans the full visible frame width, FOV is approximately:

   `h_fov_deg = 2 * atan(0.5 / d) * (180 / π)`

   Examples:
   - `d = 2.0 m` → fills full frame → `h_fov ≈ 28.07°`
   - `d = 1.0 m` → fills full frame → `h_fov ≈ 53.13°`
   - `d = 0.7 m` → fills full frame → `h_fov ≈ 71.08°` (typical webcam wide setting)

5. If the bar fills only a fraction `f ∈ (0, 1]` of the frame, scale: `h_fov_deg = 2 * atan(0.5 / (d * f)) * (180 / π)`.
6. Write the result into `pastor_tracker/config.json`:

   ```json
   {
     "camera_horizontal_fov_deg": 70.0
   }
   ```

   Or set the live slider in the UI and click **Save Config** — the app restarts the pipeline with the new value.

7. **Verify:** With FOV set correctly, panning the camera mount 10° should move the subject 10°/`h_fov` of the frame width (roughly 1/7 of the frame at 70° FOV). Eyeball this on the preview.

**Common pitfall:** The OBS Virtual Camera does not change the FOV from the source camera. The FOV the operator measures is the **physical lens FOV**, unaffected by OBS scaling. [VERIFIED: OBS VCam is a pass-through of the selected scene's render; geometric FOV equals the source camera's lens FOV at the chosen capture resolution.]

**v1 calibration is manual.** Auto-calibration (CAL-01) is v2.

## Hotkey Table Format

[VERIFIED: commit `16887d9 fix(07): WR-05 debounce E hotkey + switch S/P/H/Q to release-handler` — Phase 7 final hotkey behavior is **E debounced (key-press)**, **S/P/H/Q on key-release**.]

README table form:

| Key | Action | Trigger | Notes |
|-----|--------|---------|-------|
| `S` | Start tracking | On release | Begins pipeline; safe to spam |
| `P` | Pause / resume | On release | Toggles; motor holds last commanded angle |
| `H` | Home | On release | Commands motor to 0° |
| `E` | E-stop | On press (debounced) | Immediate; latches faulted state until app restart |
| `Q` | Quit | On release | Prompts to save unsaved slider changes if dirty |

Rationale for the on-press / on-release split:
- **E** must respond on press because operator intent is panic-stop — release latency is unacceptable on stage. Debounce prevents double-fires from a held key.
- **S/P/H/Q** are deliberate operator commands; on-release prevents accidental auto-repeat when the operator holds a key.

The README should include the table verbatim and one line: *"E-stop fires on key-press for minimum latency; other hotkeys fire on key-release to prevent auto-repeat."*

## Tuning Sliders Section

[VERIFIED: Phase 7 P03 commits `7e16f6b feat(07-03): wire 4 sliders to _pending_config` and `b17b718 feat(07-03): Save Config restart sequence`]

Four sliders, named per UI-02:

| Slider | Config field | Effect |
|--------|--------------|--------|
| Pan time-constant | `pan_time_constant_sec` | Larger = smoother but laggier pan |
| Deadband | `pan_deadband_deg` | Larger = less micro-jitter, looser tracking |
| Max velocity | `pan_max_velocity_deg_per_sec` | Larger = faster catch-up, louder motor |
| FOV | `camera_horizontal_fov_deg` | Must match physical lens (see Calibration) |

**Save Config restart behavior (locked in Phase 7 P03):**
- Slider changes go into a `_pending_config` buffer; the **unsaved badge** appears.
- **Save Config** validates the buffered config via `Config.model_validate(...)`. On success, the pipeline is **stopped and restarted with a fresh `PipelineThreadHost`**.
- On validation failure, the buffer survives, the badge stays up, and the operator can edit further.
- **Cancel does not clear the buffer.** Unsaved changes persist until Save Config succeeds or Quit (with the modal-decision dialog) is confirmed.
- The Quit modal offers three choices: **Save & Quit**, **Quit Anyway**, **Cancel**.

README should state this concisely — one paragraph plus a "Tip: changes are live in the buffer; click Save Config to commit and restart the pipeline."

## Troubleshooting Entries

Four entries (CONTEXT.md-locked), with concrete operator-facing remediation:

### 1. "OBS Virtual Camera not found"

**Symptom:** App exits with `EXIT_HARDWARE_FAILED` (65) and log line `obs_camera_not_found` lists the available DirectShow devices.

**Fixes:**
- Start OBS and click **Start Virtual Camera** in Controls panel.
- Check that the device name matches `obs_camera_name` in `config.json` (default: `"OBS Virtual Camera"`).
- Close other apps holding the camera exclusively (Skype, Zoom, Teams, browser tabs with webcam permission).

### 2. "Arduino not detected"

[VERIFIED: io/arduino_transport.py auto-detect over `serial.tools.list_ports.comports()` matches VID:PID per CLAUDE.md]

**Symptom:** App exits with `EXIT_HARDWARE_FAILED` (65) and log line `arduino_port_not_found`.

**Fixes:**
- Confirm the Uno is plugged in via USB. Check Device Manager → Ports (COM & LPT) for a `COMn` entry.
- Confirm the board is flashed with the shipped firmware from `arduino/stepper_controller/`. The firmware MUST emit `READY:v2` on boot.
- Auto-detect matches these VID:PIDs:

  | Board | VID:PID |
  |-------|---------|
  | Genuine Uno R3 | `2341:0043` |
  | Genuine Uno R4 | `2341:0069` |
  | CH340 clone | `1A86:7523` |
  | FTDI clone | `0403:6001` |

- If your board uses a different USB-serial bridge, set `arduino_port` explicitly in `config.json` (e.g. `"COM7"`). Auto-detect is preferred — COM port numbering changes per USB jack.

### 3. "Heartbeat lost — ERROR:11"

[VERIFIED: PROMPT.md ## Failure Modes — firmware halts on > 1000 ms PC-heartbeat silence; emits `ERROR:11`. IO-ARD-05 sends 200 ms heartbeat to prevent this; IO-ARD-07 surfaces ERROR:11 as ERROR-level log and halts tracking.]

**Symptom:** Log line `firmware_error code=11`. Motor stops. App requires restart.

**Fixes:**
- USB cable seated firmly. Cheap micro-USB cables under high load can drop packets.
- Close apps that hog the CPU (renderers, browsers with heavy tabs). Heartbeat is a 200 ms async task — sustained event-loop starvation > 800 ms triggers ERROR:11.
- If the firmware watchdog is still asserting after restart, power-cycle the Uno (unplug USB for 5 seconds).

### 4. "Lock-loss to subject (lock-loss > 2 s)"

[VERIFIED: PERC-05 — lock-loss > 2.0 s triggers re-acquire via central-frame heuristic, logs WARN.]

**Symptom:** Motor stops following the speaker; log line `subject_lock_lost` then `subject_lock_acquired` (re-acquire on a new ID).

**Fixes:**
- Lighting: BoT-SORT relies on appearance; very dim or strongly backlit subjects lose ID. Raise stage lights or reduce backlight.
- Detection confidence threshold: `detection_confidence_min` defaults to 0.55. If the speaker wears low-contrast clothing, lower to 0.45 in `config.json`.
- Audience or interpreter walking through frame: the central-60% heuristic on re-acquire keeps the camera on the stage center. Brief lock-loss to interpreter is expected and self-corrects within ~2 s.

## Ship-Gate Commands

Run from `pastor_tracker/` directory (cwd contains `pyproject.toml`).

| Gate | Command | Expected | Notes |
|------|---------|----------|-------|
| QA-01 (ruff) | `uv run ruff check src tests` | Exit 0, "All checks passed!" | Already clean as of 2026-05-11 |
| QA-01 (format) | `uv run ruff format --check src tests` | Exit 0 | Optional but recommended — confirms no format drift |
| QA-02 (mypy) | `uv run mypy --strict src tests` | Exit 0, "Success: no issues found in N source files" | Already clean (32 source files) |
| Tests | `uv run pytest tests` | Exit 0, 561 passed, 1 skipped | Phase 7 confirmed |
| QA-03 (commits) | `git log --oneline -30` then sample for Conventional Commits prefix | Every commit starts with `feat(NN-NN):` / `fix(NN):` / `docs(NN):` / `test(NN-NN):` / `chore(NN):` / `refactor(NN):` | [VERIFIED: sampled 25 commits — all clean] |

**If any gate regresses during README write** (only realistic risk is pre-commit hook on the README commit), fix in place before committing. The pre-commit config covers ruff + mypy + commit-message format only — README markdown is not linted.

[VERIFIED: commit `8aea0d8 docs(07): record phase verification` — `docs(07):` scope is the established pattern for documentation commits. Phase 8 README commit should use `docs(08):`.]

## QA-04 On-Stage Smoke Procedure

**Deferred to a human operator.** Phase 8 records the procedure but does not execute it. Phase 8 closes as `human_needed`, matching Phase 7 P02 SUMMARY (`8aea0d8 docs(07): record phase verification (human_needed)`).

**Procedure (write into BOTH README "First run on stage" AND VERIFICATION.md `human_verification`):**

### Pre-flight (≤ 5 min)

1. Stage camera USB-plugged into the host machine.
2. OBS Studio launched, Stage scene configured with the stage camera as Source, **Start Virtual Camera** clicked.
3. Uno (R3/R4/CH340/FTDI) USB-plugged, firmware `READY:v2` already burned. Device shows in Device Manager.
4. `config.json` has `camera_horizontal_fov_deg` set per the Calibration procedure (Section 4).
5. Speaker on stage at typical position.

### Run (5 min minimum)

6. From `pastor_tracker/`: `uv run pastor-tracker` (default UI).
7. Confirm preview window opens, skeleton overlay appears on the speaker, framing-target vertical line is drawn.
8. Press `S` to start tracking. Motor should begin tracking after a < 1 s damping ramp-up.
9. Speaker walks slowly stage-left, dwells 3 s, walks stage-right, dwells 3 s, repeats for ≥ 5 minutes.

### Pass criteria (operator checks all six)

- [ ] **Zero overshoot** — motor never overshoots the framing target and recoils.
- [ ] **Zero audible motor jerk** — pan is silent or smooth-hum; no clack, no chatter.
- [ ] **Zero lock-loss to audience or interpreter** — camera does not jump to a non-primary subject for > 2 s.
- [ ] **Rule-of-thirds framing holds** — moving-right subject sits in left third; moving-left subject sits in right third; dwelling subject sits at center. (INTENT-03)
- [ ] **E-stop works** — pressing `E` halts motor within one tick (< 50 ms perceived).
- [ ] **5+ minutes of continuous tracking** — no crashes, no `ERROR:` lines in the log.

### Fail handling

Any unchecked box = re-run with the appropriate config tweak (deadband up if jitter, pan_time_constant up if overshoot, FOV recalibration if mistracking), then re-run the full 5 min. Log the failure mode + remediation in VERIFICATION.md before the next attempt.

### Verification record

`VERIFICATION.md` `verification_result: human_needed` with `human_verification:` block listing all six checkboxes unchecked, plus a note: *"Smoke test deferred — schedule on next stage rehearsal with real speaker, real Uno, real OBS VCam. Updates committed under `docs(08): record on-stage smoke result`."*

## Runtime State Inventory

> Phase 8 is documentation-only. No renames, no refactors, no migration. **Skipped** — no runtime state changes.

## Common Pitfalls

### Pitfall 1: Field-name drift between CONTEXT.md and config.py

CONTEXT.md uses `camera_h_fov_deg` (short form, conversational). The **actual field is `camera_horizontal_fov_deg`** (config.py line 134). README MUST use the real field name — operators copy-paste from README into `config.json`, and Pydantic `extra="forbid"` will reject unknown keys at startup with `EXIT_INVALID_CONFIG` (64).

**Prevention:** Plan task should include an explicit verification step: grep README for every backtick-quoted config key and confirm it exists in `config.py`.

### Pitfall 2: CLI flag name drift

CONTEXT.md mentions `--config path.json`. The **actual flag is `--config-json` (hyphenated, with `-json` suffix)** per `__main__.py` line 227. README MUST use the real flag.

**Prevention:** Same verification step — grep README for `--` flags and confirm each appears in `__main__.py` argparse block.

### Pitfall 3: Conventional Commits scope drift

Phase 8 has one wave / one plan / two tasks. Commit scopes from Phase 7 used `feat(07-04):` (phase-plan) and `docs(07):` (phase only). Use **`docs(08):` for the README commit** and **`docs(08):` for the VERIFICATION commit**, matching the established pattern. Do NOT introduce a new scope shape.

### Pitfall 4: Pre-commit hook on README commit

The repo's pre-commit hook runs ruff + mypy on changes to `pastor_tracker/`. A README-only commit touches `pastor_tracker/README.md` — pre-commit does NOT lint markdown but DOES run the suite of Python hooks on any touched py files in the staging set. If the operator accidentally stages a stray `.py` change with the README, the hook may fire.

**Prevention:** `git add pastor_tracker/README.md` explicitly (no `git add .`, no `git add -A`).

### Pitfall 5: `uv run pastor-tracker` cwd assumption

The entry point `pastor-tracker` resolves to `pastor_tracker.__main__:main` via the package's console_scripts. Pydantic settings reads `config.json` **from CWD** (config.py line 35: `CONFIG_JSON_PATH = Path("config.json")`). README MUST tell the operator to `cd pastor_tracker` before `uv run pastor-tracker`, or to use `--config-json /full/path/to/config.json`.

**Prevention:** Every Run section command starts with `cd pastor_tracker` (or assumes that cwd from the previous step). Already in current stub at line 10.

## Environment Availability

> Phase 8 is documentation-only — no new external dependencies beyond the already-installed stack.

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| `uv` | All commands | ✓ (in `pastor_tracker/.venv` per STATE.md line 86) | — | — |
| `ruff` | QA-01 | ✓ (dev dep, `ruff>=0.15,<0.16` per pyproject.toml line 22) | — | — |
| `mypy` | QA-02 | ✓ (dev dep, `mypy>=1.20,<2.0`) | — | — |
| `pytest` | Test re-verify | ✓ (dev dep, `pytest>=8.4,<9.0`) | — | — |
| `git` | QA-03 commit log review | ✓ (repo is git-controlled) | — | — |
| OBS Studio | QA-04 only (deferred) | n/a — human-verified | — | — |
| Uno hardware | QA-04 only (deferred) | n/a — human-verified | — | — |

**No blocking dependencies.** All gate commands runnable on dev machine immediately.

## Validation Architecture

> nyquist_validation defaults to enabled. Phase 8 has a narrow validation surface — most "truths" are doc-claims-match-reality rather than runtime invariants.

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 8.4 + pytest-asyncio 0.26 + hypothesis 6.152 |
| Config file | `pastor_tracker/pyproject.toml` `[tool.pytest.ini_options]` (lines 97-108) |
| Quick run command | `cd pastor_tracker && uv run pytest tests -x` |
| Full suite command | `cd pastor_tracker && uv run pytest tests` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|--------------|
| DOC-01 | README contains required sections in order | **doc-grep** (no automated test) | `grep -nE '^## (Overview\|Requirements\|Install\|OBS Virtual Camera setup\|FOV calibration\|Run\|Hotkeys\|Tuning sliders\|First run on stage\|Troubleshooting)' pastor_tracker/README.md` returns 10 lines | n/a — manual grep in plan-task verification step |
| DOC-01 | README config field names match config.py | **doc-grep** | For each backtick-quoted key in README, `grep -F "<key>" pastor_tracker/src/pastor_tracker/config.py` returns ≥ 1 line | n/a — manual grep |
| DOC-01 | README CLI flags match `__main__.py` argparse | **doc-grep** | For each `--flag` in README, grep `__main__.py` for `add_argument("<flag>"` returns ≥ 1 line | n/a — manual grep |
| QA-01 | ruff clean | **automated** | `cd pastor_tracker && uv run ruff check src tests` exit 0 | ✅ pre-existing infrastructure |
| QA-02 | mypy --strict clean | **automated** | `cd pastor_tracker && uv run mypy --strict src tests` exit 0 | ✅ pre-existing infrastructure |
| QA-02 | ruff format check | **automated** (optional) | `cd pastor_tracker && uv run ruff format --check src tests` exit 0 | ✅ pre-existing infrastructure |
| QA-03 | Conventional Commits per Order of Work | **automated commit-msg hook** + manual sample | `git log --oneline -30` and visually confirm each line starts with `type(scope):` | ✅ pre-commit `commit-msg` hook installed |
| Tests | Suite still green | **automated** | `cd pastor_tracker && uv run pytest tests` exit 0, 561 passed | ✅ pre-existing |
| QA-04 | On-stage smoke ≥ 5 min, six pass criteria | **human-only** | n/a — physical stage with speaker required | n/a — deferred to human operator |

### Sampling Rate

- **Per task commit:** `uv run ruff check src tests && uv run mypy --strict src tests` (≤ 30 s combined).
- **Per wave merge:** Full suite `uv run pytest tests` (≤ 90 s on dev box).
- **Phase gate:** Full suite green + ruff + mypy + Conventional Commits sample before `/gsd-verify-work` accepts.

### Wave 0 Gaps

None. Phase 8 introduces no new tests. The doc-grep validations are simple shell commands invoked by the plan-task `verify` step, not new test files.

*(If the planner decides to add a `test_readme_consistency.py` that programmatically grep-checks the README against `config.py` and `__main__.py`, that is a defensible decision — but CONTEXT.md says "No new test files," so this is OUT OF SCOPE.)*

## Security Domain

> `security_enforcement` not explicitly set to false. Apply ASVS check; surface narrow.

### Applicable ASVS Categories for a Documentation Phase

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | n/a — no auth surface in v1 (local desktop app) |
| V3 Session Management | no | n/a |
| V4 Access Control | no | n/a |
| V5 Input Validation | yes (narrow) | README must NOT instruct operators to disable Pydantic `extra="forbid"` or weaken validators. Document `config.json` schema as authoritative; warn that unknown keys = boot abort. |
| V6 Cryptography | yes (narrow) | YOLO weights SHA256 — config.py line 162-167 already references "operator MUST verify SHA256 of weights file before deployment (see README — pinned hash for yolo11n-pose.pt)." **README must include the pinned SHA256.** |

### Action items the README MUST address

1. **YOLO weights SHA256.** Config.py contains a doc-string instruction (line 165): *"operator MUST verify SHA256 of weights file before deployment (see README — pinned hash for yolo11n-pose.pt)."* This is a forward-reference that the README must fulfill. The planner should add an "Install → YOLO weights verification" sub-step that publishes the SHA256 of `yolo11n-pose.pt` and shows the operator how to verify it via `Get-FileHash` (PowerShell) or `sha256sum` (bash).

   [ASSUMED] The pinned SHA256 is not in the codebase yet. Plan-task should either (a) fetch and pin the SHA256 of the current ultralytics-hosted `yolo11n-pose.pt`, or (b) document the verification *procedure* with a TODO marker linking to a v2 follow-up. Decision is the planner's; flag for user confirmation if uncertain.

2. **Config schema authority.** README explicitly states that all 27 fields are validated at boot and unknown fields cause `EXIT_INVALID_CONFIG` (64). Operators must not be told to "add custom fields" — that breaks `extra="forbid"`.

### Known Threat Patterns for this Stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Operator runs unverified YOLO weights (supply-chain) | Tampering | SHA256 verification step in README install path |
| Operator pastes a malformed `config.json` from a guide | Denial of Service (boot abort) | `extra="forbid"` already mitigates; README warns explicitly |
| Operator binds to wrong COM port (e.g. another USB device's COM6) | Spoofing of hardware | VID:PID auto-detect already mitigates; README documents the four supported VID:PIDs and discourages `arduino_port` override |

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | OBS 28+ on Win 10/11 does not need admin rights for Start Virtual Camera | OBS setup | LOW — README adds a one-line fallback ("if greyed out, run as admin"); no further impact |
| A2 | The pinned SHA256 of `yolo11n-pose.pt` is NOT yet recorded in the repo and must be either captured during Phase 8 or marked as a v2 follow-up | Security Domain | MEDIUM — if SHA256 needs to be authoritatively pinned and we punt, the V6 control is incomplete. Recommend planner asks user. |
| A3 | The QA-04 smoke procedure detailed above is what the human operator will follow exactly — no field-tested deviations yet | QA-04 procedure | LOW — procedure can be refined after first real-stage run; v1 ships with the documented procedure as guidance |
| A4 | The 1-meter-bar FOV calibration formula is sufficient for non-fisheye lenses typical of USB stage cameras | FOV calibration | LOW — formula is exact for rectilinear lenses; fisheye/ultrawide would need lens-distortion correction, but operator cameras at this project's scope are rectilinear |

**Action for planner / discuss-phase:** A2 (YOLO SHA256) is the only assumption with meaningful product impact. Recommend asking the user before plan execution: "Should Phase 8 capture and pin the current `yolo11n-pose.pt` SHA256, or defer this to a v2 supply-chain hardening pass?"

## Open Questions

1. **YOLO weights SHA256 — pin now or defer?** [Assumption A2]
   - What we know: config.py docstring forward-references a README-pinned SHA256.
   - What's unclear: whether to capture the value now (one `Get-FileHash` call) or defer to v2.
   - Recommendation: Capture in Phase 8 — it is a 30-second task and closes the V6 ASVS control cleanly. If deferred, mark with explicit `TODO(v2-supply-chain)` in README so the trace is visible.

2. **One commit or two for Phase 8?** CONTEXT.md says "one or two atomic commits (README + verification record)."
   - Recommendation: **Two commits.** Commit 1: `docs(08): expand README into operator manual` (the README write). Commit 2: `docs(08): record phase verification (human_needed - on-stage smoke deferred to next rehearsal)` (the VERIFICATION.md write). This mirrors the established Phase 7 pattern (commits `d55c9d9` + `8aea0d8`) and keeps the doc-write reviewable in isolation.

3. **Optional ASCII pipeline diagram?** (Claude's Discretion per CONTEXT.md)
   - Recommendation: **Yes, one small block.** A 5-line ASCII showing `OBS VCam → Detector → Tracker → Framer → PanController → ArduinoMotor` helps a new operator form a mental model in 3 seconds. Cheap to write, high readability ROI. Already in CLAUDE.md — copy and trim.

## Project Constraints (from CLAUDE.md)

Phase 8 produces no new code, but the README documents code that follows these constraints. Restate them so the planner can verify the README is consistent:

- **No `print()`** — README must not show `print()` examples; if examples are needed, use `log.info(...)` (structlog).
- **No magic numbers** — README references config field names (e.g. `camera_horizontal_fov_deg`), not literal numbers. Example values are allowed but must be labelled (e.g. "default 70.0").
- **Conventional Commits** — Phase 8 commits use `docs(08): <subject>` per Phase 7 pattern.
- **Frozen dataclasses / Pydantic** — README should mention that `config.json` is read **once at boot** and live edits require Save Config to commit + restart. (Already covered in Tuning Sliders section.)
- **VID:PID auto-detect mandatory** — README documents auto-detect first; `arduino_port` override is documented as a fallback only.

## Sources

### Primary (HIGH confidence)

- `pastor_tracker/src/pastor_tracker/config.py` (lines 89-266) — verified all 27 field names including the corrected `camera_horizontal_fov_deg`
- `pastor_tracker/src/pastor_tracker/__main__.py` (lines 225-245) — verified CLI flag names: `--config-json`, `--ui`, `--headless`
- `pastor_tracker/pyproject.toml` — verified dependency stack, entry point, ruff/mypy/pytest config
- `pastor_tracker/README.md` — current 11-line stub, baseline for expansion
- `.planning/REQUIREMENTS.md` — verified requirement IDs DOC-01, QA-01..04 acceptance criteria
- `.planning/phases/08-end-to-end-and-ship-gates/08-CONTEXT.md` — locked decisions
- `git log --oneline -25` — verified Conventional Commits compliance through HEAD
- `CLAUDE.md` — engineering constraints

### Secondary (MEDIUM confidence)

- OBS Studio Virtual Camera Knowledge Base (obsproject.com/kb/virtual-camera-guide) — Win 10/11 setup steps
- GitHub Docs "About READMEs" — section ordering best practice for desktop apps

### Tertiary (LOW confidence)

- None. Phase 8 surface is small enough to verify entirely against primary sources.

## Metadata

**Confidence breakdown:**

- README structure: HIGH — CONTEXT.md locks it explicitly.
- OBS VCam steps: HIGH — verified against OBS docs and `io/obs_camera.py` enumeration.
- FOV calibration formula: HIGH — standard rectilinear-lens trigonometry.
- Hotkey table: HIGH — verified against commit `16887d9`.
- Ship-gate commands: HIGH — pre-existing tooling, already exercised through Phase 7.
- QA-04 procedure: MEDIUM — procedure is canonical but unverified on real stage; A3 flagged.
- YOLO SHA256 path: MEDIUM — Q1 open.

**Research date:** 2026-05-11
**Valid until:** 2026-06-10 (30 days — stable surface, only OBS version drift could invalidate)

## RESEARCH COMPLETE
