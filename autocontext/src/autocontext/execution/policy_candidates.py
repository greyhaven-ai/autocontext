"""One-call trace-to-candidate workflow; never changes active serving state."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

from autocontext.artifacts.models import ArtifactProvenance
from autocontext.artifacts.policy_candidate import (
    DEFAULT_CANDIDATE_LIMITS,
    CandidateLimits,
    PolicyCandidateManifest,
    json_payload,
)
from autocontext.context_bundles.models import BundleComponent, BundleLifecycle, ComponentKind, ContextBundle, stable_digest
from autocontext.context_bundles.store import ContextBundleStore
from autocontext.execution.policy_candidate_data import (
    NO_SELECTION_EXCLUSIONS,
    SCENARIO_VERSION,
    CandidateCase,
    GridSkillInput,
    SelectedProcedure,
    SelectionExclusions,
    TrainingTrace,
    select_procedure,
)
from autocontext.execution.policy_candidate_runtime import (
    APPLICABILITY,
    CaseEvidence,
    SkillDecision,
    assemble_candidate_source,
    evaluate_case,
    evaluator_identity,
)
from autocontext.execution.policy_refinement import _extract_policy_from_response
from autocontext.knowledge.harness_entries import SkillReference
from autocontext.providers.base import LLMProvider

COMPONENT_KEY = "trace_derived_policy_candidate"
PROMPT_VERSION = "autocontext.grid-skill-synthesis.v1"
SYSTEM_PROMPT = """Write one reusable Python function propose_action(state: dict) -> dict.
Only the function definition, no imports, decorators, defaults or top-level code.
The caller adds an applicability wrapper and runs the function in isolated Python
with restricted builtins. No file, network, tool or external dependency access.
The input is a numeric observation, not the private seed or complete game state.
Return {"status":"act","action":{"aggression":number,"defense":number,"path_bias":number}}
or {"status":"abstain","reason":"policy_abstained"} if unsupported.
All actions are finite numbers in [0,1], aggression+defense <= 1.4.
Use reusable operations from the evidence; never replay examples or hardcode
observation-to-answer tables. No recorded actions or final answers are supplied.
GridCTF is a one-action simulator. Its public objective is:
capture=clamp(.55*aggression+.45*path_bias+noise,0,1), noise uniform[-.07,.07];
survival=clamp(1-.4*aggression+.4*defense,0,1);
energy=clamp(1-.3*aggression+.1*defense,0,1);
score=.6*capture+.25*survival+.15*energy. Maximize expected score.
Output only the Python code. This creates an inactive candidate, not a promoted skill.
"""


@dataclass(frozen=True)
class CandidateOutcome:
    manifest: PolicyCandidateManifest
    bundle_digest: str
    lifecycle: BundleLifecycle
    evaluations: tuple[CaseEvidence, ...]
    rejection_reasons: tuple[str, ...]


def _validate_cases(
    cases: Sequence[CandidateCase],
    procedure: SelectedProcedure,
    exclusions: SelectionExclusions,
) -> None:
    if len(cases) > 32 or sum(c.lane == "fresh" for c in cases) < 2 or not any(c.lane == "counterexample" for c in cases):
        raise ValueError("evaluation requires 2+ fresh inputs, a counterexample, and at most 32 cases")
    for field in ("case_id", "group_id", "seed", "observation_json"):
        if len({getattr(c, field) for c in cases}) != len(cases):
            raise ValueError("evaluation cases must have distinct IDs, groups, seeds and inputs")
    used_groups = {t.group_id for t in procedure.traces} | set(exclusions.group_ids)
    used_seeds = {t.seed for t in procedure.traces} | set(exclusions.seeds)
    used_inputs = {t.input_sha256 for t in procedure.traces} | set(exclusions.input_hashes)
    used_ids = {t.trace_id for t in procedure.traces} | set(exclusions.trace_ids)
    for case in cases:
        observation = GridSkillInput.model_validate_json(case.observation_json)
        if json_payload(observation) != case.observation_json:
            raise ValueError("evaluation observations must be canonical, typed JSON")
        if (
            case.group_id in used_groups
            or case.seed in used_seeds
            or case.case_id in used_ids
            or stable_digest(observation.model_dump()) in used_inputs
        ):
            raise ValueError("evaluation overlaps training or protected material")
        if case.lane == "fresh" and (observation.contract != SCENARIO_VERSION or observation.combined_limit != 1.4):
            raise ValueError("fresh quality cases must use the supported contract")


def synthesize_grid_candidate(
    *,
    provider: LLMProvider,
    provider_name: str,
    store: ContextBundleStore,
    run_id: str,
    pool: Sequence[TrainingTrace],
    selected_ids: Sequence[str],
    cases: Sequence[CandidateCase],
    counterexample_ids: Sequence[str] = (),
    exclusions: SelectionExclusions = NO_SELECTION_EXCLUSIONS,
    model: str | None = None,
    limits: CandidateLimits = DEFAULT_CANDIDATE_LIMITS,
    caller_limits: CandidateLimits = DEFAULT_CANDIDATE_LIMITS,
    minimum_score: float = 0.75,
) -> CandidateOutcome:
    """Select, synthesize once, evaluate and propose/reject an immutable candidate.

    This is an opt-in Python API. One complete() call, no workflow retries,
    no candidate refinement on evaluation inputs, and no production activation.
    Provider transports own their timeout/retry policy; usage is provider-reported.
    """
    limits.require_within(caller_limits)
    if not 0 <= minimum_score <= 1:
        raise ValueError("minimum_score must be finite and within [0,1]")
    if not run_id.strip() or not provider_name.strip():
        raise ValueError("run and provider identity are required")
    procedure = select_procedure(pool, selected_ids=selected_ids, counterexample_ids=counterexample_ids, exclusions=exclusions)
    # Also consume protected memberships present in the pool during evaluation.
    protected = [t for t in pool if t.split in {"heldout", "test"}]
    evaluation_exclusions = SelectionExclusions(
        trace_ids=(*exclusions.trace_ids, *(t.trace_id for t in protected)),
        group_ids=(*exclusions.group_ids, *(t.group_id for t in protected)),
        input_hashes=(*exclusions.input_hashes, *(stable_digest(t.observation.model_dump()) for t in protected)),
        seeds=(*exclusions.seeds, *(t.seed for t in protected)),
    )
    _validate_cases(cases, procedure, evaluation_exclusions)
    parent = store.active_bundle("grid_ctf")
    if parent is None:
        raise ValueError("candidate store requires an existing baseline; synthesis never bootstraps serving")
    requested_model = model or provider.default_model()
    if not requested_model:
        raise ValueError("synthesis model identity is required")
    user_prompt = (
        "Sanitized procedure evidence:\n"
        + procedure.prompt_context()
        + "\nInput schema:\n"
        + json_payload(GridSkillInput.model_json_schema())
    )
    prompt_hash = stable_digest({"system": SYSTEM_PROMPT, "user": user_prompt})
    completion = provider.complete(
        system_prompt=SYSTEM_PROMPT, user_prompt=user_prompt, model=requested_model, temperature=0.0, max_tokens=4096
    )
    response_source = _extract_policy_from_response(completion.text)
    source, source_errors = assemble_candidate_source(response_source)
    if completion.stop_reason in {"max_tokens", "length", "error", "aborted"}:
        source_errors = (*source_errors, "synthesis did not finish normally")
    evaluations = (
        () if source_errors else tuple(evaluate_case(source, case, limits, minimum_score=minimum_score) for case in cases)
    )
    rejection_reasons = source_errors or tuple(f"{r.case_id}: {r.failure}" for r in evaluations if not r.passed)
    provenance = ArtifactProvenance(
        run_id=run_id, generation=0, scenario="grid_ctf", settings={"workflow": PROMPT_VERSION, "model_calls": 1}
    )
    manifest = PolicyCandidateManifest(
        scenario="grid_ctf",
        scenario_version=SCENARIO_VERSION,
        input_schema=json_payload(GridSkillInput.model_json_schema()),
        output_schema=json_payload(SkillDecision.model_json_schema()),
        applicability=json_payload(APPLICABILITY),
        skill_json=json_payload(
            SkillReference(
                entrypoint="choose_action",
                source=source,
                call_pattern="choose_action(observation)",
                arguments={"observation": "GridSkillInput"},
            )
        ),
        provenance_json=json_payload(provenance),
        limits=limits,
        source_traces=procedure.traces,
        synthesis_provider=provider_name,
        requested_model=requested_model,
        served_model=completion.model,
        prompt_version=PROMPT_VERSION,
        prompt_sha256=prompt_hash,
        evaluator_identity=evaluator_identity(),
        evaluation_refs=tuple(result.digest for result in evaluations),
        synthesis_usage_json=json_payload(
            {
                "reported_tokens": completion.usage,
                "reported_cost_usd": completion.cost_usd,
                "stop_reason": completion.stop_reason,
                "workflow_calls": 1,
            }
        ),
    )
    component = BundleComponent.json(
        ComponentKind.TOOL_SPEC,
        COMPONENT_KEY,
        {
            "manifest_digest": manifest.digest,
            "manifest": manifest.model_dump(mode="json"),
            "evaluations": [r.model_dump(mode="json") for r in evaluations],
            "rejection_reasons": list(rejection_reasons),
            "synthesis_prompt": {"system": SYSTEM_PROMPT, "user": user_prompt},
        },
    )
    components = [c for c in parent.components if (c.kind, c.key) != (component.kind, component.key)] if parent else []
    bundle = ContextBundle.create(
        scenario="grid_ctf",
        evaluator_epoch=parent.evaluator_epoch,
        parent_digest=parent.digest,
        components=[*components, component],
    )
    store.propose(
        bundle,
        source_run_id=run_id,
        source_generation=0,
        rationale="AC-1019 offline skill candidate; synthesis/evaluation does not authorize serving",
    )
    if rejection_reasons:
        store.reject("grid_ctf", bundle.digest, rationale="; ".join(rejection_reasons))
    record = store.candidate("grid_ctf", bundle.digest)
    return CandidateOutcome(manifest, bundle.digest, record.lifecycle, evaluations, rejection_reasons)


def inspect_grid_candidate(store: ContextBundleStore, bundle_digest: str) -> CandidateOutcome:
    """Load immutable source, lineage and negative evidence without executing."""
    bundle = store.load_bundle("grid_ctf", bundle_digest)
    component = next(c for c in bundle.components if c.kind == ComponentKind.TOOL_SPEC and c.key == COMPONENT_KEY)
    raw = json.loads(component.content)
    manifest = PolicyCandidateManifest.model_validate(raw["manifest"])
    manifest.require_digest(raw["manifest_digest"])
    evaluations = tuple(CaseEvidence.model_validate(r) for r in raw["evaluations"])
    if manifest.evaluation_refs != tuple(r.digest for r in evaluations):
        raise ValueError("candidate evaluation reference mismatch")
    if any(
        r.source_sha256 != manifest.source_sha256 or r.evaluator_identity != manifest.evaluator_identity for r in evaluations
    ) or manifest.prompt_sha256 != stable_digest(raw["synthesis_prompt"]):
        raise ValueError("candidate source/evaluation/prompt lineage mismatch")
    record = store.candidate("grid_ctf", bundle_digest)
    return CandidateOutcome(manifest, bundle_digest, record.lifecycle, evaluations, tuple(raw["rejection_reasons"]))
