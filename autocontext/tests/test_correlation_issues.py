"""Tests for AC-258 + AC-257: signal correlation and thresholded issue/probe generation.

Covers the full vertical slice per the PR exit checklist:
- AC-258: ReleaseContext, CorrelationDimension, CorrelationResult data models
- AC-258: SignalCorrelator — correlates clusters with release/runtime/environment
- AC-258: CorrelationStore — persist/load/query correlation artifacts
- AC-257: ThresholdConfig, IssueCandidate, ProbeCandidate data models
- AC-257: IssueGenerator — thresholded generation with evidence, dedup, attribution
- AC-257: IssueStore — persist/load/query candidates
- Live wiring: AggregateRunner end-to-end pipeline
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# ===========================================================================
# Helper: build facets and clusters for tests
# ===========================================================================


def _make_test_facets() -> list[Any]:
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
                    signal_type="validation_failure", severity="medium",
                    generation_index=2, description="Parse failure",
                    evidence=["ev-1"],
                ),
                FrictionSignal(
                    signal_type="retry_loop", severity="low",
                    generation_index=3, description="Retried gen 3",
                    evidence=["ev-2"],
                ),
            ],
            delight_signals=[
                DelightSignal(
                    signal_type="fast_advance", generation_index=1,
                    description="Quick gen 1", evidence=["ev-3"],
                ),
            ],
            events=[], metadata={"release": "v1.0.0"},
            created_at="2026-03-14T12:00:00Z",
        ),
        RunFacet(
            run_id="run-2",
            scenario="grid_ctf",
            scenario_family="game",
            agent_provider="anthropic",
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
                    signal_type="validation_failure", severity="high",
                    generation_index=1, description="Parse failure gen 1",
                    evidence=["ev-4"],
                ),
                FrictionSignal(
                    signal_type="validation_failure", severity="medium",
                    generation_index=3, description="Parse failure gen 3",
                    evidence=["ev-5"],
                ),
            ],
            delight_signals=[],
            events=[], metadata={"release": "v1.1.0"},
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
                    signal_type="fast_advance", generation_index=1,
                    description="Clean run", evidence=["ev-6"],
                ),
            ],
            events=[], metadata={"release": "v1.1.0"},
            created_at="2026-03-14T14:00:00Z",
        ),
        RunFacet(
            run_id="run-4",
            scenario="grid_ctf",
            scenario_family="game",
            agent_provider="deterministic",
            executor_mode="local",
            total_generations=4,
            advances=2, retries=1, rollbacks=1,
            best_score=0.55, best_elo=1050.0,
            total_duration_seconds=55.0,
            total_tokens=28000, total_cost_usd=0.14,
            tool_invocations=6, validation_failures=4,
            consultation_count=2, consultation_cost_usd=0.02,
            friction_signals=[
                FrictionSignal(
                    signal_type="validation_failure", severity="high",
                    generation_index=2, description="Parse failure gen 2",
                    evidence=["ev-7"],
                ),
                FrictionSignal(
                    signal_type="rollback", severity="high",
                    generation_index=3, description="Rollback gen 3",
                    evidence=["ev-8"],
                ),
            ],
            delight_signals=[],
            events=[], metadata={"release": "v1.1.0"},
            created_at="2026-03-14T15:00:00Z",
        ),
    ]


def _make_test_clusters() -> list[Any]:
    from autocontext.analytics.clustering import PatternClusterer

    facets = _make_test_facets()
    clusterer = PatternClusterer()
    return clusterer.cluster_friction(facets)


# ===========================================================================
# AC-258: ReleaseContext data model
# ===========================================================================


class TestReleaseContext:
    def test_construction(self) -> None:
        from autocontext.analytics.correlation import ReleaseContext

        ctx = ReleaseContext(
            version="v1.1.0",
            released_at="2026-03-14T10:00:00Z",
            commit_hash="abc123",
            change_summary="Added new scenario families",
        )
        assert ctx.version == "v1.1.0"
        assert ctx.commit_hash == "abc123"

    def test_roundtrip(self) -> None:
        from autocontext.analytics.correlation import ReleaseContext

        ctx = ReleaseContext(
            version="v1.0.0",
            released_at="2026-03-14T00:00:00Z",
        )
        d = ctx.to_dict()
        restored = ReleaseContext.from_dict(d)
        assert restored.version == ctx.version
        assert restored.released_at == ctx.released_at


# ===========================================================================
# AC-258: CorrelationDimension data model
# ===========================================================================


class TestCorrelationDimension:
    def test_construction(self) -> None:
        from autocontext.analytics.correlation import CorrelationDimension

        dim = CorrelationDimension(
            dimension="agent_provider",
            value="anthropic",
            friction_count=5,
            delight_count=2,
            run_count=3,
            top_friction_types=["validation_failure", "retry_loop"],
            top_delight_types=["fast_advance"],
        )
        assert dim.dimension == "agent_provider"
        assert dim.friction_count == 5

    def test_roundtrip(self) -> None:
        from autocontext.analytics.correlation import CorrelationDimension

        dim = CorrelationDimension(
            dimension="scenario_family",
            value="game",
            friction_count=3,
            delight_count=1,
            run_count=4,
            top_friction_types=["validation_failure"],
            top_delight_types=[],
        )
        d = dim.to_dict()
        restored = CorrelationDimension.from_dict(d)
        assert restored.dimension == dim.dimension
        assert restored.friction_count == dim.friction_count


# ===========================================================================
# AC-258: CorrelationResult data model
# ===========================================================================


class TestCorrelationResult:
    def test_construction(self) -> None:
        from autocontext.analytics.correlation import CorrelationResult

        result = CorrelationResult(
            correlation_id="corr-1",
            created_at="2026-03-14T16:00:00Z",
            total_runs=4,
            total_friction=6,
            total_delight=2,
            dimensions=[],
            release_regressions=[],
            cluster_ids=["clust-1"],
            facet_run_ids=["run-1", "run-2"],
            metadata={},
        )
        assert result.correlation_id == "corr-1"
        assert result.total_runs == 4

    def test_roundtrip(self) -> None:
        from autocontext.analytics.correlation import (
            CorrelationDimension,
            CorrelationResult,
        )

        result = CorrelationResult(
            correlation_id="corr-2",
            created_at="2026-03-14T16:00:00Z",
            total_runs=2,
            total_friction=3,
            total_delight=1,
            dimensions=[
                CorrelationDimension(
                    dimension="agent_provider", value="anthropic",
                    friction_count=2, delight_count=0, run_count=1,
                    top_friction_types=["validation_failure"],
                    top_delight_types=[],
                ),
            ],
            release_regressions=[{"release": "v1.1.0", "metric": "friction_rate", "delta": 0.3}],
            cluster_ids=["c1"],
            facet_run_ids=["r1", "r2"],
        )
        d = result.to_dict()
        restored = CorrelationResult.from_dict(d)
        assert restored.correlation_id == result.correlation_id
        assert len(restored.dimensions) == 1
        assert len(restored.release_regressions) == 1


# ===========================================================================
# AC-258: SignalCorrelator
# ===========================================================================


class TestSignalCorrelator:
    def test_correlate_basic(self) -> None:
        from autocontext.analytics.correlation import (
            ReleaseContext,
            SignalCorrelator,
        )

        facets = _make_test_facets()
        clusters = _make_test_clusters()
        releases = [
            ReleaseContext(version="v1.0.0", released_at="2026-03-14T00:00:00Z"),
            ReleaseContext(version="v1.1.0", released_at="2026-03-14T12:30:00Z"),
        ]

        correlator = SignalCorrelator()
        result = correlator.correlate(facets, clusters, releases)

        assert result.total_runs == 4
        assert result.total_friction > 0
        assert result.total_delight > 0
        assert len(result.dimensions) > 0
        assert len(result.facet_run_ids) == 4

    def test_dimension_by_provider(self) -> None:
        from autocontext.analytics.correlation import SignalCorrelator

        facets = _make_test_facets()
        clusters = _make_test_clusters()
        correlator = SignalCorrelator()
        result = correlator.correlate(facets, clusters, [])

        provider_dims = [d for d in result.dimensions if d.dimension == "agent_provider"]
        assert len(provider_dims) > 0
        provider_names = {d.value for d in provider_dims}
        assert "deterministic" in provider_names or "anthropic" in provider_names


class TestPatternClustererMetadata:
    def test_cluster_metadata_includes_release_scope(self) -> None:
        from autocontext.analytics.clustering import PatternClusterer

        clusters = PatternClusterer().cluster_friction(_make_test_facets())
        validation_cluster = next(
            cluster for cluster in clusters if cluster.signal_types == ["validation_failure"]
        )

        assert validation_cluster.metadata["releases"] == ["v1.0.0", "v1.1.0"]

    def test_dimension_by_scenario(self) -> None:
        from autocontext.analytics.correlation import SignalCorrelator

        facets = _make_test_facets()
        clusters = _make_test_clusters()
        correlator = SignalCorrelator()
        result = correlator.correlate(facets, clusters, [])

        scenario_dims = [d for d in result.dimensions if d.dimension == "scenario"]
        assert len(scenario_dims) > 0

    def test_release_regression_detection(self) -> None:
        from autocontext.analytics.correlation import (
            ReleaseContext,
            SignalCorrelator,
        )

        facets = _make_test_facets()
        clusters = _make_test_clusters()
        releases = [
            ReleaseContext(version="v1.0.0", released_at="2026-03-14T00:00:00Z"),
            ReleaseContext(version="v1.1.0", released_at="2026-03-14T12:30:00Z"),
        ]

        correlator = SignalCorrelator()
        result = correlator.correlate(facets, clusters, releases)

        # v1.0.0 has 1 run (run-1), v1.1.0 has 3 runs (run-2,3,4)
        # v1.1.0 has higher friction rate (run-2 and run-4 have friction) vs v1.0.0 (run-1 has friction)
        # So regression detection should flag v1.1.0 if friction per run increased
        release_dims = [d for d in result.dimensions if d.dimension == "release"]
        assert len(release_dims) > 0

    def test_no_releases(self) -> None:
        from autocontext.analytics.correlation import SignalCorrelator

        facets = _make_test_facets()
        clusters = _make_test_clusters()
        correlator = SignalCorrelator()
        result = correlator.correlate(facets, clusters, [])

        # Should still work with no release context
        assert result.total_runs == 4
        assert result.release_regressions == []

    def test_empty_facets(self) -> None:
        from autocontext.analytics.correlation import SignalCorrelator

        correlator = SignalCorrelator()
        result = correlator.correlate([], [], [])

        assert result.total_runs == 0
        assert result.dimensions == []


# ===========================================================================
# AC-258: CorrelationStore
# ===========================================================================


class TestCorrelationStore:
    def test_persist_and_load(self, tmp_path: Path) -> None:
        from autocontext.analytics.correlation import (
            CorrelationResult,
            CorrelationStore,
        )

        store = CorrelationStore(tmp_path)
        result = CorrelationResult(
            correlation_id="corr-test",
            created_at="2026-03-14T16:00:00Z",
            total_runs=4,
            total_friction=6,
            total_delight=2,
            dimensions=[],
            release_regressions=[],
            cluster_ids=["c1"],
            facet_run_ids=["r1"],
        )
        path = store.persist(result)
        assert path.exists()

        loaded = store.load("corr-test")
        assert loaded is not None
        assert loaded.total_runs == 4

    def test_load_missing(self, tmp_path: Path) -> None:
        from autocontext.analytics.correlation import CorrelationStore

        store = CorrelationStore(tmp_path)
        assert store.load("nonexistent") is None

    def test_list_all(self, tmp_path: Path) -> None:
        from autocontext.analytics.correlation import (
            CorrelationResult,
            CorrelationStore,
        )

        store = CorrelationStore(tmp_path)
        for i in range(3):
            store.persist(CorrelationResult(
                correlation_id=f"corr-{i}",
                created_at="2026-03-14T16:00:00Z",
                total_runs=i, total_friction=i, total_delight=0,
                dimensions=[], release_regressions=[],
                cluster_ids=[], facet_run_ids=[],
            ))

        results = store.list_results()
        assert len(results) == 3


# ===========================================================================
# AC-257: ThresholdConfig data model
# ===========================================================================


class TestThresholdConfig:
    def test_defaults(self) -> None:
        from autocontext.analytics.issue_generator import ThresholdConfig

        config = ThresholdConfig()
        assert config.min_recurrence == 3
        assert config.min_confidence == 0.6
        assert config.min_recurrence_rate == 0.3
        assert config.require_correlation is True

    def test_custom(self) -> None:
        from autocontext.analytics.issue_generator import ThresholdConfig

        config = ThresholdConfig(
            min_recurrence=5,
            min_confidence=0.8,
            min_recurrence_rate=0.5,
            require_correlation=False,
        )
        assert config.min_recurrence == 5


# ===========================================================================
# AC-257: IssueCandidate data model
# ===========================================================================


class TestIssueCandidate:
    def test_construction(self) -> None:
        from autocontext.analytics.issue_generator import IssueCandidate

        candidate = IssueCandidate(
            candidate_id="issue-1",
            title="Recurring validation failures in grid_ctf",
            description="3 of 4 runs showed validation failures",
            priority="high",
            source_cluster_ids=["clust-1"],
            correlation_id="corr-1",
            recurrence_count=3,
            confidence=0.75,
            correlation_rationale="Validation failures concentrated in grid_ctf with deterministic provider",
            affected_scenarios=["grid_ctf"],
            affected_families=["game"],
            affected_providers=["deterministic", "anthropic"],
            affected_releases=["v1.1.0"],
            evidence=[{"run_id": "run-1", "gen": 2}],
            created_at="2026-03-14T16:00:00Z",
        )
        assert candidate.candidate_id == "issue-1"
        assert candidate.priority == "high"
        assert candidate.status == "proposed"

    def test_roundtrip(self) -> None:
        from autocontext.analytics.issue_generator import IssueCandidate

        candidate = IssueCandidate(
            candidate_id="issue-2",
            title="test",
            description="test desc",
            priority="medium",
            source_cluster_ids=["c1"],
            correlation_id="corr-1",
            recurrence_count=2,
            confidence=0.6,
            correlation_rationale="test",
            affected_scenarios=["s1"],
            affected_families=["f1"],
            affected_providers=["p1"],
            affected_releases=[],
            evidence=[],
            created_at="2026-03-14T16:00:00Z",
        )
        d = candidate.to_dict()
        restored = IssueCandidate.from_dict(d)
        assert restored.candidate_id == candidate.candidate_id
        assert restored.priority == candidate.priority


# ===========================================================================
# AC-257: ProbeCandidate data model
# ===========================================================================


class TestProbeCandidate:
    def test_construction(self) -> None:
        from autocontext.analytics.issue_generator import ProbeCandidate

        probe = ProbeCandidate(
            candidate_id="probe-1",
            probe_type="regression_fixture",
            title="Regression fixture for validation failures",
            description="Seeded scenario to reproduce validation failures",
            source_cluster_ids=["clust-1"],
            correlation_id="corr-1",
            target_scenario_family="game",
            target_friction_type="validation_failure",
            recurrence_count=3,
            confidence=0.75,
            correlation_rationale="Concentrated in grid_ctf",
            seed_data={"scenario": "grid_ctf", "provider": "deterministic"},
            evidence=[{"run_id": "run-1"}],
            created_at="2026-03-14T16:00:00Z",
        )
        assert probe.probe_type == "regression_fixture"
        assert probe.status == "proposed"

    def test_roundtrip(self) -> None:
        from autocontext.analytics.issue_generator import ProbeCandidate

        probe = ProbeCandidate(
            candidate_id="probe-2",
            probe_type="targeted_probe",
            title="test",
            description="test",
            source_cluster_ids=["c1"],
            correlation_id="corr-1",
            target_scenario_family="game",
            target_friction_type="retry_loop",
            recurrence_count=2,
            confidence=0.6,
            correlation_rationale="test",
            seed_data={},
            evidence=[],
            created_at="2026-03-14T16:00:00Z",
        )
        d = probe.to_dict()
        restored = ProbeCandidate.from_dict(d)
        assert restored.candidate_id == probe.candidate_id
        assert restored.probe_type == probe.probe_type


# ===========================================================================
# AC-257: IssueGenerator
# ===========================================================================


class TestIssueGenerator:
    def test_generate_above_threshold(self) -> None:
        from autocontext.analytics.correlation import SignalCorrelator
        from autocontext.analytics.issue_generator import (
            IssueGenerator,
            ThresholdConfig,
        )

        facets = _make_test_facets()
        clusters = _make_test_clusters()
        correlator = SignalCorrelator()
        correlation = correlator.correlate(facets, clusters, [])

        config = ThresholdConfig(
            min_recurrence=2,
            min_confidence=0.3,
            min_recurrence_rate=0.2,
            require_correlation=False,
        )
        generator = IssueGenerator(config)
        issues, probes = generator.generate(clusters, correlation)

        # validation_failure appears in 3 runs — should generate at least 1 issue
        assert len(issues) > 0
        issue = issues[0]
        assert issue.status == "proposed"
        assert len(issue.source_cluster_ids) > 0
        assert issue.correlation_id == correlation.correlation_id
        assert issue.recurrence_count >= 2
        assert len(issue.evidence) > 0

    def test_below_threshold_no_issues(self) -> None:
        from autocontext.analytics.correlation import SignalCorrelator
        from autocontext.analytics.issue_generator import (
            IssueGenerator,
            ThresholdConfig,
        )

        facets = _make_test_facets()
        clusters = _make_test_clusters()
        correlator = SignalCorrelator()
        correlation = correlator.correlate(facets, clusters, [])

        # Set thresholds very high — nothing should pass
        config = ThresholdConfig(
            min_recurrence=100,
            min_confidence=0.99,
            min_recurrence_rate=0.99,
        )
        generator = IssueGenerator(config)
        issues, probes = generator.generate(clusters, correlation)

        assert len(issues) == 0
        assert len(probes) == 0

    def test_require_correlation_blocks_raw_counts(self) -> None:
        """Recurring signal without meaningful correlation should NOT generate issue."""
        from autocontext.analytics.clustering import FacetCluster
        from autocontext.analytics.correlation import CorrelationResult
        from autocontext.analytics.issue_generator import (
            IssueGenerator,
            ThresholdConfig,
        )

        # Build a cluster with good stats but empty correlation (no dimensions)
        cluster = FacetCluster(
            cluster_id="clust-raw",
            label="Raw count only",
            category="friction",
            signal_types=["unknown_error"],
            run_ids=["r1", "r2", "r3", "r4"],
            frequency=4,
            recurrence_rate=0.8,
            confidence=0.9,
            evidence_summary="4 runs",
            supporting_events=[],
            metadata={},
        )

        # Empty correlation — no dimensions, no regressions
        correlation = CorrelationResult(
            correlation_id="corr-empty",
            created_at="2026-03-14T16:00:00Z",
            total_runs=4,
            total_friction=4,
            total_delight=0,
            dimensions=[],
            release_regressions=[],
            cluster_ids=["clust-raw"],
            facet_run_ids=["r1", "r2", "r3", "r4"],
        )

        config = ThresholdConfig(
            min_recurrence=2,
            min_confidence=0.3,
            require_correlation=True,
        )
        generator = IssueGenerator(config)
        issues, probes = generator.generate([cluster], correlation)

        # Should NOT generate issues from raw counts without correlation grounding
        assert len(issues) == 0

    def test_attribution_includes_evidence(self) -> None:
        from autocontext.analytics.correlation import SignalCorrelator
        from autocontext.analytics.issue_generator import (
            IssueGenerator,
            ThresholdConfig,
        )

        facets = _make_test_facets()
        clusters = _make_test_clusters()
        correlator = SignalCorrelator()
        correlation = correlator.correlate(facets, clusters, [])

        config = ThresholdConfig(
            min_recurrence=2,
            min_confidence=0.3,
            min_recurrence_rate=0.2,
            require_correlation=False,
        )
        generator = IssueGenerator(config)
        issues, probes = generator.generate(clusters, correlation)

        if issues:
            issue = issues[0]
            # Must include evidence references
            assert len(issue.evidence) > 0
            # Must include affected dimensions
            assert len(issue.affected_scenarios) > 0 or len(issue.affected_families) > 0

    def test_probe_generation(self) -> None:
        from autocontext.analytics.correlation import SignalCorrelator
        from autocontext.analytics.issue_generator import (
            IssueGenerator,
            ThresholdConfig,
        )

        facets = _make_test_facets()
        clusters = _make_test_clusters()
        correlator = SignalCorrelator()
        correlation = correlator.correlate(facets, clusters, [])

        config = ThresholdConfig(
            min_recurrence=2,
            min_confidence=0.3,
            min_recurrence_rate=0.2,
            require_correlation=False,
        )
        generator = IssueGenerator(config)
        _, probes = generator.generate(clusters, correlation)

        # Should generate probes for high-frequency friction
        assert len(probes) > 0
        probe = probes[0]
        assert probe.probe_type in ("regression_fixture", "targeted_probe", "seeded_variant")
        assert len(probe.source_cluster_ids) > 0
        assert probe.correlation_id == correlation.correlation_id


# ===========================================================================
# AC-257: IssueStore
# ===========================================================================


class TestIssueStore:
    def test_persist_and_load_issue(self, tmp_path: Path) -> None:
        from autocontext.analytics.issue_generator import IssueCandidate
        from autocontext.analytics.issue_store import IssueStore

        store = IssueStore(tmp_path)
        candidate = IssueCandidate(
            candidate_id="issue-persist",
            title="Test issue",
            description="desc",
            priority="medium",
            source_cluster_ids=["c1"],
            correlation_id="corr-1",
            recurrence_count=3,
            confidence=0.7,
            correlation_rationale="test",
            affected_scenarios=["grid_ctf"],
            affected_families=["game"],
            affected_providers=["deterministic"],
            affected_releases=[],
            evidence=[{"run_id": "r1"}],
            created_at="2026-03-14T16:00:00Z",
        )
        store.persist_issue(candidate)
        loaded = store.load_issue("issue-persist")
        assert loaded is not None
        assert loaded.title == "Test issue"

    def test_persist_and_load_probe(self, tmp_path: Path) -> None:
        from autocontext.analytics.issue_generator import ProbeCandidate
        from autocontext.analytics.issue_store import IssueStore

        store = IssueStore(tmp_path)
        probe = ProbeCandidate(
            candidate_id="probe-persist",
            probe_type="regression_fixture",
            title="Test probe",
            description="desc",
            source_cluster_ids=["c1"],
            correlation_id="corr-1",
            target_scenario_family="game",
            target_friction_type="validation_failure",
            recurrence_count=3,
            confidence=0.7,
            correlation_rationale="test",
            seed_data={"scenario": "grid_ctf"},
            evidence=[{"run_id": "r1"}],
            created_at="2026-03-14T16:00:00Z",
        )
        store.persist_probe(probe)
        loaded = store.load_probe("probe-persist")
        assert loaded is not None
        assert loaded.probe_type == "regression_fixture"

    def test_load_missing(self, tmp_path: Path) -> None:
        from autocontext.analytics.issue_store import IssueStore

        store = IssueStore(tmp_path)
        assert store.load_issue("missing") is None
        assert store.load_probe("missing") is None

    def test_list_issues(self, tmp_path: Path) -> None:
        from autocontext.analytics.issue_generator import IssueCandidate
        from autocontext.analytics.issue_store import IssueStore

        store = IssueStore(tmp_path)
        for i in range(3):
            store.persist_issue(IssueCandidate(
                candidate_id=f"issue-{i}",
                title=f"Issue {i}",
                description="d",
                priority="medium",
                source_cluster_ids=["c1"],
                correlation_id="corr-1",
                recurrence_count=2,
                confidence=0.6,
                correlation_rationale="test",
                affected_scenarios=[], affected_families=[],
                affected_providers=[], affected_releases=[],
                evidence=[], created_at="2026-03-14T16:00:00Z",
            ))
        assert len(store.list_issues()) == 3

    def test_dedup_same_evidence(self, tmp_path: Path) -> None:
        """Reruns should not create duplicate candidates for the same evidence."""
        from autocontext.analytics.issue_generator import IssueCandidate
        from autocontext.analytics.issue_store import IssueStore

        store = IssueStore(tmp_path)
        candidate = IssueCandidate(
            candidate_id="issue-dup-1",
            title="Duplicate test",
            description="same evidence",
            priority="medium",
            source_cluster_ids=["c1"],
            correlation_id="corr-1",
            recurrence_count=3,
            confidence=0.7,
            correlation_rationale="test",
            affected_scenarios=["grid_ctf"],
            affected_families=["game"],
            affected_providers=["deterministic"],
            affected_releases=[],
            evidence=[{"run_id": "r1"}, {"run_id": "r2"}],
            created_at="2026-03-14T16:00:00Z",
        )
        store.persist_issue(candidate)
        assert store.has_issue_for_cluster("c1") is True

        # Should detect existing candidate for same cluster
        assert store.has_issue_for_cluster("nonexistent") is False


# ===========================================================================
# Live wiring: AggregateRunner end-to-end
# ===========================================================================


class TestAggregateRunner:
    def test_end_to_end(self, tmp_path: Path) -> None:
        """Full pipeline: facets → clusters → correlation → persisted issues/probes."""
        from autocontext.analytics.aggregate_runner import AggregateRunner
        from autocontext.analytics.correlation import (
            CorrelationStore,
            ReleaseContext,
        )
        from autocontext.analytics.issue_generator import ThresholdConfig
        from autocontext.analytics.issue_store import IssueStore
        from autocontext.analytics.store import FacetStore

        # 1. Populate facet store
        facet_store = FacetStore(tmp_path)
        for facet in _make_test_facets():
            facet_store.persist(facet)

        # 2. Setup stores
        correlation_store = CorrelationStore(tmp_path)
        issue_store = IssueStore(tmp_path)

        # 3. Run aggregate pipeline
        runner = AggregateRunner(
            facet_store=facet_store,
            correlation_store=correlation_store,
            issue_store=issue_store,
        )
        releases = [
            ReleaseContext(version="v1.0.0", released_at="2026-03-14T00:00:00Z"),
            ReleaseContext(version="v1.1.0", released_at="2026-03-14T12:30:00Z"),
        ]
        config = ThresholdConfig(
            min_recurrence=2,
            min_confidence=0.3,
            min_recurrence_rate=0.2,
            require_correlation=False,
        )

        result = runner.run(release_context=releases, threshold_config=config)

        # 4. Verify persisted correlation
        assert result.correlation is not None
        assert result.correlation.total_runs == 4
        loaded_corr = correlation_store.load(result.correlation.correlation_id)
        assert loaded_corr is not None

        # 5. Verify persisted issues/probes
        assert len(result.issues) > 0 or len(result.probes) > 0
        all_issues = issue_store.list_issues()
        all_probes = issue_store.list_probes()
        assert len(all_issues) + len(all_probes) > 0

    def test_idempotent_rerun(self, tmp_path: Path) -> None:
        """Running twice should not create duplicate candidates."""
        from autocontext.analytics.aggregate_runner import AggregateRunner
        from autocontext.analytics.correlation import CorrelationStore
        from autocontext.analytics.issue_generator import ThresholdConfig
        from autocontext.analytics.issue_store import IssueStore
        from autocontext.analytics.store import FacetStore

        facet_store = FacetStore(tmp_path)
        for facet in _make_test_facets():
            facet_store.persist(facet)

        correlation_store = CorrelationStore(tmp_path)
        issue_store = IssueStore(tmp_path)

        runner = AggregateRunner(
            facet_store=facet_store,
            correlation_store=correlation_store,
            issue_store=issue_store,
        )
        config = ThresholdConfig(
            min_recurrence=2, min_confidence=0.3,
            min_recurrence_rate=0.2, require_correlation=False,
        )

        runner.run(threshold_config=config)
        first_count = len(issue_store.list_issues()) + len(issue_store.list_probes())

        runner.run(threshold_config=config)
        second_count = len(issue_store.list_issues()) + len(issue_store.list_probes())

        # Should not create duplicates
        assert second_count == first_count

    def test_derives_release_context_from_facets(self, tmp_path: Path) -> None:
        from autocontext.analytics.aggregate_runner import AggregateRunner
        from autocontext.analytics.correlation import CorrelationStore
        from autocontext.analytics.issue_generator import ThresholdConfig
        from autocontext.analytics.issue_store import IssueStore
        from autocontext.analytics.store import FacetStore

        facet_store = FacetStore(tmp_path)
        for facet in _make_test_facets():
            facet_store.persist(facet)

        runner = AggregateRunner(
            facet_store=facet_store,
            correlation_store=CorrelationStore(tmp_path),
            issue_store=IssueStore(tmp_path),
        )
        result = runner.run(
            threshold_config=ThresholdConfig(
                min_recurrence=2,
                min_confidence=0.3,
                min_recurrence_rate=0.2,
                require_correlation=False,
            )
        )

        release_dims = [d for d in result.correlation.dimensions if d.dimension == "release"]
        assert release_dims
        assert {d.value for d in release_dims} == {"v1.0.0", "v1.1.0"}

    def test_dedup_allows_same_signal_for_distinct_release_windows(self, tmp_path: Path) -> None:
        from autocontext.analytics.issue_generator import IssueCandidate
        from autocontext.analytics.issue_store import IssueStore

        store = IssueStore(tmp_path)
        store.persist_issue(IssueCandidate(
            candidate_id="issue-old",
            title="Recurring validation_failure across 3 runs",
            description="older release regression",
            priority="high",
            source_cluster_ids=["c1"],
            correlation_id="corr-1",
            recurrence_count=3,
            confidence=0.9,
            correlation_rationale="release v1.0.0",
            affected_scenarios=["grid_ctf"],
            affected_families=["game"],
            affected_providers=["deterministic"],
            affected_releases=["v1.0.0"],
            evidence=[],
            created_at="2026-03-14T12:00:00Z",
        ))

        assert store.has_issue_for_signature(
            signal_type="validation_failure",
            scenarios=["grid_ctf"],
            families=["game"],
            providers=["deterministic"],
            releases=["v1.0.0"],
        ) is True
        assert store.has_issue_for_signature(
            signal_type="validation_failure",
            scenarios=["grid_ctf"],
            families=["game"],
            providers=["deterministic"],
            releases=["v1.1.0"],
        ) is False

    def test_generated_candidates_use_cluster_release_scope(self, tmp_path: Path) -> None:
        from autocontext.analytics.aggregate_runner import AggregateRunner
        from autocontext.analytics.correlation import CorrelationStore
        from autocontext.analytics.issue_generator import ThresholdConfig
        from autocontext.analytics.issue_store import IssueStore
        from autocontext.analytics.store import FacetStore

        facet_store = FacetStore(tmp_path)
        for facet in _make_test_facets():
            facet_store.persist(facet)

        result = AggregateRunner(
            facet_store=facet_store,
            correlation_store=CorrelationStore(tmp_path),
            issue_store=IssueStore(tmp_path),
        ).run(
            threshold_config=ThresholdConfig(
                min_recurrence=2,
                min_confidence=0.3,
                min_recurrence_rate=0.2,
                require_correlation=False,
            )
        )

        validation_issue = next(
            issue for issue in result.issues if "validation_failure" in issue.title
        )
        validation_probe = next(
            probe for probe in result.probes if probe.target_friction_type == "validation_failure"
        )

        assert validation_issue.affected_releases == ["v1.0.0", "v1.1.0"]
        assert validation_probe.seed_data["releases"] == ["v1.0.0", "v1.1.0"]

    def test_empty_store(self, tmp_path: Path) -> None:
        from autocontext.analytics.aggregate_runner import AggregateRunner
        from autocontext.analytics.correlation import CorrelationStore
        from autocontext.analytics.issue_store import IssueStore
        from autocontext.analytics.store import FacetStore

        runner = AggregateRunner(
            facet_store=FacetStore(tmp_path),
            correlation_store=CorrelationStore(tmp_path),
            issue_store=IssueStore(tmp_path),
        )
        result = runner.run()

        assert result.correlation.total_runs == 0
        assert result.issues == []
        assert result.probes == []
