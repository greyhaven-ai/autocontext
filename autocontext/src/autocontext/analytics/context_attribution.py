"""Ablation-backed attribution for immutable context-bundle components (AC-974)."""

from __future__ import annotations

from collections import defaultdict
from statistics import fmean
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field

from autocontext.context_bundles.models import BundleComponent, ContextBundle, stable_digest

EvidenceLevel = Literal["causal_ablation", "paired_shadow", "component_correlated"]
ComponentDisposition = Literal["retained", "uncertain", "demotion_candidate", "harmful"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls.model_validate(data)


class ControlledAttributionTrial(_StrictModel):
    """One matched with/without-component observation."""

    trial_id: str = Field(min_length=1)
    component_kind: str = Field(min_length=1)
    component_key: str = Field(min_length=1)
    component_digest: str = Field(min_length=1)
    tested_bundle_digest: str = Field(min_length=1)
    comparison_bundle_digest: str = Field(min_length=1)
    evaluator_epoch: str = Field(min_length=1)
    trial_cohort: str = Field(min_length=1)
    fixture_digest: str = Field(min_length=1)
    seed: int
    evidence_level: EvidenceLevel
    with_component_score: float
    without_component_score: float
    token_cost: int = Field(ge=0)
    tested_at: str = Field(min_length=1)
    interaction_component_digests: list[str] = Field(default_factory=list)

    @property
    def effect(self) -> float:
        return round(self.with_component_score - self.without_component_score, 6)


class ComponentAttribution(_StrictModel):
    attribution_id: str = Field(min_length=1)
    component_kind: str = Field(min_length=1)
    component_key: str = Field(min_length=1)
    component_digest: str = Field(min_length=1)
    evidence_level: EvidenceLevel
    effect: float
    confidence: float = Field(ge=0.0, le=1.0)
    evaluator_epoch: str = Field(min_length=1)
    trial_cohort: str = Field(min_length=1)
    token_cost: int = Field(ge=0)
    last_tested_bundle_digest: str = Field(min_length=1)
    tested_at: str = Field(min_length=1)
    disposition: ComponentDisposition
    trial_ids: list[str]
    interaction_component_digests: list[str]
    supersedes_attribution_id: str | None = None


class ContextAttributionLedger(_StrictModel):
    schema_version: int = 1
    scenario: str = Field(min_length=1)
    trials: list[ControlledAttributionTrial]
    attributions: list[ComponentAttribution]


class ReablationCandidate(_StrictModel):
    component_kind: str = Field(min_length=1)
    component_key: str = Field(min_length=1)
    component_digest: str = Field(min_length=1)
    token_cost: int = Field(ge=0)
    estimated_trial_cost: int = Field(ge=1)
    confidence: float = Field(ge=0.0, le=1.0)
    last_tested_generation: int = Field(ge=0)
    last_tested_bundle_digest: str = Field(min_length=1)
    interaction_risk: float = Field(ge=0.0, le=1.0)


class ReablationPolicy(_StrictModel):
    cadence_generations: int = Field(default=5, ge=1)
    plateau_generations: int = Field(default=3, ge=1)
    budget: int = Field(default=10, ge=0)


class ReablationPlan(_StrictModel):
    trigger: str
    selected: list[ReablationCandidate]
    deferred: list[ReablationCandidate]
    spent: int = Field(ge=0)
    budget: int = Field(ge=0)


class PromptComponentSelection(_StrictModel):
    component_kind: str
    component_key: str
    component_digest: str
    included: bool
    disposition: ComponentDisposition
    evidence_level: EvidenceLevel | None
    confidence: float
    evaluator_epoch: str | None
    trial_cohort: str | None
    last_tested_bundle_digest: str | None
    token_cost: int
    reason: str


def attribute_controlled_trials(
    trials: list[ControlledAttributionTrial],
    *,
    evaluator_epoch: str,
    neutral_effect: float = 0.0,
    high_token_cost: int = 256,
) -> list[ComponentAttribution]:
    """Aggregate matched trials without upgrading their declared evidence level."""

    groups: dict[tuple[str, str, str, str, str, EvidenceLevel], list[ControlledAttributionTrial]] = defaultdict(list)
    seen_trial_ids: set[str] = set()
    for trial in trials:
        if trial.trial_id in seen_trial_ids:
            raise ValueError(f"duplicate attribution trial: {trial.trial_id}")
        seen_trial_ids.add(trial.trial_id)
        if trial.evaluator_epoch != evaluator_epoch:
            raise ValueError("attribution trial evaluator epoch mismatch")
        if trial.evidence_level == "component_correlated":
            raise ValueError("component_correlated evidence cannot be constructed from a controlled trial")
        groups[
            (
                trial.component_kind,
                trial.component_key,
                trial.component_digest,
                trial.tested_bundle_digest,
                trial.trial_cohort,
                trial.evidence_level,
            )
        ].append(trial)

    records: list[ComponentAttribution] = []
    for key, group in sorted(groups.items(), key=lambda item: item[0]):
        component_kind, component_key, component_digest, bundle_digest, cohort, evidence_level = key
        effects = [trial.effect for trial in group]
        effect = round(fmean(effects), 6)
        confidence = _trial_confidence(effects)
        token_cost = max(trial.token_cost for trial in group)
        disposition = _classify(effect, token_cost, neutral_effect, high_token_cost)
        trial_ids = sorted(trial.trial_id for trial in group)
        interactions = sorted({digest for trial in group for digest in trial.interaction_component_digests})
        tested_at = max(trial.tested_at for trial in group)
        attribution_id = stable_digest(
            {
                "component_digest": component_digest,
                "tested_bundle_digest": bundle_digest,
                "evaluator_epoch": evaluator_epoch,
                "trial_ids": trial_ids,
                "evidence_level": evidence_level,
            }
        )
        records.append(
            ComponentAttribution(
                attribution_id=attribution_id,
                component_kind=component_kind,
                component_key=component_key,
                component_digest=component_digest,
                evidence_level=evidence_level,
                effect=effect,
                confidence=confidence,
                evaluator_epoch=evaluator_epoch,
                trial_cohort=cohort,
                token_cost=token_cost,
                last_tested_bundle_digest=bundle_digest,
                tested_at=tested_at,
                disposition=disposition,
                trial_ids=trial_ids,
                interaction_component_digests=interactions,
            )
        )
    return records


def reconstruct_causal_credit(
    attribution: ComponentAttribution,
    trials: list[ControlledAttributionTrial],
) -> float:
    """Recompute a causal effect solely from the referenced persisted pairs."""

    if attribution.evidence_level != "causal_ablation":
        raise ValueError("only causal_ablation records carry causal credit")
    by_id = {trial.trial_id: trial for trial in trials}
    try:
        referenced = [by_id[trial_id] for trial_id in attribution.trial_ids]
    except KeyError as exc:
        raise ValueError(f"missing referenced trial: {exc.args[0]}") from exc
    if not referenced:
        raise ValueError("causal attribution has no referenced trials")
    for trial in referenced:
        if (
            trial.evidence_level != "causal_ablation"
            or trial.component_digest != attribution.component_digest
            or trial.tested_bundle_digest != attribution.last_tested_bundle_digest
            or trial.evaluator_epoch != attribution.evaluator_epoch
            or trial.trial_cohort != attribution.trial_cohort
        ):
            raise ValueError("referenced trial does not match the causal attribution")
    return round(fmean(trial.effect for trial in referenced), 6)


def append_reablation(
    ledger: ContextAttributionLedger,
    *,
    trials: list[ControlledAttributionTrial],
    attributions: list[ComponentAttribution],
) -> ContextAttributionLedger:
    """Append new evidence and link it to prior component attribution history."""

    latest: dict[tuple[str, str, str], ComponentAttribution] = {}
    for record in ledger.attributions:
        latest[(record.component_kind, record.component_key, record.component_digest)] = record
    linked: list[ComponentAttribution] = []
    for record in attributions:
        previous = latest.get((record.component_kind, record.component_key, record.component_digest))
        linked.append(
            record.model_copy(
                update={"supersedes_attribution_id": previous.attribution_id if previous is not None else None}
            )
        )
    return ledger.model_copy(
        update={"trials": [*ledger.trials, *trials], "attributions": [*ledger.attributions, *linked]}
    )


def plan_reablation(
    candidates: list[ReablationCandidate],
    *,
    current_generation: int,
    last_reablation_generation: int,
    plateau_length: int,
    current_bundle_digest: str,
    policy: ReablationPolicy | None = None,
) -> ReablationPlan:
    """Select high-value re-tests under a hard cost budget."""

    active_policy = policy or ReablationPolicy()
    cadence_due = current_generation - last_reablation_generation >= active_policy.cadence_generations
    plateau_due = plateau_length >= active_policy.plateau_generations
    bundle_changed = any(item.last_tested_bundle_digest != current_bundle_digest for item in candidates)
    if not cadence_due and not plateau_due and not bundle_changed:
        return ReablationPlan(trigger="not_due", selected=[], deferred=candidates, spent=0, budget=active_policy.budget)
    trigger = "plateau" if plateau_due else "cadence" if cadence_due else "bundle_changed"
    ordered = sorted(
        candidates,
        key=lambda item: (
            -_reablation_priority(item, current_generation, current_bundle_digest),
            item.component_kind,
            item.component_key,
        ),
    )
    selected: list[ReablationCandidate] = []
    deferred: list[ReablationCandidate] = []
    spent = 0
    for candidate in ordered:
        if spent + candidate.estimated_trial_cost <= active_policy.budget:
            selected.append(candidate)
            spent += candidate.estimated_trial_cost
        else:
            deferred.append(candidate)
    return ReablationPlan(
        trigger=trigger,
        selected=selected,
        deferred=deferred,
        spent=spent,
        budget=active_policy.budget,
    )


def select_prompt_components(
    bundle: ContextBundle,
    attributions: list[ComponentAttribution],
    *,
    token_costs: dict[str, int] | None = None,
) -> list[PromptComponentSelection]:
    """Demote current-bundle low-value components without deleting their evidence."""

    latest: dict[str, ComponentAttribution] = {}
    for record in attributions:
        if record.evaluator_epoch != bundle.evaluator_epoch:
            continue
        latest[record.component_digest] = record
    costs = token_costs or {}
    selections: list[PromptComponentSelection] = []
    for component in bundle.components:
        current_record = latest.get(component.digest)
        cost = max(0, costs.get(component.digest, len(component.content.split())))
        selections.append(_select_component(component, current_record, bundle.digest, cost))
    return selections


def render_context_attribution_report(attributions: list[ComponentAttribution]) -> str:
    """Render promotion/pruning evidence without conflating correlation and causality."""

    lines = ["# Context attribution"]
    for record in attributions:
        qualifier = {
            "causal_ablation": "controlled with/without-component effect",
            "paired_shadow": "matched shadow effect; causal isolation not established",
            "component_correlated": "edit-size correlation only; not causal",
        }[record.evidence_level]
        lines.append(
            f"- {record.component_kind}/{record.component_key} `{record.component_digest}`: "
            f"{record.effect:+.4f}, {record.disposition}, evidence={record.evidence_level} "
            f"({qualifier}), confidence={record.confidence:.2f}, evaluator={record.evaluator_epoch}, "
            f"cohort={record.trial_cohort}, tokens={record.token_cost}, "
            f"last_bundle={record.last_tested_bundle_digest}"
        )
    return "\n".join(lines)


def _trial_confidence(effects: list[float]) -> float:
    if not effects:
        return 0.0
    nonzero = [effect for effect in effects if effect != 0]
    if not nonzero:
        agreement = 1.0
    else:
        positive = sum(effect > 0 for effect in nonzero)
        agreement = max(positive, len(nonzero) - positive) / len(nonzero)
    return round(min(1.0, len(effects) / 4.0) * agreement, 6)


def _classify(
    effect: float,
    token_cost: int,
    neutral_effect: float,
    high_token_cost: int,
) -> ComponentDisposition:
    if effect < -neutral_effect:
        return "harmful"
    if effect > neutral_effect:
        return "retained"
    if token_cost >= high_token_cost:
        return "demotion_candidate"
    return "uncertain"


def _reablation_priority(
    candidate: ReablationCandidate,
    current_generation: int,
    current_bundle_digest: str,
) -> float:
    age = max(0, current_generation - candidate.last_tested_generation)
    bundle_change = 1.0 if candidate.last_tested_bundle_digest != current_bundle_digest else 0.0
    return (
        candidate.token_cost / 16.0
        + (1.0 - candidate.confidence) * 10.0
        + age
        + candidate.interaction_risk * 10.0
        + bundle_change * 15.0
    )


def _select_component(
    component: BundleComponent,
    record: ComponentAttribution | None,
    current_bundle_digest: str,
    token_cost: int,
) -> PromptComponentSelection:
    if record is None:
        return PromptComponentSelection(
            component_kind=component.kind.value,
            component_key=component.key,
            component_digest=component.digest,
            included=True,
            disposition="uncertain",
            evidence_level=None,
            confidence=0.0,
            evaluator_epoch=None,
            trial_cohort=None,
            last_tested_bundle_digest=None,
            token_cost=token_cost,
            reason="no attribution evidence; retain pending a controlled test",
        )
    if record.last_tested_bundle_digest != current_bundle_digest:
        return PromptComponentSelection(
            component_kind=component.kind.value,
            component_key=component.key,
            component_digest=component.digest,
            included=True,
            disposition="uncertain",
            evidence_level=record.evidence_level,
            confidence=record.confidence,
            evaluator_epoch=record.evaluator_epoch,
            trial_cohort=record.trial_cohort,
            last_tested_bundle_digest=record.last_tested_bundle_digest,
            token_cost=token_cost,
            reason="bundle composition changed; retain until interaction re-ablation",
        )
    demoted = record.disposition in {"demotion_candidate", "harmful"}
    return PromptComponentSelection(
        component_kind=component.kind.value,
        component_key=component.key,
        component_digest=component.digest,
        included=not demoted,
        disposition=record.disposition,
        evidence_level=record.evidence_level,
        confidence=record.confidence,
        evaluator_epoch=record.evaluator_epoch,
        trial_cohort=record.trial_cohort,
        last_tested_bundle_digest=record.last_tested_bundle_digest,
        token_cost=token_cost,
        reason=(
            "demoted from prompt assembly; attribution history retained"
            if demoted
            else "retained by current-bundle attribution"
        ),
    )


__all__ = [
    "ComponentAttribution",
    "ComponentDisposition",
    "ContextAttributionLedger",
    "ControlledAttributionTrial",
    "EvidenceLevel",
    "PromptComponentSelection",
    "ReablationCandidate",
    "ReablationPlan",
    "ReablationPolicy",
    "append_reablation",
    "attribute_controlled_trials",
    "plan_reablation",
    "reconstruct_causal_credit",
    "render_context_attribution_report",
    "select_prompt_components",
]
