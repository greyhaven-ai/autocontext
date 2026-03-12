"""Meta-optimization coordinator — wires audit, cost, and profiling."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from autocontext.harness.audit.types import AuditCategory, AuditEntry
from autocontext.harness.audit.writer import AppendOnlyAuditWriter
from autocontext.harness.core.types import RoleUsage
from autocontext.harness.cost.calculator import CostCalculator
from autocontext.harness.cost.tracker import CostTracker
from autocontext.harness.cost.types import CostSummary
from autocontext.harness.meta.advisor import ConfigAdvisor
from autocontext.harness.meta.collector import MetricsCollector
from autocontext.harness.meta.profiler import PerformanceProfiler
from autocontext.harness.meta.types import ConfigRecommendation, RoleMetric, RoleProfile

if TYPE_CHECKING:
    from autocontext.config.settings import AppSettings


class MetaOptimizer:
    """Thin coordinator wiring audit, cost tracking, and performance profiling."""

    def __init__(
        self,
        audit_writer: AppendOnlyAuditWriter | None = None,
        cost_tracker: CostTracker | None = None,
        collector: MetricsCollector | None = None,
        profiler: PerformanceProfiler | None = None,
        advisor: ConfigAdvisor | None = None,
    ) -> None:
        self._audit = audit_writer
        self._cost = cost_tracker
        self._collector = collector
        self._profiler = profiler
        self._advisor = advisor

    @classmethod
    def from_settings(cls, settings: AppSettings) -> MetaOptimizer:
        """Factory: create a MetaOptimizer from AppSettings."""
        audit_writer = None
        cost_tracker = None
        collector = None
        profiler = None
        advisor = None

        if settings.audit_enabled:
            audit_writer = AppendOnlyAuditWriter(settings.audit_log_path)

        if settings.cost_tracking_enabled:
            cost_tracker = CostTracker(
                budget_limit=settings.cost_budget_limit,
            )

        if settings.meta_profiling_enabled:
            collector = MetricsCollector()
            profiler = PerformanceProfiler(collector, min_observations=settings.meta_min_observations)
            # Build current model config for advisor
            current_config = {
                "model_competitor": settings.model_competitor,
                "model_analyst": settings.model_analyst,
                "model_coach": settings.model_coach,
                "model_architect": settings.model_architect,
            }
            advisor = ConfigAdvisor(profiler, current_config=current_config)

        return cls(
            audit_writer=audit_writer,
            cost_tracker=cost_tracker,
            collector=collector,
            profiler=profiler,
            advisor=advisor,
        )

    def record_llm_call(self, role: str, usage: RoleUsage, generation: int | None = None) -> None:
        """Record a single LLM API call to audit log and cost tracker."""
        if self._audit:
            cost_info: dict[str, Any] = {"model": usage.model, "tokens": usage.input_tokens + usage.output_tokens}
            if self._cost:
                record = self._cost.record(usage, role, generation)
                cost_info["cost_usd"] = record.total_cost
            self._audit.append(AuditEntry(
                timestamp=AuditEntry.now(),
                category=AuditCategory.LLM_CALL,
                actor=role,
                action="llm_call",
                metadata=cost_info,
            ))
        elif self._cost:
            self._cost.record(usage, role, generation)

    def record_gate_decision(self, decision: str, delta: float, generation: int) -> None:
        """Record a backpressure gate decision to the audit log."""
        if self._audit:
            self._audit.append(AuditEntry(
                timestamp=AuditEntry.now(),
                category=AuditCategory.GATE_DECISION,
                actor="system",
                action=f"gate_{decision}",
                detail=f"generation {generation}",
                metadata={"delta": delta, "generation": generation},
            ))

    def record_generation(
        self,
        generation: int,
        role_usages: dict[str, RoleUsage],
        gate_decision: str,
        score_delta: float,
    ) -> None:
        """Record a full generation's role metrics to the collector."""
        if self._collector is None:
            return
        calculator = self._cost._calculator if self._cost else CostCalculator()
        metrics = []
        for role, usage in role_usages.items():
            cost_record = calculator.from_usage(usage)
            metrics.append(RoleMetric(
                role=role,
                generation=generation,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                latency_ms=usage.latency_ms,
                cost=cost_record.total_cost,
                gate_decision=gate_decision,
                score_delta=score_delta,
            ))
        self._collector.add_generation(metrics)

    def cost_summary(self) -> CostSummary | None:
        """Return aggregated cost summary, or None if cost tracking is disabled."""
        if self._cost:
            return self._cost.summary()
        return None

    def profiles(self) -> dict[str, RoleProfile]:
        """Return role performance profiles, or empty dict if profiling is disabled."""
        if self._profiler:
            return self._profiler.all_profiles()
        return {}

    def recommendations(self) -> list[ConfigRecommendation]:
        """Return configuration recommendations, or empty list if advisor is disabled."""
        if self._advisor:
            return self._advisor.recommend()
        return []

    def report(self) -> str:
        """Generate a human-readable meta-optimization report."""
        sections: list[str] = ["# Meta-Optimization Report", ""]

        if self._cost:
            summary = self._cost.summary()
            sections.append(f"## Cost: ${summary.total_cost:.4f} total ({summary.records_count} API calls)")
            by_role = self._cost.cost_by_role()
            if by_role:
                for role, cost in sorted(by_role.items(), key=lambda x: x[1], reverse=True):
                    sections.append(f"  - {role}: ${cost:.4f}")
            sections.append("")

        if self._profiler:
            sections.append(self._profiler.summary())
            sections.append("")

        if self._advisor:
            sections.append(self._advisor.summary())

        return "\n".join(sections)
