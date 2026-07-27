# AC-897: Emit durable agent-authored progress notes

## Goal

Add an opt-in `agent_progress_notes_v1` extension to the TypeScript interactive
run protocol so clients can present concise, safe, Codex-like progress notes
while an agent works. Notes must be useful during the live run and replay as the
exact same ordered transcript frames after reconnect or process restart.

## Scope decision

The TypeScript server owns the durable transcript, run binding, sequence
allocation, and replay machinery required by this capability. The Python server
does not. This change therefore:

- implements note production and durable replay in TypeScript;
- advertises `agent_progress_notes_v1` only on transcript-opted interactive
  sessions, alongside `run_transcript_v1`;
- keeps Python's advertised capability set unchanged; and
- documents the extension as TypeScript-only until Python has an equivalent
  durable transcript implementation.

The existing generic `event` envelope remains unchanged. The extension adds the
named event `agent_progress_note`, not a new top-level protocol message or
protocol-version bump.

## Public contract

Transcript frames use the existing outer metadata:

- `run_id`
- `client_run_id`
- `event_id`
- monotonically increasing `sequence`
- `occurred_at`

The strict event payload is:

```ts
{
  run_id: string
  generation: number
  kind: "intent" | "discovery" | "decision" | "verification" | "blocker"
  text: string
  evidence_targets?: Array<
    | { kind: "action"; action_id: string }
    | { kind: "artifact"; action_id: string; artifact_id: string }
  >
}
```

Limits match the Autowork ERP-111 consumer:

- note text: 480 characters after redaction;
- evidence targets: at most 5;
- action and artifact IDs: at most 200 characters;
- IDs: `^[A-Za-z0-9][A-Za-z0-9._:-]*$`;
- generation: nonnegative integer;
- unknown fields: rejected.

## Safety and durability invariants

- Validate and redact before calling `EventStreamEmitter.emit()`. This prevents
  unsafe copy from reaching the raw event log, `/ws/events`, legacy interactive
  clients, or the durable transcript.
- Emit only concise agent-authored summaries. Never emit hidden reasoning, raw
  prompts, raw model/tool I/O, credentials, selectors, or URLs.
- Re-bound text after redaction because replacement text can be longer than the
  sensitive source.
- Drop the entire note on invalid shape or oversize serialization; never retain
  a misleading partially truncated note.
- A payload `run_id` must match the transcript frame's outer `run_id`.
- Evidence targets must reference action/artifact identifiers already emitted
  earlier in the same retained client-run transcript. Cross-run, future, and
  unknown references reject the note atomically.
- Duplicate semantic publications within one publisher/run are suppressed.
- The transcript store remains the owner of durable `event_id`, sequence, and
  timestamp metadata.
- Resume/restart replay uses exact retained wire frames and existing sequence
  dedupe behavior.
- Legacy and non-transcript-negotiated clients keep their existing bootstrap
  and message shapes; they may safely ignore the additive named event.

## Implementation

### 1. Strict producer module

Create `ts/src/loop/agent-progress-note.ts`, following the safe publisher
precedent in `ts/src/loop/agent-task-plan.ts`.

- Define and export the capability/event constants, kinds, evidence schemas,
  payload schema, types, and limits.
- Add a sanitizer that:
  - strict-parses the payload;
  - redacts credential-shaped material with
    `redactPresentationText()`;
  - removes URL/selector-shaped presentation fragments;
  - trims and re-bounds text after redaction;
  - rejects duplicate or invalid evidence identities; and
  - verifies the final serialized event size.
- Add a best-effort publisher that emits only sanitized payloads, reports
  publication failure without failing the run, and suppresses identical note
  fingerprints during a run.

### 2. Protocol and transcript integration

Update:

- `ts/src/server/protocol.ts`
- `ts/src/server/index.ts`
- `ts/src/server/run-transcript-frame.ts`
- `ts/src/server/run-transcript-store.ts`

Changes:

- export the strict note contract publicly;
- append `agent_progress_notes_v1` to the TypeScript server capability list;
- add a dedicated transcript sanitizer branch so notes retain their full strict
  payload rather than becoming `{}`;
- enforce payload/outer-run identity;
- resolve every optional evidence target against an earlier retained
  `action_detail` frame in the same client-run scope; and
- preserve existing append-before-delivery, retention, restart loading, and
  cursor replay behavior.

No changes are required in `ws-server.ts`, `run-manager.ts`, or
`websocket-session-bootstrap.ts`: the generic event bridge already persists and
replays frames, while bootstrap already advertises capabilities only for
transcript-opted sessions.

### 3. Semantic emission cadence

Use short, deterministic summaries at meaningful lifecycle boundaries rather
than mirroring every low-level event.

#### Built-in generation workflow

Update `ts/src/loop/generation-runner.ts` to publish:

- `intent` after `run_started`;
- `discovery` after each completed generation, using only safe generation and
  score metadata;
- `decision` when the workflow rolls back or enters recovery;
- `verification` immediately before natural completion; and
- a static `blocker` before ordinary failure.

Do not publish a blocker for cooperative operator stop; `run_stopped` already
owns that higher-priority lifecycle outcome.

#### Saved agent-task workflow

Update `ts/src/server/run-start-workflow.ts` around its existing progress
phases:

- `intent` at start;
- `discovery` after context/draft and evaluation checkpoints;
- `decision` before revision;
- `verification` before finalization and natural completion; and
- a static `blocker` on ordinary failure.

#### Generated custom workflow

Update the generated workflow runner to publish:

- `intent` at start;
- `discovery` after each completed generation;
- `verification` before natural completion; and
- a static `blocker` on ordinary failure.

Built-in producers initially omit evidence targets because they do not currently
emit stable `action_detail` identifiers. The wire contract and store validation
still support external producers that do.

### 4. Public documentation and parity

Update:

- `docs/websocket-protocol-contract.json`
- `ts/README.md`
- `docs/README.md`
- `CHANGELOG.md`

Document capability negotiation, the exact ERP-111-compatible fixture, semantic
kinds, safety boundaries, transcript-only durability, evidence constraints,
finite retention, and Python's intentional non-advertisement.

## Tests

### Producer

- strict payload parsing and unknown-field rejection;
- each kind and evidence-target variant;
- consumer-identical text/ID/evidence limits;
- credential, URL, and selector redaction before raw emission;
- re-bounding after redaction expansion;
- atomic invalid/oversize drop;
- collision-safe evidence dedupe; and
- duplicate semantic publication suppression.

### Workflow cadence

- built-in run emits intent, per-generation discovery, decision where
  applicable, verification, and blocker at the intended boundaries;
- saved task phases map to the expected semantic kinds;
- generated workflow emits start, generation, terminal, and failure notes;
- notes never include raw model output, raw error details, prompts, selectors,
  URLs, or credentials; and
- operator stop does not gain a misleading failure blocker.

### Transcript

- strict note payload survives sanitization and restart replay unchanged;
- outer/payload run mismatch is rejected;
- evidence must resolve to an earlier same-run action/artifact;
- cross-run, unknown, future, and partially invalid evidence is rejected;
- live and replay frame order/metadata match;
- reconnect cursor replay does not duplicate acknowledged frames; and
- legacy/non-negotiated sessions remain unchanged.

### Contract parity

- TypeScript transcript bootstrap includes the new capability;
- non-opted TypeScript bootstrap advertises no transcript capabilities;
- the machine-readable fixture parses with the exported TypeScript schema;
- Python explicitly remains without `agent_progress_notes_v1`; and
- existing protocol inventories remain unchanged.

## Validation

1. Focused Vitest files for the producer, workflows, protocol, transcript store,
   bootstrap, and WebSocket replay.
2. `npm run lint`
3. `npm test`
4. `npm run build`
5. Focused Python WebSocket protocol contract tests.
6. `uv run ruff check src tests`
7. `uv run mypy src`
8. `uv run pytest -m "not live"`
