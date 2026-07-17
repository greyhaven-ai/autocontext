# AC-894 Slice A1: cooperative-stop wire protocol

Date: 2026-07-13
Status: approved in brainstorming; pending written review
Linear: AC-894 (Add cooperative, idempotent active-run stopping)
Scope: first of three slices. A1 = the wire protocol (this slice). A2 = the Python cooperative-stop engine. A3 = the TypeScript engine.

## Purpose

AC-894 lets an operator safely stop an active interactive run at a cooperative execution boundary. It is
decomposed into three slices: A1 defines the stop command, its terminal receipt, and their cross-package
parity so A2 (Python engine) and A3 (TS engine) build behavior on a settled contract. A1 adds NO
behavior: receiving the command does nothing yet, and no server advertises the capability yet.

## Context (from the code map)

The interactive-run protocol is defined in `autocontext/src/autocontext/server/protocol.py` (Pydantic
models). `export_json_schema()` there is the single source of truth: `scripts/generate_protocol.py`
regenerates `ts/src/tui/protocol.generated.ts` + `protocol/autocontext-protocol.json` from it, and its
`--check` mode is a CI parity gate. A separate hand-authored Zod mirror,
`ts/src/server/protocol.ts`, carries the runtime validators plus parity lists
(`PYTHON_SHARED_CLIENT_MESSAGE_TYPES`, `PYTHON_SHARED_SERVER_MESSAGE_TYPES`) that must stay in sync with
the Python union. Client commands subclass `RunCommandMetadata` (`client_run_id`, `command_id`); the
existing `AckMsg` already echoes `command_id`.

Confirmed: Python advertises no `hello` capabilities today while TS advertises `run_transcript_v1`, and
the suite is green, so there is no cross-package capability-parity gate. Capabilities are therefore
advertised per package, so the `safe_run_stop_v1` advertisement belongs with each engine (A2 Python, A3
TS), not here, a server should never claim a capability it cannot honor.

## Decisions of record

1. **Mirror the existing command pattern exactly.** `StopCmd(RunCommandMetadata)` with
   `type: Literal["stop_run"] = "stop_run"` is added to the `ClientMessage` union, mirroring `PauseCmd`.
   It reuses the inherited `client_run_id` + `command_id` envelope and adds one optional field
   `reason: str | None` (operator context, echoed on the receipt). The command's acknowledgement reuses
   the existing `AckMsg` (which already carries `command_id`); no new ack type.
2. **Terminal receipt as a server message.** `RunStoppedMsg(RunMessageMetadata)` with
   `type: Literal["run_stopped"] = "run_stopped"` is added to the `ServerMessage` union. It carries
   `command_id: str | None` (to correlate with the stop command that caused it) and `reason: str | None`.
   It is the durable terminal receipt A2/A3 emit when a stop wins the terminal race.
3. **Protocol is generated, parity is gated.** After editing `protocol.py`, run
   `scripts/generate_protocol.py` to regenerate the `.generated.ts` + JSON; never hand-edit the generated
   file. Add the two schemas to the hand-authored Zod mirror and the two new literals to the
   `PYTHON_SHARED_*_MESSAGE_TYPES` parity lists.
4. **No behavior, no capability advertisement in A1.** The command type and receipt exist on the wire;
   dispatch, the stop primitive, the terminal CAS, dedup/scope, and the `safe_run_stop_v1` advertisement
   land in A2 (Python) and A3 (TS). Because no server advertises the capability, no conforming client
   sends `stop_run` in the A1-only state.

## Architecture

### A1.1: Python protocol (`server/protocol.py`)

```python
class StopCmd(RunCommandMetadata):
    model_config = ConfigDict(extra="forbid")

    type: Literal["stop_run"] = "stop_run"
    reason: str | None = Field(default=None, exclude_if=_is_none)
```

Add `StopCmd` to the `ClientMessage` union (line ~310, next to `PauseCmd`).

```python
class RunStoppedMsg(RunMessageMetadata):
    model_config = ConfigDict(extra="forbid")

    type: Literal["run_stopped"] = "run_stopped"
    command_id: str | None = Field(default=None, exclude_if=_is_none)
    reason: str | None = Field(default=None, exclude_if=_is_none)
```

Add `RunStoppedMsg` to the `ServerMessage` union (line ~212). (Follow the `AckMsg` `command_id`
`exclude_if=_is_none` convention.)

### A1.2: regenerate the TS artifacts

Run `python scripts/generate_protocol.py` (from the repo root, or wherever the script expects) to
regenerate `ts/src/tui/protocol.generated.ts` and `protocol/autocontext-protocol.json`. Confirm
`scripts/generate_protocol.py --check` then exits clean.

### A1.3: TS Zod mirror (`ts/src/server/protocol.ts`)

- Add `StopRunCmdSchema` (extends `RunCommandMetadataSchema` with `type: z.literal("stop_run")`,
  `reason: z.string().nullish()`) and register it in `ClientMessageSchema`.
- Add `RunStoppedMsgSchema` (extends `RunMessageMetadataSchema` with `type: z.literal("run_stopped")`,
  `command_id: z.string().nullish()`, `reason: z.string().nullish()`) and register it in
  `ServerMessageSchema`.
- Add `"stop_run"` to `PYTHON_SHARED_CLIENT_MESSAGE_TYPES` and `"run_stopped"` to
  `PYTHON_SHARED_SERVER_MESSAGE_TYPES` (NOT the `TYPESCRIPT_ONLY_*` lists, these are shared).

## Testing

- Python (`autocontext/tests/test_protocol.py`): `parse_client_message` round-trips a `stop_run` command
  (with and without `reason`); `RunStoppedMsg` serializes with `type: "run_stopped"` and correlates
  `command_id`; `export_json_schema()` includes both new types.
- Python parity: `test_protocol_parity.py` + `test_websocket_protocol_contract.py` stay green, and
  `scripts/generate_protocol.py --check` exits 0 (the regenerated file matches).
- TS (`ts/tests/server-protocol.test.ts`): `ClientMessageSchema` parses a `stop_run` command;
  `ServerMessageSchema` parses a `run_stopped` message; the shared-type lists include the two literals.
- TS parity (`ts/tests/websocket-protocol-contract.test.ts`): the contract test stays green with the two
  new shared message types.

## Documentation

CHANGELOG entry: the interactive-run protocol gains a `stop_run` client command and a `run_stopped`
terminal server receipt (AC-894 wire contract); cooperative-stop behavior and the `safe_run_stop_v1`
capability follow in the engine slices.

## Deferred (A2, A3)

- The controller stop primitive (`request_stop()` that wakes a paused run), the loop cooperative-boundary
  stop check, the first-terminal-wins status CAS, command dispatch + ack + `run_stopped` receipt,
  idempotent dedup + run-scope validation, and the `safe_run_stop_v1` capability advertisement, on the
  Python side (A2) and the TypeScript side (A3).

## Acceptance criteria advanced by this slice

- AC-894 "Preserve ... TypeScript/Python protocol parity": A1 lands the `stop_run` / `run_stopped` types
  on both packages with the parity gates green, the contract A2/A3 implement against.
