"""Tests for TrustTracker — evaluation, persistence, audit, and thread safety."""

import threading

from mts.harness.audit.types import AuditCategory
from mts.harness.audit.writer import AppendOnlyAuditWriter
from mts.harness.meta.collector import MetricsCollector
from mts.harness.meta.profiler import PerformanceProfiler
from mts.harness.meta.types import RoleMetric
from mts.harness.trust.policy import TrustPolicy
from mts.harness.trust.tracker import TrustTracker
from mts.harness.trust.types import TrustBudget, TrustTier


def _make_profiler(role: str, n_obs: int, n_advances: int) -> PerformanceProfiler:
    """Build a profiler with a single role populated with synthetic metrics."""
    collector = MetricsCollector()
    for i in range(n_obs):
        gate = "advance" if i < n_advances else "retry"
        collector.add(
            RoleMetric(
                role=role,
                generation=i + 1,
                input_tokens=100,
                output_tokens=50,
                latency_ms=500,
                cost=0.01,
                gate_decision=gate,
                score_delta=0.01 if gate == "advance" else 0.0,
            )
        )
    return PerformanceProfiler(collector, min_observations=1)


def _make_multi_role_profiler(
    roles: dict[str, tuple[int, int]],
) -> PerformanceProfiler:
    """Build a profiler with multiple roles. roles = {name: (n_obs, n_advances)}."""
    collector = MetricsCollector()
    for role, (n_obs, n_advances) in roles.items():
        for i in range(n_obs):
            gate = "advance" if i < n_advances else "retry"
            collector.add(
                RoleMetric(
                    role=role,
                    generation=i + 1,
                    input_tokens=100,
                    output_tokens=50,
                    latency_ms=500,
                    cost=0.01,
                    gate_decision=gate,
                    score_delta=0.01 if gate == "advance" else 0.0,
                )
            )
    return PerformanceProfiler(collector, min_observations=1)


def test_evaluate_all_with_profiler() -> None:
    """evaluate_all should populate scores from the profiler."""
    profiler = _make_profiler("analyst", n_obs=10, n_advances=7)
    policy = TrustPolicy()
    tracker = TrustTracker(policy, profiler=profiler)

    scores = tracker.evaluate_all()
    assert "analyst" in scores
    assert scores["analyst"].role == "analyst"
    assert scores["analyst"].observations == 10
    # advance_rate = 7/10 = 0.7, with 10 obs -> TRUSTED (>= 0.6, < 0.8)
    assert scores["analyst"].tier == TrustTier.TRUSTED


def test_score_for_query() -> None:
    """score_for should return the score for a known role."""
    profiler = _make_profiler("coach", n_obs=10, n_advances=5)
    policy = TrustPolicy()
    tracker = TrustTracker(policy, profiler=profiler)
    tracker.evaluate_all()

    score = tracker.score_for("coach")
    assert score is not None
    assert score.role == "coach"
    assert score.tier == TrustTier.ESTABLISHED  # 0.5 advance rate


def test_budget_for_query() -> None:
    """budget_for should return the budget for a known role's tier."""
    profiler = _make_profiler("competitor", n_obs=10, n_advances=9)
    policy = TrustPolicy()
    tracker = TrustTracker(policy, profiler=profiler)
    tracker.evaluate_all()

    budget = tracker.budget_for("competitor")
    assert budget is not None
    expected = TrustBudget.for_tier(TrustTier.EXEMPLARY)  # 0.9 advance rate
    assert budget == expected


def test_all_scores() -> None:
    """all_scores should return a copy of all current scores."""
    profiler = _make_multi_role_profiler({
        "analyst": (10, 7),
        "coach": (10, 3),
    })
    policy = TrustPolicy()
    tracker = TrustTracker(policy, profiler=profiler)
    tracker.evaluate_all()

    scores = tracker.all_scores()
    assert len(scores) == 2
    assert "analyst" in scores
    assert "coach" in scores
    # Verify it's a copy
    scores["analyst"] = None  # type: ignore[assignment]
    assert tracker.score_for("analyst") is not None


def test_no_profiler_returns_empty() -> None:
    """evaluate_all with no profiler should return empty dict."""
    policy = TrustPolicy()
    tracker = TrustTracker(policy)

    scores = tracker.evaluate_all()
    assert scores == {}


def test_save_load_round_trip(tmp_path) -> None:
    """Scores should survive save/load round-trip."""
    profiler = _make_multi_role_profiler({
        "analyst": (10, 7),
        "coach": (10, 2),
    })
    policy = TrustPolicy()
    tracker = TrustTracker(policy, profiler=profiler)
    tracker.evaluate_all()

    path = tmp_path / "trust_scores.json"
    tracker.save(path)

    # Load into a fresh tracker
    tracker2 = TrustTracker(policy)
    tracker2.load(path)

    scores1 = tracker.all_scores()
    scores2 = tracker2.all_scores()

    assert set(scores1.keys()) == set(scores2.keys())
    for role in scores1:
        assert scores1[role].tier == scores2[role].tier
        assert scores1[role].raw_score == scores2[role].raw_score
        assert scores1[role].observations == scores2[role].observations
        assert scores1[role].confidence == scores2[role].confidence


def test_thread_safety() -> None:
    """Concurrent evaluate_all and score_for should not raise."""
    profiler = _make_multi_role_profiler({
        "analyst": (10, 7),
        "coach": (10, 5),
        "competitor": (10, 9),
    })
    policy = TrustPolicy()
    tracker = TrustTracker(policy, profiler=profiler)

    errors: list[Exception] = []

    def worker() -> None:
        try:
            for _ in range(50):
                tracker.evaluate_all()
                tracker.score_for("analyst")
                tracker.all_scores()
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []


def test_audit_on_tier_change(tmp_path) -> None:
    """When a role's tier changes, an audit entry should be written."""
    audit_path = tmp_path / "audit.ndjson"
    writer = AppendOnlyAuditWriter(audit_path)

    # Start with low-performing profiler -> PROBATION
    profiler_low = _make_profiler("analyst", n_obs=10, n_advances=1)
    policy = TrustPolicy()
    tracker = TrustTracker(policy, profiler=profiler_low, audit_writer=writer)
    tracker.evaluate_all()
    assert tracker.score_for("analyst").tier == TrustTier.PROBATION  # type: ignore[union-attr]

    # Now swap to high-performing profiler -> should change tier
    profiler_high = _make_profiler("analyst", n_obs=10, n_advances=9)
    tracker._profiler = profiler_high
    tracker.evaluate_all()
    assert tracker.score_for("analyst").tier == TrustTier.EXEMPLARY  # type: ignore[union-attr]

    # Check audit log
    entries = writer.read_all()
    tier_changes = [e for e in entries if e.action.startswith("tier_change:")]
    assert len(tier_changes) == 1
    assert tier_changes[0].category == AuditCategory.CONFIG_CHANGE
    assert tier_changes[0].actor == "trust_tracker"
    assert tier_changes[0].action == "tier_change:analyst"
    assert "probation -> exemplary" in tier_changes[0].detail


def test_no_audit_on_same_tier(tmp_path) -> None:
    """No audit entry should be written when the tier stays the same."""
    audit_path = tmp_path / "audit.ndjson"
    writer = AppendOnlyAuditWriter(audit_path)

    profiler = _make_profiler("analyst", n_obs=10, n_advances=7)
    policy = TrustPolicy()
    tracker = TrustTracker(policy, profiler=profiler, audit_writer=writer)

    # Evaluate twice with the same profiler -> same tier
    tracker.evaluate_all()
    tracker.evaluate_all()

    entries = writer.read_all()
    tier_changes = [e for e in entries if e.action.startswith("tier_change:")]
    assert len(tier_changes) == 0


def test_summary() -> None:
    """summary() should return a formatted table."""
    profiler = _make_multi_role_profiler({
        "analyst": (10, 7),
        "coach": (10, 3),
    })
    policy = TrustPolicy()
    tracker = TrustTracker(policy, profiler=profiler)
    tracker.evaluate_all()

    text = tracker.summary()
    assert "Role" in text
    assert "Tier" in text
    assert "analyst" in text
    assert "coach" in text
    assert "trusted" in text  # analyst tier
    assert "established" in text  # coach tier


def test_score_updates_on_re_evaluate() -> None:
    """Re-evaluating with a different profiler should update scores."""
    profiler1 = _make_profiler("analyst", n_obs=10, n_advances=2)
    policy = TrustPolicy()
    tracker = TrustTracker(policy, profiler=profiler1)

    tracker.evaluate_all()
    assert tracker.score_for("analyst").tier == TrustTier.PROBATION  # type: ignore[union-attr]

    # Swap profiler
    profiler2 = _make_profiler("analyst", n_obs=10, n_advances=9)
    tracker._profiler = profiler2
    tracker.evaluate_all()
    assert tracker.score_for("analyst").tier == TrustTier.EXEMPLARY  # type: ignore[union-attr]


def test_unknown_role_returns_none() -> None:
    """Querying an unknown role should return None."""
    policy = TrustPolicy()
    tracker = TrustTracker(policy)

    assert tracker.score_for("nonexistent") is None
    assert tracker.budget_for("nonexistent") is None
