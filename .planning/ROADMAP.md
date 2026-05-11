# Roadmap: Pastor Tracking System (PTS)

## Milestones

- ✅ **v1.0 — Pastor Tracker MVP** — Phases 1-8 (shipped 2026-05-11). See [MILESTONES.md](MILESTONES.md) and [milestones/v1.0-ROADMAP.md](milestones/v1.0-ROADMAP.md).

## Phases

<details>
<summary>✅ v1.0 — Pastor Tracker MVP (Phases 1-8) — SHIPPED 2026-05-11</summary>

- [x] Phase 1: Scaffold, Config, Core Math (3/3 plans) — completed 2026-05-03
- [x] Phase 2: Arduino I/O (3/3 plans) — completed 2026-05-04
- [x] Phase 3: Camera I/O (2/2 plans) — completed 2026-05-05
- [x] Phase 4: Perception (3/3 plans) — completed 2026-05-06
- [x] Phase 5: Intent and Control (6/6 plans) — completed 2026-05-06
- [x] Phase 6: Pipeline Orchestrator (4/4 plans) — completed 2026-05-10
- [x] Phase 7: UI Dashboard (4/4 plans) — completed 2026-05-08
- [x] Phase 8: End-to-End and Ship Gates (1/1 plan) — completed 2026-05-11

Full details: [.planning/milestones/v1.0-ROADMAP.md](milestones/v1.0-ROADMAP.md)

</details>

### 📋 v1.1 (Planned — pending v1.0 follow-ups)

No phases planned yet. Known v1.0 tech debt that may seed v1.1 scope:

- **QA-04 on-stage smoke** — schedule on next stage rehearsal (real Uno + OBS VCam + speaker); tick the 6 items in `.planning/phases/08-end-to-end-and-ship-gates/08-HUMAN-UAT.md`, re-run `/gsd-verify-work 8`.
- **QA-02 mypy env regression** — pin `mypy = "1.19.x"` (or wait for upstream fix) so `mypy --strict src tests` exits 0.
- **QA-01 ruff format drift** — `uv run ruff format src tests` cleanup + add `ruff format --check` to `.pre-commit-config.yaml`.
- **YOLO weights SHA256 supply-chain hardening** — pin `yolo11n-pose.pt` SHA256 in README once the weights file ships in repo.
- **`test_pipeline.py:439` re-arm skip** — implement motor.start() second-call support so e_stopped → start re-arm is fully tested.

Start the next milestone with `/gsd-new-milestone`.

## Progress

| Phase | Milestone | Plans Complete | Status   | Completed  |
|-------|-----------|----------------|----------|------------|
| 1. Scaffold, Config, Core Math | v1.0 | 3/3 | Complete | 2026-05-03 |
| 2. Arduino I/O | v1.0 | 3/3 | Complete | 2026-05-04 |
| 3. Camera I/O | v1.0 | 2/2 | Complete | 2026-05-05 |
| 4. Perception | v1.0 | 3/3 | Complete | 2026-05-06 |
| 5. Intent and Control | v1.0 | 6/6 | Complete | 2026-05-06 |
| 6. Pipeline Orchestrator | v1.0 | 4/4 | Complete | 2026-05-10 |
| 7. UI Dashboard | v1.0 | 4/4 | Complete | 2026-05-08 |
| 8. End-to-End and Ship Gates | v1.0 | 1/1 | Complete | 2026-05-11 |

---
*Roadmap reorganized: 2026-05-11 after v1.0 milestone close*
