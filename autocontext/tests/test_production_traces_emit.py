"""Tests for autocontext.production_traces.emit.

``build_trace`` argument names mirror spec §4 ``ProductionTrace`` fields verbatim
(DDD discipline). ``write_jsonl`` and ``TraceBatch`` follow spec §6.1 directory
layout and §6.5 dedup key semantics.
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

ULID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")


def _timing(offset_seconds: int = 0) -> dict[str, Any]:
    start = datetime(2026, 4, 17, 12, 0, offset_seconds, tzinfo=UTC)
    end = start + timedelta(seconds=1)
    return {
        "startedAt": start.isoformat().replace("+00:00", "Z"),
        "endedAt": end.isoformat().replace("+00:00", "Z"),
        "latencyMs": 1000,
    }


def _messages() -> list[dict[str, Any]]:
    return [{"role": "user", "content": "hello", "timestamp": "2026-04-17T12:00:00.000Z"}]


def _usage() -> dict[str, Any]:
    return {"tokensIn": 10, "tokensOut": 5}


def _env() -> dict[str, Any]:
    return {"environmentTag": "production", "appId": "my-app"}


# ---- build_trace ----


def test_build_trace_with_minimum_args_returns_valid_trace() -> None:
    from autocontext.production_traces import build_trace, validate_production_trace

    trace = build_trace(
        provider="anthropic",
        model="claude-sonnet-4-20250514",
        messages=_messages(),
        timing=_timing(),
        usage=_usage(),
        env=_env(),
    )
    assert isinstance(trace, dict)
    # Pydantic round-trip: result must validate against the schema.
    parsed = validate_production_trace(trace)
    assert parsed.provider.name == "anthropic"
    assert parsed.model == "claude-sonnet-4-20250514"


def test_build_trace_generates_ulid_trace_id_by_default() -> None:
    from autocontext.production_traces import build_trace

    trace = build_trace(
        provider="openai",
        model="gpt-4o",
        messages=_messages(),
        timing=_timing(),
        usage=_usage(),
        env=_env(),
    )
    assert ULID_RE.fullmatch(trace["traceId"]) is not None


def test_build_trace_honors_explicit_trace_id() -> None:
    from autocontext.production_traces import build_trace

    explicit = "01KFDQ9XZ3M7RT2V8K1PHY4BNC"
    trace = build_trace(
        provider="openai",
        model="gpt-4o",
        messages=_messages(),
        timing=_timing(),
        usage=_usage(),
        env=_env(),
        trace_id=explicit,
    )
    assert trace["traceId"] == explicit


def test_build_trace_default_source_is_py_sdk() -> None:
    from autocontext.production_traces import build_trace

    trace = build_trace(
        provider="anthropic",
        model="claude-sonnet-4-20250514",
        messages=_messages(),
        timing=_timing(),
        usage=_usage(),
        env=_env(),
    )
    assert trace["source"]["emitter"] == "sdk"
    assert trace["source"]["sdk"]["name"] == "autocontext-py"
    assert isinstance(trace["source"]["sdk"]["version"], str)
    assert len(trace["source"]["sdk"]["version"]) > 0


def test_build_trace_accepts_optional_fields() -> None:
    from autocontext.production_traces import build_trace, validate_production_trace

    trace = build_trace(
        provider="anthropic",
        model="claude-sonnet-4-20250514",
        messages=_messages(),
        timing=_timing(),
        usage=_usage(),
        env=_env(),
        tool_calls=[{"toolName": "search", "args": {"q": "foo"}}],
        session={"userIdHash": "a" * 64, "sessionIdHash": "b" * 64, "requestId": "r-1"},
        outcome={"label": "success", "score": 0.9},
        feedback_refs=[
            {"kind": "thumbs", "submittedAt": "2026-04-17T12:05:00.000Z", "ref": "fb-1"}
        ],
        links={"scenarioId": "grid_ctf", "runId": "run-42"},
        redactions=[
            {
                "path": "/messages/0/content",
                "reason": "pii-email",
                "detectedBy": "ingestion",
                "detectedAt": "2026-04-17T12:00:02.000Z",
            }
        ],
        metadata={"customer": "acme-corp"},
    )
    parsed = validate_production_trace(trace)
    assert parsed.outcome is not None and parsed.outcome.label == "success"
    assert parsed.links.scenarioId == "grid_ctf"
    assert len(parsed.toolCalls) == 1


def test_build_trace_defaults_toolcalls_and_feedbackrefs_to_empty_lists() -> None:
    from autocontext.production_traces import build_trace

    trace = build_trace(
        provider="anthropic",
        model="claude-sonnet-4-20250514",
        messages=_messages(),
        timing=_timing(),
        usage=_usage(),
        env=_env(),
    )
    assert trace["toolCalls"] == []
    assert trace["feedbackRefs"] == []
    assert trace["redactions"] == []
    assert trace["links"] == {}


def test_build_trace_sets_schema_version_1_0() -> None:
    from autocontext.production_traces import build_trace

    trace = build_trace(
        provider="anthropic",
        model="claude-sonnet-4-20250514",
        messages=_messages(),
        timing=_timing(),
        usage=_usage(),
        env=_env(),
    )
    assert trace["schemaVersion"] == "1.0"


def test_build_trace_rejects_invalid_input_via_pydantic() -> None:
    from pydantic import ValidationError

    from autocontext.production_traces import build_trace

    with pytest.raises(ValidationError):
        build_trace(
            provider="aliens",  # not in enum
            model="gpt-4",
            messages=_messages(),
            timing=_timing(),
            usage=_usage(),
            env=_env(),
        )


def test_build_trace_rejects_empty_messages() -> None:
    from pydantic import ValidationError

    from autocontext.production_traces import build_trace

    with pytest.raises(ValidationError):
        build_trace(
            provider="anthropic",
            model="claude-sonnet-4-20250514",
            messages=[],
            timing=_timing(),
            usage=_usage(),
            env=_env(),
        )


def test_build_trace_allows_caller_to_mutate_returned_dict() -> None:
    # build_trace returns a plain dict (not a frozen Pydantic instance) so
    # customer code can merge/mutate freely.
    from autocontext.production_traces import build_trace

    trace = build_trace(
        provider="anthropic",
        model="claude-sonnet-4-20250514",
        messages=_messages(),
        timing=_timing(),
        usage=_usage(),
        env=_env(),
    )
    trace["metadata"] = {"note": "mutated"}
    assert trace["metadata"] == {"note": "mutated"}


# ---- write_jsonl ----


def test_write_jsonl_writes_single_trace_to_incoming_path(tmp_path: Path) -> None:
    from autocontext.production_traces import build_trace, write_jsonl

    trace = build_trace(
        provider="anthropic",
        model="claude-sonnet-4-20250514",
        messages=_messages(),
        timing=_timing(),
        usage=_usage(),
        env=_env(),
    )
    path = write_jsonl(trace, cwd=tmp_path)
    assert path.is_file()
    # Layout: .autocontext/production-traces/incoming/YYYY-MM-DD/<batch>.jsonl
    parts = path.relative_to(tmp_path).parts
    assert parts[0] == ".autocontext"
    assert parts[1] == "production-traces"
    assert parts[2] == "incoming"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", parts[3])
    assert parts[4].endswith(".jsonl")
    # Batch id in filename is a ULID.
    batch_id = parts[4].removesuffix(".jsonl")
    assert ULID_RE.fullmatch(batch_id) is not None


def test_write_jsonl_writes_list_of_traces_one_per_line(tmp_path: Path) -> None:
    from autocontext.production_traces import build_trace, write_jsonl

    traces = [
        build_trace(
            provider="anthropic",
            model="claude-sonnet-4-20250514",
            messages=_messages(),
            timing=_timing(i),
            usage=_usage(),
            env=_env(),
        )
        for i in range(3)
    ]
    path = write_jsonl(traces, cwd=tmp_path)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    parsed = [json.loads(line) for line in lines]
    assert [t["traceId"] for t in parsed] == [t["traceId"] for t in traces]


def test_write_jsonl_date_partitions_by_first_trace_started_at(tmp_path: Path) -> None:
    from autocontext.production_traces import build_trace, write_jsonl

    trace = build_trace(
        provider="anthropic",
        model="claude-sonnet-4-20250514",
        messages=_messages(),
        timing={
            "startedAt": "2025-12-31T23:59:59Z",
            "endedAt": "2026-01-01T00:00:00Z",
            "latencyMs": 1000,
        },
        usage=_usage(),
        env=_env(),
    )
    path = write_jsonl(trace, cwd=tmp_path)
    # Partition derived from UTC date of first trace's startedAt.
    assert "2025-12-31" in str(path)


def test_write_jsonl_uses_explicit_batch_id(tmp_path: Path) -> None:
    from autocontext.production_traces import build_trace, write_jsonl

    trace = build_trace(
        provider="anthropic",
        model="claude-sonnet-4-20250514",
        messages=_messages(),
        timing=_timing(),
        usage=_usage(),
        env=_env(),
    )
    batch_id = "01KFDQ9XZ3M7RT2V8K1PHY4BNC"
    path = write_jsonl(trace, cwd=tmp_path, batch_id=batch_id)
    assert path.name == f"{batch_id}.jsonl"


def test_write_jsonl_honors_autocontext_registry_path_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autocontext.production_traces import build_trace, write_jsonl

    trace = build_trace(
        provider="anthropic",
        model="claude-sonnet-4-20250514",
        messages=_messages(),
        timing=_timing(),
        usage=_usage(),
        env=_env(),
    )
    monkeypatch.setenv("AUTOCONTEXT_REGISTRY_PATH", str(tmp_path))
    path = write_jsonl(trace)  # cwd omitted — should use env var
    assert str(path).startswith(str(tmp_path))


def test_write_jsonl_defaults_to_cwd_when_no_env_or_arg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autocontext.production_traces import build_trace, write_jsonl

    trace = build_trace(
        provider="anthropic",
        model="claude-sonnet-4-20250514",
        messages=_messages(),
        timing=_timing(),
        usage=_usage(),
        env=_env(),
    )
    monkeypatch.delenv("AUTOCONTEXT_REGISTRY_PATH", raising=False)
    monkeypatch.chdir(tmp_path)
    path = write_jsonl(trace)
    assert path.is_absolute()
    # The returned path should live under the cwd we chdir'd to.
    assert str(path).startswith(str(tmp_path.resolve()))


def test_write_jsonl_creates_intermediate_directories(tmp_path: Path) -> None:
    from autocontext.production_traces import build_trace, write_jsonl

    trace = build_trace(
        provider="anthropic",
        model="claude-sonnet-4-20250514",
        messages=_messages(),
        timing=_timing(),
        usage=_usage(),
        env=_env(),
    )
    # tmp_path has no .autocontext yet.
    assert not (tmp_path / ".autocontext").exists()
    write_jsonl(trace, cwd=tmp_path)
    assert (tmp_path / ".autocontext" / "production-traces" / "incoming").is_dir()


def test_write_jsonl_produces_valid_jsonl_roundtrip(tmp_path: Path) -> None:
    from autocontext.production_traces import build_trace, validate_production_trace, write_jsonl

    trace = build_trace(
        provider="anthropic",
        model="claude-sonnet-4-20250514",
        messages=_messages(),
        timing=_timing(),
        usage=_usage(),
        env=_env(),
    )
    path = write_jsonl(trace, cwd=tmp_path)
    line = path.read_text(encoding="utf-8").splitlines()[0]
    roundtrip = json.loads(line)
    # Must revalidate cleanly on the way back in.
    parsed = validate_production_trace(roundtrip)
    assert parsed.traceId == trace["traceId"]


# ---- TraceBatch ----


def test_trace_batch_accumulates_and_reports_length() -> None:
    from autocontext.production_traces import TraceBatch, build_trace

    batch = TraceBatch()
    assert len(batch) == 0
    for _ in range(5):
        batch.add(
            build_trace(
                provider="anthropic",
                model="claude-sonnet-4-20250514",
                messages=_messages(),
                timing=_timing(),
                usage=_usage(),
                env=_env(),
            )
        )
    assert len(batch) == 5


def test_trace_batch_flush_writes_accumulated_and_empties(tmp_path: Path) -> None:
    from autocontext.production_traces import TraceBatch, build_trace

    batch = TraceBatch()
    for i in range(3):
        batch.add(
            build_trace(
                provider="anthropic",
                model="claude-sonnet-4-20250514",
                messages=_messages(),
                timing=_timing(i),
                usage=_usage(),
                env=_env(),
            )
        )
    path = batch.flush(cwd=tmp_path)
    assert path is not None and path.is_file()
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    # After flush, the batch is empty; flushing again returns None.
    assert len(batch) == 0
    assert batch.flush(cwd=tmp_path) is None


def test_trace_batch_flush_empty_returns_none(tmp_path: Path) -> None:
    from autocontext.production_traces import TraceBatch

    batch = TraceBatch()
    assert batch.flush(cwd=tmp_path) is None


def test_trace_batch_filename_is_valid_ulid(tmp_path: Path) -> None:
    from autocontext.production_traces import TraceBatch, build_trace

    batch = TraceBatch()
    batch.add(
        build_trace(
            provider="anthropic",
            model="claude-sonnet-4-20250514",
            messages=_messages(),
            timing=_timing(),
            usage=_usage(),
            env=_env(),
        )
    )
    path = batch.flush(cwd=tmp_path)
    assert path is not None
    stem = path.stem
    assert ULID_RE.fullmatch(stem) is not None


def test_write_jsonl_json_is_utf8_encoded_no_ascii_escape(tmp_path: Path) -> None:
    # Customer content may contain non-ASCII (emoji, CJK). JSONL must preserve
    # the bytes cleanly; we write utf-8, not `ensure_ascii=True`.
    from autocontext.production_traces import build_trace, write_jsonl

    unicode_msg = [
        {"role": "user", "content": "héllo 世界 🚀", "timestamp": "2026-04-17T12:00:00.000Z"}
    ]
    trace = build_trace(
        provider="anthropic",
        model="claude-sonnet-4-20250514",
        messages=unicode_msg,
        timing=_timing(),
        usage=_usage(),
        env=_env(),
    )
    path = write_jsonl(trace, cwd=tmp_path)
    raw = path.read_bytes()
    assert "世界".encode() in raw
    assert "🚀".encode() in raw


def test_write_jsonl_str_cwd_accepted(tmp_path: Path) -> None:
    from autocontext.production_traces import build_trace, write_jsonl

    trace = build_trace(
        provider="anthropic",
        model="claude-sonnet-4-20250514",
        messages=_messages(),
        timing=_timing(),
        usage=_usage(),
        env=_env(),
    )
    path = write_jsonl(trace, cwd=str(tmp_path))
    assert path.is_file()


def test_write_jsonl_returns_absolute_path(tmp_path: Path) -> None:
    from autocontext.production_traces import build_trace, write_jsonl

    # Even if a relative cwd is supplied, the returned path should be absolute
    # so customer code can print / log it without ambiguity.
    trace = build_trace(
        provider="anthropic",
        model="claude-sonnet-4-20250514",
        messages=_messages(),
        timing=_timing(),
        usage=_usage(),
        env=_env(),
    )
    # Use the tmp_path as cwd to avoid polluting the current dir.
    old = os.getcwd()
    try:
        os.chdir(tmp_path)
        path = write_jsonl(trace, cwd=".")
        assert path.is_absolute()
    finally:
        os.chdir(old)
