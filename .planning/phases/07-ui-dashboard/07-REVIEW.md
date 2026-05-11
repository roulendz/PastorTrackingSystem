---
phase: 07-ui-dashboard
reviewed: 2026-05-08T00:00:00Z
depth: standard
files_reviewed: 12
files_reviewed_list:
  - pastor_tracker/src/pastor_tracker/ui/__init__.py
  - pastor_tracker/src/pastor_tracker/ui/dashboard.py
  - pastor_tracker/src/pastor_tracker/ui/_overlays.py
  - pastor_tracker/src/pastor_tracker/ui/_pipeline_thread.py
  - pastor_tracker/src/pastor_tracker/ui/_event_bus.py
  - pastor_tracker/src/pastor_tracker/ui/_status_panel.py
  - pastor_tracker/src/pastor_tracker/logging_config.py
  - pastor_tracker/src/pastor_tracker/__main__.py
  - pastor_tracker/src/pastor_tracker/core/types.py
  - pastor_tracker/src/pastor_tracker/config.py
  - pastor_tracker/src/pastor_tracker/pipeline.py
  - pastor_tracker/tests/test_dashboard.py (spot)
findings:
  critical: 2
  warning: 6
  info: 4
  total: 12
status: clean
fixed_at: 2026-05-08T00:00:00Z
fixed_scope: critical_warning
---

# Phase 7: UI Dashboard - Code Review Report

**Reviewed:** 2026-05-08
**Depth:** standard
**Files Reviewed:** 12
**Status:** issues_found

## Summary

Phase 7 ships a coherent DearPyGui dashboard wired against the Phase 6 Pipeline public surface. Pure-core / dirty-edges holds (`_overlays.py` has zero DPG imports; `_event_bus.py` has zero asyncio imports). No `print()`, no bare `except:`, no mocked Kalman/damping. EventBusProcessor is correctly inserted BEFORE `JSONRenderer` per RESEARCH §5, contradicting CONTEXT D-16 wording — that correction is justified and documented.

Two BLOCKERS were found, both in the Save Config restart sequence:
1. The new pipeline's `start()` is fire-and-forget (no `.result()`), so hardware-failure on restart silently leaves the dashboard with a non-running pipeline + cleared buffer + cleared error banner — opposite of the operator's expectation.
2. The restart has no rollback: if `_pipeline_factory(new_config)` or the new `host.start()` raises, the dashboard is permanently wedged with `self._config` already swapped, `old_host` already stopped, and `self._pipeline`/`self._host` pointing at dead or partially-constructed objects.

Six warnings cover the unsaved-changes modal lifecycle (leaks DPG widgets on every Cancel/X cycle), 11 `assert` statements that violate CLAUDE.md §1 tiger-style under `python -O`, and an inconsistency between the start-time and restart-time pipeline boot. Four INFO items cover minor robustness and resource-lifecycle concerns.

## Critical Issues

### CR-01: Save Config restart silently swallows hardware-failure on the new pipeline

**File:** `pastor_tracker/src/pastor_tracker/ui/dashboard.py:574-582`
**Severity:** BLOCKER
**Issue:**
In `run()` (line 263-265), `pipeline.start()` is awaited via `.result(timeout=_PIPELINE_START_TIMEOUT_SEC)` so hardware failures (camera-open error, motor-handshake timeout, perception model load fail) raise into `main()` and translate to `EXIT_HARDWARE_FAILED` via the documented translator ladder.

In `_on_save_config_pressed` (line 577) the new pipeline is started with a bare:
```python
self._host.submit(self._pipeline.start())
```
No `.result(...)`. The future is fire-and-forget. If the new Config produces hardware that fails to start (e.g., the operator just lowered `motor_max_speed_steps_per_sec` to a value the firmware rejects in `send_settings`, or `pan_max_velocity_deg_per_sec` no longer admits a valid `home(0.0)`), this path:
1. Clears `_pending_config` (line 579).
2. Clears the error banner (line 581).
3. Logs `"ui_pipeline_restart_complete"` (line 582).

The dashboard then shows a "clean" status with NO error indication, while the background pipeline is actually stuck in `_PipelineState.STOPPED` (after `_on_tick_task_done` latches it) or never advanced past the partial-start hardware error. The operator gets no signal that the new config is broken; the on-stage scenario is exactly the failure mode the dual-bound D-12 validation was supposed to prevent — but D-12 only catches Pydantic range violations, not runtime hardware rejects.

This violates CLAUDE.md §1 "fail loud" — a silently-broken restart cannot be debugged from stage.

**Fix:**
Mirror `run()`'s blocking start. Catch the same exception family `main()` catches, surface it through the error banner, and ROLL BACK to the previous config + pipeline (see CR-02 for the rollback-state-machine fix this couples to):
```python
try:
    self._host.submit(self._pipeline.start()).result(
        timeout=_PIPELINE_START_TIMEOUT_SEC
    )
except (CameraError, ArduinoError, ArduinoPortNotFoundError,
        PerceptionError, OrchestratorRejected, TimeoutError) as exc:
    self._logger.error(
        "ui_pipeline_restart_start_failed",
        exc_type=type(exc).__name__, exc_msg=str(exc),
    )
    self._show_error_banner(
        f"{_ERROR_BANNER_PREFIX}restart failed: {exc}"
    )
    # do NOT clear _pending_config; restart did not succeed
    return
self._pending_config.clear()
self._refresh_unsaved_badge()
self._clear_error_banner()
```

---

### CR-02: Save Config restart has no rollback; failure between line 573 and 576 wedges the dashboard permanently

**File:** `pastor_tracker/src/pastor_tracker/ui/dashboard.py:570-577`
**Severity:** BLOCKER
**Issue:**
The teardown of the old host happens at line 570:
```python
old_host.stop()
# 4. Fresh PipelineThreadHost + fresh Pipeline (Pitfall 3 ...).
self._config = new_config                                # 573 — committed before allocation
self._pipeline = self._pipeline_factory(new_config)      # 574 — may raise
self._host = self._host_factory()                        # 575
self._host.start()                                       # 576 — may raise RuntimeError (loop_ready timeout)
self._host.submit(self._pipeline.start())                # 577
```

Failure modes:
- `_pipeline_factory(new_config)` raises `ArduinoPortNotFoundError` (USB just got unplugged), `CameraError`, etc. — `self._pipeline` retains the OLD object, `self._host` retains the OLD (already-stopped) host. Subsequent button presses call `self._host.submit(...)` which raises `RuntimeError("submit called before start()")` (actually `RuntimeError` from `asyncio.run_coroutine_threadsafe` against a stopped loop), but DPG callbacks swallow this and the dashboard appears to do nothing.
- `self._host_factory()` raises — same dangling-old-state outcome.
- `self._host.start()` raises (loop_ready barrier timeout — `RuntimeError`) — `self._host` now points at the broken new host, the old host is gone, `self._pipeline` is the new (unstarted) pipeline, `self._config` is new. The dashboard is permanently broken with no recovery surface.

In every failure between line 573 and line 577 the exception propagates up through the DPG button callback, which is typically logged by DPG and discarded. The operator sees a frozen-looking UI; pipeline is dead; no operator-visible error; pressing Save Config again hits the same broken `self._host`.

This is exactly the "tiger-style fail-loud" violation CLAUDE.md §1 forbids.

**Fix:**
Build the new pipeline + host EAGERLY (before touching `self._config` / `self._pipeline` / `self._host`), and only commit the swap once the new pipeline is up. On any failure, restore the old state (the old host was already torn down — that part is unavoidable, but the user can be told to restart). Sketch:
```python
# Tear down old state.
old_host, old_pipeline = self._host, self._pipeline
self._save_quit_old(old_host, old_pipeline)
old_host.stop()
# Build new state in locals -- DO NOT commit to self until all-up.
try:
    new_pipeline = self._pipeline_factory(new_config)
    new_host = self._host_factory()
    new_host.start()
    new_host.submit(new_pipeline.start()).result(
        timeout=_PIPELINE_START_TIMEOUT_SEC
    )
except (CameraError, ArduinoError, ArduinoPortNotFoundError,
        PerceptionError, OrchestratorRejected, TimeoutError,
        RuntimeError) as exc:
    # Old host is already dead; surface the failure loudly.
    self._show_error_banner(
        f"{_ERROR_BANNER_PREFIX}restart failed: {exc} — quit & relaunch"
    )
    self._logger.error("ui_pipeline_restart_aborted",
                       exc_type=type(exc).__name__, exc_msg=str(exc))
    return
# Commit only on full success.
self._config = new_config
self._pipeline = new_pipeline
self._host = new_host
self._pending_config.clear()
self._refresh_unsaved_badge()
self._clear_error_banner()
```

## Warnings

### WR-01: Unsaved-changes modal leaks DPG widget on every Cancel/X cycle

**File:** `pastor_tracker/src/pastor_tracker/ui/dashboard.py:629-654, 683-688, 818-830`
**Severity:** WARNING
**Issue:**
`_show_unsaved_modal` constructs a new `dpg.window(modal=True)` every time the viewport close-X fires while `_pending_config` is non-empty. The window has no captured tag and no callback deletes it. If the operator picks "Cancel" the modal is dismissed visually by DPG (modal flag) but the window stays in the widget tree. Pressing X again creates a NEW modal layered on top of the first. The old modal's three buttons are still bound to `self._on_modal_*` callbacks and still fire when clicked, which means the user can interact with stale modals beneath the new one in unspecified ways.

Same issue with `_on_modal_save_and_quit` (when Save fails the modal is left up but the banner is the only feedback channel) and `_on_modal_quit_anyway` (calls `dpg.stop_dearpygui()` without deleting the modal — minor, since the process is exiting).

**Fix:**
Capture the modal window tag and delete it on all three modal-callback exits. Also guard against re-entry:
```python
def _show_unsaved_modal(self) -> None:
    if self._tag_modal:                  # already up
        return
    with dpg.window(label="Unsaved changes", modal=True, no_close=True) as tag:
        self._tag_modal = tag
        ...

def _on_modal_cancel(self, sender, app_data, user_data) -> None:
    del sender, app_data, user_data
    if self._tag_modal:
        dpg.delete_item(self._tag_modal)
        self._tag_modal = 0
```

### WR-02: 11 `assert` statements violate CLAUDE.md §1 tiger-style under `python -O`

**File:** `pastor_tracker/src/pastor_tracker/ui/dashboard.py:481-482, 490-491, 506-507, 515-516, 556-557, 737`
**Severity:** WARNING
**Issue:**
Dashboard uses `assert self._pipeline is not None` / `assert self._host is not None` in every button callback and in `_render_tick`. `_pipeline_thread.py:60-75, 106-117` explicitly chose `raise RuntimeError(...)` over `assert` and documented the rationale: "tiger-style: explicit raise, not `assert` — the assert is stripped under `python -O`". Dashboard.py contradicts that convention. Under `python -O` (which is a plausible production deployment path on Windows packagers), every button press and every render tick has its tiger-style guard silently removed; a None-deref would then raise `AttributeError` from a deep DPG callback frame, breaking the structured diagnostics the rest of the module invests in.

**Fix:**
Replace each `assert self._<x> is not None` with a typed guard:
```python
if self._pipeline is None or self._host is None:
    raise RuntimeError(
        "Dashboard button fired before run() initialized pipeline/host"
    )
```
Alternative: keep the asserts but add `# pragma: no cover` and document the invariant up in the class docstring. Note: ruff has a `S101` rule that would have flagged these — re-enable it for `ui/dashboard.py` if disabled.

### WR-03: `_redraw_overlays` rebuilds the drawlist every frame; long-running drift of DPG widget IDs

**File:** `pastor_tracker/src/pastor_tracker/ui/dashboard.py:758-783`
**Severity:** WARNING
**Issue:**
Each render tick that observes a fresh `frame.timestamp_ns` calls `dpg.delete_item(self._tag_drawlist, children_only=True)` and re-adds 1 `draw_image` + up to 2 `draw_line`/`draw_rectangle` + 2 `draw_text` items. At nominal 30 Hz that is ~150 widget creations/sec. DPG allocates widget IDs from a monotonic int32 counter without recycling stale IDs. Over a 24 h on-stage run that is ~13 M widget allocations; well within int32 but the DPG hash-map of widgets grows monotonically with deletions because some internal registries don't compact freed slots. Memory growth is small per item but accumulates across multi-hour shoots (the operator profile for this app).

**Fix:**
Allocate the overlay items ONCE in `_build_ui` (store tags), and per-tick call `dpg.configure_item(tag, ...)` to update endpoints / visibility, plus `dpg.show_item` / `dpg.hide_item` for None-target cases. Drawlist becomes O(1) widget churn instead of O(n) per frame.

### WR-04: `_handle_home_done` does not handle `Future.exception()` raising `CancelledError`

**File:** `pastor_tracker/src/pastor_tracker/ui/dashboard.py:699-711`
**Severity:** WARNING
**Issue:**
`concurrent.futures.Future.exception()` raises `CancelledError` itself when the future was cancelled (not just returns it). The done-callbacks invoke `fut.exception()` unguarded — if `host.stop()` cancels in-flight futures during Save Config restart or quit, the done-callback raises `CancelledError` into asyncio's loop callback machinery, which logs it as an unhandled callback exception. This is benign per se but pollutes the log and risks tripping `filterwarnings=['error']` policies the rest of the project uses (Phase 2 W-05 precedent).

**Fix:**
```python
def _handle_home_done(self, fut: Future[None]) -> None:
    if fut.cancelled():
        return
    exc = fut.exception()
    ...
```
Same pattern in `_log_command_completion`.

### WR-05: Hotkeys fire on autorepeat; held E key spams `pipeline.e_stop()` submissions

**File:** `pastor_tracker/src/pastor_tracker/ui/dashboard.py:444-460`
**Severity:** WARNING
**Issue:**
`dpg.add_key_press_handler` fires repeatedly while the key is held (DPG v2.x default — there is no separate "press once" handler). Holding `E` enqueues many `e_stop()` coroutines on the asyncio loop. `pipeline.e_stop()` is idempotent so the wire behavior is fine, but the OrchestratorRejected catch is wide and the structured-log spam can drown real events in the EventBusProcessor deque (maxlen=64 — eight bursts and the real "Last error" entry is gone). For start/pause/home, holding the key spams state-transition rejects per frame.

**Fix:**
Track last-fired timestamp per key on `self`, debounce to ~250 ms; or switch to `dpg.add_key_release_handler` which fires once per release. Recommend release-handler for S/P/H/Q and keep press-handler for E only (operator wants instant e-stop on first key-down).

### WR-06: `_event_bus.py` uses fragile `MutableMapping[str, Any]` signature; missing `level` key silently skips capture

**File:** `pastor_tracker/src/pastor_tracker/ui/_event_bus.py:79-101`
**Severity:** WARNING
**Issue:**
`event_dict.get("level")` returns None when the `add_log_level` processor was not configured upstream (or runs AFTER `EventBusProcessor` in the chain — exactly the misordering CONTEXT.md D-16 originally specified). None is not in `_CAPTURED_LEVELS`, so the processor silently drops every event — the operator's "Last error" field is permanently `—`. There is no fail-fast guard for this misconfiguration; a future structlog-chain refactor that reorders processors will produce a regression that no test catches because `_event_bus.py` unit tests construct the deque themselves and don't exercise the chain.

**Fix:**
Add an explicit boundary check at first call (one-time, gated on `_first_call: bool` slot) or, simpler, add an integration test in `test_logging.py` that captures a real WARNING through `configure_logging(event_bus_buffer=buf)` and asserts the deque was appended. The module already calls out the contract in its docstring — back it with a test.

## Info

### IN-01: `compute_fps(now_ns)` accepts an unused parameter, expanding API surface for a future-only feature

**File:** `pastor_tracker/src/pastor_tracker/ui/_status_panel.py:95-117`
**Severity:** INFO
**Issue:**
`now_ns` is documented as "reserved for stale-window detection" but is immediately `del`-ed. CLAUDE.md §3 DRY discourages speculative APIs. Adding the parameter when needed is a one-line change and easier to review than carrying a dead parameter.

**Fix:**
Remove `now_ns`. Add it back in the same commit as the stale-window detection implementation.

### IN-02: `Pipeline.snapshot()` reads multiple cache fields without coherence guarantees

**File:** `pastor_tracker/src/pastor_tracker/ui/dashboard.py:741` (consumer); `pastor_tracker/src/pastor_tracker/pipeline.py:235-248` (producer)
**Severity:** INFO
**Issue:**
D-03 explicitly accepts atomic-per-field reads; CONTEXT.md authorizes "no lock". This is documented and intentional. Recording here only so a future reader does not need to re-derive the invariant: if a tick task is mid-update, `snap.last_target_x_normalized` may correspond to frame N while `snap.last_pan_angle_deg` corresponds to frame N-1. The dashboard renders both in the same overlay; visual incoherence on heavy CPU contention is possible. Accept; no fix in v1.

### IN-03: `_format_state_line` `.rstrip()` strips trailing whitespace including non-empty badge values containing only spaces

**File:** `pastor_tracker/src/pastor_tracker/ui/_status_panel.py:65-67`
**Severity:** INFO
**Issue:**
`f"{_STATE_LABEL}   {snap_state}   {unsaved_badge}".rstrip()` — if `unsaved_badge` is ever a string of pure whitespace (it currently can't be — `_unsaved_badge_text` returns either `""` or `"(unsaved changes)"`) the rstrip would trim it. Defensive coding nit; no current trigger.

**Fix:**
Use conditional concat:
```python
if unsaved_badge:
    return f"{_STATE_LABEL}   {snap_state}   {unsaved_badge}"
return f"{_STATE_LABEL}   {snap_state}"
```

### IN-04: Modal callback signatures accept positional args used only via `del`; minor code-quality

**File:** `pastor_tracker/src/pastor_tracker/ui/dashboard.py:656-660, 667-681, 683-688`
**Severity:** INFO
**Issue:**
Same `(sender, app_data, user_data)` triplet immediately `del`-ed pattern repeated across 8 callbacks. Consider a one-line helper to keep the boilerplate uniform, or accept it as DPG-callback signature noise.

---

_Reviewed: 2026-05-08_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_

## Fix Log

**Fixed:** 2026-05-08
**Scope:** critical + warning (INFO findings deferred per default fix scope)
**Gates after fix:** `mypy --strict` clean (10 baseline `import-untyped` errors only),
`ruff check` clean, full `pytest` 561 passed + 1 skipped (+23 over the 538+1
baseline).

| ID    | Commit SHA | Summary |
|-------|------------|---------|
| CR-02 | `90f37b5`  | Eager-build new Pipeline + PipelineThreadHost in locals; on factory / host.start() raise, retain old self._* + preserve _pending_config |
| CR-01 | `87dd416`  | Block on new_host.submit(new_pipeline.start()).result(timeout) inside the eager-build try; classify CameraError/ArduinoError/ArduinoPortNotFoundError/PerceptionError/OrchestratorRejected/TimeoutError/RuntimeError; surface ui_save_config_restart_failed + red banner; best-effort orphan-host cleanup |
| WR-01 | `6461f38`  | Capture _tag_modal in _show_unsaved_modal; _delete_modal_if_open from every _on_modal_* exit; re-entry no-op while modal is open |
| WR-02 | `dd4b880`  | Replace 9 `assert self._<x> is not None` with shared _require_initialized() (explicit RuntimeError); render-tick inlines the check (hot path); python -O smoke confirmed |
| WR-02 | `5a65e5c`  | Use _require_initialized() return values inside Save Config so mypy --strict narrows the Optional[...] attributes (3 union-attr errors cleared) |
| WR-03 | `a2eac3f`  | Pre-allocate stable overlay-item tags (third-line, bbox, ID-text, angle-text) in _build_ui; _redraw_overlays uses configure_item + show_item/hide_item; zero widget churn at 30 Hz |
| WR-04 | `ea19112`  | Guard Future.exception() with fut.cancelled() in _handle_home_done + _log_command_completion so a cancelled future no longer surfaces CancelledError into asyncio's callback machinery |
| WR-05 | `16887d9`  | Switch S/P/H/Q to add_key_release_handler (fires once per release); keep E on add_key_press_handler (zero-latency first press) but debounce subsequent autorepeats against a 250 ms _HOTKEY_DEBOUNCE_NS window |
| WR-06 | `fe13d0b`  | Fail-fast RuntimeError in EventBusProcessor when the first event lacks the 'level' key (mandatory upstream add_log_level processor missing); one-shot guard via _chain_validated slot; integration test against configure_logging already exists in test_logging.py |

**INFO findings (deferred, out of scope):** IN-01, IN-02, IN-03, IN-04 — see the
Info section above. None block the phase; revisit in a follow-up cleanup pass.

_Fixed: 2026-05-08_
_Fixer: Claude (gsd-code-fixer)_
