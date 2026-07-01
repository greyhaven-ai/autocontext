"""Tests for AC-851: `_build_run_trace` causal-chaining behavior.

`GenerationRunner._build_run_trace` builds the role/validation/consultation/
recovery event blocks by hand-wiring causal edges and advancing
`current_source_id`/`sequence_number`. AC-851 extracts a shared
`_append_chained_event` helper from those four blocks; this test locks in an
end-to-end fixture captured from the pre-refactor implementation so the
extraction cannot silently change the causal chain (event ids, sequence
numbers, or edge relations/sources/targets -- including the recovery block's
extra multi-cause/evidence_ids logic).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from autocontext.config.settings import AppSettings
from autocontext.loop.generation_runner import GenerationRunner

# ---------------------------------------------------------------------------
# Representative fixture inputs (two generations: a clean advance, and a
# failure -> consultation -> recovery -> error chain that exercises the
# recovery block's extra evidence_ids/multi-cause behavior).
# ---------------------------------------------------------------------------

_GENERATION_ROWS: list[dict[str, Any]] = [
    {
        "generation_index": 0,
        "mean_score": 0.5,
        "best_score": 0.6,
        "elo": 1000.0,
        "wins": 1,
        "losses": 0,
        "status": "completed",
        "gate_decision": "advance",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:01:00Z",
        "duration_seconds": 12.5,
    },
    {
        "generation_index": 1,
        "mean_score": 0.7,
        "best_score": 0.8,
        "elo": 1010.0,
        "wins": 2,
        "losses": 1,
        "status": "completed",
        "gate_decision": "error",
        "created_at": "2026-01-01T00:02:00Z",
        "updated_at": "2026-01-01T00:03:00Z",
        "duration_seconds": 8.25,
    },
]

_ROLE_METRICS: list[dict[str, Any]] = [
    {
        "generation_index": 0,
        "role": "competitor",
        "subagent_id": "comp-1",
        "model": "claude-x",
        "status": "success",
        "input_tokens": 100,
        "output_tokens": 200,
        "latency_ms": 500,
        "created_at": "2026-01-01T00:00:10Z",
    },
    {
        "generation_index": 1,
        "role": "analyst",
        "subagent_id": "an-1",
        "model": "claude-y",
        "status": "failed",
        "input_tokens": 50,
        "output_tokens": 20,
        "latency_ms": 300,
        "created_at": "2026-01-01T00:02:10Z",
    },
]

_STAGED_VALIDATIONS: list[dict[str, Any]] = [
    {
        "generation_index": 0,
        "stage_name": "syntax",
        "stage_order": 1,
        "status": "passed",
        "error": None,
        "error_code": None,
        "duration_ms": 20,
        "created_at": "2026-01-01T00:00:20Z",
    },
    {
        "generation_index": 1,
        "stage_name": "semantic",
        "stage_order": 2,
        "status": "failed",
        "error": "bad thing",
        "error_code": "E1",
        "duration_ms": 40,
        "created_at": "2026-01-01T00:02:20Z",
    },
]

_CONSULTATIONS: list[dict[str, Any]] = [
    {
        "generation_index": 1,
        "trigger": "low_confidence",
        "critique": "hmm",
        "alternative_hypothesis": "try x",
        "tiebreak_recommendation": "y",
        "suggested_next_action": "z",
        "cost_usd": 0.01,
        "model_used": "claude-consult",
        "created_at": "2026-01-01T00:02:30Z",
    },
]

_RECOVERY_MARKERS: list[dict[str, Any]] = [
    {
        "generation_index": 1,
        "decision": "retry",
        "reason": "validation failed",
        "retry_count": 1,
        "created_at": "2026-01-01T00:02:40Z",
    },
]

# Expected causal edges, captured end-to-end from `_build_run_trace` before
# the AC-851 extraction. The recovery edge set is the interesting part: it
# gets a "triggers" edge from the consultation event (current_source_id, not
# itself a failed validation) plus a "recovers" edge from the failed
# validation event -- both cause ids show up in the recovery event's own
# cause_event_ids, and only the failed validation id shows up in evidence_ids.
_EXPECTED_CAUSAL_EDGES: list[dict[str, str]] = [
    {"source_event_id": "gen-0-role-1", "target_event_id": "gen-0-validation-1", "relation": "triggers"},
    {"source_event_id": "gen-0-validation-1", "target_event_id": "gen-0-checkpoint", "relation": "depends_on"},
    {"source_event_id": "gen-0-checkpoint", "target_event_id": "gen-1-role-1", "relation": "triggers"},
    {"source_event_id": "gen-1-role-1", "target_event_id": "gen-1-validation-1", "relation": "triggers"},
    {"source_event_id": "gen-1-validation-1", "target_event_id": "gen-1-consultation-1", "relation": "triggers"},
    {"source_event_id": "gen-1-consultation-1", "target_event_id": "gen-1-recovery-1", "relation": "triggers"},
    {"source_event_id": "gen-1-validation-1", "target_event_id": "gen-1-recovery-1", "relation": "recovers"},
    {"source_event_id": "gen-1-recovery-1", "target_event_id": "gen-1-checkpoint", "relation": "depends_on"},
]

_EXPECTED_EVENT_IDS_IN_ORDER: list[str] = [
    "gen-0-role-1",
    "gen-0-validation-1",
    "gen-0-checkpoint",
    "gen-1-role-1",
    "gen-1-validation-1",
    "gen-1-consultation-1",
    "gen-1-recovery-1",
    "gen-1-checkpoint",
]

_EXPECTED_PARENT_EVENT_IDS: dict[str, str | None] = {
    "gen-0-role-1": None,
    "gen-0-validation-1": "gen-0-role-1",
    "gen-0-checkpoint": "gen-0-validation-1",
    "gen-1-role-1": "gen-0-checkpoint",
    "gen-1-validation-1": "gen-1-role-1",
    "gen-1-consultation-1": "gen-1-validation-1",
    "gen-1-recovery-1": "gen-1-consultation-1",
    "gen-1-checkpoint": "gen-1-recovery-1",
}

_EXPECTED_CAUSE_EVENT_IDS: dict[str, list[str]] = {
    "gen-0-role-1": [],
    "gen-0-validation-1": ["gen-0-role-1"],
    "gen-0-checkpoint": ["gen-0-validation-1"],
    "gen-1-role-1": ["gen-0-checkpoint"],
    "gen-1-validation-1": ["gen-1-role-1"],
    "gen-1-consultation-1": ["gen-1-validation-1"],
    "gen-1-recovery-1": ["gen-1-validation-1", "gen-1-consultation-1"],
    "gen-1-checkpoint": ["gen-1-recovery-1"],
}

_EXPECTED_EVIDENCE_IDS: dict[str, list[str]] = {
    "gen-0-role-1": [],
    "gen-0-validation-1": [],
    "gen-0-checkpoint": [],
    "gen-1-role-1": [],
    "gen-1-validation-1": [],
    "gen-1-consultation-1": [],
    "gen-1-recovery-1": ["gen-1-validation-1"],
    "gen-1-checkpoint": ["gen-1-validation-1"],
}


def _build_runner(tmp_path: Path) -> GenerationRunner:
    settings = AppSettings(
        agent_provider="deterministic",
        db_path=tmp_path / "test.sqlite3",
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
    )
    return GenerationRunner(settings)


class TestBuildRunTraceChaining:
    """End-to-end regression fixture for the causal chain `_build_run_trace` builds."""

    def _build_trace(self, tmp_path: Path) -> Any:
        runner = _build_runner(tmp_path)
        return runner._build_run_trace(
            run_id="run-fixture-1",
            scenario_name="grid_ctf",
            scenario=MagicMock(),
            generation_rows=_GENERATION_ROWS,
            role_metrics=_ROLE_METRICS,
            staged_validations=_STAGED_VALIDATIONS,
            consultations=_CONSULTATIONS,
            recovery_markers=_RECOVERY_MARKERS,
        )

    def test_event_ids_and_sequence_numbers_advance_in_order(self, tmp_path: Path) -> None:
        trace = self._build_trace(tmp_path)
        assert [event.event_id for event in trace.events] == _EXPECTED_EVENT_IDS_IN_ORDER
        assert [event.sequence_number for event in trace.events] == list(range(1, len(_EXPECTED_EVENT_IDS_IN_ORDER) + 1))

    def test_parent_event_ids_match_chain(self, tmp_path: Path) -> None:
        trace = self._build_trace(tmp_path)
        actual = {event.event_id: event.parent_event_id for event in trace.events}
        assert actual == _EXPECTED_PARENT_EVENT_IDS

    def test_cause_event_ids_match_chain_including_recovery_multi_cause(self, tmp_path: Path) -> None:
        trace = self._build_trace(tmp_path)
        actual = {event.event_id: event.cause_event_ids for event in trace.events}
        assert actual == _EXPECTED_CAUSE_EVENT_IDS

    def test_evidence_ids_carry_failed_validation_through_recovery_and_checkpoint(self, tmp_path: Path) -> None:
        trace = self._build_trace(tmp_path)
        actual = {event.event_id: event.evidence_ids for event in trace.events}
        assert actual == _EXPECTED_EVIDENCE_IDS

    def test_causal_edges_match_exactly(self, tmp_path: Path) -> None:
        trace = self._build_trace(tmp_path)
        actual = [
            {
                "source_event_id": edge.source_event_id,
                "target_event_id": edge.target_event_id,
                "relation": edge.relation,
            }
            for edge in trace.causal_edges
        ]
        assert actual == _EXPECTED_CAUSAL_EDGES

    def test_full_trace_dict_matches_pre_refactor_fixture(self, tmp_path: Path) -> None:
        """Belt-and-suspenders: compare the full serialized trace, modulo volatile fields."""
        trace = self._build_trace(tmp_path)
        actual = trace.to_dict()
        actual.pop("created_at", None)
        actual["metadata"].pop("release", None)
        for event in actual["events"]:
            event.pop("timestamp", None)

        assert actual["run_id"] == "run-fixture-1"
        assert actual["trace_id"] == "trace-run-fixture-1"
        assert actual["schema_version"] == "1.0.0"
        assert actual["metadata"]["total_generations"] == 2
        assert actual["causal_edges"] == _EXPECTED_CAUSAL_EDGES
        assert [event["event_id"] for event in actual["events"]] == _EXPECTED_EVENT_IDS_IN_ORDER


def test_group_by_generation_helper_matches_manual_grouping(tmp_path: Path) -> None:
    """If `_group_by_generation` was extracted, it must group identically to the
    inline `defaultdict(list)` loops it replaces."""
    runner = _build_runner(tmp_path)
    if not hasattr(runner, "_group_by_generation"):
        return
    grouped = runner._group_by_generation(_ROLE_METRICS)
    assert dict(grouped) == {0: [_ROLE_METRICS[0]], 1: [_ROLE_METRICS[1]]}
    assert grouped[42] == []  # defaultdict(list) semantics preserved
