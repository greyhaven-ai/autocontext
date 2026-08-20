"""Shared goal-run supervisor report for continue-until-verified execution."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Self

from pydantic import Field, model_validator

from autocontext.util.models import StrictModel

GoalRunStatus = Literal[
    "continued",
    "verified_complete",
    "blocked",
    "budget_exhausted",
    "verifier_failed",
    "no_progress",
    "canceled",
]
GoalActionKind = Literal["run", "solve", "improve", "mission", "campaign"]
GoalActionStatus = Literal["planned", "running", "completed", "failed", "canceled"]
GoalDecisionKind = Literal["continue", "stop"]
GoalStopReason = Literal["verified_complete", "blocked", "budget_exhausted", "verifier_failed", "no_progress", "canceled"]


class GoalEvidenceRef(StrictModel):
    uri: str = Field(min_length=1)
    summary: str = Field(min_length=1)


class GoalBudget(StrictModel):
    max_iterations: int | None = Field(ge=0)
    max_actions: int | None = Field(ge=0)
    max_seconds: float | None = Field(ge=0)
    max_tokens: int | None = Field(ge=0)
    max_no_progress_iterations: int | None = Field(ge=0)


class GoalUsage(StrictModel):
    iterations: int = Field(ge=0)
    actions: int = Field(ge=0)
    elapsed_seconds: float = Field(ge=0)
    tokens: int = Field(ge=0)
    no_progress_count: int = Field(ge=0)


class GoalActionRecord(StrictModel):
    action_id: str = Field(min_length=1)
    action_kind: GoalActionKind
    status: GoalActionStatus
    started_at: str | None
    completed_at: str | None
    inner_run_ref: str | None
    summary: str = Field(min_length=1)
    evidence_refs: list[GoalEvidenceRef]
    negative_result_ledger_uri: str | None


class GoalVerifierState(StrictModel):
    verifier_ref: str = Field(min_length=1)
    verified: bool
    verifier_failed: bool
    confidence: float | None
    summary: str = Field(min_length=1)
    evidence_refs: list[GoalEvidenceRef]

    @model_validator(mode="after")
    def verified_or_failed(self) -> Self:
        if self.verified and self.verifier_failed:
            raise ValueError("verified and verifier_failed are mutually exclusive")
        return self


class GoalSupervisorDecision(StrictModel):
    decision_id: str = Field(min_length=1)
    decision_kind: GoalDecisionKind
    status: GoalRunStatus
    next_action_kind: GoalActionKind | None
    stop_reason: GoalStopReason | None
    rationale: str = Field(min_length=1)
    evidence_refs: list[GoalEvidenceRef]

    @model_validator(mode="after")
    def decision_matches_shape(self) -> Self:
        if self.decision_kind == "continue":
            if self.status != "continued" or self.next_action_kind is None or self.stop_reason is not None:
                raise ValueError("continue decisions require continued status, next action, and no stop reason")
            return self
        if self.status == "continued" or self.next_action_kind is not None or self.stop_reason != self.status:
            raise ValueError("stop decisions require terminal status, no next action, and matching stop reason")
        return self


class GoalRunReport(StrictModel):
    schema_version: Literal[1]
    goal_id: str = Field(min_length=1)
    goal_run_id: str = Field(min_length=1)
    scenario_name: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    generated_at: str = Field(min_length=1)
    resume_token: str = Field(min_length=1)
    status: GoalRunStatus
    verifier_ref: str = Field(min_length=1)
    budget: GoalBudget
    usage: GoalUsage
    actions: list[GoalActionRecord]
    verifier_state: GoalVerifierState
    decision: GoalSupervisorDecision

    @model_validator(mode="after")
    def status_matches_decision(self) -> Self:
        if self.status != self.decision.status:
            raise ValueError("status must match decision.status")
        return self

    def to_markdown(self) -> str:
        decision = self.decision
        next_action = decision.next_action_kind or "none"
        return "\n".join(
            [
                f"# Goal Run Report: {self.goal_run_id}",
                f"- Goal: {self.goal_id}",
                f"- Scenario: {self.scenario_name}",
                f"- Status: {self.status}",
                f"- Decision: {decision.decision_kind}",
                f"- Next action: {next_action}",
                f"- Rationale: {decision.rationale}",
                "",
            ]
        )


def build_goal_run_report(
    *,
    goal_id: str,
    goal_run_id: str,
    scenario_name: str,
    objective: str,
    verifier_ref: str,
    budget: dict[str, Any],
    usage: dict[str, Any],
    actions: list[dict[str, Any]],
    verifier_state: dict[str, Any],
    next_action_kind: GoalActionKind | None = None,
    blocked_reason: str | None = None,
    requested_cancel: bool = False,
    decision_id: str | None = None,
    generated_at: str | None = None,
    resume_token: str | None = None,
) -> GoalRunReport:
    budget_model = GoalBudget.from_dict(budget)
    usage_model = GoalUsage.from_dict(usage)
    verifier_model = GoalVerifierState.from_dict(verifier_state)
    action_models = [GoalActionRecord.from_dict(action) for action in actions]
    decision = _decide_goal_run(
        budget=budget_model,
        usage=usage_model,
        verifier_state=verifier_model,
        next_action_kind=next_action_kind,
        blocked_reason=blocked_reason,
        requested_cancel=requested_cancel,
        decision_id=decision_id or f"{goal_run_id}:decision:{usage_model.iterations}",
    )
    return GoalRunReport(
        schema_version=1,
        goal_id=goal_id,
        goal_run_id=goal_run_id,
        scenario_name=scenario_name,
        objective=objective,
        generated_at=generated_at or datetime.now().astimezone().isoformat(),
        resume_token=resume_token or f"{goal_run_id}:{usage_model.iterations}",
        status=decision.status,
        verifier_ref=verifier_ref,
        budget=budget_model,
        usage=usage_model,
        actions=action_models,
        verifier_state=verifier_model,
        decision=decision,
    )


def _decide_goal_run(
    *,
    budget: GoalBudget,
    usage: GoalUsage,
    verifier_state: GoalVerifierState,
    next_action_kind: GoalActionKind | None,
    blocked_reason: str | None,
    requested_cancel: bool,
    decision_id: str,
) -> GoalSupervisorDecision:
    evidence_refs = verifier_state.evidence_refs
    if requested_cancel:
        return _stop_decision(decision_id, "canceled", "Goal canceled by operator.", evidence_refs)
    if verifier_state.verifier_failed:
        return _stop_decision(decision_id, "verifier_failed", "Verifier failed before goal completion.", evidence_refs)
    if verifier_state.verified:
        return _stop_decision(decision_id, "verified_complete", "Verifier confirmed the goal is complete.", evidence_refs)
    if blocked_reason:
        return _stop_decision(decision_id, "blocked", blocked_reason, evidence_refs)
    if _budget_exhausted(budget, usage):
        return _stop_decision(decision_id, "budget_exhausted", "Goal budget exhausted before verification.", evidence_refs)
    if _no_progress_exhausted(budget, usage):
        return _stop_decision(decision_id, "no_progress", "No-progress limit reached with evidence.", evidence_refs)
    return GoalSupervisorDecision(
        decision_id=decision_id,
        decision_kind="continue",
        status="continued",
        next_action_kind=next_action_kind or "mission",
        stop_reason=None,
        rationale=f"Verifier incomplete; continue with {next_action_kind or 'mission'} checkpoint.",
        evidence_refs=evidence_refs,
    )


def _stop_decision(
    decision_id: str,
    reason: GoalStopReason,
    rationale: str,
    evidence_refs: list[GoalEvidenceRef],
) -> GoalSupervisorDecision:
    return GoalSupervisorDecision(
        decision_id=decision_id,
        decision_kind="stop",
        status=reason,
        next_action_kind=None,
        stop_reason=reason,
        rationale=rationale,
        evidence_refs=evidence_refs,
    )


def _budget_exhausted(budget: GoalBudget, usage: GoalUsage) -> bool:
    return any(
        [
            budget.max_iterations is not None and usage.iterations >= budget.max_iterations,
            budget.max_actions is not None and usage.actions >= budget.max_actions,
            budget.max_seconds is not None and usage.elapsed_seconds >= budget.max_seconds,
            budget.max_tokens is not None and usage.tokens >= budget.max_tokens,
        ]
    )


def _no_progress_exhausted(budget: GoalBudget, usage: GoalUsage) -> bool:
    return budget.max_no_progress_iterations is not None and usage.no_progress_count >= budget.max_no_progress_iterations


__all__ = [
    "GoalActionKind",
    "GoalActionRecord",
    "GoalActionStatus",
    "GoalBudget",
    "GoalDecisionKind",
    "GoalEvidenceRef",
    "GoalRunReport",
    "GoalRunStatus",
    "GoalStopReason",
    "GoalSupervisorDecision",
    "GoalUsage",
    "GoalVerifierState",
    "build_goal_run_report",
]
