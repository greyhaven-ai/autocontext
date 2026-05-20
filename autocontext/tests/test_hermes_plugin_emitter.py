"""AC-707 (spike): Hermes plugin emitter prototype.

DDD/TDD coverage for the spike-prototype shape that a production
Hermes plugin can adopt without redesigning the surface:

* :class:`PluginEvent` (sealed-union-ish) is the per-hook payload
  shape: ``llm_call`` / ``tool_call`` / ``session_end``. Lightweight
  value types, no Hermes runtime dependency.
* :class:`LocalJsonlSink` writes one ProductionTrace JSONL row per
  finalized session into a local file. Fail-open: a write failure
  is recorded but never propagated to the caller (AC-707 safety
  requirement: "must never break a Hermes turn").
* :class:`HermesTraceEmitter` orchestrates ``record_llm_call`` /
  ``record_tool_call`` / ``finalize_session`` and routes through
  the existing :class:`RedactionPolicy` (DRY with AC-706) and
  :func:`production_traces.emit.build_trace` (DRY with AC-704
  curator ingest and AC-706 session ingest).
* Fail-open contract: any exception raised inside an emitter hook
  is swallowed and recorded so it cannot propagate into the
  Hermes turn that called the hook.
* Default mode is local-only; no network IO is performed by the
  prototype.

The spike is the *shape*, not the production wire-up. These tests
pin the contract a future production implementation must keep.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from autocontext.hermes.plugin_emitter import (
    HermesTraceEmitter,
    LLMCallEvent,
    LocalJsonlSink,
    PluginEmitterError,
    ToolCallEvent,
)
from autocontext.hermes.redaction import RedactionPolicy, UserPattern


def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# --- Event value types ----------------------------------------------------


def test_llm_call_event_carries_provider_model_prompt_response() -> None:
    event = LLMCallEvent(
        provider="anthropic",
        model="claude-sonnet-4-5",
        prompt="user prompt here",
        response="assistant response",
        latency_ms=1234,
    )
    assert event.provider == "anthropic"
    assert event.latency_ms == 1234


def test_tool_call_event_carries_name_args_error() -> None:
    event = ToolCallEvent(
        tool_name="run_bash",
        args={"cmd": "ls"},
        error=None,
        latency_ms=42,
    )
    assert event.tool_name == "run_bash"
    assert event.error is None


# --- LocalJsonlSink -------------------------------------------------------


def test_local_jsonl_sink_writes_one_line_per_finalized_session(tmp_path: Path) -> None:
    output = tmp_path / "traces.jsonl"
    sink = LocalJsonlSink(path=output)
    sink.write({"trace": "row-1"})
    sink.write({"trace": "row-2"})
    sink.close()
    rows = _load_jsonl(output)
    assert [r["trace"] for r in rows] == ["row-1", "row-2"]


def test_local_jsonl_sink_fail_open_when_path_is_unwritable(tmp_path: Path) -> None:
    """AC-707 safety: a sink-write failure must not raise into the
    caller. The sink records the error so an operator can audit it,
    but Hermes turns are untouched."""
    bogus = tmp_path / "does-not-exist-dir" / "traces.jsonl"
    sink = LocalJsonlSink(path=bogus, create_parents=False)
    # Must not raise even though the path is unwritable.
    sink.write({"trace": "doomed"})
    assert sink.errors, "expected a recorded error on unwritable path"
    assert isinstance(sink.errors[0], PluginEmitterError)


def test_local_jsonl_sink_creates_parent_directories_by_default(tmp_path: Path) -> None:
    output = tmp_path / "deep" / "nested" / "traces.jsonl"
    sink = LocalJsonlSink(path=output)
    sink.write({"trace": "row"})
    sink.close()
    assert output.exists()


# --- HermesTraceEmitter ---------------------------------------------------


def test_emitter_finalizes_a_session_into_a_production_trace(tmp_path: Path) -> None:
    output = tmp_path / "traces.jsonl"
    emitter = HermesTraceEmitter(
        sink=LocalJsonlSink(path=output),
        policy=RedactionPolicy(mode="standard"),
    )
    emitter.start_session(session_id="s1", agent_id="claude")
    emitter.record_llm_call(
        session_id="s1",
        event=LLMCallEvent(
            provider="anthropic",
            model="claude-sonnet-4-5",
            prompt="hi",
            response="hello",
            latency_ms=100,
        ),
    )
    emitter.finalize_session(session_id="s1")

    rows = _load_jsonl(output)
    assert len(rows) == 1
    trace = rows[0]
    # ProductionTrace shape: messages array with at least a system summary
    # plus the redacted LLM exchange.
    assert any(m.get("role") == "system" for m in trace["messages"])
    assert any(m.get("role") == "assistant" for m in trace["messages"])
    assert trace["metadata"]["source"] == "hermes.plugin"
    assert trace["metadata"]["session_id"] == "s1"


def test_emitter_redacts_llm_content_via_shared_policy(tmp_path: Path) -> None:
    """DRY: the prototype must reuse the RedactionPolicy from AC-706
    so a strict-mode user pattern behaves identically across the
    file importers and the plugin emitter."""
    output = tmp_path / "traces.jsonl"
    emitter = HermesTraceEmitter(
        sink=LocalJsonlSink(path=output),
        policy=RedactionPolicy(
            mode="strict",
            user_patterns=(UserPattern(name="ticket", pattern=re.compile(r"TKT-\d+")),),
        ),
    )
    emitter.start_session(session_id="s1", agent_id="claude")
    emitter.record_llm_call(
        session_id="s1",
        event=LLMCallEvent(
            provider="anthropic",
            model="claude-sonnet-4-5",
            prompt="key sk-ant-abcdef1234567890abcdef tkt TKT-42",
            response="ack TKT-99",
            latency_ms=10,
        ),
    )
    emitter.finalize_session(session_id="s1")
    serialized = json.dumps(_load_jsonl(output)[0])
    assert "sk-ant-" not in serialized
    assert "TKT-42" not in serialized
    assert "TKT-99" not in serialized
    assert "[REDACTED_API_KEY]" in serialized
    assert "[REDACTED_USER_PATTERN:ticket]" in serialized


def test_emitter_carries_tool_calls_into_the_finalized_trace(tmp_path: Path) -> None:
    output = tmp_path / "traces.jsonl"
    emitter = HermesTraceEmitter(
        sink=LocalJsonlSink(path=output),
        policy=RedactionPolicy(mode="standard"),
    )
    emitter.start_session(session_id="s1", agent_id="claude")
    emitter.record_tool_call(
        session_id="s1",
        event=ToolCallEvent(
            tool_name="run_bash",
            args={"cmd": "echo hi"},
            error=None,
            latency_ms=5,
        ),
    )
    emitter.finalize_session(session_id="s1")
    trace = _load_jsonl(output)[0]
    tool_calls = trace.get("toolCalls", []) or trace.get("tool_calls", [])
    assert any(t.get("toolName") == "run_bash" or t.get("tool_name") == "run_bash" for t in tool_calls)


def test_emitter_fail_open_when_record_llm_call_raises(tmp_path: Path) -> None:
    """AC-707: if redaction or trace assembly throws, the hook must
    swallow it and record the error rather than propagate into the
    Hermes turn."""
    output = tmp_path / "traces.jsonl"
    emitter = HermesTraceEmitter(
        sink=LocalJsonlSink(path=output),
        policy=RedactionPolicy(mode="standard"),
    )
    emitter.start_session(session_id="s1", agent_id="claude")

    # Force an internal error by passing a non-string content via a
    # value type that misuses the field. The emitter must not propagate.
    bad = LLMCallEvent(
        provider="anthropic",
        model="x",
        prompt=None,  # type: ignore[arg-type]  intentionally bad
        response=None,  # type: ignore[arg-type]
        latency_ms=0,
    )
    emitter.record_llm_call(session_id="s1", event=bad)
    assert emitter.errors, "expected the emitter to record the swallowed error"


def test_emitter_drops_finalize_calls_for_unknown_sessions(tmp_path: Path) -> None:
    """A late `finalize_session` for a session that was never started
    must not raise. Plugin lifecycles aren't strictly bracketed."""
    output = tmp_path / "traces.jsonl"
    emitter = HermesTraceEmitter(
        sink=LocalJsonlSink(path=output),
        policy=RedactionPolicy(mode="standard"),
    )
    emitter.finalize_session(session_id="never-started")
    assert _load_jsonl(output) == [] if output.exists() else True


def test_emitter_handles_concurrent_sessions(tmp_path: Path) -> None:
    """Two sessions interleaved should each finalize into their own
    trace; no event leaks across sessions."""
    output = tmp_path / "traces.jsonl"
    emitter = HermesTraceEmitter(
        sink=LocalJsonlSink(path=output),
        policy=RedactionPolicy(mode="standard"),
    )
    emitter.start_session(session_id="a", agent_id="claude")
    emitter.start_session(session_id="b", agent_id="claude")
    emitter.record_llm_call(
        session_id="a",
        event=LLMCallEvent(provider="anthropic", model="m", prompt="aaaa", response="A!", latency_ms=1),
    )
    emitter.record_llm_call(
        session_id="b",
        event=LLMCallEvent(provider="anthropic", model="m", prompt="bbbb", response="B!", latency_ms=1),
    )
    emitter.finalize_session(session_id="a")
    emitter.finalize_session(session_id="b")
    rows = _load_jsonl(output)
    assert len(rows) == 2
    by_id = {r["metadata"]["session_id"]: r for r in rows}
    serialized_a = json.dumps(by_id["a"])
    serialized_b = json.dumps(by_id["b"])
    assert "aaaa" in serialized_a and "bbbb" not in serialized_a
    assert "bbbb" in serialized_b and "aaaa" not in serialized_b


def test_emitter_does_no_network_io_in_default_mode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """AC-707 safety: the minimal emitter is local-only. Patch
    `socket.socket` and assert nothing tries to construct one."""
    import socket

    real_socket = socket.socket

    def _no_socket(*args: object, **kwargs: object) -> object:
        raise AssertionError("plugin emitter must not open sockets in default mode")

    monkeypatch.setattr(socket, "socket", _no_socket)
    try:
        output = tmp_path / "traces.jsonl"
        emitter = HermesTraceEmitter(
            sink=LocalJsonlSink(path=output),
            policy=RedactionPolicy(mode="standard"),
        )
        emitter.start_session(session_id="s1", agent_id="claude")
        emitter.record_llm_call(
            session_id="s1",
            event=LLMCallEvent(provider="anthropic", model="m", prompt="p", response="r", latency_ms=1),
        )
        emitter.finalize_session(session_id="s1")
    finally:
        monkeypatch.setattr(socket, "socket", real_socket)
    assert _load_jsonl(output), "expected the local sink to still produce a row"
