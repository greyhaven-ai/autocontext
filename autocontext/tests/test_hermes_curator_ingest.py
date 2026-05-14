"""AC-704: ingest Hermes curator reports into autocontext ProductionTrace JSONL.

Fixtures under ``tests/fixtures/hermes_curator/`` mimic Hermes v0.12
curator ``run.json`` shapes (normal run with all action types, consolidation
only, auto-transition only with no actions, malformed JSON). The ingest
pipeline must:

* tolerate missing fields with warnings, not hard failure,
* synthesize at least one message per trace (ProductionTrace requires it),
* preserve curator metadata (counts, action lists, auto-transitions) for
  downstream dataset exporters,
* validate every emitted trace against the ProductionTrace schema.
"""

from __future__ import annotations

import json
from pathlib import Path

from autocontext.hermes.curator_ingest import (
    IngestSummary,
    ingest_curator_reports,
)

from autocontext.production_traces.contract.models import ProductionTrace

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "hermes_curator"


def _make_hermes_home(tmp_path: Path, *fixture_dirs: str) -> Path:
    """Lay out a fake Hermes home with curator run reports under
    ``logs/curator/<name>/run.json`` so the ingest pipeline finds them."""

    home = tmp_path / "hermes-home"
    curator_root = home / "logs" / "curator"
    curator_root.mkdir(parents=True)
    for name in fixture_dirs:
        src = FIXTURE_ROOT / name / "run.json"
        dest_dir = curator_root / name
        dest_dir.mkdir(parents=True)
        (dest_dir / "run.json").write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return home


def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_ingest_emits_valid_production_trace_per_run(tmp_path: Path) -> None:
    home = _make_hermes_home(tmp_path, "normal-run")
    output = tmp_path / "out.jsonl"

    summary = ingest_curator_reports(home=home, output=output)

    assert isinstance(summary, IngestSummary)
    assert summary.runs_read == 1
    assert summary.traces_written == 1
    assert summary.skipped == 0

    traces = _load_jsonl(output)
    assert len(traces) == 1
    # Validate against the canonical ProductionTrace schema; any field
    # divergence raises ValidationError on construction.
    ProductionTrace.model_validate(traces[0])


def test_normal_run_carries_provider_model_and_curator_metadata(tmp_path: Path) -> None:
    home = _make_hermes_home(tmp_path, "normal-run")
    output = tmp_path / "out.jsonl"
    ingest_curator_reports(home=home, output=output)
    trace = _load_jsonl(output)[0]

    assert trace["provider"]["name"] == "anthropic"
    assert trace["model"] == "claude-sonnet-4-5"
    # Curator action counts land in metadata for downstream dataset
    # exporters (AC-705 will consume this shape).
    assert trace["metadata"]["curator_counts"]["consolidated_this_run"] == 2
    assert trace["metadata"]["curator_counts"]["pruned_this_run"] == 1
    assert trace["metadata"]["curator_actions"]["consolidated"] == ["skill-a", "skill-b"]
    assert trace["metadata"]["curator_actions"]["pruned"] == ["skill-c"]
    assert trace["metadata"]["curator_actions"]["added"] == ["skill-d"]


def test_messages_synthesized_to_satisfy_schema(tmp_path: Path) -> None:
    """ProductionTrace.messages requires at least one entry. Curator
    run.json doesn't carry a conversation; the ingester must synthesize a
    minimal system message describing the run."""
    home = _make_hermes_home(tmp_path, "normal-run")
    output = tmp_path / "out.jsonl"
    ingest_curator_reports(home=home, output=output)
    trace = _load_jsonl(output)[0]

    assert len(trace["messages"]) >= 1
    assert trace["messages"][0]["role"] == "system"


def test_include_llm_final_attaches_assistant_message(tmp_path: Path) -> None:
    """Without ``--include-llm-final`` the LLM final summary stays out of
    the trace (privacy default). With it, the summary lands as an
    assistant message."""
    home = _make_hermes_home(tmp_path, "normal-run")
    output_off = tmp_path / "off.jsonl"
    output_on = tmp_path / "on.jsonl"

    ingest_curator_reports(home=home, output=output_off, include_llm_final=False)
    ingest_curator_reports(home=home, output=output_on, include_llm_final=True)

    trace_off = _load_jsonl(output_off)[0]
    trace_on = _load_jsonl(output_on)[0]

    off_roles = [m["role"] for m in trace_off["messages"]]
    on_roles = [m["role"] for m in trace_on["messages"]]
    assert "assistant" not in off_roles
    assert "assistant" in on_roles
    assistant_content = next(m["content"] for m in trace_on["messages"] if m["role"] == "assistant")
    assert "Consolidated skill-a" in assistant_content


def test_consolidation_only_run_preserves_action_list(tmp_path: Path) -> None:
    home = _make_hermes_home(tmp_path, "consolidation-only")
    output = tmp_path / "out.jsonl"
    ingest_curator_reports(home=home, output=output)
    trace = _load_jsonl(output)[0]

    assert trace["metadata"]["curator_actions"]["consolidated"] == ["skill-x", "skill-y", "skill-z"]
    assert trace["metadata"]["curator_actions"]["pruned"] == []
    assert trace["metadata"]["curator_counts"]["consolidated_this_run"] == 3


def test_auto_transition_only_run_records_transitions(tmp_path: Path) -> None:
    home = _make_hermes_home(tmp_path, "auto-transition-only")
    output = tmp_path / "out.jsonl"
    ingest_curator_reports(home=home, output=output)
    trace = _load_jsonl(output)[0]

    assert trace["metadata"]["auto_transitions"]["stale_to_archived"] == 2
    assert trace["metadata"]["auto_transitions"]["pinned_to_active"] == 0
    assert trace["metadata"]["curator_actions"]["consolidated"] == []


def test_malformed_run_is_skipped_with_warning(tmp_path: Path) -> None:
    """Tolerant parser: a malformed run.json must NOT abort the whole
    ingest; it should produce a warning, be skipped, and let the rest of
    the runs complete."""
    home = _make_hermes_home(tmp_path, "normal-run", "malformed")
    output = tmp_path / "out.jsonl"

    summary = ingest_curator_reports(home=home, output=output)

    assert summary.runs_read == 2
    assert summary.traces_written == 1
    assert summary.skipped == 1
    assert len(summary.warnings) >= 1
    assert any("malformed" in w.lower() or "json" in w.lower() for w in summary.warnings)


def test_missing_curator_dir_returns_empty_summary(tmp_path: Path) -> None:
    """A Hermes home without any curator reports must NOT throw."""
    home = tmp_path / "empty-home"
    home.mkdir()
    output = tmp_path / "out.jsonl"

    summary = ingest_curator_reports(home=home, output=output)

    assert summary.runs_read == 0
    assert summary.traces_written == 0
    assert summary.skipped == 0
    # Output file is created but empty.
    assert output.exists()
    assert output.read_text(encoding="utf-8") == ""


def test_since_filter_drops_older_runs(tmp_path: Path) -> None:
    """The normal-run fixture has started_at=2026-05-13T15:00:00Z; the
    consolidation-only fixture has 16:00:00Z. A ``since`` filter at
    15:30:00Z keeps only the second."""
    home = _make_hermes_home(tmp_path, "normal-run", "consolidation-only")
    output = tmp_path / "out.jsonl"

    summary = ingest_curator_reports(home=home, output=output, since="2026-05-13T15:30:00Z")

    assert summary.traces_written == 1
    trace = _load_jsonl(output)[0]
    # Only the 16:00 run survives the filter.
    assert "16:00:00" in trace["timing"]["startedAt"]


def test_limit_caps_output(tmp_path: Path) -> None:
    home = _make_hermes_home(tmp_path, "normal-run", "consolidation-only", "auto-transition-only")
    output = tmp_path / "out.jsonl"

    summary = ingest_curator_reports(home=home, output=output, limit=2)

    assert summary.runs_read == 3
    assert summary.traces_written == 2


def test_timing_uses_started_at_and_duration(tmp_path: Path) -> None:
    home = _make_hermes_home(tmp_path, "normal-run")
    output = tmp_path / "out.jsonl"
    ingest_curator_reports(home=home, output=output)
    trace = _load_jsonl(output)[0]

    assert trace["timing"]["startedAt"] == "2026-05-13T15:00:00Z"
    # endedAt = startedAt + duration_seconds (42.5s) = 15:00:42.500000+00:00
    # Accept whichever ISO format the emitter picks; just pin it's > startedAt.
    assert trace["timing"]["endedAt"] > trace["timing"]["startedAt"]
    assert trace["timing"]["latencyMs"] == 42500
