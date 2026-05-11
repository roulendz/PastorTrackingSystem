# Phase 8: End-to-End and Ship Gates - Context

**Gathered:** 2026-05-11
**Status:** Ready for planning

<domain>
## Phase Boundary

Phase 8 closes the v1 milestone with three deliverables:

1. **DOC-01** — `pastor_tracker/README.md` expanded from the current 11-line stub into a complete operator manual: install (`uv sync`), OBS Virtual Camera setup, FOV calibration procedure, run command + flags (`--ui/--headless`), hotkey table, and a troubleshooting section.
2. **Ship-gate verification** — confirm `ruff check` and `mypy --strict` exit zero across `src/` and `tests/`, confirm the test suite still passes, and confirm git history follows Conventional Commits per the PROMPT.md Order of Work.
3. **QA-04 (on-stage smoke)** — deferred to a human-operated test on real hardware (auto-detected Uno + real OBS VCam + real speaker, ≥ 5 minutes of cinematic pan with no overshoot/oscillation/lock-loss/audible jerk). Phase 8 records the smoke procedure but does not execute it.

No new feature code. No new modules. README writing + verification commands + a deferred-smoke checklist only.

</domain>

<decisions>
## Implementation Decisions

### README Scope (DOC-01)
- Full operator README — not a minimal stub.
- Required sections, in order:
  1. **Overview** — one paragraph: what the system does and the cinematic constraint.
  2. **Requirements** — Windows 10/11, Python 3.12, `uv`, OBS Studio with Virtual Camera plugin, Arduino Uno R3/R4 (or CH340/FTDI clone) flashed with the firmware in `arduino/stepper_controller/`.
  3. **Install** — `git clone`, `cd pastor_tracker`, `uv sync`.
  4. **OBS Virtual Camera setup** — start OBS, add stage camera as scene, click "Start Virtual Camera". One screenshot allowed but optional (skip if cost > value).
  5. **FOV calibration** — measure horizontal field of view at the lens distance, set `camera_horizontal_fov_deg` in `config.json`; reference the existing Config field.
  6. **Run** — `uv run pastor-tracker` (default UI), `--headless` flag, `--config-json path.json` flag.
  7. **Hotkey table** — `S` start, `P` pause, `H` home, `E` e-stop (debounced), `Q` quit (matches UI-05).
  8. **Tuning sliders** — list the 4 live sliders (pan time-constant, deadband, max velocity, FOV) and the Save Config restart behavior.
  9. **Troubleshooting** — "OBS VCam not found", "Arduino not detected" (VID:PID list), "Heartbeat lost (ERROR:11)", "Lock-loss > 2 s".

### QA-04 On-Stage Smoke
- Deferred to human verification — autonomous mode cannot drive a real stage.
- Phase 8 writes the smoke procedure into both the README ("First run on stage" subsection) and the phase VERIFICATION.md `human_verification` section.
- Acceptance criteria for the human run: ≥ 5 minutes continuous tracking, zero overshoot, zero audible motor jerk, zero lock-loss to audience/interpreter, FOV calibration verified, e-stop hotkey verified.
- Phase 8 verification result will be `human_needed` (matching Phase 7's pattern) — Phase 8 commits cleanly, the on-stage smoke is logged as outstanding human work in STATE.md.

### Plan Structure
- Single combined plan: `08-01-PLAN.md` covering README write + ship-gate re-verify + VERIFICATION recording.
- One or two atomic commits (README + verification record).
- No new test files. No new source files.

### Ship Gates (QA-01 / QA-02 / QA-03)
- QA-01 (ruff): already clean as of 2026-05-11 — re-run during plan execution to confirm.
- QA-02 (mypy --strict): already clean (32 source files, no issues) — re-run.
- QA-03 (Conventional Commits): git log review confirms one logical change per commit through Phase 7; no rewrite needed.
- If any gate regresses during the README work, fail loud and fix before committing.

### Claude's Discretion
- README tone, formatting, and exact wording.
- Exact ordering of troubleshooting entries.
- Whether to add a small ASCII diagram of the pipeline (only if it adds clarity).

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `pastor_tracker/README.md` — current 11-line stub at repo root; will be expanded in place.
- `src/pastor_tracker/config.py` — frozen Pydantic `Config` with all 23 fields including `camera_h_fov_deg`, `arduino_port`, `preview_width_px`, etc. README references field names directly.
- `pyproject.toml` — declares the `pastor-tracker` entry point. README run section uses `uv run pastor-tracker`.
- `arduino/stepper_controller/` — shipped firmware; README links to its location but does not document flashing (out of scope for v1 operator README).
- Phase 7 SUMMARY artifacts — hotkey behavior (E debounced, S/P/H/Q on release) is locked; README must match.

### Established Patterns
- Conventional Commits with scope `(<phase>-<plan>)` or `(<phase>)`.
- All commits gated by `.pre-commit-config.yaml` (ruff + mypy hooks).
- `pastor_tracker/` is the python project; everything in `pastor_tracker/.venv/`. README run examples assume cwd = `pastor_tracker/`.

### Integration Points
- README is the only user-facing artifact — no UI strings change.
- VERIFICATION.md for Phase 8 records human_needed status and the smoke procedure.
- STATE.md update at phase close: mark Phase 8 complete-pending-smoke, milestone v1.0 transitioning to lifecycle.

</code_context>

<specifics>
## Specific Ideas

- README must let a fresh operator bring the system up without reading any other file. That is the literal Phase 8 success criterion.
- The hotkey table must reflect the Phase 7 debounce behavior (E debounced, others on release).
- Reference Phase 7 Save Config restart semantics in the Tuning sliders section — unsaved changes survive Cancel, modal blocks Quit.

</specifics>

<deferred>
## Deferred Ideas

- Screenshots of OBS VCam setup and dashboard — optional, only if cheap.
- Arduino firmware flashing documentation — belongs in `arduino/README.md`, out of scope for v1 operator README.
- Auto FOV calibration (CAL-01) — v2 requirement.
- Service-mode / autostart documentation — v2.

</deferred>
