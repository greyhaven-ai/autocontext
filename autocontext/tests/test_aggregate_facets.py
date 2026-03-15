"""Tests for AC-255 + AC-256: aggregate run facets, signal extraction, and pattern clustering.

Full vertical-slice tests:
- AC-255: RunEvent, FrictionSignal, DelightSignal, RunFacet data models
- AC-255: FacetExtractor — builds facets from completed run data
- AC-255: FacetStore — persist/load/query facets
- AC-256: EventPattern, FacetCluster, TaxonomyEntry data models
- AC-256: PatternClusterer — groups similar patterns across runs
- AC-256: FacetTaxonomy — evolving category taxonomy
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

# ===========================================================================
# AC-255: RunEvent data model
# ===========================================================================


class TestRunEvent:
    def test_construction(self) -> None:
        from autocontext.analytics.facets import RunEvent

        event = RunEvent(
            event_id="ev-1",
            run_id="run-1",
            category="validation",
            event_type="validation_failure",
            timestamp="2026-03-14T12:00:00Z",
            generation_index=2,
            payload={"stage": "syntax", "error": "parse error"},
            severity="error",
        )
        assert event.event_id == "ev-1"
        assert event.category == "validation"
        assert event.severity == "error"
        assert event.payload["stage"] == "syntax"

    def test_roundtrip(self) -> None:
        from autocontext.analytics.facets import RunEvent

        event = RunEvent(
            event_id="ev-2",
            run_id="run-1",
            category="action",
            event_type="tool_call",
            timestamp="2026-03-14T12:01:00Z",
            generation_index=1,
            payload={"tool": "search"},
        )
        d = event.to_dict()
        restored = RunEvent.from_dict(d)
        assert restored.event_id == event.event_id
        assert restored.category == event.category
        assert restored.payload == event.payload
        assert restored.severity == "info"  # default

    def test_defaults(self) -> None:
        from autocontext.analytics.facets import RunEvent

        event = RunEvent(
            event_id="ev-3",
            run_id="run-1",
            category="observation",
            event_type="score_change",
            timestamp="2026-03-14T12:02:00Z",
            generation_index=0,
            payload={},
        )
        assert event.severity == "info"


# ===========================================================================
# AC-255: FrictionSignal data model
# ===========================================================================


class TestFrictionSignal:
    def test_construction(self) -> None:
        from autocontext.analytics.facets import FrictionSignal

        signal = FrictionSignal(
            signal_type="validation_failure",
            severity="high",
            generation_index=3,
            description="Repeated parse failures in validation stage",
            evidence=["ev-1", "ev-2"],
            recoverable=True,
        )
        assert signal.signal_type == "validation_failure"
        assert signal.severity == "high"
        assert len(signal.evidence) == 2

    def test_roundtrip(self) -> None:
        from autocontext.analytics.facets import FrictionSignal

        signal = FrictionSignal(
            signal_type="retry_loop",
            severity="medium",
            generation_index=1,
            description="Retry loop detected",
            evidence=["ev-3"],
        )
        d = signal.to_dict()
        restored = FrictionSignal.from_dict(d)
        assert restored.signal_type == signal.signal_type
        assert restored.evidence == signal.evidence
        assert restored.recoverable is True  # default


# ===========================================================================
# AC-255: DelightSignal data model
# ===========================================================================


class TestDelightSignal:
    def test_construction(self) -> None:
        from autocontext.analytics.facets import DelightSignal

        signal = DelightSignal(
            signal_type="fast_advance",
            generation_index=1,
            description="Advanced on first attempt with high score",
            evidence=["ev-4"],
        )
        assert signal.signal_type == "fast_advance"
        assert signal.generation_index == 1

    def test_roundtrip(self) -> None:
        from autocontext.analytics.facets import DelightSignal

        signal = DelightSignal(
            signal_type="clean_recovery",
            generation_index=2,
            description="Recovered cleanly after rollback",
            evidence=["ev-5", "ev-6"],
        )
        d = signal.to_dict()
        restored = DelightSignal.from_dict(d)
        assert restored.signal_type == signal.signal_type
        assert restored.evidence == signal.evidence


# ===========================================================================
# AC-255: RunFacet data model
# ===========================================================================


class TestRunFacet:
    def test_construction(self) -> None:
        from autocontext.analytics.facets import DelightSignal, FrictionSignal, RunFacet

        facet = RunFacet(
            run_id="run-1",
            scenario="grid_ctf",
            scenario_family="game",
            agent_provider="anthropic",
            executor_mode="local",
            total_generations=5,
            advances=3,
            retries=1,
            rollbacks=1,
            best_score=0.85,
            best_elo=1200.0,
            total_duration_seconds=120.5,
            total_tokens=50000,
            total_cost_usd=0.25,
            tool_invocations=10,
            validation_failures=2,
            consultation_count=1,
            consultation_cost_usd=0.01,
            friction_signals=[
                FrictionSignal(
                    signal_type="validation_failure",
                    severity="medium",
                    generation_index=2,
                    description="Parse failure",
                    evidence=["ev-1"],
                ),
            ],
            delight_signals=[
                DelightSignal(
                    signal_type="fast_advance",
                    generation_index=1,
                    description="Quick advance",
                    evidence=["ev-2"],
                ),
            ],
            events=[],
            metadata={"rlm_enabled": False},
            created_at="2026-03-14T12:00:00Z",
        )
        assert facet.run_id == "run-1"
        assert facet.scenario_family == "game"
        assert facet.advances == 3
        assert len(facet.friction_signals) == 1
        assert len(facet.delight_signals) == 1

    def test_roundtrip(self) -> None:
        from autocontext.analytics.facets import RunFacet

        facet = RunFacet(
            run_id="run-2",
            scenario="othello",
            scenario_family="game",
            agent_provider="deterministic",
            executor_mode="local",
            total_generations=3,
            advances=2,
            retries=1,
            rollbacks=0,
            best_score=0.6,
            best_elo=1100.0,
            total_duration_seconds=45.0,
            total_tokens=20000,
            total_cost_usd=0.10,
            tool_invocations=5,
            validation_failures=0,
            consultation_count=0,
            consultation_cost_usd=0.0,
            friction_signals=[],
            delight_signals=[],
            events=[],
            metadata={},
            created_at="2026-03-14T13:00:00Z",
        )
        d = facet.to_dict()
        restored = RunFacet.from_dict(d)
        assert restored.run_id == facet.run_id
        assert restored.scenario == facet.scenario
        assert restored.best_score == facet.best_score
        assert restored.total_tokens == facet.total_tokens


# ===========================================================================
# AC-255: FacetExtractor
# ===========================================================================


class TestFacetExtractor:
    def _make_run_data(self) -> dict[str, Any]:
        """Build mock data matching what SQLiteStore + ArtifactStore return."""
        return {
            "run": {
                "run_id": "test-run",
                "scenario": "grid_ctf",
                "agent_provider": "deterministic",
                "executor_mode": "local",
                "status": "completed",
            },
            "generations": [
                {
                    "generation_index": 1,
                    "mean_score": 0.3,
                    "best_score": 0.4,
                    "elo": 1050.0,
                    "gate_decision": "advance",
                    "duration_seconds": 10.0,
                },
                {
                    "generation_index": 2,
                    "mean_score": 0.5,
                    "best_score": 0.6,
                    "elo": 1100.0,
                    "gate_decision": "retry",
                    "duration_seconds": 15.0,
                },
                {
                    "generation_index": 3,
                    "mean_score": 0.7,
                    "best_score": 0.8,
                    "elo": 1150.0,
                    "gate_decision": "advance",
                    "duration_seconds": 20.0,
                },
            ],
            "role_metrics": [
                {
                    "role": "competitor",
                    "input_tokens": 5000,
                    "output_tokens": 2000,
                    "generation_index": 1,
                },
                {
                    "role": "analyst",
                    "input_tokens": 4000,
                    "output_tokens": 1500,
                    "generation_index": 1,
                },
                {
                    "role": "competitor",
                    "input_tokens": 6000,
                    "output_tokens": 2500,
                    "generation_index": 2,
                },
            ],
            "staged_validations": [
                {
                    "generation_index": 2,
                    "stage_name": "syntax",
                    "status": "failed",
                    "error": "parse error",
                },
            ],
            "consultations": [
                {
                    "generation_index": 2,
                    "cost_usd": 0.005,
                    "trigger": "score_stall",
                },
            ],
            "recovery": [
                {"generation_index": 2, "decision": "retry", "reason": "low score"},
            ],
        }

    def test_extract_basic_facet(self) -> None:
        from autocontext.analytics.extractor import FacetExtractor

        data = self._make_run_data()
        extractor = FacetExtractor()
        facet = extractor.extract(data)

        assert facet.run_id == "test-run"
        assert facet.scenario == "grid_ctf"
        assert facet.total_generations == 3
        assert facet.advances == 2
        assert facet.retries == 1
        assert facet.rollbacks == 0
        assert facet.best_score == 0.8
        assert facet.best_elo == 1150.0

    def test_extract_token_totals(self) -> None:
        from autocontext.analytics.extractor import FacetExtractor

        data = self._make_run_data()
        extractor = FacetExtractor()
        facet = extractor.extract(data)

        # 5000+2000 + 4000+1500 + 6000+2500 = 21000
        assert facet.total_tokens == 21000

    def test_extract_friction_signals(self) -> None:
        from autocontext.analytics.extractor import FacetExtractor

        data = self._make_run_data()
        extractor = FacetExtractor()
        facet = extractor.extract(data)

        # Should detect validation_failure and retry friction
        friction_types = {s.signal_type for s in facet.friction_signals}
        assert "validation_failure" in friction_types

    def test_extract_delight_signals(self) -> None:
        from autocontext.analytics.extractor import FacetExtractor

        data = self._make_run_data()
        extractor = FacetExtractor()
        facet = extractor.extract(data)

        # Should detect fast_advance (gen 1 advanced)
        delight_types = {s.signal_type for s in facet.delight_signals}
        assert "fast_advance" in delight_types

    def test_extract_duration(self) -> None:
        from autocontext.analytics.extractor import FacetExtractor

        data = self._make_run_data()
        extractor = FacetExtractor()
        facet = extractor.extract(data)

        # 10 + 15 + 20 = 45
        assert facet.total_duration_seconds == 45.0

    def test_extract_consultation_count(self) -> None:
        from autocontext.analytics.extractor import FacetExtractor

        data = self._make_run_data()
        extractor = FacetExtractor()
        facet = extractor.extract(data)

        assert facet.consultation_count == 1
        assert facet.consultation_cost_usd == 0.005

    def test_extract_empty_run(self) -> None:
        from autocontext.analytics.extractor import FacetExtractor

        data = {
            "run": {
                "run_id": "empty-run",
                "scenario": "test",
                "agent_provider": "deterministic",
                "executor_mode": "local",
                "status": "completed",
            },
            "generations": [],
            "role_metrics": [],
            "staged_validations": [],
            "consultations": [],
            "recovery": [],
        }
        extractor = FacetExtractor()
        facet = extractor.extract(data)

        assert facet.total_generations == 0
        assert facet.best_score == 0.0
        assert facet.friction_signals == []
        assert facet.delight_signals == []


# ===========================================================================
# AC-255: FacetStore
# ===========================================================================


class TestFacetStore:
    def _make_facet(self, run_id: str = "run-1", scenario: str = "grid_ctf") -> Any:
        from autocontext.analytics.facets import RunFacet

        return RunFacet(
            run_id=run_id,
            scenario=scenario,
            scenario_family="game",
            agent_provider="deterministic",
            executor_mode="local",
            total_generations=3,
            advances=2,
            retries=1,
            rollbacks=0,
            best_score=0.7,
            best_elo=1100.0,
            total_duration_seconds=45.0,
            total_tokens=20000,
            total_cost_usd=0.10,
            tool_invocations=5,
            validation_failures=1,
            consultation_count=0,
            consultation_cost_usd=0.0,
            friction_signals=[],
            delight_signals=[],
            events=[],
            metadata={},
            created_at="2026-03-14T12:00:00Z",
        )

    def test_persist_and_load(self, tmp_path: Path) -> None:
        from autocontext.analytics.store import FacetStore

        store = FacetStore(tmp_path)
        facet = self._make_facet()
        path = store.persist(facet)

        assert path.exists()
        loaded = store.load("run-1")
        assert loaded is not None
        assert loaded.run_id == "run-1"
        assert loaded.best_score == 0.7

    def test_load_missing_returns_none(self, tmp_path: Path) -> None:
        from autocontext.analytics.store import FacetStore

        store = FacetStore(tmp_path)
        result = store.load("nonexistent")
        assert result is None

    def test_list_facets_all(self, tmp_path: Path) -> None:
        from autocontext.analytics.store import FacetStore

        store = FacetStore(tmp_path)
        store.persist(self._make_facet("run-1", "grid_ctf"))
        store.persist(self._make_facet("run-2", "othello"))
        store.persist(self._make_facet("run-3", "grid_ctf"))

        all_facets = store.list_facets()
        assert len(all_facets) == 3

    def test_list_facets_by_scenario(self, tmp_path: Path) -> None:
        from autocontext.analytics.store import FacetStore

        store = FacetStore(tmp_path)
        store.persist(self._make_facet("run-1", "grid_ctf"))
        store.persist(self._make_facet("run-2", "othello"))
        store.persist(self._make_facet("run-3", "grid_ctf"))

        ctf_facets = store.list_facets(scenario="grid_ctf")
        assert len(ctf_facets) == 2
        assert all(f.scenario == "grid_ctf" for f in ctf_facets)

    def test_query_by_provider(self, tmp_path: Path) -> None:
        from autocontext.analytics.facets import RunFacet
        from autocontext.analytics.store import FacetStore

        store = FacetStore(tmp_path)

        facet_anthropic = RunFacet(
            run_id="run-a",
            scenario="grid_ctf",
            scenario_family="game",
            agent_provider="anthropic",
            executor_mode="local",
            total_generations=3,
            advances=2, retries=1, rollbacks=0,
            best_score=0.8, best_elo=1200.0,
            total_duration_seconds=60.0,
            total_tokens=30000, total_cost_usd=0.15,
            tool_invocations=8, validation_failures=0,
            consultation_count=0, consultation_cost_usd=0.0,
            friction_signals=[], delight_signals=[], events=[],
            metadata={}, created_at="2026-03-14T12:00:00Z",
        )
        store.persist(facet_anthropic)
        store.persist(self._make_facet("run-b", "grid_ctf"))

        results = store.query(agent_provider="anthropic")
        assert len(results) == 1
        assert results[0].agent_provider == "anthropic"


# ===========================================================================
# AC-256: EventPattern data model
# ===========================================================================


class TestEventPattern:
    def test_construction(self) -> None:
        from autocontext.analytics.clustering import EventPattern

        pattern = EventPattern(
            pattern_id="pat-1",
            pattern_type="sequence",
            description="Retry then alternate tool then success",
            event_sequence=["retry", "tool_switch", "advance"],
            frequency=5,
            run_ids=["run-1", "run-2", "run-3", "run-4", "run-5"],
            confidence=0.75,
            evidence=[{"run_id": "run-1", "gen": 2}],
        )
        assert pattern.pattern_id == "pat-1"
        assert pattern.frequency == 5
        assert len(pattern.event_sequence) == 3

    def test_roundtrip(self) -> None:
        from autocontext.analytics.clustering import EventPattern

        pattern = EventPattern(
            pattern_id="pat-2",
            pattern_type="single_event",
            description="test",
            event_sequence=["retry"],
            frequency=3,
            run_ids=["r1", "r2", "r3"],
            confidence=0.6,
            evidence=[],
        )
        d = pattern.to_dict()
        restored = EventPattern.from_dict(d)
        assert restored.pattern_id == pattern.pattern_id
        assert restored.event_sequence == pattern.event_sequence


# ===========================================================================
# AC-256: FacetCluster data model
# ===========================================================================


class TestFacetCluster:
    def test_construction(self) -> None:
        from autocontext.analytics.clustering import FacetCluster

        cluster = FacetCluster(
            cluster_id="clust-1",
            label="Repeated validation failures",
            category="friction",
            signal_types=["validation_failure"],
            run_ids=["run-1", "run-2", "run-3"],
            frequency=3,
            recurrence_rate=0.6,
            confidence=0.8,
            evidence_summary="3 of 5 runs showed validation failures",
            supporting_events=[{"run_id": "run-1", "gen": 2}],
            metadata={"scenario_family": "game"},
        )
        assert cluster.cluster_id == "clust-1"
        assert cluster.category == "friction"
        assert cluster.recurrence_rate == 0.6

    def test_roundtrip(self) -> None:
        from autocontext.analytics.clustering import FacetCluster

        cluster = FacetCluster(
            cluster_id="clust-2",
            label="test",
            category="delight",
            signal_types=["fast_advance"],
            run_ids=["r1"],
            frequency=1,
            recurrence_rate=0.2,
            confidence=0.5,
            evidence_summary="",
            supporting_events=[],
            metadata={},
        )
        d = cluster.to_dict()
        restored = FacetCluster.from_dict(d)
        assert restored.cluster_id == cluster.cluster_id
        assert restored.category == cluster.category


# ===========================================================================
# AC-256: TaxonomyEntry data model
# ===========================================================================


class TestTaxonomyEntry:
    def test_construction(self) -> None:
        from autocontext.analytics.taxonomy import TaxonomyEntry

        entry = TaxonomyEntry(
            entry_id="tax-1",
            name="validation_failure",
            parent_category="friction",
            description="Repeated validation failures in generated code",
            is_system_defined=True,
            source_cluster_id=None,
            created_at="2026-03-14T12:00:00Z",
            recurrence_count=10,
            confidence=1.0,
        )
        assert entry.name == "validation_failure"
        assert entry.is_system_defined is True
        assert entry.source_cluster_id is None

    def test_roundtrip(self) -> None:
        from autocontext.analytics.taxonomy import TaxonomyEntry

        entry = TaxonomyEntry(
            entry_id="tax-2",
            name="dependency_misordering",
            parent_category="friction",
            description="Discovered pattern",
            is_system_defined=False,
            source_cluster_id="clust-5",
            created_at="2026-03-14T13:00:00Z",
            recurrence_count=4,
            confidence=0.7,
        )
        d = entry.to_dict()
        restored = TaxonomyEntry.from_dict(d)
        assert restored.entry_id == entry.entry_id
        assert restored.source_cluster_id == "clust-5"
        assert restored.is_system_defined is False


# ===========================================================================
# AC-256: PatternClusterer
# ===========================================================================


class TestPatternClusterer:
    def _make_facets(self) -> list[Any]:
        from autocontext.analytics.facets import (
            DelightSignal,
            FrictionSignal,
            RunFacet,
        )

        return [
            RunFacet(
                run_id="run-1",
                scenario="grid_ctf",
                scenario_family="game",
                agent_provider="deterministic",
                executor_mode="local",
                total_generations=5,
                advances=3, retries=1, rollbacks=1,
                best_score=0.7, best_elo=1100.0,
                total_duration_seconds=60.0,
                total_tokens=30000, total_cost_usd=0.15,
                tool_invocations=5, validation_failures=2,
                consultation_count=1, consultation_cost_usd=0.01,
                friction_signals=[
                    FrictionSignal(
                        signal_type="validation_failure",
                        severity="medium",
                        generation_index=2,
                        description="Parse failure in gen 2",
                        evidence=["ev-1"],
                    ),
                    FrictionSignal(
                        signal_type="retry_loop",
                        severity="low",
                        generation_index=3,
                        description="Retried gen 3",
                        evidence=["ev-2"],
                    ),
                ],
                delight_signals=[
                    DelightSignal(
                        signal_type="fast_advance",
                        generation_index=1,
                        description="Quick gen 1",
                        evidence=["ev-3"],
                    ),
                ],
                events=[], metadata={},
                created_at="2026-03-14T12:00:00Z",
            ),
            RunFacet(
                run_id="run-2",
                scenario="grid_ctf",
                scenario_family="game",
                agent_provider="deterministic",
                executor_mode="local",
                total_generations=4,
                advances=2, retries=2, rollbacks=0,
                best_score=0.65, best_elo=1080.0,
                total_duration_seconds=50.0,
                total_tokens=25000, total_cost_usd=0.12,
                tool_invocations=4, validation_failures=3,
                consultation_count=0, consultation_cost_usd=0.0,
                friction_signals=[
                    FrictionSignal(
                        signal_type="validation_failure",
                        severity="high",
                        generation_index=1,
                        description="Parse failure in gen 1",
                        evidence=["ev-4"],
                    ),
                ],
                delight_signals=[],
                events=[], metadata={},
                created_at="2026-03-14T13:00:00Z",
            ),
            RunFacet(
                run_id="run-3",
                scenario="othello",
                scenario_family="game",
                agent_provider="anthropic",
                executor_mode="local",
                total_generations=3,
                advances=3, retries=0, rollbacks=0,
                best_score=0.9, best_elo=1300.0,
                total_duration_seconds=30.0,
                total_tokens=15000, total_cost_usd=0.08,
                tool_invocations=3, validation_failures=0,
                consultation_count=0, consultation_cost_usd=0.0,
                friction_signals=[],
                delight_signals=[
                    DelightSignal(
                        signal_type="fast_advance",
                        generation_index=1,
                        description="Clean run",
                        evidence=["ev-5"],
                    ),
                    DelightSignal(
                        signal_type="strong_improvement",
                        generation_index=2,
                        description="Big jump",
                        evidence=["ev-6"],
                    ),
                ],
                events=[], metadata={},
                created_at="2026-03-14T14:00:00Z",
            ),
        ]

    def test_cluster_friction(self) -> None:
        from autocontext.analytics.clustering import PatternClusterer

        facets = self._make_facets()
        clusterer = PatternClusterer()
        clusters = clusterer.cluster_friction(facets)

        # Should group validation_failure signals from run-1 and run-2
        assert len(clusters) > 0
        vf_cluster = next(
            (c for c in clusters if "validation_failure" in c.signal_types), None
        )
        assert vf_cluster is not None
        assert vf_cluster.category == "friction"
        assert vf_cluster.frequency >= 2

    def test_cluster_delight(self) -> None:
        from autocontext.analytics.clustering import PatternClusterer

        facets = self._make_facets()
        clusterer = PatternClusterer()
        clusters = clusterer.cluster_delight(facets)

        assert len(clusters) > 0
        fa_cluster = next(
            (c for c in clusters if "fast_advance" in c.signal_types), None
        )
        assert fa_cluster is not None
        assert fa_cluster.category == "delight"
        assert fa_cluster.frequency >= 2

    def test_cluster_recurrence_rate(self) -> None:
        from autocontext.analytics.clustering import PatternClusterer

        facets = self._make_facets()
        clusterer = PatternClusterer()
        clusters = clusterer.cluster_friction(facets)

        # validation_failure appears in 2 of 3 runs = 0.667
        vf_cluster = next(
            (c for c in clusters if "validation_failure" in c.signal_types), None
        )
        assert vf_cluster is not None
        assert vf_cluster.recurrence_rate == pytest.approx(2 / 3, abs=0.01)

    def test_query_clusters_by_scenario(self) -> None:
        from autocontext.analytics.clustering import PatternClusterer

        facets = self._make_facets()
        clusterer = PatternClusterer()
        clusters = clusterer.cluster_friction(facets)

        filtered = clusterer.query_clusters(
            clusters, scenario="grid_ctf"
        )
        # All friction clusters should involve grid_ctf runs
        for cluster in filtered:
            assert any(
                rid in ["run-1", "run-2"]
                for rid in cluster.run_ids
            )

    def test_query_clusters_by_provider(self) -> None:
        from autocontext.analytics.clustering import PatternClusterer

        facets = self._make_facets()
        clusterer = PatternClusterer()
        clusters = clusterer.cluster_delight(facets)

        filtered = clusterer.query_clusters(
            clusters, agent_provider="anthropic"
        )
        for cluster in filtered:
            assert "run-3" in cluster.run_ids

    def test_empty_facets(self) -> None:
        from autocontext.analytics.clustering import PatternClusterer

        clusterer = PatternClusterer()
        assert clusterer.cluster_friction([]) == []
        assert clusterer.cluster_delight([]) == []


# ===========================================================================
# AC-256: FacetTaxonomy
# ===========================================================================


class TestFacetTaxonomy:
    def test_builtin_entries(self) -> None:
        from autocontext.analytics.taxonomy import FacetTaxonomy

        taxonomy = FacetTaxonomy()
        entries = taxonomy.get_entries()
        # Should have built-in friction/delight categories
        assert len(entries) > 0
        names = {e.name for e in entries}
        assert "validation_failure" in names
        assert "fast_advance" in names

    def test_add_entry(self) -> None:
        from autocontext.analytics.taxonomy import FacetTaxonomy, TaxonomyEntry

        taxonomy = FacetTaxonomy()
        initial_count = len(taxonomy.get_entries())

        entry = TaxonomyEntry(
            entry_id="tax-custom",
            name="dependency_misordering",
            parent_category="friction",
            description="Actions taken in wrong dependency order",
            is_system_defined=False,
            source_cluster_id="clust-99",
            created_at="2026-03-14T12:00:00Z",
            recurrence_count=3,
            confidence=0.7,
        )
        taxonomy.add_entry(entry)
        assert len(taxonomy.get_entries()) == initial_count + 1

    def test_propose_from_cluster(self) -> None:
        from autocontext.analytics.clustering import FacetCluster
        from autocontext.analytics.taxonomy import FacetTaxonomy

        taxonomy = FacetTaxonomy()
        cluster = FacetCluster(
            cluster_id="clust-new",
            label="Repeated cancellation cascades",
            category="friction",
            signal_types=["cancellation_cascade"],
            run_ids=["r1", "r2", "r3", "r4"],
            frequency=4,
            recurrence_rate=0.8,
            confidence=0.85,
            evidence_summary="4 of 5 runs had cancellation cascades",
            supporting_events=[],
            metadata={},
        )

        proposed = taxonomy.propose_from_cluster(cluster, min_confidence=0.6)
        assert proposed is not None
        assert proposed.name == "cancellation_cascade"
        assert proposed.is_system_defined is False
        assert proposed.source_cluster_id == "clust-new"

    def test_propose_low_confidence_returns_none(self) -> None:
        from autocontext.analytics.clustering import FacetCluster
        from autocontext.analytics.taxonomy import FacetTaxonomy

        taxonomy = FacetTaxonomy()
        cluster = FacetCluster(
            cluster_id="clust-weak",
            label="Weak pattern",
            category="friction",
            signal_types=["unknown_type"],
            run_ids=["r1"],
            frequency=1,
            recurrence_rate=0.1,
            confidence=0.3,
            evidence_summary="Only 1 run",
            supporting_events=[],
            metadata={},
        )

        proposed = taxonomy.propose_from_cluster(cluster, min_confidence=0.6)
        assert proposed is None

    def test_evolve_adds_new_categories(self) -> None:
        from autocontext.analytics.clustering import FacetCluster
        from autocontext.analytics.taxonomy import FacetTaxonomy

        taxonomy = FacetTaxonomy()
        initial_count = len(taxonomy.get_entries())

        clusters = [
            FacetCluster(
                cluster_id="clust-a",
                label="Cancellation cascade",
                category="friction",
                signal_types=["cancellation_cascade"],
                run_ids=["r1", "r2", "r3"],
                frequency=3,
                recurrence_rate=0.6,
                confidence=0.75,
                evidence_summary="3 runs showed cascading cancellations",
                supporting_events=[],
                metadata={},
            ),
        ]
        new_entries = taxonomy.evolve(clusters, min_confidence=0.6)
        assert len(new_entries) == 1
        assert len(taxonomy.get_entries()) == initial_count + 1

    def test_evolve_skips_existing(self) -> None:
        from autocontext.analytics.clustering import FacetCluster
        from autocontext.analytics.taxonomy import FacetTaxonomy

        taxonomy = FacetTaxonomy()

        # Try to evolve with a cluster that maps to an existing category
        clusters = [
            FacetCluster(
                cluster_id="clust-dup",
                label="Validation failures",
                category="friction",
                signal_types=["validation_failure"],
                run_ids=["r1", "r2"],
                frequency=2,
                recurrence_rate=0.5,
                confidence=0.9,
                evidence_summary="Already known",
                supporting_events=[],
                metadata={},
            ),
        ]
        initial_count = len(taxonomy.get_entries())
        new_entries = taxonomy.evolve(clusters, min_confidence=0.6)
        assert len(new_entries) == 0
        assert len(taxonomy.get_entries()) == initial_count

    def test_persist_and_load(self, tmp_path: Path) -> None:
        from autocontext.analytics.taxonomy import FacetTaxonomy, TaxonomyEntry

        taxonomy = FacetTaxonomy()
        taxonomy.add_entry(TaxonomyEntry(
            entry_id="tax-persist",
            name="custom_category",
            parent_category="friction",
            description="A custom category",
            is_system_defined=False,
            source_cluster_id=None,
            created_at="2026-03-14T12:00:00Z",
            recurrence_count=1,
            confidence=0.5,
        ))

        path = tmp_path / "taxonomy.json"
        taxonomy.save(path)

        loaded = FacetTaxonomy.load(path)
        names = {e.name for e in loaded.get_entries()}
        assert "custom_category" in names
