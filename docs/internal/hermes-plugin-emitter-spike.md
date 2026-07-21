# Hermes plugin emitter — AC-707 spike

## TL;DR

**Recommendation: DEFER.** The file importers we already shipped
(AC-704 / AC-706) cover the realistic operator scenarios at a
fraction of the surface area. A plugin emitter solves a real
problem (precise per-hook timing, tool-call boundaries that file
artifacts smear) but the cost — owning a Hermes runtime contract
across `hermes-agent` releases — is high enough that we should
not pay it until a concrete operator workflow demands it.

A working prototype shape is checked in at
[`autocontext/src/autocontext/hermes/plugin_emitter.py`](../autocontext/src/autocontext/hermes/plugin_emitter.py)
with TDD coverage in
[`tests/test_hermes_plugin_emitter.py`](../autocontext/tests/test_hermes_plugin_emitter.py).
When this ticket is revisited, the production plugin glues
Hermes's hook decorators to the orchestrator methods this module
already exposes.

## Why file importers are usually enough

Today we ship four file-based capture surfaces:

| Source                                | Importer                             | What it preserves                                |
| ------------------------------------- | ------------------------------------ | ------------------------------------------------ |
| `<home>/logs/curator/**/run.json`     | `autoctx hermes ingest-curator`      | Curator decision lists, counts, auto-transitions |
| `<home>/state.db`                     | `autoctx hermes ingest-sessions`     | Sessions + messages, redacted, schema-drift safe |
| `trajectory_samples.jsonl` (& failed) | `autoctx hermes ingest-trajectories` | ShareGPT-like trajectories, redacted             |
| AC-705 curator-decisions export       | `autoctx hermes export-dataset`      | Strong-label training rows for the advisor       |

Operationally this is enough for:

- training the AC-708 advisor (decisions are pre-baked by Curator);
- replaying long-form sessions (the SQLite store keeps the bytes);
- auditing what changed in `~/.hermes/skills/` over time (curator
  reports are the source of truth).

A plugin emitter does **not** unlock any of those use cases. It
unlocks more precise _timing_, _tool-call boundaries_, and
_provider usage_ than the file artifacts retain. Whether that
extra fidelity is worth the maintenance cost is the open
question, and the rest of this doc tries to answer it honestly.

## What a plugin emitter would actually give us

Three things the file importers cannot:

1. **Sub-second timing.** `state.db` records per-message
   timestamps but not the gap between "prompt sent" and "first
   token received" or per-tool-call latency. The emitter can
   capture these from `pre_*` / `post_*` hook pairs.
2. **Tool-call structure.** `state.db` stores tool calls as
   serialized strings; the plugin sees the structured `ToolCall`
   object (name, args, error, duration) directly.
3. **Provider usage.** Hermes records the provider name on the
   session row, but not per-call token counts or rate-limit
   metadata. The plugin sees the provider's response object.

The plugin also gives us a single funnel for **future remote
sinks** (OTLP, HTTP, object store) without building four parallel
file importers when we add a fifth artifact type.

## What a plugin emitter would cost

- **Cross-package contract.** The plugin lives in (or shims to)
  `hermes-agent`'s plugin API. Every Hermes minor release that
  reshapes the hook payloads or the registration decorator becomes
  a coordinated release for the plugin too. Hermes v0.12's
  observability hooks are documented but not contractually
  stable.
- **Operational surface.** Operators have to opt the plugin into
  every Hermes install (one extra config block, one extra restart,
  one extra failure mode at startup). The file importers run on
  demand from `autoctx hermes ingest-*`; no agent-side install
  required.
- **Privacy posture.** Plugin emitters see live prompts and
  responses _before_ Hermes writes them anywhere. We'd be the
  first writer of that content to disk; the AC-706 file importers
  have the benefit of running over content the operator already
  chose to retain in `state.db`.
- **Concurrency.** Hermes runs hooks on the agent's hot path.
  Sloppy work in the emitter directly slows turns. Even with the
  fail-open posture we pin in tests, an emitter that holds the
  event loop is a real risk that the file importers don't carry.

## Options evaluated

### Option A — implement now as an autocontext-owned plugin

Plugin module ships in this repo (or a sibling
`autocontext-hermes-plugin` package) that depends on
`hermes-agent`. Operators install via `pip install
autocontext-hermes-plugin` plus a one-line Hermes config block.

- **Pro:** maximum fidelity available today; covers the three
  fidelity wins above.
- **Con:** we own the Hermes API drift forever, including for
  hooks we're not even sure are stable. We'd be the first writer
  of raw content to disk in the operator's environment.

### Option B — defer to a Hermes-upstream plugin

Propose the emitter upstream as a bundled Hermes plugin (similar
to the existing Langfuse plugin). Operators would get it via
their Hermes install with no extra package.

- **Pro:** Hermes owns the hook payload; we keep the autocontext
  side narrow.
- **Con:** requires Hermes maintainer engagement and a public
  schema for the autocontext-side sink. Not a thing we can
  unilaterally land.

### Option C — defer entirely until a concrete demand lands

Keep this prototype as a tested shape. Revisit when an operator
brings a workflow where the file importers genuinely fall short.

- **Pro:** zero ongoing maintenance cost. The prototype shape
  already exists in tests, so revisiting is days not weeks.
- **Con:** we leak the fidelity wins to whatever third-party
  observability the operator is already running.

## Recommendation

**Option C (defer).** Reasons:

- The advisor pipeline (AC-708 / AC-709) is the active payoff
  thread, and it consumes file-importer outputs. No part of it
  needs sub-second timing.
- Hermes's plugin API is not yet documented as version-stable. We
  would be writing against a moving contract.
- The prototype in `hermes/plugin_emitter.py` plus its 12 tests
  already pin the shape a future production implementation must
  honor. If we revisit, the work is "glue Hermes decorators to
  the orchestrator methods" — a small ticket, not a green-field
  spike.

When to revisit:

- An operator presents a concrete workflow where file-importer
  fidelity demonstrably falls short (e.g. per-tool latency
  attribution).
- Hermes publishes a stable plugin API contract.
- We add a non-file sink (OTLP, HTTP) and want a single funnel
  to feed it.

## Prototype shape (worked example)

The module exposes:

```python
from autocontext.hermes.plugin_emitter import (
    HermesTraceEmitter,
    LocalJsonlSink,
    LLMCallEvent,
    ToolCallEvent,
)
from autocontext.hermes.redaction import RedactionPolicy

emitter = HermesTraceEmitter(
    sink=LocalJsonlSink(path=Path("/.../traces.jsonl")),
    policy=RedactionPolicy(mode="standard"),
)

# Bound from a future Hermes plugin's hook decorators:

@hermes.hook("on_session_start")          # pseudocode
def on_start(session): emitter.start_session(
    session_id=session.id, agent_id=session.agent_id,
)

@hermes.hook("post_llm_call")             # pseudocode
def on_llm(session, call): emitter.record_llm_call(
    session_id=session.id,
    event=LLMCallEvent(
        provider=call.provider, model=call.model,
        prompt=call.prompt, response=call.response,
        latency_ms=call.latency_ms,
    ),
)

@hermes.hook("post_tool_call")            # pseudocode
def on_tool(session, tool): emitter.record_tool_call(
    session_id=session.id,
    event=ToolCallEvent(
        tool_name=tool.name, args=tool.args,
        error=tool.error, latency_ms=tool.latency_ms,
    ),
)

@hermes.hook("on_session_finalize")       # pseudocode
def on_end(session): emitter.finalize_session(session_id=session.id)
```

The orchestrator is fail-open: every hook body sits inside
`try / except Exception` so a plugin defect cannot break a
Hermes turn. The sink does the same for its write path.

## Safety properties pinned by tests

| Property                            | Test                                                       |
| ----------------------------------- | ---------------------------------------------------------- |
| Sink failure does not propagate     | `test_local_jsonl_sink_fail_open_when_path_is_unwritable`  |
| Bad event does not propagate        | `test_emitter_fail_open_when_record_llm_call_raises`       |
| Late finalize is silently ignored   | `test_emitter_drops_finalize_calls_for_unknown_sessions`   |
| No event leaks across sessions      | `test_emitter_handles_concurrent_sessions`                 |
| No network IO in default mode       | `test_emitter_does_no_network_io_in_default_mode`          |
| Redaction reuses the AC-706 policy  | `test_emitter_redacts_llm_content_via_shared_policy`       |
| Trace shape matches AC-704 / AC-706 | `test_emitter_finalizes_a_session_into_a_production_trace` |

## Follow-up ticket if Option A is revisited

Suggested scope (bounded):

- New package (or sibling module) `autocontext-hermes-plugin`
  that depends on the Hermes plugin SDK.
- Hook decorators that adapt Hermes's `Session` / `LLMCall` /
  `ToolCall` types into the existing `LLMCallEvent` /
  `ToolCallEvent` value types in this spike.
- One CI smoke test that boots a stub Hermes session with the
  plugin registered and asserts a JSONL line appears.
- No new sinks; the file sink shipped here is enough for the
  first release. Remote sinks behind the `TraceSink` protocol are
  a separate ticket.

Estimated effort if revisited: ~3 days for a working plugin

- smoke test, conditional on the Hermes API being stable when we
  return to it.
