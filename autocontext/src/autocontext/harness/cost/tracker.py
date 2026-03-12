"""Cost tracker — cumulative cost tracking per role, model, and generation."""
from __future__ import annotations

import threading
from collections.abc import Callable

from autocontext.harness.core.types import RoleUsage
from autocontext.harness.cost.calculator import CostCalculator
from autocontext.harness.cost.types import CostRecord, CostSummary


class CostTracker:
    """Tracks cumulative API costs with per-role and per-generation breakdown."""

    def __init__(
        self,
        calculator: CostCalculator | None = None,
        budget_limit: float | None = None,
        on_budget_alert: Callable[[float, float], None] | None = None,
    ) -> None:
        self._calculator = calculator or CostCalculator()
        self._budget_limit = budget_limit
        self._on_budget_alert = on_budget_alert
        self._alerted = False
        self._records: list[tuple[CostRecord, str, int | None]] = []  # (record, role, generation)
        self._lock = threading.Lock()

    def record(self, usage: RoleUsage, role: str, generation: int | None = None) -> CostRecord:
        cost = self._calculator.from_usage(usage)
        with self._lock:
            self._records.append((cost, role, generation))
            total = sum(r.total_cost for r, _, _ in self._records)
        if (
            self._budget_limit is not None
            and total > self._budget_limit
            and not self._alerted
            and self._on_budget_alert
        ):
            self._alerted = True
            self._on_budget_alert(total, self._budget_limit)
        return cost

    def cost_by_role(self) -> dict[str, float]:
        with self._lock:
            by_role: dict[str, float] = {}
            for record, role, _ in self._records:
                by_role[role] = by_role.get(role, 0.0) + record.total_cost
            return {k: round(v, 6) for k, v in by_role.items()}

    def cost_by_model(self) -> dict[str, float]:
        with self._lock:
            by_model: dict[str, float] = {}
            for record, _, _ in self._records:
                by_model[record.model] = by_model.get(record.model, 0.0) + record.total_cost
            return {k: round(v, 6) for k, v in by_model.items()}

    def cost_per_generation(self) -> list[tuple[int, float]]:
        with self._lock:
            by_gen: dict[int, float] = {}
            for record, _, gen in self._records:
                if gen is not None:
                    by_gen[gen] = by_gen.get(gen, 0.0) + record.total_cost
            return sorted(by_gen.items())

    def summary(self) -> CostSummary:
        with self._lock:
            records = [r for r, _, _ in self._records]
        return CostSummary.from_records(records)

    def reset(self) -> None:
        with self._lock:
            self._records.clear()
            self._alerted = False
