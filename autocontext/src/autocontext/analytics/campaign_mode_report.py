"""Shared campaign-mode report for multi-branch runs."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field

CampaignTerminalState = Literal["active", "completed", "failed", "budget_exhausted", "canceled"]
BranchTerminalState = Literal["pending", "running", "continued", "pruned", "succeeded", "failed", "budget_exhausted", "canceled"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls.model_validate(data)


class CampaignBranchBudget(_StrictModel):
    max_tokens: int | None = Field(ge=0)
    max_seconds: float | None = Field(ge=0)
    max_evaluations: int | None = Field(ge=0)


class CampaignBranchUsage(_StrictModel):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    evaluations: int = Field(ge=0)
    runner_seconds: float = Field(ge=0)


class CampaignEvalLane(_StrictModel):
    lane_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    verifier_contract_ref: str = Field(min_length=1)
    seeds: list[str]
    holdout_refs: list[str]
    weight: float = Field(ge=0)


class CampaignBranch(_StrictModel):
    branch_id: str = Field(min_length=1)
    parent_branch_id: str | None
    hypothesis_node_id: str | None
    objective: str = Field(min_length=1)
    budget: CampaignBranchBudget
    usage: CampaignBranchUsage
    terminal_state: BranchTerminalState
    score: float | None
    verifier_passed: bool | None
    terminal_reason: str = Field(min_length=1)


class CampaignBranchLineageEdge(_StrictModel):
    parent_branch_id: str = Field(min_length=1)
    child_branch_id: str = Field(min_length=1)


class CampaignEvidenceReference(_StrictModel):
    uri: str = Field(min_length=1)
    summary: str = Field(min_length=1)


class CampaignEvidenceShareItem(_StrictModel):
    share_id: str = Field(min_length=1)
    from_branch_id: str = Field(min_length=1)
    to_branch_ids: list[str]
    summary: str = Field(min_length=1)
    included: bool
    evidence_refs: list[CampaignEvidenceReference]


class CampaignEvidencePolicy(_StrictModel):
    max_shared_items: int = Field(ge=0)
    max_summary_chars: int = Field(ge=1)


class CampaignEvidenceSharing(_StrictModel):
    policy: CampaignEvidencePolicy
    items: list[CampaignEvidenceShareItem]


class CampaignBranchSummary(_StrictModel):
    branch_count: int = Field(ge=0)
    succeeded: int = Field(ge=0)
    failed: int = Field(ge=0)
    pruned: int = Field(ge=0)
    budget_exhausted: int = Field(ge=0)
    running: int = Field(ge=0)


class CampaignRecommendation(_StrictModel):
    branch_id: str = Field(min_length=1)
    score: float | None
    reason: str = Field(min_length=1)


class CampaignLinkedReports(_StrictModel):
    progress_report_uri: str | None
    utilization_report_uri: str | None
    negative_result_ledger_uri: str | None


class CampaignModeReport(_StrictModel):
    schema_version: Literal[1]
    campaign_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    scenario_name: str = Field(min_length=1)
    generated_at: str = Field(min_length=1)
    terminal_state: CampaignTerminalState
    branch_budget_defaults: CampaignBranchBudget
    eval_lanes: list[CampaignEvalLane]
    branches: list[CampaignBranch]
    branch_lineage: list[CampaignBranchLineageEdge]
    evidence_sharing: CampaignEvidenceSharing
    branch_summary: CampaignBranchSummary
    final_recommendation: CampaignRecommendation | None
    linked_reports: CampaignLinkedReports

    def to_markdown(self) -> str:
        recommendation = (
            f"- Recommendation: {self.final_recommendation.branch_id} "
            f"(score={self.final_recommendation.score}) — {self.final_recommendation.reason}"
            if self.final_recommendation
            else "- Recommendation: none"
        )
        return "\n".join(
            [
                f"# Campaign Mode Report: {self.campaign_id}",
                f"- Run: {self.run_id}",
                f"- Scenario: {self.scenario_name}",
                f"- Terminal state: {self.terminal_state}",
                f"- Branches: {self.branch_summary.branch_count}",
                recommendation,
                "",
                "## Shared Evidence",
                render_campaign_evidence_share(self) or "- None",
                "",
            ]
        )


def build_campaign_mode_report(
    *,
    campaign_id: str,
    run_id: str,
    scenario_name: str,
    terminal_state: CampaignTerminalState,
    branch_budget_defaults: dict[str, Any],
    eval_lanes: list[dict[str, Any]],
    branches: list[dict[str, Any]],
    shared_evidence: list[dict[str, Any]],
    linked_reports: dict[str, Any],
    evidence_policy: dict[str, Any] | None = None,
    generated_at: str | None = None,
) -> CampaignModeReport:
    defaults = CampaignBranchBudget.from_dict(branch_budget_defaults).to_dict()
    branch_models = [_branch_from_dict(branch, defaults) for branch in branches]
    policy = _evidence_policy_from_builder_input(evidence_policy)
    return CampaignModeReport(
        schema_version=1,
        campaign_id=campaign_id,
        run_id=run_id,
        scenario_name=scenario_name,
        generated_at=generated_at or datetime.now().astimezone().isoformat(),
        terminal_state=terminal_state,
        branch_budget_defaults=CampaignBranchBudget.from_dict(defaults),
        eval_lanes=[CampaignEvalLane.from_dict(lane) for lane in eval_lanes],
        branches=branch_models,
        branch_lineage=_branch_lineage(branch_models),
        evidence_sharing=CampaignEvidenceSharing(
            policy=policy,
            items=_evidence_items(shared_evidence, policy),
        ),
        branch_summary=_branch_summary(branch_models),
        final_recommendation=_recommendation(branch_models),
        linked_reports=CampaignLinkedReports.from_dict(linked_reports),
    )


def render_campaign_evidence_share(report: CampaignModeReport) -> str:
    lines: list[str] = []
    for item in report.evidence_sharing.items:
        if not item.included:
            continue
        evidence = "; ".join(ref.summary for ref in item.evidence_refs[:2])
        targets = ", ".join(item.to_branch_ids) or "all branches"
        lines.append(f"- {item.share_id}: {item.from_branch_id} -> {targets}: {item.summary}; evidence: {evidence}")
    return "\n".join(lines)


def _branch_from_dict(data: dict[str, Any], defaults: dict[str, Any]) -> CampaignBranch:
    merged = {**data, "budget": data.get("budget") or defaults}
    return CampaignBranch.from_dict(merged)


def _evidence_policy_from_builder_input(data: dict[str, Any] | None) -> CampaignEvidencePolicy:
    if data is None:
        return CampaignEvidencePolicy(max_shared_items=2, max_summary_chars=240)
    return CampaignEvidencePolicy.from_dict(data)


def _branch_lineage(branches: list[CampaignBranch]) -> list[CampaignBranchLineageEdge]:
    return [
        CampaignBranchLineageEdge(parent_branch_id=branch.parent_branch_id, child_branch_id=branch.branch_id)
        for branch in branches
        if branch.parent_branch_id
    ]


def _branch_summary(branches: list[CampaignBranch]) -> CampaignBranchSummary:
    active = {"pending", "running", "continued"}
    return CampaignBranchSummary(
        branch_count=len(branches),
        succeeded=sum(1 for branch in branches if branch.terminal_state == "succeeded"),
        failed=sum(1 for branch in branches if branch.terminal_state == "failed"),
        pruned=sum(1 for branch in branches if branch.terminal_state == "pruned"),
        budget_exhausted=sum(1 for branch in branches if branch.terminal_state == "budget_exhausted"),
        running=sum(1 for branch in branches if branch.terminal_state in active),
    )


def _recommendation(branches: list[CampaignBranch]) -> CampaignRecommendation | None:
    eligible = [branch for branch in branches if branch.score is not None and branch.verifier_passed is True]
    if not eligible:
        eligible = [branch for branch in branches if branch.score is not None]
    if not eligible:
        return None
    best = max(eligible, key=lambda branch: branch.score if branch.score is not None else float("-inf"))
    return CampaignRecommendation(branch_id=best.branch_id, score=best.score, reason=best.terminal_reason)


def _evidence_items(
    items: list[dict[str, Any]],
    policy: CampaignEvidencePolicy,
) -> list[CampaignEvidenceShareItem]:
    result: list[CampaignEvidenceShareItem] = []
    included_count = 0
    for item in items:
        included = bool(item.get("evidence_refs")) and included_count < policy.max_shared_items
        if included:
            included_count += 1
        result.append(
            CampaignEvidenceShareItem.from_dict(
                {
                    "share_id": item.get("share_id"),
                    "from_branch_id": item.get("from_branch_id"),
                    "to_branch_ids": item.get("to_branch_ids", []),
                    "summary": _truncate(str(item.get("summary", "")), policy.max_summary_chars),
                    "included": included,
                    "evidence_refs": item.get("evidence_refs", []),
                }
            )
        )
    return result


def _truncate(value: str, max_chars: int) -> str:
    return value if len(value) <= max_chars else value[: max_chars - 1].rstrip() + "…"


__all__ = [
    "BranchTerminalState",
    "CampaignBranch",
    "CampaignBranchBudget",
    "CampaignBranchLineageEdge",
    "CampaignBranchSummary",
    "CampaignBranchUsage",
    "CampaignEvalLane",
    "CampaignEvidencePolicy",
    "CampaignEvidenceReference",
    "CampaignEvidenceShareItem",
    "CampaignEvidenceSharing",
    "CampaignLinkedReports",
    "CampaignModeReport",
    "CampaignRecommendation",
    "CampaignTerminalState",
    "build_campaign_mode_report",
    "render_campaign_evidence_share",
]
