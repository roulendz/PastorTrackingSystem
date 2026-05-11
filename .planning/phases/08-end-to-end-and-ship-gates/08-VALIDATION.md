---
phase: 8
slug: end-to-end-and-ship-gates
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-05-11
---

# Phase 8 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.4 + pytest-asyncio 0.26 + hypothesis 6.152 |
| **Config file** | `pastor_tracker/pyproject.toml` `[tool.pytest.ini_options]` |
| **Quick run command** | `cd pastor_tracker && uv run pytest tests -x` |
| **Full suite command** | `cd pastor_tracker && uv run pytest tests` |
| **Estimated runtime** | ~55 seconds full suite (Phase 7 confirmed 51s on dev box) |

---

## Sampling Rate

- **After every task commit:** `cd pastor_tracker && uv run ruff check src tests && uv run mypy --strict src tests`
- **After every plan wave:** `cd pastor_tracker && uv run pytest tests`
- **Before `/gsd-verify-work`:** Full suite + ruff + mypy + Conventional Commits sample all green
- **Max feedback latency:** 90 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 08-01-01 | 01 | 1 | DOC-01 | — | README field names + flags match source-of-truth (no Pydantic boot abort from stale doc) | doc-grep | `grep -nE '^## (Overview\|Requirements\|Install\|OBS Virtual Camera setup\|FOV calibration\|Run\|Hotkeys\|Tuning sliders\|First run on stage\|Troubleshooting)' pastor_tracker/README.md` returns 10 lines | n/a — shell grep | ⬜ pending |
| 08-01-01 | 01 | 1 | DOC-01 | — | Every backtick-quoted config key in README exists in `config.py` | doc-grep | for each `\``key`\`` in README, `grep -F "<key>" pastor_tracker/src/pastor_tracker/config.py` ≥ 1 match | n/a — shell grep | ⬜ pending |
| 08-01-01 | 01 | 1 | DOC-01 | — | Every `--flag` in README exists in `__main__.py` argparse | doc-grep | for each `--flag` in README, `grep -F 'add_argument("<flag>"' pastor_tracker/src/pastor_tracker/__main__.py` ≥ 1 match | n/a — shell grep | ⬜ pending |
| 08-01-02 | 01 | 1 | QA-01 | — | ruff zero | automated | `cd pastor_tracker && uv run ruff check src tests` exit 0 | ✅ pre-existing | ⬜ pending |
| 08-01-02 | 01 | 1 | QA-02 | — | mypy --strict zero | automated | `cd pastor_tracker && uv run mypy --strict src tests` exit 0 | ✅ pre-existing | ⬜ pending |
| 08-01-02 | 01 | 1 | — | — | Test suite green | automated | `cd pastor_tracker && uv run pytest tests` exit 0 | ✅ pre-existing | ⬜ pending |
| 08-01-02 | 01 | 1 | QA-03 | — | Conventional Commits per Order of Work | automated commit-msg hook + manual sample | `git log --oneline -30` every line starts `type(scope):` | ✅ pre-commit hook | ⬜ pending |
| 08-01-03 | 01 | 1 | QA-04 | — | On-stage smoke ≥ 5 min, six pass criteria | human-only | n/a — physical stage required | n/a — deferred | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

*No new test files. Phase 8 introduces no test infrastructure. Existing pytest + ruff + mypy stack covers all automated validations. Doc-grep validations are inline shell commands in the plan task's verify step, not new test modules.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| On-stage 5-minute smoke run | QA-04 | Requires real Uno, real OBS VCam, real speaker on a real stage | See README "First run on stage" section + 08-VERIFICATION.md `human_verification` block. Six checkboxes: zero overshoot, zero motor jerk, zero lock-loss, rule-of-thirds holds, E-stop works, ≥ 5 min continuous. |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies (doc-grep = automated shell, QA-04 = manual-only by design)
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify (08-01-01 doc-grep, 08-01-02 lint/type/test, 08-01-03 records human deferral — chain unbroken)
- [ ] Wave 0 covers all MISSING references (none — no MISSING refs)
- [ ] No watch-mode flags
- [ ] Feedback latency < 90s
- [ ] `nyquist_compliant: true` set in frontmatter (flip after planner approves task map)

**Approval:** pending
