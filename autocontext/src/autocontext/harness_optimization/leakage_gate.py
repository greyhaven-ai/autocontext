"""Verified/exploratory leakage gate (AC-879).

Consumes a LeakageAudit and the run mode. Verified runs fail closed on
contaminated or unknown status, or on missing prompt provenance. Exploratory
runs always advance but are stamped non-promotion-grade. Caller-gated: this
function never reads settings, so default runs are unaffected until a caller
invokes it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from autocontext.harness_optimization.leakage import LeakageAudit

if TYPE_CHECKING:
    from autocontext.config.settings import AppSettings


def leakage_gate_active_for(settings: AppSettings, scenario_name: str) -> bool:
    """True iff the leakage gate is globally enabled AND the scenario is allowlisted.

    The allowlist is the comma-separated ``harness_leakage_gate_scenarios``
    setting; an empty allowlist means no scenario is active even when the global
    flag is on. This is the sole opt-in decision: callers check it and only run
    the leakage audit + gate when it returns True.
    """

    allowlist = {s.strip() for s in settings.harness_leakage_gate_scenarios.split(",") if s.strip()}
    return settings.harness_leakage_gate_enabled and scenario_name in allowlist


@dataclass(frozen=True, slots=True)
class LeakageGateDecision:
    advance: bool
    non_promotion_grade: bool
    rationale: str


def evaluate_leakage_gate(audit: LeakageAudit, mode: str, prompt_provenance: str) -> LeakageGateDecision:
    if mode == "exploratory":
        return LeakageGateDecision(
            advance=True,
            non_promotion_grade=True,
            rationale="exploratory override: advancing non-promotion-grade regardless of leakage",
        )
    # verified
    blockers: list[str] = []
    if audit.status != "clean":
        blockers.append(f"leakage status {audit.status}: {'; '.join(audit.reasons)}")
    if not prompt_provenance.strip():
        blockers.append("missing prompt provenance")
    if blockers:
        return LeakageGateDecision(
            advance=False,
            non_promotion_grade=True,
            rationale="verified run blocked: " + "; ".join(blockers),
        )
    return LeakageGateDecision(advance=True, non_promotion_grade=False, rationale="verified run clean")
