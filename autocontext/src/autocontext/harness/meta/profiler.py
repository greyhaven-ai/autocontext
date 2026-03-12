"""Performance profiler — builds role profiles from collected metrics."""
from __future__ import annotations

import math

from autocontext.harness.meta.collector import MetricsCollector
from autocontext.harness.meta.types import RoleMetric, RoleProfile


class PerformanceProfiler:
    """Builds aggregated performance profiles from MetricsCollector data."""

    def __init__(self, collector: MetricsCollector, min_observations: int = 3) -> None:
        self._collector = collector
        self._min_observations = min_observations

    def profile(self, role: str) -> RoleProfile | None:
        observations = self._collector.for_role(role)
        if len(observations) < self._min_observations:
            return None
        return self._build_profile(role, observations)

    def all_profiles(self) -> dict[str, RoleProfile]:
        result: dict[str, RoleProfile] = {}
        for role in self._collector.roles():
            p = self.profile(role)
            if p is not None:
                result[role] = p
        return result

    def ranked_by_efficiency(self) -> list[RoleProfile]:
        profiles = list(self.all_profiles().values())
        return sorted(
            profiles,
            key=lambda p: p.cost_per_advance if math.isfinite(p.cost_per_advance) else float("inf"),
        )

    def ranked_by_cost(self) -> list[RoleProfile]:
        profiles = list(self.all_profiles().values())
        return sorted(profiles, key=lambda p: p.mean_cost_per_gen, reverse=True)

    def summary(self) -> str:
        profiles = self.all_profiles()
        if not profiles:
            return "No profiles available (insufficient observations)."
        lines = ["# Role Performance Profiles", ""]
        lines.append("| Role | Gens | Advance% | Mean Cost | Cost/Advance | Token Eff |")
        lines.append("|------|------|----------|-----------|--------------|-----------|")
        for name in sorted(profiles):
            p = profiles[name]
            cpa = f"${p.cost_per_advance:.4f}" if math.isfinite(p.cost_per_advance) else "N/A"
            lines.append(
                f"| {p.role} | {p.generations_observed} | "
                f"{p.advance_rate:.0%} | ${p.mean_cost_per_gen:.4f} | "
                f"{cpa} | {p.token_efficiency:.4f} |"
            )
        return "\n".join(lines)

    @staticmethod
    def _build_profile(role: str, observations: list[RoleMetric]) -> RoleProfile:
        n = len(observations)
        advances = sum(1 for m in observations if m.gate_decision == "advance")
        total_tokens = sum(m.total_tokens for m in observations)
        total_cost = sum(m.cost for m in observations)
        total_latency = sum(m.latency_ms for m in observations)

        advance_rate = advances / n if n > 0 else 0.0
        mean_tokens = total_tokens / n if n > 0 else 0.0
        mean_latency = total_latency / n if n > 0 else 0.0
        mean_cost = total_cost / n if n > 0 else 0.0
        cost_per_advance = total_cost / advances if advances > 0 else float("inf")

        # Token efficiency: score improvement per 1K tokens (only counting positive deltas)
        positive_deltas = [(m.score_delta, m.total_tokens) for m in observations if m.score_delta > 0]
        if positive_deltas:
            total_positive_delta = sum(d for d, _ in positive_deltas)
            total_positive_tokens = sum(t for _, t in positive_deltas)
            token_efficiency = (total_positive_delta / (total_positive_tokens / 1000)) if total_positive_tokens > 0 else 0.0
        else:
            token_efficiency = 0.0

        return RoleProfile(
            role=role,
            generations_observed=n,
            advance_rate=round(advance_rate, 4),
            mean_tokens=round(mean_tokens, 1),
            mean_latency_ms=round(mean_latency, 1),
            mean_cost_per_gen=round(mean_cost, 6),
            cost_per_advance=round(cost_per_advance, 6) if math.isfinite(cost_per_advance) else float("inf"),
            token_efficiency=round(token_efficiency, 6),
        )
