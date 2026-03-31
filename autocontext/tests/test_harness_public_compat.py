"""Compatibility checks for published harness import surfaces."""

from __future__ import annotations

from pathlib import Path

from autocontext.harness.heartbeat import (
    AgentStatus,
    EscalationLevel,
    HeartbeatRecord,
    StallEvent,
    StallPolicy,
)
from autocontext.harness.heartbeat.monitor import HeartbeatMonitor
from autocontext.harness.identity import AgentIdentity, IdentityTrait, SoulDocument
from autocontext.harness.identity.store import IdentityStore
from autocontext.harness.pipeline.tiered_gate import TieredGateOrchestrator, TieredGateResult
from autocontext.harness.trust import TrustBudget, TrustScore, TrustTier
from autocontext.harness.trust.policy import TrustPolicy
from autocontext.harness.validation.strategy_validator import StrategyValidator


def test_legacy_harness_public_imports_still_work(tmp_path: Path) -> None:
    assert AgentStatus.ACTIVE == "active"
    assert EscalationLevel.WARN == "warn"
    assert TrustTier.PROBATION == "probation"

    identity = AgentIdentity(
        role="competitor",
        soul=SoulDocument(
            role="competitor",
            purpose="Win the task",
            principles=("Act on evidence",),
            constraints=("Produce valid output",),
        ),
        traits=(IdentityTrait(name="advance_rate", value=0.5, trend=0.0, observations=3),),
        trust_tier="probation",
        total_generations=3,
        total_advances=1,
        created_at=AgentIdentity.now(),
        last_updated=AgentIdentity.now(),
    )
    store = IdentityStore(tmp_path / "identities")
    store.save(identity)
    assert store.load("competitor") is not None

    monitor = HeartbeatMonitor(StallPolicy())
    monitor.record_heartbeat(
        HeartbeatRecord(
            agent_id="agent-1",
            role="competitor",
            timestamp=HeartbeatRecord.now(),
            generation=1,
            status=AgentStatus.ACTIVE,
        )
    )
    assert monitor.status("agent-1") is not None

    validator = StrategyValidator.from_required_fields({"moves"})
    report = validator.validate({})
    assert report.is_valid is False

    budget = TrustBudget.for_tier(TrustTier.PROBATION)
    assert budget.max_retries == 1

    score = TrustScore(
        role="competitor",
        tier=TrustTier.PROBATION,
        raw_score=0.2,
        observations=3,
        confidence=0.15,
        last_updated=TrustScore.now(),
    )
    assert TrustPolicy().budget_for(score).tier == TrustTier.PROBATION

    result = TieredGateResult(
        tier="validity",
        decision="retry",
        validity_passed=False,
        validity_errors=["missing moves"],
        quality_delta=None,
        quality_threshold=None,
        retry_budget_remaining=1,
        validity_retry_budget_remaining=1,
    )
    assert result.tier == "validity"
    assert TieredGateOrchestrator is not None
    assert StallEvent is not None
