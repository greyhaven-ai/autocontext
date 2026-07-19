# AC-894: Cooperative, idempotent active-run stopping

## Goal

Add an opt-in `safe_run_stop_v1` capability to the TypeScript interactive
server so a run-scoped, retry-safe stop request takes effect at a cooperative
execution boundary, wakes a paused run, preserves completed work, and produces
one replayable `run_stopped` terminal receipt.

## Scope decision

The TypeScript server owns the durable transcript, command journal, run binding,
and replay machinery required to make this guarantee. The Python server does
not. This change therefore:

- implements the complete capability in TypeScript;
- keeps the shared Python/TypeScript wire schema aligned with a required-ID
  `stop` command and `RunStoppedPayload`;
- makes Python reject `stop` explicitly with a correlated unsupported-capability
  error; and
- does not advertise `safe_run_stop_v1` from Python.

This follows the existing `run_transcript_v1` precedent and avoids claiming
behavioral parity without durable command records and replay.

## Invariants

- A stop command requires both `client_run_id` and `command_id`.
- Only the currently bound engine run can be stopped.
- The durable command record is written before the stop side effect.
- The correlated ack is persisted and sent in the same synchronous JavaScript
  stack that requests stop, before a woken paused run can emit its terminal
  receipt.
- The first terminal outcome wins:
  - stop-first suppresses later completion/failure;
  - completion/failure-first returns `already_terminal` and never sets stop.
- A stable command ID never repeats the side effect.
- Once available, the `run_stopped` frame becomes the command's preferred
  replay response; reconnect backfill also returns the exact retained ack and
  terminal frames after the requested cursor.
- The controller's stop state is reset before a later run.
- Ordinary v1 clients and non-opted WebSocket connections retain their existing
  shapes and behavior.

## Implementation

### 1. Protocol and parity

- Add `safe_run_stop_v1` to the TypeScript transcript capability list.
- Add strict `StopCmd` schemas with required, bounded `client_run_id` and
  `command_id` fields to:
  - `ts/src/server/protocol.ts`
  - `autocontext/src/autocontext/server/protocol.py`
- Add `stop` to the shared client-message inventory and export the TypeScript
  schema from `ts/src/server/index.ts`.
- Add a Python `RunStoppedPayload` model with `run_id`, `reason`,
  `command_id`, `completed_generations`, and optional `best_score`.
- Update `docs/websocket-protocol-contract.json`, regenerate
  `protocol/autocontext-protocol.json` and
  `ts/src/tui/protocol.generated.ts`, and extend parity tests.
- Add an explicit Python WebSocket handler that returns a correlated error and
  leaves the global controller untouched.

### 2. Cooperative controller

- Add a typed stop-request sentinel to `ts/src/loop/controller.ts`.
- Add methods to:
  - reset stop state at the beginning of a run;
  - retain the first run/command identity;
  - request stop idempotently;
  - inspect the pending request; and
  - wait at a boundary, checking before and after a pause wait.
- Requesting stop clears pause and resolves every paused waiter.
- A second distinct command reports `already_requested` without replacing the
  first command identity.

### 3. Run manager and synchronous command path

- Reset controller stop state before marking a new run active.
- Track completed-generation/best-score progress from durable generation events.
- Add a run-ID-checked manager stop method returning `requested`,
  `already_requested`, or `already_terminal`.
- Move the state to `stopping` and clear paused presentation state on the first
  request.
- Handle `stop` in a dedicated synchronous `ws-server` branch:
  bind scope, begin the durable command, request stop, then persist/send the
  correlated ack without an `await`.

### 4. Safe execution boundaries and terminal arbitration

- Replace pause-only waits with stop-aware cooperative boundaries.
- Add checks after opaque/long awaits and immediately before every natural
  `run_completed` write/event.
- Built-in generation runs:
  - preserve already-persisted generations, match output, agent output, replay,
    metrics, and artifacts;
  - derive stopped progress from the durable score trajectory;
  - write run status `stopped`;
  - emit stopped rather than failed lifecycle hooks; and
  - rethrow the typed sentinel for outer cleanup.
- Generated and agent-task custom runs:
  - finish and retain the current completed checkpoint after an opaque execution
    returns;
  - stop before any subsequent execution or natural completion; and
  - enrich the sentinel with retained progress.
- Make `active-run-lifecycle.ts` the single `run_stopped` event owner after
  execution/provider cleanup unwinds.
- Suppress duplicate failure emission when an inner runner has already produced
  a terminal event.
- Map `run_stopped` to stopped run state before the existing idle transition.

### 5. Durable receipt and replay

- Allowlist the stopped receipt fields in
  `ts/src/server/run-transcript-frame.ts`.
- Teach the transcript store to associate the terminal event's payload
  `command_id` with the originating stop command.
- Promote the terminal frame over the initial ack as the preferred completed
  command response while retaining both ordered frames for cursor replay.
- Preserve existing finite retention, crash-safe append ordering, conflict
  detection, and pending-after-crash fail-closed behavior.

### 6. Documentation

- Document capability gating, command/ack/terminal shapes, cooperative-boundary
  semantics, retained partial work, terminal arbitration, and the finite
  idempotency horizon in `ts/README.md`.
- Add the capability to `CHANGELOG.md`.

## Tests

### TypeScript

- Protocol:
  capability appears only on transcript opt-in; required IDs; strict fields;
  shared inventory parity.
- Controller:
  first request retained; duplicate/different requests; paused wakeup; checks
  before/after wait; reset for the next run.
- Manager/workflow:
  exact ack decisions and correlation; wrong engine run rejected; terminal state
  is immutable.
- Execution:
  stop after one durable generation; retained DB trajectory/artifacts; custom
  checkpoint retention; stop-first over provider failure; no `run_completed` or
  `run_failed` after stop.
- Active lifecycle:
  one stopped terminal, cleanup, and no duplicate ordinary terminal.
- Transcript:
  sanitizer retention, restart reload, terminal-response promotion, duplicate
  command replay, and cursor replay.
- WebSocket:
  paused stop ack precedes terminal receipt; stable IDs; stale run rejection;
  duplicate delivery; reconnect replay; completion-first behavior; no late
  terminal frames.

### Python

- `StopCmd` parsing requires both IDs.
- `RunStoppedPayload` validates and rejects extra fields.
- shared protocol inventory and generated schema remain in sync.
- the interactive handler returns a correlated unsupported-capability error and
  does not mutate the controller.

## Validation

1. Focused TypeScript Vitest files for protocol, controller, lifecycle,
   run-start, transcript, and WebSocket behavior.
2. `npm run lint`
3. `npm test`
4. Focused Python protocol/WebSocket tests.
5. `uv run python ../scripts/generate_protocol.py --check`
6. `uv run ruff check src tests`
7. `uv run mypy src`
8. `uv run pytest -m "not live"`

## Release follow-up

Merge AC-894 without version bumps. Then prepare a separate `0.12.0` release PR
that synchronizes both core package versions, locks, release manifest, changelog,
README/assets/banner surfaces, and release tests. Tag the exact release merge SHA
with `ts-v0.12.0`, verify npm, then `py-v0.12.0`, verify PyPI. Autowork may update
only after `autoctx@0.12.0` is live.
