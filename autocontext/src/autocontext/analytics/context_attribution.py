"""Ablation-backed attribution for immutable context-bundle components (AC-974)."""

from __future__ import annotations

import math
from collections import defaultdict
from statistics import fmean
from typing import Any, Literal

from pydantic import Field, FiniteFloat, model_validator

from autocontext.context_bundles.models import BundleComponent, ContextBundle, stable_digest, utf16_sort_key
from autocontext.util.models import StrictModel

EvidenceLevel = Literal["causal_ablation", "paired_shadow", "component_correlated"]
ComponentDisposition = Literal["retained", "uncertain", "demotion_candidate", "harmful"]


class ControlledAttributionTrial(StrictModel):
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
    with_component_score: FiniteFloat
    without_component_score: FiniteFloat
    token_cost: int = Field(ge=0)
    tested_at: str = Field(min_length=1)
    interaction_component_digests: list[str] = Field(default_factory=list)

    @property
    def effect(self) -> float:
        effect = self.with_component_score - self.without_component_score
        if not math.isfinite(effect):
            raise ValueError("controlled attribution trial effect must be finite")
        return _round_six(effect)


class ComponentAttribution(StrictModel):
    attribution_id: str = Field(min_length=1)
    component_kind: str = Field(min_length=1)
    component_key: str = Field(min_length=1)
    component_digest: str = Field(min_length=1)
    evidence_level: EvidenceLevel
    effect: FiniteFloat
    confidence: float = Field(ge=0.0, le=1.0)
    evaluator_epoch: str = Field(min_length=1)
    trial_cohort: str = Field(min_length=1)
    token_cost: int = Field(ge=0)
    last_tested_bundle_digest: str = Field(min_length=1)
    comparison_bundle_digest: str | None = None
    tested_at: str = Field(min_length=1)
    disposition: ComponentDisposition
    classification_neutral_effect: FiniteFloat = Field(ge=0.0)
    classification_high_token_cost: int = Field(ge=0)
    trial_ids: list[str]
    matched_pair_keys: list[str] = Field(default_factory=list)
    source_trial_digests: list[str] = Field(default_factory=list)
    interaction_component_digests: list[str]
    supersedes_attribution_id: str | None = None
    legacy_unverified: bool = False


class ContextAttributionLedger(StrictModel):
    schema_version: Literal[2] = 2
    scenario: str = Field(min_length=1)
    trials: list[ControlledAttributionTrial]
    attributions: list[ComponentAttribution]

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_ledger(cls, value: Any) -> Any:
        if isinstance(value, dict) and value.get("schema_version") == 1:
            return _migrate_v1_attribution_ledger(value)
        return value


class ReablationCandidate(StrictModel):
    component_kind: str = Field(min_length=1)
    component_key: str = Field(min_length=1)
    component_digest: str = Field(min_length=1)
    token_cost: int = Field(ge=0)
    estimated_trial_cost: int = Field(ge=1)
    confidence: float = Field(ge=0.0, le=1.0)
    last_tested_generation: int = Field(ge=0)
    last_tested_bundle_digest: str = Field(min_length=1)
    interaction_risk: float = Field(ge=0.0, le=1.0)


class ReablationPolicy(StrictModel):
    cadence_generations: int = Field(default=5, ge=1)
    plateau_generations: int = Field(default=3, ge=1)
    budget: int = Field(default=10, ge=0)


class ReablationPlan(StrictModel):
    trigger: str
    selected: list[ReablationCandidate]
    deferred: list[ReablationCandidate]
    spent: int = Field(ge=0)
    budget: int = Field(ge=0)


class PromptComponentSelection(StrictModel):
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

    if not math.isfinite(neutral_effect) or neutral_effect < 0:
        raise ValueError("neutral_effect must be finite and non-negative")
    if isinstance(high_token_cost, bool) or not isinstance(high_token_cost, int) or high_token_cost < 0:
        raise ValueError("high_token_cost must be a non-negative integer")
    groups: dict[tuple[str, str, str, str, EvidenceLevel], list[ControlledAttributionTrial]] = defaultdict(list)
    seen_trial_ids: set[str] = set()
    seen_pair_keys: set[str] = set()
    for trial in trials:
        if trial.trial_id in seen_trial_ids:
            raise ValueError(f"duplicate attribution trial: {trial.trial_id}")
        seen_trial_ids.add(trial.trial_id)
        if trial.evaluator_epoch != evaluator_epoch:
            raise ValueError("attribution trial evaluator epoch mismatch")
        if trial.evidence_level == "component_correlated":
            raise ValueError("component_correlated evidence cannot be constructed from a controlled trial")
        if trial.tested_bundle_digest == trial.comparison_bundle_digest:
            raise ValueError("controlled attribution requires distinct tested and comparison bundles")
        pair_key = _controlled_trial_pair_key(trial)
        if pair_key in seen_pair_keys:
            raise ValueError("duplicate matched attribution pair")
        seen_pair_keys.add(pair_key)
        groups[
            (
                trial.component_kind,
                trial.component_key,
                trial.component_digest,
                trial.tested_bundle_digest,
                trial.evidence_level,
            )
        ].append(trial)

    records: list[ComponentAttribution] = []
    for key, group in sorted(
        groups.items(),
        key=lambda item: tuple(utf16_sort_key(value) for value in item[0]),
    ):
        component_kind, component_key, component_digest, bundle_digest, evidence_level = key
        comparison_digests = {trial.comparison_bundle_digest for trial in group}
        if len(comparison_digests) != 1:
            raise ValueError("controlled attribution group mixes comparison bundles")
        cohorts = {trial.trial_cohort for trial in group}
        if len(cohorts) != 1:
            raise ValueError("controlled attribution group mixes trial cohorts")
        comparison_digest = next(iter(comparison_digests))
        cohort = next(iter(cohorts))
        effects = [trial.effect for trial in group]
        effect = _round_six(fmean(effects))
        confidence = _trial_confidence(effects)
        token_cost = max(trial.token_cost for trial in group)
        disposition = _classify(effect, token_cost, neutral_effect, high_token_cost)
        trial_ids = sorted((trial.trial_id for trial in group), key=utf16_sort_key)
        matched_pair_keys = sorted((_controlled_trial_pair_key(trial) for trial in group), key=utf16_sort_key)
        source_trial_digests = sorted((_controlled_trial_digest(trial) for trial in group), key=utf16_sort_key)
        interactions = sorted(
            {digest for trial in group for digest in trial.interaction_component_digests},
            key=utf16_sort_key,
        )
        tested_at = max(trial.tested_at for trial in group)
        attribution_id = _controlled_attribution_id(
            component_kind=component_kind,
            component_key=component_key,
            component_digest=component_digest,
            tested_bundle_digest=bundle_digest,
            comparison_bundle_digest=comparison_digest,
            evaluator_epoch=evaluator_epoch,
            trial_cohort=cohort,
            evidence_level=evidence_level,
            classification_neutral_effect=neutral_effect,
            classification_high_token_cost=high_token_cost,
            trial_ids=trial_ids,
            matched_pair_keys=matched_pair_keys,
            source_trial_digests=source_trial_digests,
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
                comparison_bundle_digest=comparison_digest,
                tested_at=tested_at,
                disposition=disposition,
                classification_neutral_effect=neutral_effect,
                classification_high_token_cost=high_token_cost,
                trial_ids=trial_ids,
                matched_pair_keys=matched_pair_keys,
                source_trial_digests=source_trial_digests,
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
    by_id: dict[str, ControlledAttributionTrial] = {}
    for trial in trials:
        if trial.trial_id in by_id:
            raise ValueError(f"duplicate attribution trial: {trial.trial_id}")
        by_id[trial.trial_id] = trial
    if attribution.trial_ids != sorted(set(attribution.trial_ids), key=utf16_sort_key):
        raise ValueError("causal attribution trial IDs must be unique and sorted")
    try:
        referenced = [by_id[trial_id] for trial_id in attribution.trial_ids]
    except KeyError as exc:
        raise ValueError(f"missing referenced trial: {exc.args[0]}") from exc
    if not referenced:
        raise ValueError("causal attribution has no referenced trials")
    effect = _validate_controlled_attribution_binding(attribution, referenced)
    if effect != attribution.effect:
        raise ValueError("causal attribution effect does not match its source trials")
    return effect


def append_reablation(
    ledger: ContextAttributionLedger,
    *,
    trials: list[ControlledAttributionTrial],
    attributions: list[ComponentAttribution],
) -> ContextAttributionLedger:
    """Append new evidence and link it to prior component attribution history."""

    existing_trial_ids = {trial.trial_id for trial in ledger.trials}
    if len(existing_trial_ids) != len(ledger.trials):
        raise ValueError("attribution ledger contains duplicate trial IDs")
    existing_pair_keys = {_controlled_trial_pair_key(trial) for trial in ledger.trials}
    if len(existing_pair_keys) != len(ledger.trials):
        raise ValueError("attribution ledger contains duplicate matched pairs")
    new_by_id: dict[str, ControlledAttributionTrial] = {}
    new_pair_keys: set[str] = set()
    for trial in trials:
        if trial.trial_id in existing_trial_ids or trial.trial_id in new_by_id:
            raise ValueError(f"duplicate attribution trial: {trial.trial_id}")
        pair_key = _controlled_trial_pair_key(trial)
        if pair_key in existing_pair_keys or pair_key in new_pair_keys:
            raise ValueError("duplicate matched attribution pair")
        new_by_id[trial.trial_id] = trial
        new_pair_keys.add(pair_key)

    referenced_trial_ids: set[str] = set()
    seen_attribution_ids = {record.attribution_id for record in ledger.attributions}
    for record in attributions:
        if record.attribution_id in seen_attribution_ids:
            raise ValueError(f"duplicate component attribution: {record.attribution_id}")
        seen_attribution_ids.add(record.attribution_id)
        if record.evidence_level == "component_correlated":
            raise ValueError("re-ablation evidence must come from controlled trials")
        if record.trial_ids != sorted(set(record.trial_ids), key=utf16_sort_key):
            raise ValueError("controlled attribution trial IDs must be unique and sorted")
        try:
            referenced = [new_by_id[trial_id] for trial_id in record.trial_ids]
        except KeyError as exc:
            raise ValueError(f"re-ablation attribution references a non-new trial: {exc.args[0]}") from exc
        if not referenced:
            raise ValueError("re-ablation attribution has no referenced trials")
        overlap = referenced_trial_ids.intersection(record.trial_ids)
        if overlap:
            raise ValueError(f"re-ablation trial is referenced more than once: {min(overlap)}")
        referenced_trial_ids.update(record.trial_ids)
        effect = _validate_controlled_attribution_binding(record, referenced)
        if effect != record.effect:
            raise ValueError("controlled attribution effect does not match its source trials")
    unreferenced = set(new_by_id).difference(referenced_trial_ids)
    if unreferenced:
        raise ValueError(f"re-ablation trial is not bound to an attribution: {min(unreferenced)}")

    latest: dict[tuple[str, str, str], ComponentAttribution] = {}
    for record in ledger.attributions:
        latest[(record.component_kind, record.component_key, record.component_digest)] = record
    linked: list[ComponentAttribution] = []
    for record in attributions:
        previous = latest.get((record.component_kind, record.component_key, record.component_digest))
        linked.append(
            record.model_copy(update={"supersedes_attribution_id": previous.attribution_id if previous is not None else None})
        )
    return ledger.model_copy(update={"trials": [*ledger.trials, *trials], "attributions": [*ledger.attributions, *linked]})


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
            utf16_sort_key(item.component_kind),
            utf16_sort_key(item.component_key),
            utf16_sort_key(item.component_digest),
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


def _controlled_trial_pair_key(trial: ControlledAttributionTrial) -> str:
    """Identify one evaluation unit independently of its caller-provided trial ID."""

    return stable_digest(
        {
            "component_kind": trial.component_kind,
            "component_key": trial.component_key,
            "component_digest": trial.component_digest,
            "tested_bundle_digest": trial.tested_bundle_digest,
            "comparison_bundle_digest": trial.comparison_bundle_digest,
            "evaluator_epoch": trial.evaluator_epoch,
            "trial_cohort": trial.trial_cohort,
            "fixture_digest": trial.fixture_digest,
            "seed": trial.seed,
        }
    )


def _controlled_trial_digest(trial: ControlledAttributionTrial) -> str:
    """Bind an attribution to the exact persisted source observation."""

    return stable_digest(trial.to_dict())


def _controlled_attribution_id(
    *,
    component_kind: str,
    component_key: str,
    component_digest: str,
    tested_bundle_digest: str,
    comparison_bundle_digest: str,
    evaluator_epoch: str,
    trial_cohort: str,
    evidence_level: EvidenceLevel,
    classification_neutral_effect: float,
    classification_high_token_cost: int,
    trial_ids: list[str],
    matched_pair_keys: list[str],
    source_trial_digests: list[str],
) -> str:
    return stable_digest(
        {
            "component_kind": component_kind,
            "component_key": component_key,
            "component_digest": component_digest,
            "tested_bundle_digest": tested_bundle_digest,
            "comparison_bundle_digest": comparison_bundle_digest,
            "evaluator_epoch": evaluator_epoch,
            "trial_cohort": trial_cohort,
            "evidence_level": evidence_level,
            "classification_neutral_effect": classification_neutral_effect,
            "classification_high_token_cost": classification_high_token_cost,
            "trial_ids": trial_ids,
            "matched_pair_keys": matched_pair_keys,
            "source_trial_digests": source_trial_digests,
        }
    )


def _validate_controlled_attribution_binding(
    attribution: ComponentAttribution,
    trials: list[ControlledAttributionTrial],
) -> float:
    if attribution.legacy_unverified:
        raise ValueError("legacy attribution lacks verified controlled-trial provenance")
    if attribution.comparison_bundle_digest is None:
        raise ValueError("controlled attribution is missing its comparison bundle")
    if not trials:
        raise ValueError("controlled attribution has no referenced trials")
    _validate_classification_policy(attribution)

    pair_keys: list[str] = []
    for trial in trials:
        if (
            trial.evidence_level != attribution.evidence_level
            or trial.component_kind != attribution.component_kind
            or trial.component_key != attribution.component_key
            or trial.component_digest != attribution.component_digest
            or trial.tested_bundle_digest != attribution.last_tested_bundle_digest
            or trial.comparison_bundle_digest != attribution.comparison_bundle_digest
            or trial.evaluator_epoch != attribution.evaluator_epoch
            or trial.trial_cohort != attribution.trial_cohort
        ):
            raise ValueError("referenced trial does not match the controlled attribution")
        if trial.evidence_level == "component_correlated":
            raise ValueError("component_correlated evidence cannot back a controlled attribution")
        if trial.tested_bundle_digest == trial.comparison_bundle_digest:
            raise ValueError("controlled attribution requires distinct tested and comparison bundles")
        pair_keys.append(_controlled_trial_pair_key(trial))

    if len(pair_keys) != len(set(pair_keys)):
        raise ValueError("controlled attribution references duplicate matched pairs")
    expected_pair_keys = sorted(pair_keys, key=utf16_sort_key)
    if attribution.matched_pair_keys != expected_pair_keys:
        raise ValueError("controlled attribution matched-pair binding mismatch")
    expected_source_digests = sorted(
        (_controlled_trial_digest(trial) for trial in trials),
        key=utf16_sort_key,
    )
    if attribution.source_trial_digests != expected_source_digests:
        raise ValueError("controlled attribution source-trial binding mismatch")

    expected_id = _controlled_attribution_id(
        component_kind=attribution.component_kind,
        component_key=attribution.component_key,
        component_digest=attribution.component_digest,
        tested_bundle_digest=attribution.last_tested_bundle_digest,
        comparison_bundle_digest=attribution.comparison_bundle_digest,
        evaluator_epoch=attribution.evaluator_epoch,
        trial_cohort=attribution.trial_cohort,
        evidence_level=attribution.evidence_level,
        classification_neutral_effect=attribution.classification_neutral_effect,
        classification_high_token_cost=attribution.classification_high_token_cost,
        trial_ids=attribution.trial_ids,
        matched_pair_keys=attribution.matched_pair_keys,
        source_trial_digests=attribution.source_trial_digests,
    )
    if attribution.attribution_id != expected_id:
        raise ValueError("controlled attribution identity does not match its source trials")

    effects = [trial.effect for trial in trials]
    if attribution.confidence != _trial_confidence(effects):
        raise ValueError("controlled attribution confidence does not match its source trials")
    expected_token_cost = max(trial.token_cost for trial in trials)
    if attribution.token_cost != expected_token_cost:
        raise ValueError("controlled attribution token cost does not match its source trials")
    if attribution.tested_at != max(trial.tested_at for trial in trials):
        raise ValueError("controlled attribution test time does not match its source trials")
    expected_interactions = sorted(
        {digest for trial in trials for digest in trial.interaction_component_digests},
        key=utf16_sort_key,
    )
    if attribution.interaction_component_digests != expected_interactions:
        raise ValueError("controlled attribution interactions do not match its source trials")
    effect = _round_six(fmean(effects))
    expected_disposition = _classify(
        effect,
        expected_token_cost,
        attribution.classification_neutral_effect,
        attribution.classification_high_token_cost,
    )
    if attribution.disposition != expected_disposition:
        raise ValueError("controlled attribution disposition does not match its classification policy")
    return effect


def _round_six(value: float) -> float:
    """Round binary64 values to six decimals, breaking ties away from zero."""

    scaled = abs(value) * 1_000_000
    if not math.isfinite(scaled):
        return value
    rounded = math.floor(scaled + 0.5) / 1_000_000
    result = math.copysign(rounded, value)
    return 0.0 if result == 0 else result


def _validate_classification_policy(attribution: ComponentAttribution) -> None:
    neutral_effect = attribution.classification_neutral_effect
    if (
        isinstance(neutral_effect, bool)
        or not isinstance(neutral_effect, (int, float))
        or not math.isfinite(neutral_effect)
        or neutral_effect < 0
    ):
        raise ValueError("classification neutral effect must be finite and non-negative")
    high_token_cost = attribution.classification_high_token_cost
    if (
        isinstance(high_token_cost, bool)
        or not isinstance(high_token_cost, int)
        or high_token_cost < 0
        or high_token_cost > (1 << 53) - 1
    ):
        raise ValueError("classification high token cost must be a non-negative safe integer")


def _verified_disposition(attribution: ComponentAttribution) -> ComponentDisposition:
    if attribution.legacy_unverified:
        raise ValueError("legacy attribution lacks verified classification provenance")
    _validate_classification_policy(attribution)
    if not math.isfinite(attribution.effect):
        raise ValueError("attribution effect must be finite")
    if (
        isinstance(attribution.token_cost, bool)
        or not isinstance(attribution.token_cost, int)
        or attribution.token_cost < 0
        or attribution.token_cost > (1 << 53) - 1
    ):
        raise ValueError("attribution token cost must be a non-negative safe integer")
    return _classify(
        attribution.effect,
        attribution.token_cost,
        attribution.classification_neutral_effect,
        attribution.classification_high_token_cost,
    )


def _trial_confidence(effects: list[float]) -> float:
    if not effects:
        return 0.0
    nonzero = [effect for effect in effects if effect != 0]
    if not nonzero:
        agreement = 1.0
    else:
        positive = sum(effect > 0 for effect in nonzero)
        agreement = max(positive, len(nonzero) - positive) / len(nonzero)
    return _round_six(min(1.0, len(effects) / 4.0) * agreement)


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
    try:
        verified_disposition = _verified_disposition(record)
    except (TypeError, ValueError):
        verified_disposition = "uncertain"
        policy_valid = False
    else:
        policy_valid = True
    if not policy_valid or record.disposition != verified_disposition:
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
            reason="attribution disposition failed classification-policy verification; retain pending re-ablation",
        )
    demoted = verified_disposition in {"demotion_candidate", "harmful"}
    return PromptComponentSelection(
        component_kind=component.kind.value,
        component_key=component.key,
        component_digest=component.digest,
        included=not demoted,
        disposition=verified_disposition,
        evidence_level=record.evidence_level,
        confidence=record.confidence,
        evaluator_epoch=record.evaluator_epoch,
        trial_cohort=record.trial_cohort,
        last_tested_bundle_digest=record.last_tested_bundle_digest,
        token_cost=token_cost,
        reason=(
            "demoted from prompt assembly; attribution history retained" if demoted else "retained by current-bundle attribution"
        ),
    )


def _migrate_v1_attribution_ledger(data: dict[str, Any]) -> dict[str, Any]:
    """Preserve legacy history without treating invented bindings as verified."""

    migrated = dict(data)
    migrated["schema_version"] = 2
    raw_attributions = data.get("attributions")
    attributions: list[Any] = []
    provenance_fields = {
        "comparison_bundle_digest",
        "classification_neutral_effect",
        "classification_high_token_cost",
        "matched_pair_keys",
        "source_trial_digests",
    }
    if isinstance(raw_attributions, list):
        for raw_record in raw_attributions:
            if not isinstance(raw_record, dict):
                attributions.append(raw_record)
                continue
            record = dict(raw_record)
            has_verified_shape = provenance_fields.issubset(record)
            record.setdefault("comparison_bundle_digest", None)
            record.setdefault("classification_neutral_effect", 0.0)
            record.setdefault("classification_high_token_cost", 256)
            record.setdefault("matched_pair_keys", [])
            record.setdefault("source_trial_digests", [])
            record["legacy_unverified"] = bool(record.get("legacy_unverified", False) or not has_verified_shape)
            attributions.append(record)
    migrated["attributions"] = attributions
    return migrated


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
