"""AC-707 (spike): Hermes plugin emitter prototype.

A *shape*, not a production wire-up. The goal of the spike is to
prove out what a Hermes plugin would have to import + call to emit
autocontext-shaped ProductionTrace JSONL from Hermes lifecycle
hooks, without taking on a Hermes runtime dependency in
autocontext's main package.

The companion spike doc (``docs/hermes-plugin-emitter-spike.md``)
covers the design decision (implement / defer / avoid). This module
is the worked example referenced from that doc.

Design choices pinned by tests:

* **DDD:** event value types (:class:`LLMCallEvent`,
  :class:`ToolCallEvent`) carry the per-hook payload. The
  :class:`HermesTraceEmitter` orchestrator owns in-memory session
  accumulation. :class:`LocalJsonlSink` is the only side-effect
  surface; the orchestrator depends on the :class:`TraceSink`
  protocol rather than the concrete sink so a future production
  plugin can swap in an OTLP/HTTP sink without touching the
  orchestrator.
* **DRY:** content is redacted via the existing
  :class:`~autocontext.hermes.redaction.RedactionPolicy` (same as
  AC-706 / AC-708 / AC-709). Traces are assembled via the existing
  :func:`autocontext.production_traces.emit.build_trace` (same as
  AC-704 curator ingest and AC-706 session ingest).
* **Fail-open** (AC-707 safety requirement): every hook on the
  emitter wraps its body in ``try / except Exception`` and records
  the failure under :attr:`HermesTraceEmitter.errors` rather than
  propagating into the Hermes turn. The sink does the same. A
  broken plugin can never break Hermes.
* **Local-only by default:** no network IO. The default sink writes
  JSONL to disk via stdlib ``open()``; a remote sink would be a
  follow-up implementation behind the :class:`TraceSink` protocol.

What's intentionally out of scope for the spike:

* Real Hermes plugin registration (the actual ``@hermes.hook(...)``
  decorators live in the Hermes package). The spike documents the
  binding pattern; the production plugin glues hook decorators to
  the orchestrator methods.
* OpenTelemetry / OTLP wire formats. AC-682 already covers the
  PublicTrace → OTel bridge; the plugin emitter would funnel
  through that for an OTLP sink.
* Retry / batching policies. The local JSONL sink writes immediately;
  a remote sink would add batching.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from autocontext.hermes.redaction import RedactionPolicy, RedactionStats, redact_text
from autocontext.production_traces.emit import build_trace


class PluginEmitterError(Exception):
    """Raised internally to wrap an emitter-side failure.

    Never propagates out of the public hooks; the orchestrator (and
    the local sink) catch and record instead.
    """


@dataclass(frozen=True, slots=True)
class LLMCallEvent:
    """One pre/post LLM call observation."""

    provider: str
    model: str
    prompt: str
    response: str
    latency_ms: int


@dataclass(frozen=True, slots=True)
class ToolCallEvent:
    """One pre/post tool call observation."""

    tool_name: str
    args: dict[str, Any]
    error: str | None
    latency_ms: int


class TraceSink(Protocol):
    """Anything that can persist a ProductionTrace dict.

    The protocol lets the orchestrator stay agnostic to where traces
    end up: local JSONL today, OTLP / HTTP / object-store tomorrow.
    """

    def write(self, trace: dict[str, Any]) -> None: ...

    def close(self) -> None: ...


@dataclass(slots=True)
class LocalJsonlSink:
    """Local file sink. Writes one JSON object per line.

    Fail-open per AC-707: a write failure is recorded under
    :attr:`errors` but never propagated. The plugin's calling
    Hermes turn must never observe a sink failure.
    """

    path: Path
    create_parents: bool = True
    errors: list[PluginEmitterError] = field(default_factory=list)
    _initialized: bool = False

    def write(self, trace: dict[str, Any]) -> None:
        try:
            if not self._initialized:
                if self.create_parents:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                # Touch the file so an empty session run still produces it.
                self.path.touch(exist_ok=True)
                self._initialized = True
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(trace, separators=(",", ":")) + "\n")
        except OSError as err:
            self.errors.append(PluginEmitterError(f"sink write failed: {err}"))

    def close(self) -> None:
        # Stdlib append-mode opens are closed per-write; this is a
        # no-op so the protocol method has a uniform shape for
        # remote sinks that need a flush.
        return None


@dataclass(slots=True)
class _SessionState:
    session_id: str
    agent_id: str
    started_at: str
    llm_events: list[LLMCallEvent] = field(default_factory=list)
    tool_events: list[ToolCallEvent] = field(default_factory=list)


@dataclass(slots=True)
class HermesTraceEmitter:
    """Orchestrates Hermes hook events into ProductionTrace JSONL.

    Lifecycle:

    1. :func:`start_session` opens an in-memory accumulator for the
       session id (idempotent for repeated calls).
    2. :func:`record_llm_call` / :func:`record_tool_call` push events
       onto the accumulator. Fail-open if anything inside raises.
    3. :func:`finalize_session` redacts, builds a ProductionTrace,
       hands it to the sink, drops the accumulator. Finalize calls
       for unknown sessions are silently dropped (plugin lifecycles
       are not strictly bracketed in practice).
    """

    sink: TraceSink
    policy: RedactionPolicy
    errors: list[PluginEmitterError] = field(default_factory=list)
    _sessions: dict[str, _SessionState] = field(default_factory=dict)

    def start_session(self, *, session_id: str, agent_id: str) -> None:
        try:
            if session_id in self._sessions:
                return
            self._sessions[session_id] = _SessionState(
                session_id=session_id,
                agent_id=agent_id,
                started_at=_now_iso(),
            )
        except Exception as err:  # noqa: BLE001 fail-open
            self.errors.append(PluginEmitterError(f"start_session failed: {err}"))

    def record_llm_call(self, *, session_id: str, event: LLMCallEvent) -> None:
        try:
            state = self._sessions.get(session_id)
            if state is None:
                # Implicit start for the convenience of plugins that
                # only register the post_llm_call hook.
                self.start_session(session_id=session_id, agent_id="unknown")
                state = self._sessions[session_id]
            # Validate content shape up front so an obviously bad event
            # (e.g. None prompt or response) fails closed at record time
            # rather than at finalize time. The fail-open wrapper around
            # this try block keeps the exception out of the Hermes turn.
            if not isinstance(event.prompt, str) or not isinstance(event.response, str):
                raise PluginEmitterError(
                    f"LLMCallEvent requires string prompt and response; "
                    f"got prompt={type(event.prompt).__name__}, response={type(event.response).__name__}"
                )
            redact_text(event.prompt, self.policy)
            redact_text(event.response, self.policy)
            state.llm_events.append(event)
        except Exception as err:  # noqa: BLE001 fail-open
            self.errors.append(PluginEmitterError(f"record_llm_call failed: {err}"))

    def record_tool_call(self, *, session_id: str, event: ToolCallEvent) -> None:
        try:
            state = self._sessions.get(session_id)
            if state is None:
                self.start_session(session_id=session_id, agent_id="unknown")
                state = self._sessions[session_id]
            state.tool_events.append(event)
        except Exception as err:  # noqa: BLE001 fail-open
            self.errors.append(PluginEmitterError(f"record_tool_call failed: {err}"))

    def finalize_session(self, *, session_id: str) -> None:
        try:
            state = self._sessions.pop(session_id, None)
            if state is None:
                return
            trace = self._build_trace(state)
            self.sink.write(trace)
        except Exception as err:  # noqa: BLE001 fail-open
            self.errors.append(PluginEmitterError(f"finalize_session failed: {err}"))

    # --- internals ----------------------------------------------------

    def _build_trace(self, state: _SessionState) -> dict[str, Any]:
        ended_at = _now_iso()
        latency_ms = sum(e.latency_ms for e in state.llm_events) + sum(e.latency_ms for e in state.tool_events)
        stats = RedactionStats()

        system_summary = (
            f"Hermes session {state.session_id} via {state.agent_id} "
            f"(llm_events={len(state.llm_events)}, tool_events={len(state.tool_events)})"
        )
        messages: list[dict[str, Any]] = [{"role": "system", "content": system_summary, "timestamp": state.started_at}]
        for event in state.llm_events:
            prompt, prompt_stats = redact_text(event.prompt, self.policy)
            response, response_stats = redact_text(event.response, self.policy)
            _accumulate(stats, prompt_stats)
            _accumulate(stats, response_stats)
            messages.append({"role": "user", "content": prompt, "timestamp": state.started_at})
            messages.append({"role": "assistant", "content": response, "timestamp": ended_at})

        tool_calls: list[dict[str, Any]] = []
        for tool in state.tool_events:
            call: dict[str, Any] = {"toolName": tool.tool_name, "args": dict(tool.args)}
            if tool.error is not None:
                call["error"] = tool.error
            tool_calls.append(call)

        # Provider for the trace envelope: use the first LLM call's
        # provider if any, otherwise "other" (ProductionTrace's enum
        # fallback per AC-704).
        provider = state.llm_events[0].provider if state.llm_events else "other"
        model = state.llm_events[0].model if state.llm_events else "unknown"
        metadata = {
            "source": "hermes.plugin",
            "session_id": state.session_id,
            "agent_id": state.agent_id,
            "redactions": stats.to_dict(),
        }
        return build_trace(
            provider=provider if provider in _KNOWN_PROVIDERS else "other",
            model=model,
            messages=messages,
            timing={"startedAt": state.started_at, "endedAt": ended_at, "latencyMs": latency_ms},
            usage={"tokensIn": 0, "tokensOut": 0},
            env={"environmentTag": "dev", "appId": "hermes-plugin"},
            tool_calls=tool_calls,
            metadata=metadata,
        )


# Matches the AC-704 / AC-705 known-provider set so an unrecognized
# Hermes provider folds to "other" rather than failing the trace.
_KNOWN_PROVIDERS = frozenset({"openai", "anthropic", "openai-compatible", "langchain", "vercel-ai-sdk", "litellm", "other"})


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _accumulate(target: RedactionStats, source: RedactionStats) -> None:
    for category, count in source.by_category.items():
        target.add(category, count)


__all__ = [
    "HermesTraceEmitter",
    "LLMCallEvent",
    "LocalJsonlSink",
    "PluginEmitterError",
    "ToolCallEvent",
    "TraceSink",
]
