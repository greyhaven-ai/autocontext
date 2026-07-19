# AC-894 Slice A2: Python cooperative-stop engine

Date: 2026-07-19
Status: approved in brainstorming; pending written review
Linear: AC-894 (Add cooperative, idempotent active-run stopping)
Scope: second of three slices. A1 (wire protocol) is merged (#1218). A2 = the Python cooperative-stop engine (this slice). A3 = the TypeScript engine.

## Purpose

A1 defined the `stop_run` command + `run_stopped` receipt on the wire. A2 makes the Python interactive
WS server actually honor a stop: cooperatively stop an active run at a generation boundary, wake it if
paused, deduplicate retries, reject stops that target another run, emit a correlated ack plus a terminal
`run_stopped` receipt, and make stop-vs-completion deterministic (first terminal outcome wins) while
preserving the durable per-generation results. It advertises `safe_run_stop_v1` on the Python `hello`.

## Context (from the code map)

- The interactive WS server (`server/app.py`) has one process-global `LoopController` and a
  `RunManager` that runs the loop on a daemon thread. Commands are dispatched in a `match cmd:` block;
  `StopCmd` currently returns a graceful `ErrorMsg("safe_run_stop_v1 is not supported")` stub (A2
  replaces it).
- `RunManager` tracks `_active: bool` but NOT the active run's `client_run_id` (needed for scope
  validation) and has no command dedup. `start_run` records nothing client-scoped today.
- The loop's cooperative boundary is `generation_runner.py:1143-1146` (`for generation in range(...)`:
  `wait_if_paused()` then `take_hint()`). Natural completion calls `mark_run_completed(active_run_id)`
  after the loop (line ~1308).
- `mark_run_completed` (`sqlite_store.py:778`) is an unconditional `UPDATE runs SET status='completed'`.
  It has 4 callers: the main loop, the recovery path (`generation_runner.py:1015`), solve
  (`solve_task_execution.py:327`), and package (`package.py:277`), all operating on running (or
  already-completed) runs.
- Run lifecycle events reach the socket via `self.events.emit(name, payload)` -> the WS handler's
  `_on_event(event, payload)` -> `EventMsg`.
- `HelloMsg().capabilities` is None today (Python advertises no capabilities; there is no cross-package
  capability-parity gate, confirmed in A1).

## Decisions of record

1. **First-terminal-wins via two surgical guards (not a global CAS).** Add a `stopped` run status. Add
   `mark_run_stopped(run_id) -> bool` = `UPDATE runs SET status='stopped', updated_at=datetime('now')
WHERE run_id=? AND status='running'` returning `rowcount > 0` (won the race). Guard `mark_run_completed`
   with `AND (status IS NULL OR status != 'stopped')` so a completion never overwrites a stop. Together
   these are deterministic in both directions: a stop only wins while the run is still `running`; a
   completion never clobbers a `stopped` run; every other `mark_run_completed` caller (running/completed
   runs) is unchanged. This avoids the riskier `WHERE status='running'` on `mark_run_completed` (whose
   solve/package/recovery callers do not all guarantee a running row).
2. **The loop owns the terminal, the dispatch thread only signals.** The WS dispatch thread validates +
   dedups + calls `controller.request_stop(...)` + acks, but writes no status. The loop thread, at the
   top-of-generation boundary, detects the stop, runs `mark_run_stopped` (CAS), and on winning emits the
   `run_stopped` receipt. Completion and stop are mutually exclusive in the loop's control flow (break for
   stop XOR fall through to completion), so no torn terminal write happens.
3. **Cooperative boundary = top-of-generation only.** A stop is honored at the start of the next
   generation. A stop that arrives mid-final-generation lets the run complete (completion wins), which
   preserves the completed result and its checkpoint. This satisfies "preserve completed checkpoints".
4. **`request_stop` wakes a paused run.** `LoopController.request_stop` sets the stop flag AND calls
   `_pause_event.set()`, so a thread parked in `wait_if_paused()` returns immediately and then re-checks
   `stop_requested()` at the boundary and stops.
5. **Scope + dedup live in `RunManager` (per active run).** `start_run` records the starting
   `client_run_id` and resets a per-run set of processed stop `command_id`s; both clear when the run
   ends. A new `RunManager.stop_run(client_run_id, command_id, reason) -> StopOutcome` does: no active run
   -> `"not_active"`; `client_run_id` provided and != the active run's -> `"scope_mismatch"`;
   `command_id` already processed -> `"duplicate"` (idempotent, no re-trigger); otherwise record the id,
   call `controller.request_stop(command_id, reason)`, return `"accepted"`.
6. **Advertise the capability.** Define `SERVER_CAPABILITIES = ["safe_run_stop_v1"]` in
   `server/protocol.py` and send `HelloMsg(capabilities=SERVER_CAPABILITIES)`.

## Architecture

### A2.1: storage terminal arbitration (`storage/sqlite_store.py`)

```python
def mark_run_stopped(self, run_id: str) -> bool:
    """First-wins stop transition: only a still-running run can be stopped. Returns True if it won."""
    with self.connect() as conn:
        cur = conn.execute(
            "UPDATE runs SET status = 'stopped', updated_at = datetime('now') "
            "WHERE run_id = ? AND status = 'running'",
            (run_id,),
        )
        return cur.rowcount > 0
```

And guard `mark_run_completed` so it never overwrites a stopped run:

```python
"UPDATE runs SET status = 'completed', updated_at = datetime('now') "
"WHERE run_id = ? AND (status IS NULL OR status != 'stopped')"
```

### A2.2: controller stop primitive (`harness/core/controller.py`)

Add to `LoopController.__init__`: `self._stop_requested = False`, `self._stop_command_id: str | None = None`,
`self._stop_reason: str | None = None`.

```python
def request_stop(self, command_id: str | None = None, reason: str | None = None) -> None:
    with self._lock:
        self._stop_requested = True
        self._stop_command_id = command_id
        self._stop_reason = reason
    self._pause_event.set()  # wake a thread parked in wait_if_paused()

def stop_requested(self) -> bool:
    with self._lock:
        return self._stop_requested

def stop_details(self) -> tuple[str | None, str | None]:
    with self._lock:
        return self._stop_command_id, self._stop_reason
```

(Re-exported through `loop/controller.py` like the rest of `LoopController`.)

### A2.3: loop cooperative-boundary stop check (`loop/generation_runner.py`)

At the boundary (currently 1144-1146), after `wait_if_paused()` returns, check for a stop; on stop, run
the CAS, emit the receipt, and break to a stop terminal that SKIPS `mark_run_completed`:

```python
for generation in range(1, generations + 1):
    if self.controller:
        self.controller.wait_if_paused()
        if self.controller.stop_requested():
            command_id, reason = self.controller.stop_details()
            if self.sqlite.mark_run_stopped(active_run_id):
                self.events.emit("run_stopped", {"command_id": command_id, "reason": reason})
            stopped = True
            break
        hint = self.controller.take_hint()
    ...
```

Wrap the post-loop completion block (the `mark_run_completed(active_run_id)` + final checkpoint at ~1308)
in `if not stopped:`. Per-generation artifacts/metrics/checkpoints already persisted before the stop are
retained (the stop does not roll them back); only the run-level "completed" transition and the final
"completed" checkpoint are skipped.

### A2.4: dispatch + scope + dedup + receipt mapping + capability (`server/run_manager.py`, `server/app.py`)

- `RunManager.start_run(...)`: accept the starting `client_run_id` (thread it from the `StartRunCmd`),
  set `self._active_client_run_id = client_run_id` and `self._processed_stop_command_ids = set()`; clear
  both in the `_target` `finally`.
- `RunManager.stop_run(client_run_id, command_id, reason) -> StopOutcome` (a `Literal["accepted",
"duplicate", "scope_mismatch", "not_active"]`): implements decision 5 against `self._active`,
  `self._active_client_run_id`, `self._processed_stop_command_ids`, and `self.controller.request_stop`.
- `app.py` `StopCmd` case: call `run_manager.stop_run(...)`; map `accepted`/`duplicate` -> `AckMsg(action=
"stop_run", command_id=..., client_run_id=...)`; `scope_mismatch` -> `ErrorMsg("stop targets a
different run than the active one")`; `not_active` -> `ErrorMsg("no active run to stop")`. Pass the
  `StartRunCmd.client_run_id` to `run_manager.start_run` at the start-run case.
- `app.py` `_on_event`: map `event == "run_stopped"` to `RunStoppedMsg(command_id=payload.get(
"command_id"), reason=payload.get("reason"))`, else the existing `EventMsg`.
- `app.py` `HelloMsg(capabilities=SERVER_CAPABILITIES)` (from `protocol.py`).

## Data flow

```
client -> stop_run {client_run_id, command_id, reason}
  app.py dispatch (WS thread): run_manager.stop_run(...)
    -> scope check (client_run_id vs active) + dedup (command_id) -> controller.request_stop(...)
    -> AckMsg(action="stop_run", command_id)              [immediate]
  loop thread @ next generation boundary: wait_if_paused() wakes -> stop_requested() true
    -> mark_run_stopped(run_id) CAS ; if won -> events.emit("run_stopped", {command_id, reason})
    -> break, skip mark_run_completed
  app.py _on_event("run_stopped", ...) -> RunStoppedMsg over WS   [terminal receipt]
Natural completion race: loop finishes before the boundary re-check -> mark_run_completed (guarded);
  a later stop CAS finds status != 'running' and loses -> no run_stopped (completion won).
```

## Error handling and edge cases

- **Stop before any run / after it ended:** `stop_run` returns `not_active` -> `ErrorMsg`; nothing is
  triggered. (A stop delivered after the run already ended cannot target it.)
- **Stop targeting a different run:** non-null `client_run_id` != the active run's -> `scope_mismatch`
  -> `ErrorMsg`; the active run is untouched.
- **Duplicate / reconnected stop (same command_id):** `duplicate` -> the ack is re-sent, but
  `request_stop` is NOT called again (idempotent); a single terminal results.
- **Stop while paused:** `request_stop` sets `_pause_event`, the parked `wait_if_paused()` returns, the
  boundary sees `stop_requested()` and stops.
- **Completion-first:** the loop reaches `mark_run_completed` before any stop-break; the stop's CAS then
  finds a non-running row and loses; no `run_stopped` is emitted.
- **Preserved results:** per-generation checkpoints/artifacts/metrics written before the stop remain; the
  runtime-session history is unaffected. Only the run-level completed transition + final checkpoint are
  skipped on stop.

## Testing

- Storage (`test_sqlite_store.py` / a new `test_run_stop_store.py`): `mark_run_stopped` sets `stopped`
  from `running` and returns True; returns False when the run is not running (already completed/stopped);
  `mark_run_completed` no longer overwrites a `stopped` run but still completes a `running` one.
- Controller (`test_loop_controller.py`): `request_stop` sets `stop_requested()` and wakes a thread
  parked in `wait_if_paused()`; `stop_details` returns the command_id/reason.
- RunManager (`test_run_manager*` or a new `test_run_stop.py`): `stop_run` returns
  `not_active`/`scope_mismatch`/`duplicate`/`accepted` for the corresponding states; a duplicate does not
  re-trigger `request_stop`; `start_run` records the client_run_id and resets the dedup set.
- Loop integration (a new `test_run_stop.py`, using a fake/short scenario or a stubbed runner): stop-first
  -> run ends `stopped`, `run_stopped` event emitted; completion-first -> `completed`, no `run_stopped`;
  stop-from-paused wakes and stops.
- Dispatch (extend the interactive WS test, e.g. `test_cli_json`/`test_websocket_*` or a focused test):
  a `stop_run` yields an `AckMsg(action="stop_run")` with the command_id; a mismatched `client_run_id`
  yields an `ErrorMsg`; a duplicate command_id yields a second `AckMsg` without a second trigger; `hello`
  advertises `safe_run_stop_v1`.
- Gates: module-size, serde-convention, protocol tests + `generate_protocol.py --check` (unchanged; A2
  adds no wire types), ruff/mypy, full Python suite, lockfiles unchanged.

## Documentation

`docs/evaluator-epochs.md` is unrelated; instead add operator notes where interactive-run control is
documented (or a short section in the AC-894 design lineage). CHANGELOG entry: the Python interactive WS
server now honors `stop_run` (cooperative stop at a generation boundary, wakes paused runs, idempotent by
command_id, run-scoped, first-terminal-wins vs completion, emits a `run_stopped` receipt) and advertises
`safe_run_stop_v1`.

## Deferred (A3)

The TypeScript engine (controller `requestStop`, loop boundary check, terminal arbitration reusing the TS
`active-run-lifecycle`, dispatch reusing the existing `RunTranscriptStore` dedup + `resolveCommandScope`,
the durable `run_stopped` retention noted in the A1 design, and the TS `safe_run_stop_v1` advertisement).

## Acceptance criteria advanced by this slice

- AC-894 "Repeated or reconnected stop commands are idempotent and cannot target another run": dedup by
  command_id + run-scope validation in `RunManager.stop_run`.
- AC-894 "Stop works from running or paused state and prevents further execution": the boundary check +
  `request_stop` waking the pause event + the loop breaking.
- AC-894 "the first terminal outcome wins": the `mark_run_stopped` CAS + the `mark_run_completed` stopped
  guard.
- AC-894 "Preserve completed checkpoints ... artifacts, metrics": per-generation results persist; only
  the run-level completed transition is skipped on stop.
