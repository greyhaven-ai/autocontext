"""Fail-closed quality and lifecycle-cost admission for routed executable skills."""

from __future__ import annotations

import hashlib
import math
import statistics
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from autocontext.artifacts.policy_candidate import FrozenContract
from autocontext.context_bundles.models import ComponentKind, ContextBundle, TrialLane, stable_digest
from autocontext.context_bundles.store import ContextBundleStore
from autocontext.execution.executable_skills import (
    COMPONENT_KEY,
    SCENARIO,
    ProfileV1,
    _json,
    inspect_executable_skill,
    verify_migration_output,
)
from autocontext.execution.skill_routing_models import SkillRoutingConfig, SkillRoutingResult
from autocontext.harness.benchmark_stats import paired_interval
from autocontext.util.json_io import read_json

ROUTE_KEY = "execution_policy"


class PolicyCase(FrozenContract):
    fixture_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    seed: int = Field(strict=True)
    group: str = Field(min_length=1)
    cohort: Literal["supported", "shifted"]
    input_json: str
    candidate_correct: bool = Field(strict=True)
    incumbent_correct: bool = Field(strict=True)
    candidate_cost_usd: float = Field(ge=0)
    incumbent_cost_usd: float = Field(ge=0)
    candidate_local_cost_usd: float = Field(ge=0)
    incumbent_local_cost_usd: float = Field(ge=0)
    candidate_trace_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    incumbent_trace_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    candidate_route: SkillRoutingResult
    incumbent_route: SkillRoutingResult
    skill_proposed: bool = Field(default=False, strict=True)
    skill_verified: bool = Field(default=False, strict=True)
    fallback: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def truthful_case(self) -> PolicyCase:
        value = _json(self.input_json)
        try:
            supported = ProfileV1.model_validate(value).schema_version == 1
        except ValueError:
            supported = False
        if (supported != (self.cohort == "supported") or self.skill_verified and not self.skill_proposed
                or self.fixture_digest != stable_digest(value)):
            raise ValueError("policy case cohort, identity or skill proposal is inconsistent")
        return self


class ExecutionPolicyEvidence(FrozenContract):
    schema_version: Literal["autocontext.execution-policy-gate.v1"] = "autocontext.execution-policy-gate.v1"
    candidate_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    incumbent_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    route_config_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    incumbent_config_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    manifest_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    evaluator_identity: str = Field(pattern=r"^[a-f0-9]{64}$")
    matched_trials_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    cohort: str = Field(min_length=1)
    trace_root: str = Field(min_length=1)
    source_split_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    candidate_setup_cost_usd: float = Field(gt=0)
    incumbent_setup_cost_usd: float = Field(ge=0)
    reuse_horizon: int = Field(default=100, ge=1, strict=True)
    shifted_frequency: float = Field(default=0.1, ge=0, le=1)
    min_savings: float = Field(default=0.05, ge=0, lt=1)
    quality_floor: float = Field(default=0.95, ge=0, le=1)
    regression_tolerance: float = Field(default=0.02, ge=0, le=1)
    min_skill_coverage: float = Field(default=0.5, ge=0, le=1)
    cost_weight: float = Field(default=1.0, gt=0)
    local_route_cost_per_second_usd: float = Field(gt=0)
    isolated_skill_cost_per_second_usd: float = Field(gt=0)
    monitor_min_cases: int = Field(default=12, ge=1, strict=True)
    monitor_quality_floor: float = Field(default=0.95, ge=0, le=1)
    monitor_max_fallback_frequency: float = Field(default=0.25, ge=0, le=1)
    monitor_max_cost_multiplier: float = Field(default=1.25, ge=1)
    cases: tuple[PolicyCase, ...] = Field(min_length=24)

    @property
    def digest(self) -> str:
        return stable_digest(self.model_dump(mode="json"))


class PolicyObservation(FrozenContract):
    request_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    bundle_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    evidence_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    evaluator_identity: str = Field(pattern=r"^[a-f0-9]{64}$")
    input_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    trace_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    cohort: Literal["supported", "shifted"]
    correct: bool = Field(strict=True)
    fallback: bool = Field(strict=True)
    cost_usd: float | None = Field(default=None, ge=0)


def monitored_cost_limit(evidence: ExecutionPolicyEvidence) -> float:
    supported = [case for case in evidence.cases if case.cohort == "supported"]
    shifted = [case for case in evidence.cases if case.cohort == "shifted"]
    expected_cost = evidence.candidate_setup_cost_usd / evidence.reuse_horizon + sum(
        weight * statistics.mean(case.candidate_cost_usd for case in cases)
        for weight, cases in ((1 - evidence.shifted_frequency, supported), (evidence.shifted_frequency, shifted)))
    expected_success = sum(weight * statistics.mean(case.candidate_correct for case in cases)
                           for weight, cases in ((1 - evidence.shifted_frequency, supported),
                                                 (evidence.shifted_frequency, shifted)))
    if expected_success <= 0:
        raise ValueError("monitored policy has no expected successes")
    return expected_cost / expected_success * evidence.monitor_max_cost_multiplier


def policy_template(bundle: ContextBundle) -> SkillRoutingConfig:
    components = [c for c in bundle.components if c.kind == ComponentKind.ROUTING_CONFIG and c.key == ROUTE_KEY]
    if len(components) != 1 or components[0].media_type != "application/json":
        raise ValueError("complete execution policy routing component is missing")
    template = SkillRoutingConfig.model_validate(_json(components[0].content))
    if not template.enabled or template.bundle_digest is not None or template.override is not None:
        raise ValueError("execution policy route must be enabled, bundle-independent and without override")
    return template


def is_execution_policy(bundle: ContextBundle) -> bool:
    return bundle.scenario == SCENARIO and any(
        c.kind == ComponentKind.ROUTING_CONFIG and c.key == ROUTE_KEY for c in bundle.components)


def has_executable_policy_skill(bundle: ContextBundle) -> bool:
    return is_execution_policy(bundle) and any(
        c.kind == ComponentKind.TOOL_SPEC and c.key == COMPONENT_KEY for c in bundle.components)


def _correct_input(cohort: Literal["supported", "shifted"], input_json: str, result: SkillRoutingResult) -> bool:
    if cohort == "shifted":
        return (result.status == "abstention" and result.output_json is None and bool(result.attempts)
                and result.attempts[-1].reason == "model_abstained")
    if result.status != "success" or result.output_json is None:
        return False
    try:
        verify_migration_output(ProfileV1.model_validate(_json(input_json)), result.output_json)
    except ValueError:
        return False
    return True


def _correct(case: PolicyCase, result: SkillRoutingResult) -> bool:
    return _correct_input(case.cohort, case.input_json, result)


def observe_execution_policy(evidence: ExecutionPolicyEvidence, result: SkillRoutingResult) -> PolicyObservation:
    config = SkillRoutingConfig.model_validate(_json(result.config_json))
    if (result.mode != "serving" or result.input_json is None or result.input_sha256 is None
            or result.evaluator_identity != evidence.evaluator_identity or config.bundle_digest != evidence.candidate_digest
            or config.digest != result.config_digest
            or config.model_copy(update={"bundle_digest": None}).digest != evidence.route_config_digest):
        raise ValueError("serving outcome is not bound to the active execution policy")
    if (result.input_sha256 != hashlib.sha256(result.input_json.encode()).hexdigest()
            or result.model_calls != sum(a.model_calls for a in result.attempts)
            or abs(result.accounted_model_cost_usd - sum(a.accounted_cost_usd for a in result.attempts)) > 1e-9
            or result.model_cost_complete != all(a.accounting in {"none", "provider-reported"}
                                                     for a in result.attempts)):
        raise ValueError("serving receipt is inconsistent")
    if read_json(Path(result.trace_path)) != result.model_dump(mode="json"):
        raise ValueError("serving trace changed before observation")
    try:
        cohort: Literal["supported", "shifted"] = (
            "supported" if ProfileV1.model_validate(_json(result.input_json)).schema_version == 1 else "shifted")
    except ValueError:
        cohort = "shifted"
    local_cost = (result.elapsed_seconds * evidence.local_route_cost_per_second_usd
                  + sum(a.elapsed_seconds for a in result.attempts if a.route == "skill")
                  * evidence.isolated_skill_cost_per_second_usd)
    return PolicyObservation(
        request_id=result.request_id, bundle_digest=evidence.candidate_digest, evidence_digest=evidence.digest,
        evaluator_identity=result.evaluator_identity, input_sha256=result.input_sha256,
        trace_digest=stable_digest(result.model_dump(mode="json")), cohort=cohort,
        correct=_correct_input(cohort, result.input_json, result), fallback=result.model_calls > 0,
        cost_usd=result.accounted_model_cost_usd + local_cost if result.model_cost_complete else None)


def require_execution_policy_gate(
    store: ContextBundleStore, bundle: ContextBundle, incumbent: ContextBundle, cohort: str,
    evidence: ExecutionPolicyEvidence | None = None,
) -> ExecutionPolicyEvidence:
    from autocontext.execution.skill_routing import routing_evaluator_identity

    if not has_executable_policy_skill(bundle):
        raise ValueError("no routed executable policy is installed")
    template = policy_template(bundle)
    incumbent_template = policy_template(incumbent)
    manifest = inspect_executable_skill(store, bundle.digest)
    evidence = evidence or ExecutionPolicyEvidence.model_validate(store.execution_policy_evidence(bundle.scenario, bundle.digest))
    trials = store.matched_trials(bundle.scenario, bundle.digest)
    if (evidence.candidate_digest != bundle.digest or evidence.incumbent_digest != incumbent.digest
            or evidence.route_config_digest != template.digest or evidence.incumbent_config_digest != incumbent_template.digest
            or evidence.manifest_digest != manifest.digest or evidence.evaluator_identity != routing_evaluator_identity()
            or evidence.cohort != cohort or evidence.matched_trials_digest != stable_digest([t.to_dict() for t in trials])):
        raise ValueError("execution policy evidence does not bind the current artifacts and evaluator")
    heldout = [t for t in trials if t.lane == TrialLane.HELDOUT]
    observed = {(case.fixture_digest, case.seed): case for case in evidence.cases}
    if evidence.source_split_digest != stable_digest(sorted(case.fixture_digest for case in evidence.cases)):
        raise ValueError("protected evidence split identity does not match its cases")
    training_inputs = set()
    for item in manifest.source_evidence:
        source = _json(item.content_json)
        if isinstance(source, dict) and isinstance(source.get("input_json"), str):
            training_inputs.add(stable_digest(_json(source["input_json"])))
    if training_inputs & {case.fixture_digest for case in evidence.cases}:
        raise ValueError("held-out case overlaps executable training evidence")
    trace_root = Path(evidence.trace_root).resolve()
    candidate_config = template.model_copy(update={"bundle_digest": bundle.digest})
    for case in evidence.cases:
        input_hash = hashlib.sha256(case.input_json.encode()).hexdigest()
        for route, expected_config, digest, local_cost, claimed_cost in (
            (case.candidate_route, candidate_config.digest, case.candidate_trace_digest,
             case.candidate_local_cost_usd, case.candidate_cost_usd),
            (case.incumbent_route, incumbent_template.digest, case.incumbent_trace_digest,
             case.incumbent_local_cost_usd, case.incumbent_cost_usd),
        ):
            path = Path(route.trace_path).resolve()
            if not path.is_relative_to(trace_root) or not path.is_file():
                raise ValueError("execution policy route trace is unavailable or outside the frozen trace root")
            raw = read_json(path)
            skill_elapsed = [a.elapsed_seconds for a in route.attempts if a.route == "skill" and a.status != "skipped"]
            measured_seconds = (route.elapsed_seconds, *skill_elapsed)
            measured_local_cost = (route.elapsed_seconds * evidence.local_route_cost_per_second_usd
                                   + sum(skill_elapsed) * evidence.isolated_skill_cost_per_second_usd)
            if (any(not math.isfinite(seconds) or seconds < 0 for seconds in measured_seconds)
                    or abs(local_cost - measured_local_cost) > 1e-9
                    or raw != route.model_dump(mode="json") or stable_digest(raw) != digest
                    or route.mode != "evaluation" or route.input_json != case.input_json
                    or route.input_sha256 != input_hash or route.config_digest != expected_config
                    or SkillRoutingConfig.model_validate(_json(route.config_json)).digest != expected_config
                    or route.evaluator_identity != evidence.evaluator_identity or not route.model_cost_complete
                    or route.model_calls != sum(a.model_calls for a in route.attempts)
                    or route.accounted_tokens != sum(a.accounted_tokens for a in route.attempts)
                    or abs(route.accounted_model_cost_usd - sum(a.accounted_cost_usd for a in route.attempts)) > 1e-9
                    or any(a.model_calls and (a.accounting != "provider-reported" or a.reported_cost_usd is None)
                           for a in route.attempts)
                    or abs(route.accounted_model_cost_usd + local_cost - claimed_cost) > 1e-9):
                raise ValueError("execution policy route trace or cost is stale or inconsistent")
        proposed = any(a.route == "skill" and a.status in {"success", "verification_failure"}
                       for a in case.candidate_route.attempts)
        verified = case.candidate_route.selected_route == "skill" and _correct(case, case.candidate_route)
        fallback = case.candidate_route.model_calls > 0
        if (case.candidate_correct != _correct(case, case.candidate_route)
                or case.incumbent_correct != _correct(case, case.incumbent_route)
                or case.skill_proposed != proposed or case.skill_verified != verified or case.fallback != fallback):
            raise ValueError("execution policy claimed outcomes do not replay against the route receipts")
    if (len(observed) != len(evidence.cases) or len(heldout) != len(evidence.cases)
            or {(t.fixture_digest, t.seed) for t in heldout} != set(observed)
            or len({case.input_json for case in evidence.cases}) != len(evidence.cases)):
        raise ValueError("execution policy held-out pairs are missing, repeated or stale")
    for trial in heldout:
        case = observed[trial.fixture_digest, trial.seed]
        candidate_score = float(case.candidate_correct) - evidence.cost_weight * case.candidate_cost_usd
        incumbent_score = float(case.incumbent_correct) - evidence.cost_weight * case.incumbent_cost_usd
        if (not trial.candidate_valid or not trial.incumbent_valid or trial.cohort != cohort
                or abs(trial.candidate_score - candidate_score) > 1e-9
                or abs(trial.incumbent_score - incumbent_score) > 1e-9):
            raise ValueError("execution policy outcomes disagree with matched serving trials")
    per_cohort = {name: [case for case in evidence.cases if case.cohort == name]
                  for name in ("supported", "shifted")}
    if {c.group for c in per_cohort["supported"]} & {c.group for c in per_cohort["shifted"]}:
        raise ValueError("execution policy sampling groups cannot span contract cohorts")
    for cases in per_cohort.values():
        groups = {c.group for c in cases}
        if len(cases) < 12 or len(groups) < 4:
            raise ValueError("execution policy needs independent grouped supported and shifted evidence")
        difference = [statistics.mean(float(c.candidate_correct) - float(c.incumbent_correct)
                                      for c in cases if c.group == group) for group in sorted(groups)]
        if (statistics.mean(c.candidate_correct for c in cases) < evidence.quality_floor
                or paired_interval(difference, seed=1021, resamples=2000)[0] < -evidence.regression_tolerance):
            raise ValueError("execution policy quality floor or paired regression gate failed")
    supported = per_cohort["supported"]
    proposals = [c for c in supported if c.skill_proposed]
    if (not proposals or any(not c.skill_verified for c in proposals)
            or sum(c.skill_verified for c in supported) / len(supported) < evidence.min_skill_coverage
            or any(c.skill_proposed or not c.fallback for c in per_cohort["shifted"])):
        raise ValueError("execution policy precision, coverage or shifted applicability failed")
    def projection(candidate: bool) -> float:
        setup = evidence.candidate_setup_cost_usd if candidate else evidence.incumbent_setup_cost_usd
        return setup + evidence.reuse_horizon * sum(
            weight * statistics.mean(c.candidate_cost_usd if candidate else c.incumbent_cost_usd for c in cases)
            for weight, cases in ((1 - evidence.shifted_frequency, supported),
                                  (evidence.shifted_frequency, per_cohort["shifted"])))
    def success_rate(candidate: bool) -> float:
        return sum(weight * statistics.mean(c.candidate_correct if candidate else c.incumbent_correct for c in cases)
                   for weight, cases in ((1 - evidence.shifted_frequency, supported),
                                         (evidence.shifted_frequency, per_cohort["shifted"])))
    incumbent_success, candidate_success = success_rate(False), success_rate(True)
    if incumbent_success <= 0 or candidate_success <= 0:
        raise ValueError("execution policy cannot price a workload with no successful tasks")
    incumbent_cost = projection(False) / (evidence.reuse_horizon * incumbent_success)
    candidate_cost = projection(True) / (evidence.reuse_horizon * candidate_success)
    if (incumbent_cost <= 0 or candidate_cost >= incumbent_cost
            or candidate_cost > (1 - evidence.min_savings) * incumbent_cost):
        raise ValueError("execution policy has no measured amortized cost benefit per successful task")
    return evidence
