---
phase: 3
slug: camera-i-o
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-05-05
---

# Phase 3 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution. Source: `03-RESEARCH.md` § Validation Architecture.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.4 + pytest-asyncio 1.3.0 (already installed in `pastor_tracker/.venv` since Phase 1) |
| **Config file** | `pastor_tracker/pyproject.toml` `[tool.pytest.ini_options]` (`asyncio_mode="auto"`, `filterwarnings=error`) |
| **Quick run command** | `pastor_tracker\.venv\Scripts\pytest.exe tests/test_obs_camera.py -x` |
| **Full suite command** | `pastor_tracker\.venv\Scripts\pytest.exe -ra` |
| **Coverage gate command** | `pastor_tracker\.venv\Scripts\coverage.exe report --include="src/pastor_tracker/io/obs_camera.py"` ≥ 90 % line; 100 % branch on the discovery / match / not-found paths |
| **Estimated runtime** | ~6 s for Phase 3 tests on top of the existing Phase 1+2 baseline |

---

## Sampling Rate

- **After every task commit:** Run `pastor_tracker\.venv\Scripts\pytest.exe tests/test_obs_camera.py -x`
- **After every plan wave:** Run `pastor_tracker\.venv\Scripts\pytest.exe -ra`
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** 10 seconds (test suite ≤ 10 s end-to-end)

---

## Per-Task Verification Map

> Filled by gsd-planner once PLAN.md tasks exist. Skeleton row provided so the planner sees the expected shape.

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 03-01-01 | 01 | 1 | IO-CAM-01 | — | OBS VCam enumerated; exact-match resolution | unit | `pytest tests/test_obs_camera.py::test_enumerate_match -x` | ❌ W0 | ⬜ pending |
| 03-01-02 | 01 | 1 | IO-CAM-02 | — | Hard-fail with device list when OBS missing | unit | `pytest tests/test_obs_camera.py::test_obs_camera_not_found -x` | ❌ W0 | ⬜ pending |
| 03-02-01 | 02 | 2 | IO-CAM-03 | — | 1080p target → fallback 720p inside warmup window | unit | `pytest tests/test_obs_camera.py::test_resolution_fallback_warmup -x` | ❌ W0 | ⬜ pending |
| 03-02-02 | 02 | 2 | IO-CAM-04 | — | perf_counter_ns timestamp on every frame; >100 ms drops | unit | `pytest tests/test_obs_camera.py::test_stale_frame_drop -x` | ❌ W0 | ⬜ pending |
| 03-02-03 | 02 | 2 | IO-CAM-04 | — | 200 ms stall detected; up to 3 reopen attempts then `CameraStallError` | unit | `pytest tests/test_obs_camera.py::test_stall_reopen_giveup -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `pastor_tracker/tests/test_obs_camera.py` — file does not yet exist; created in Wave 0 with skeleton tests for IO-CAM-01..04 (all marked `pytest.mark.skip("Wave 0")` if implementation absent)
- [ ] `pastor_tracker/tests/fakes/fake_video_source.py` — in-tree zero-deps `FakeVideoSource` implementing the new `VideoSource` Protocol (canned BGR ndarray sequences, scripted stalls / errors / index-mapping)
- [ ] Verify `opencv-python>=4.10,<5.0` and `pygrabber==0.2` already installed via `uv pip list`; if not, add to `pyproject.toml` and `uv sync` in Wave 0
- [ ] Confirm `pytest-asyncio` `asyncio_mode="auto"` covers the new test file (no per-file marker needed)

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Real OBS Virtual Camera open + first frame within 3 s | IO-CAM-01 / IO-CAM-02 | Requires OBS Studio running with Virtual Camera plugin enabled — out of scope for CI; deferred to Phase 8 QA-04 stage smoke | `python -m pastor_tracker --camera-only` against a live OBS install; verify INFO log `OBS Virtual Camera opened idx=N` and first `Frame` within 3 s |
| Resolution-fallback under genuine driver pressure | IO-CAM-03 | The p95 trigger only meaningfully fires when the host OBS / driver is overloaded; CI fakes time but cannot simulate driver back-pressure faithfully | Run with CPU-pinned OBS at 1080p60; observe WARN `resolution_fallback from=1920x1080 to=1280x720`; confirm post-fallback p95 within budget |
| pygrabber-index ↔ cv2-index correspondence | IO-CAM-01 | RESEARCH.md Pitfall 1 calls out that `FilterGraph().get_input_devices()` and `cv2.VideoCapture(idx, CAP_DSHOW)` enumeration order *should* match because both use `ICreateDevEnum`, but this needs verification on the actual dev box | Wave 0 ad-hoc script: enumerate with pygrabber, open each idx with cv2, log device name from each; confirm names align |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references (`test_obs_camera.py`, `fake_video_source.py`, dep-install verification)
- [ ] No watch-mode flags
- [ ] Feedback latency < 10 s
- [ ] `nyquist_compliant: true` set in frontmatter once planner fills the per-task map and Wave 0 lands

**Approval:** pending
