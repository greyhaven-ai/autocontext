"""Tests for AC-326 + AC-328: evidence freshness/decay and friction→regression fixtures.

AC-326: EvidenceFreshness, FreshnessPolicy, apply_freshness_decay, detect_stale_context
AC-328: RegressionFixture, generate_fixtures_from_friction, FixtureStore
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

# ===========================================================================
# AC-326: EvidenceFreshness
# ===========================================================================


class TestEvidenceFreshness:
    def test_construction(self) -> None:
        from autocontext.knowledge.evidence_freshness import EvidenceFreshness

        f = EvidenceFreshness(
            item_id="lesson-1",
            support_count=3,
            last_validated_gen=5,
            confidence=0.85,
            created_at_gen=1,
        )
        assert f.support_count == 3
        assert f.confidence == 0.85

    def test_age(self) -> None:
        from autocontext.knowledge.evidence_freshness import EvidenceFreshness

        f = EvidenceFreshness(item_id="x", support_count=1, last_validated_gen=3, confidence=0.9, created_at_gen=1)
        assert f.age(current_gen=8) == 5  # 8 - 3

    def test_roundtrip(self) -> None:
        from autocontext.knowledge.evidence_freshness import EvidenceFreshness

        f = EvidenceFreshness(item_id="y", support_count=2, last_validated_gen=4, confidence=0.7, created_at_gen=2)
        d = f.to_dict()
        restored = EvidenceFreshness.from_dict(d)
        assert restored.item_id == "y"
        assert restored.last_validated_gen == 4


# ===========================================================================
# AC-326: FreshnessPolicy + apply_freshness_decay
# ===========================================================================


class TestFreshnessPolicy:
    def test_defaults(self) -> None:
        from autocontext.knowledge.evidence_freshness import FreshnessPolicy

        p = FreshnessPolicy()
        assert p.max_age_gens > 0
        assert p.min_confidence > 0

    def test_custom(self) -> None:
        from autocontext.knowledge.evidence_freshness import FreshnessPolicy

        p = FreshnessPolicy(max_age_gens=5, min_confidence=0.6, min_support=2)
        assert p.max_age_gens == 5


class TestApplyFreshnessDecay:
    def test_fresh_items_kept(self) -> None:
        from autocontext.knowledge.evidence_freshness import (
            EvidenceFreshness,
            FreshnessPolicy,
            apply_freshness_decay,
        )

        items = [
            EvidenceFreshness("a", support_count=3, last_validated_gen=9, confidence=0.9, created_at_gen=1),
            EvidenceFreshness("b", support_count=2, last_validated_gen=8, confidence=0.8, created_at_gen=2),
        ]
        policy = FreshnessPolicy(max_age_gens=5, min_confidence=0.5, min_support=1)
        active, stale = apply_freshness_decay(items, current_gen=10, policy=policy)
        assert len(active) == 2
        assert len(stale) == 0

    def test_old_items_decayed(self) -> None:
        from autocontext.knowledge.evidence_freshness import (
            EvidenceFreshness,
            FreshnessPolicy,
            apply_freshness_decay,
        )

        items = [
            EvidenceFreshness("fresh", support_count=3, last_validated_gen=9, confidence=0.9, created_at_gen=1),
            EvidenceFreshness("stale", support_count=1, last_validated_gen=2, confidence=0.4, created_at_gen=1),
        ]
        policy = FreshnessPolicy(max_age_gens=5, min_confidence=0.5, min_support=1)
        active, stale = apply_freshness_decay(items, current_gen=10, policy=policy)
        assert len(active) == 1
        assert len(stale) == 1
        assert stale[0].item_id == "stale"

    def test_low_support_decayed(self) -> None:
        from autocontext.knowledge.evidence_freshness import (
            EvidenceFreshness,
            FreshnessPolicy,
            apply_freshness_decay,
        )

        items = [
            EvidenceFreshness("weak", support_count=0, last_validated_gen=9, confidence=0.3, created_at_gen=9),
        ]
        policy = FreshnessPolicy(min_confidence=0.5, min_support=1)
        active, stale = apply_freshness_decay(items, current_gen=10, policy=policy)
        assert len(stale) == 1


class TestDetectStaleContext:
    def test_detects_stale(self) -> None:
        from autocontext.knowledge.evidence_freshness import (
            EvidenceFreshness,
            FreshnessPolicy,
            detect_stale_context,
        )

        items = [
            EvidenceFreshness("old", support_count=1, last_validated_gen=1, confidence=0.3, created_at_gen=1),
        ]
        warnings = detect_stale_context(items, current_gen=10, policy=FreshnessPolicy())
        assert len(warnings) > 0
        assert "old" in warnings[0].lower()

    def test_no_warnings_for_fresh(self) -> None:
        from autocontext.knowledge.evidence_freshness import (
            EvidenceFreshness,
            FreshnessPolicy,
            detect_stale_context,
        )

        items = [
            EvidenceFreshness("fresh", support_count=5, last_validated_gen=9, confidence=0.95, created_at_gen=1),
        ]
        warnings = detect_stale_context(items, current_gen=10, policy=FreshnessPolicy())
        assert len(warnings) == 0


# ===========================================================================
# AC-328: RegressionFixture
# ===========================================================================


class TestRegressionFixture:
    def test_construction(self) -> None:
        from autocontext.analytics.regression_fixtures import RegressionFixture

        fix = RegressionFixture(
            fixture_id="fix-1",
            scenario="grid_ctf",
            description="Regression on high-aggression strategies",
            seed=42,
            strategy={"aggression": 0.9},
            expected_min_score=0.6,
            source_evidence=["friction:validation_failure:gen3"],
            confidence=0.8,
        )
        assert fix.fixture_id == "fix-1"
        assert fix.expected_min_score == 0.6

    def test_roundtrip(self) -> None:
        from autocontext.analytics.regression_fixtures import RegressionFixture

        fix = RegressionFixture(
            fixture_id="fix-2", scenario="othello",
            description="Corner control regression",
            seed=100, strategy={"x": 1},
            expected_min_score=0.5,
            source_evidence=["cluster:rollback_pattern"],
            confidence=0.7,
        )
        d = fix.to_dict()
        restored = RegressionFixture.from_dict(d)
        assert restored.fixture_id == "fix-2"
        assert restored.source_evidence == ["cluster:rollback_pattern"]


# ===========================================================================
# AC-328: generate_fixtures_from_friction
# ===========================================================================


class TestGenerateFixturesFromFriction:
    def test_generates_from_clusters(self) -> None:
        from autocontext.analytics.regression_fixtures import generate_fixtures_from_friction

        clusters = [
            {
                "pattern": "validation_failure",
                "count": 3,
                "generations": [2, 4, 6],
                "description": "Repeated validation failures on high aggression",
            },
            {
                "pattern": "rollback",
                "count": 2,
                "generations": [3, 5],
                "description": "Rollbacks after low defense strategies",
            },
        ]
        fixtures = generate_fixtures_from_friction(
            clusters, scenario="grid_ctf", min_occurrences=2,
        )
        assert len(fixtures) >= 1
        assert all(f.scenario == "grid_ctf" for f in fixtures)

    def test_filters_low_count(self) -> None:
        from autocontext.analytics.regression_fixtures import generate_fixtures_from_friction

        clusters = [
            {"pattern": "rare", "count": 1, "generations": [1], "description": "Happened once"},
        ]
        fixtures = generate_fixtures_from_friction(clusters, scenario="test", min_occurrences=2)
        assert len(fixtures) == 0

    def test_empty_clusters(self) -> None:
        from autocontext.analytics.regression_fixtures import generate_fixtures_from_friction

        assert generate_fixtures_from_friction([], scenario="test") == []

    def test_fixture_ids_are_stable_for_same_pattern(self) -> None:
        from autocontext.analytics.regression_fixtures import generate_fixtures_from_friction

        clusters = [{"pattern": "rollback", "count": 2, "generations": [1, 2]}]
        first = generate_fixtures_from_friction(clusters, scenario="grid_ctf")
        second = generate_fixtures_from_friction(clusters, scenario="grid_ctf")

        assert len(first) == 1
        assert first[0].fixture_id == second[0].fixture_id


# ===========================================================================
# AC-328: FixtureStore
# ===========================================================================


class TestFixtureStore:
    def test_persist_and_load(self, tmp_path: Path) -> None:
        from autocontext.analytics.regression_fixtures import FixtureStore, RegressionFixture

        store = FixtureStore(tmp_path)
        fix = RegressionFixture(
            fixture_id="fix-store", scenario="grid_ctf",
            description="test", seed=1, strategy={},
            expected_min_score=0.5, source_evidence=["ev"],
            confidence=0.8,
        )
        store.persist(fix)
        loaded = store.load("fix-store")
        assert loaded is not None
        assert loaded.scenario == "grid_ctf"

    def test_load_missing(self, tmp_path: Path) -> None:
        from autocontext.analytics.regression_fixtures import FixtureStore

        store = FixtureStore(tmp_path)
        assert store.load("nonexistent") is None

    def test_list_for_scenario(self, tmp_path: Path) -> None:
        from autocontext.analytics.regression_fixtures import FixtureStore, RegressionFixture

        store = FixtureStore(tmp_path)
        store.persist(
            RegressionFixture(
                fixture_id="f1",
                scenario="grid_ctf",
                description="d",
                seed=1,
                strategy={},
                expected_min_score=0.5,
                source_evidence=[],
                confidence=0.8,
            )
        )
        store.persist(
            RegressionFixture(
                fixture_id="f2",
                scenario="grid_ctf",
                description="d",
                seed=2,
                strategy={},
                expected_min_score=0.5,
                source_evidence=[],
                confidence=0.8,
            )
        )
        store.persist(
            RegressionFixture(
                fixture_id="f3",
                scenario="othello",
                description="d",
                seed=3,
                strategy={},
                expected_min_score=0.5,
                source_evidence=[],
                confidence=0.8,
            )
        )

        grid_fixtures = store.list_for_scenario("grid_ctf")
        assert len(grid_fixtures) == 2

    def test_replace_for_scenario_replaces_stale_fixture_set(self, tmp_path: Path) -> None:
        from autocontext.analytics.regression_fixtures import FixtureStore, RegressionFixture

        store = FixtureStore(tmp_path)
        store.persist(
            RegressionFixture(
                fixture_id="old",
                scenario="grid_ctf",
                description="old",
                seed=1,
                strategy={},
                expected_min_score=0.5,
                source_evidence=[],
                confidence=0.8,
            )
        )

        store.replace_for_scenario(
            "grid_ctf",
            [
                RegressionFixture(
                    fixture_id="new",
                    scenario="grid_ctf",
                    description="new",
                    seed=2,
                    strategy={},
                    expected_min_score=0.5,
                    source_evidence=[],
                    confidence=0.9,
                )
            ],
        )

        fixtures = store.list_for_scenario("grid_ctf")
        assert [fixture.fixture_id for fixture in fixtures] == ["new"]


class TestGenerationRunnerFixtureWiring:
    def test_generate_aggregate_analytics_persists_regression_fixtures(self, tmp_path: Path, monkeypatch) -> None:
        from autocontext.analytics.clustering import FacetCluster
        from autocontext.analytics.facets import RunFacet
        from autocontext.analytics.regression_fixtures import FixtureStore
        from autocontext.config.settings import AppSettings
        from autocontext.loop.generation_runner import GenerationRunner

        settings = AppSettings(
            agent_provider="deterministic",
            db_path=tmp_path / "runs" / "autocontext.sqlite3",
            runs_root=tmp_path / "runs",
            knowledge_root=tmp_path / "knowledge",
            skills_root=tmp_path / "skills",
            claude_skills_path=tmp_path / ".claude" / "skills",
        )
        runner = GenerationRunner(settings)
        scenario = runner._scenario("grid_ctf")

        runner.sqlite.get_generation_metrics = MagicMock(return_value=[])
        runner.sqlite.get_agent_role_metrics = MagicMock(return_value=[])
        runner.sqlite.get_staged_validation_results_for_run = MagicMock(return_value=[])
        runner.sqlite.get_consultations_for_run = MagicMock(return_value=[])
        runner.sqlite.get_recovery_markers_for_run = MagicMock(return_value=[])

        fake_facet = RunFacet(
            run_id="run-1",
            scenario="grid_ctf",
            scenario_family="game",
            agent_provider="deterministic",
            executor_mode="local",
            total_generations=2,
            advances=1,
            retries=0,
            rollbacks=1,
            best_score=0.6,
            best_elo=1000.0,
            total_duration_seconds=1.0,
            total_tokens=0,
            total_cost_usd=0.0,
            tool_invocations=0,
            validation_failures=0,
            consultation_count=0,
            consultation_cost_usd=0.0,
            friction_signals=[],
            delight_signals=[],
            events=[],
            metadata={},
            created_at="",
        )

        cluster = FacetCluster(
            cluster_id="clust-rollback",
            label="Recurring rollback",
            category="friction",
            signal_types=["rollback"],
            run_ids=["run-1", "run-2"],
            frequency=2,
            recurrence_rate=1.0,
            confidence=0.9,
            evidence_summary="2 of 2 runs exhibited rollback",
            supporting_events=[{"generation_index": 2, "description": "Rollback at generation 2"}],
            metadata={
                "scenarios": ["grid_ctf"],
                "scenario_families": ["game"],
                "providers": ["deterministic"],
                "releases": [],
            },
        )

        extractor = MagicMock()
        extractor.extract.return_value = fake_facet
        monkeypatch.setattr("autocontext.loop.generation_runner.FacetExtractor", lambda: extractor)

        clusterer = MagicMock()
        clusterer.cluster_friction.return_value = [cluster]
        clusterer.cluster_delight.return_value = []
        clusterer.query_clusters.return_value = [cluster]
        monkeypatch.setattr("autocontext.loop.generation_runner.PatternClusterer", lambda: clusterer)

        fake_taxonomy = MagicMock()
        monkeypatch.setattr("autocontext.loop.generation_runner.FacetTaxonomy.load", lambda _path: fake_taxonomy)
        fake_aggregate_runner = MagicMock()
        fake_aggregate_runner.run.return_value = None
        monkeypatch.setattr("autocontext.loop.generation_runner.AggregateRunner", lambda **_kwargs: fake_aggregate_runner)
        monkeypatch.setattr(
            runner,
            "_generate_rubric_drift_and_calibration",
            lambda **_kwargs: None,
        )

        runner._generate_aggregate_analytics("run-1", "grid_ctf", scenario)

        fixtures = FixtureStore(settings.knowledge_root / "analytics").list_for_scenario("grid_ctf")
        assert len(fixtures) == 1
        assert fixtures[0].fixture_id == "fix-grid-ctf-rollback"

    def test_generate_aggregate_analytics_persists_credit_assignment_patterns(
        self,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        from autocontext.analytics.clustering import FacetCluster
        from autocontext.analytics.credit_assignment import (
            AttributionResult,
            ComponentChange,
            CreditAssignmentRecord,
            GenerationChangeVector,
        )
        from autocontext.analytics.facets import RunFacet
        from autocontext.config.settings import AppSettings
        from autocontext.loop.generation_runner import GenerationRunner

        settings = AppSettings(
            agent_provider="deterministic",
            db_path=tmp_path / "runs" / "autocontext.sqlite3",
            runs_root=tmp_path / "runs",
            knowledge_root=tmp_path / "knowledge",
            skills_root=tmp_path / "skills",
            claude_skills_path=tmp_path / ".claude" / "skills",
        )
        runner = GenerationRunner(settings)
        scenario = runner._scenario("grid_ctf")

        runner.sqlite.get_generation_metrics = MagicMock(return_value=[])
        runner.sqlite.get_agent_role_metrics = MagicMock(return_value=[])
        runner.sqlite.get_staged_validation_results_for_run = MagicMock(return_value=[])
        runner.sqlite.get_consultations_for_run = MagicMock(return_value=[])
        runner.sqlite.get_recovery_markers_for_run = MagicMock(return_value=[])

        runner.artifacts.write_credit_assignment(
            "grid_ctf",
            "run-1",
            1,
            CreditAssignmentRecord(
                run_id="run-1",
                generation=1,
                vector=GenerationChangeVector(
                    generation=1,
                    score_delta=0.1,
                    changes=[ComponentChange(component="playbook", magnitude=0.6, description="changed")],
                ),
                attribution=AttributionResult(
                    generation=1,
                    total_delta=0.1,
                    credits={"playbook": 0.1},
                ),
            ),
        )

        fake_facet = RunFacet(
            run_id="run-1",
            scenario="grid_ctf",
            scenario_family="game",
            agent_provider="deterministic",
            executor_mode="local",
            total_generations=1,
            advances=1,
            retries=0,
            rollbacks=0,
            best_score=0.6,
            best_elo=1000.0,
            total_duration_seconds=1.0,
            total_tokens=0,
            total_cost_usd=0.0,
            tool_invocations=0,
            validation_failures=0,
            consultation_count=0,
            consultation_cost_usd=0.0,
            friction_signals=[],
            delight_signals=[],
            events=[],
            metadata={},
            created_at="",
        )
        cluster = FacetCluster(
            cluster_id="clust-credit",
            label="Recurring gain",
            category="delight",
            signal_types=["fast_advance"],
            run_ids=["run-1"],
            frequency=1,
            recurrence_rate=1.0,
            confidence=0.9,
            evidence_summary="1 of 1 runs advanced quickly",
            supporting_events=[],
            metadata={
                "scenarios": ["grid_ctf"],
                "scenario_families": ["game"],
                "providers": ["deterministic"],
                "releases": [],
            },
        )

        extractor = MagicMock()
        extractor.extract.return_value = fake_facet
        monkeypatch.setattr("autocontext.loop.generation_runner.FacetExtractor", lambda: extractor)

        clusterer = MagicMock()
        clusterer.cluster_friction.return_value = []
        clusterer.cluster_delight.return_value = [cluster]
        monkeypatch.setattr("autocontext.loop.generation_runner.PatternClusterer", lambda: clusterer)

        fake_taxonomy = MagicMock()
        monkeypatch.setattr("autocontext.loop.generation_runner.FacetTaxonomy.load", lambda _path: fake_taxonomy)
        fake_aggregate_runner = MagicMock()
        fake_aggregate_runner.run.return_value = None
        monkeypatch.setattr("autocontext.loop.generation_runner.AggregateRunner", lambda **_kwargs: fake_aggregate_runner)
        monkeypatch.setattr(
            runner,
            "_generate_rubric_drift_and_calibration",
            lambda **_kwargs: None,
        )

        runner._generate_aggregate_analytics("run-1", "grid_ctf", scenario)

        pattern_path = settings.knowledge_root / "analytics" / "credit_assignment_patterns" / "grid_ctf.json"
        payload = json.loads(pattern_path.read_text(encoding="utf-8"))
        assert payload["total_records"] == 1
        assert payload["components"][0]["component"] == "playbook"
