"""Bounded GridCTF candidate validation on the existing policy boundary."""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError, model_validator

from autocontext.artifacts.policy_candidate import (
    DEFAULT_CANDIDATE_LIMITS,
    CandidateLimits,
    FrozenContract,
    PolicyCandidateManifest,
    json_payload,
)
from autocontext.context_bundles.models import stable_digest
from autocontext.execution import ast_safety, isolated_python, policy_candidate_data, policy_executor
from autocontext.execution.ast_safety import check_ast_safety
from autocontext.execution.isolated_python import IsolatedExecutionError, IsolatedExecutionTimeout, IsolationUnavailableError
from autocontext.execution.policy_candidate_data import SCENARIO_VERSION, CandidateCase, GridSkillAction, GridSkillInput
from autocontext.execution.policy_executor import PolicyExecutor
from autocontext.scenarios.grid_ctf import GridCtfScenario
from autocontext.scenarios.grid_ctf import scenario as grid_module

APPLICABILITY = {"contract": SCENARIO_VERSION, "combined_limit": 1.4}
# Last definition wins; synthesis is restricted to the propose_action function.
SCOPE_WRAPPER = """
def choose_action(state: dict) -> dict:
    if state.get("contract") != "grid-ctf-limit-1.4-v1" or state.get("combined_limit") != 1.4:
        return {"status": "abstain", "reason": "unsupported_contract"}
    return propose_action(state)
"""


class SkillDecision(FrozenContract):
    status: Literal["act", "abstain"]
    action: GridSkillAction | None = None
    reason: Literal["unsupported_contract", "invalid_input", "policy_abstained"] | None = None

    @model_validator(mode="after")
    def consistent(self) -> SkillDecision:
        if self.status == "act" and (self.action is None or self.reason is not None):
            raise ValueError("act requires an action and no abstention reason")
        if self.status == "abstain" and (self.action is not None or self.reason is None):
            raise ValueError("abstain requires a reason and no action")
        return self


def evaluator_identity() -> str:
    """Bind trusted verifier, adapter and execution implementation, not a name."""
    paths = [
        Path(__file__),
        Path(grid_module.__file__),
        Path(policy_executor.__file__),
        Path(isolated_python.__file__),
        Path(ast_safety.__file__),
        Path(policy_candidate_data.__file__),
    ]
    return stable_digest({p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})


def assemble_candidate_source(response_source: str) -> tuple[str, tuple[str, ...]]:
    """Keep invalid source for inspection; never run it if these checks fail."""
    errors = check_ast_safety(response_source)
    if len(response_source.encode()) > 65536:
        errors.append("candidate source exceeds 64 KiB")
    try:
        tree = ast.parse(response_source)
        if len(tree.body) != 1 or not isinstance(tree.body[0], ast.FunctionDef):
            errors.append("synthesis must define only propose_action(state)")
        else:
            fn = tree.body[0]
            if (
                fn.name != "propose_action"
                or fn.decorator_list
                or fn.args.defaults
                or fn.args.kwonlyargs
                or fn.args.posonlyargs
                or fn.args.vararg
                or fn.args.kwarg
                or [arg.arg for arg in fn.args.args] != ["state"]
            ):
                errors.append("synthesis must define an undecorated propose_action(state) without defaults")
    except SyntaxError:
        pass  # check_ast_safety already reports the syntax failure.
    return response_source.rstrip() + "\n\n" + SCOPE_WRAPPER, tuple(errors)


def require_compatible(manifest: PolicyCandidateManifest, caller_limits: CandidateLimits) -> None:
    manifest.limits.require_within(caller_limits)
    if (
        manifest.scenario != "grid_ctf"
        or manifest.scenario_version != SCENARIO_VERSION
        or manifest.input_schema != json_payload(GridSkillInput.model_json_schema())
        or manifest.output_schema != json_payload(SkillDecision.model_json_schema())
        or manifest.applicability != json_payload(APPLICABILITY)
        or manifest.evaluator_identity != evaluator_identity()
    ):
        raise ValueError("candidate scenario, schema or evaluator implementation is incompatible")
    if not manifest.skill.source.endswith(SCOPE_WRAPPER):
        raise ValueError("candidate is missing the bound applicability wrapper")
    _, errors = assemble_candidate_source(manifest.skill.source.removesuffix(SCOPE_WRAPPER).rstrip())
    if errors:
        raise ValueError("candidate source is invalid: " + "; ".join(errors))


def invoke_candidate(
    manifest: PolicyCandidateManifest,
    observation_json: str,
    *,
    expected_digest: str,
    caller_limits: CandidateLimits = DEFAULT_CANDIDATE_LIMITS,
) -> SkillDecision:
    manifest.require_digest(expected_digest)
    require_compatible(manifest, caller_limits)
    return execute_source(manifest.skill.source, observation_json, manifest.limits)


def execute_source(source: str, observation_json: str, limits: CandidateLimits) -> SkillDecision:
    try:
        observation = GridSkillInput.model_validate_json(observation_json)
    except (ValidationError, ValueError):
        return SkillDecision(status="abstain", reason="invalid_input")
    if observation.contract != SCENARIO_VERSION or observation.combined_limit != 1.4:
        return SkillDecision(status="abstain", reason="unsupported_contract")
    executor = PolicyExecutor(
        GridCtfScenario(),
        timeout_per_match=limits.timeout_seconds,
        max_memory_mb=limits.max_memory_mb,
        max_output_bytes=limits.max_output_bytes,
    )
    decision = SkillDecision.model_validate(executor.execute_action(source, observation.model_dump()))
    if decision.action and decision.action.aggression + decision.action.defense > observation.combined_limit:
        raise ValueError("candidate action exceeds combined limit")
    return decision


class CaseEvidence(FrozenContract):
    case_id: str
    group_id: str
    seed: int
    lane: Literal["fresh", "counterexample"]
    input_json: str
    source_sha256: str
    evaluator_identity: str
    decision_json: str | None = None
    score: float | None = Field(default=None, ge=0, le=1)
    minimum_score: float = Field(ge=0, le=1)
    passed: bool
    failure: str | None = None

    @property
    def digest(self) -> str:
        return stable_digest(self.model_dump(mode="json"))


def evaluate_case(source: str, case: CandidateCase, limits: CandidateLimits, *, minimum_score: float) -> CaseEvidence:
    values = dict(case.model_dump())
    values["input_json"] = values.pop("observation_json")
    values.update(
        source_sha256=hashlib.sha256(source.encode()).hexdigest(),
        evaluator_identity=evaluator_identity(),
        minimum_score=minimum_score,
        passed=False,
    )
    try:
        decision = execute_source(source, case.observation_json, limits)
        values["decision_json"] = json_payload(decision)
        if case.lane == "counterexample":
            values["passed"] = decision.status == "abstain"
        elif decision.status == "act" and decision.action is not None:
            # The seed stays in the trusted verifier, never in the policy input.
            scenario = GridCtfScenario()
            state = scenario.initial_state(case.seed)
            state.update(GridSkillInput.model_validate_json(case.observation_json).model_dump())
            score = scenario.get_result(scenario.step(state, decision.action.model_dump())).score
            values.update(score=score, passed=score >= minimum_score)
        if not values["passed"]:
            values["failure"] = "unexpected_action_or_abstention" if case.lane == "counterexample" else "quality_or_coverage"
    except (ValueError, IsolatedExecutionError, IsolatedExecutionTimeout, IsolationUnavailableError) as exc:
        # Do not persist arbitrary generated exception text or output in lineage.
        values["failure"] = type(exc).__name__
    return CaseEvidence.model_validate(values)
