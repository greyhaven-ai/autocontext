"""Answer-free, explicitly selected GridCTF trace projections for AC-1019.

This is a bounded numeric adapter, not a general free-text secret detector.
It follows operational-memory's evidence/behavior/exclusion convention without
copying raw messages, actions, final answers or arbitrary trace fields.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import Field

from autocontext.artifacts.policy_candidate import FrozenContract, TraceEvidence, json_payload
from autocontext.context_bundles.models import stable_digest
from autocontext.scenarios.grid_ctf import GridCtfScenario

SCENARIO_VERSION = "grid-ctf-limit-1.4-v1"
Operation = Literal["observe", "validate_action", "execute_action", "verify_outcome"]


class GridSkillInput(FrozenContract):
    contract: str = Field(min_length=1, max_length=80)
    combined_limit: float = Field(ge=0, le=2, strict=True)
    enemy_spawn_bias: float = Field(ge=0, le=1, strict=True)
    resource_density: float = Field(ge=0, le=1, strict=True)


class GridSkillAction(FrozenContract):
    aggression: float = Field(ge=0, le=1, strict=True)
    defense: float = Field(ge=0, le=1, strict=True)
    path_bias: float = Field(ge=0, le=1, strict=True)


class TrainingTrace(FrozenContract):
    trace_id: str = Field(min_length=1, max_length=160)
    group_id: str = Field(min_length=1, max_length=160)
    split: Literal["train", "development", "heldout", "test"]
    seed: int = Field(strict=True)
    observation: GridSkillInput
    score: float = Field(ge=0, le=1, strict=True)
    successful: bool
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    operations: tuple[Operation, ...]

    def evidence(self, *, counterexample: bool) -> TraceEvidence:
        return TraceEvidence(
            trace_id=self.trace_id,
            group_id=self.group_id,
            source_sha256=self.source_sha256,
            input_sha256=stable_digest(self.observation.model_dump()),
            seed=self.seed,
            role="training_counterexample" if counterexample else "successful_training",
        )


class SelectionExclusions(FrozenContract):
    """Caller-supplied protected memberships; never infer a split from success."""

    trace_ids: tuple[str, ...] = ()
    group_ids: tuple[str, ...] = ()
    input_hashes: tuple[str, ...] = ()
    seeds: tuple[int, ...] = ()


NO_SELECTION_EXCLUSIONS = SelectionExclusions()


class SelectedProcedure(FrozenContract):
    traces: tuple[TraceEvidence, ...]
    operations: tuple[Operation, ...]
    observations: tuple[GridSkillInput, ...]

    def prompt_context(self) -> str:
        # IDs can carry customer metadata; only content hashes enter the prompt.
        return json_payload(
            {
                "evidenceRefs": [t.source_sha256 for t in self.traces],
                "targetFamilies": ["grid_ctf"],
                "risk": "low",
                "containsTaskAnswer": False,
                "containsSecret": False,
                "reusableBehavior": list(self.operations),
                "observations": [o.model_dump() for o in self.observations],
            }
        )


def grid_observation(seed: int, *, shifted: bool = False) -> GridSkillInput:
    state = GridCtfScenario().initial_state(seed)
    return GridSkillInput(
        contract="grid-ctf-limit-0.8-v2" if shifted else SCENARIO_VERSION,
        combined_limit=0.8 if shifted else 1.4,
        enemy_spawn_bias=state["enemy_spawn_bias"],
        resource_density=state["resource_density"],
    )


def trace_from_grid_match(
    raw: Mapping[str, Any],
    *,
    trace_id: str,
    group_id: str,
    split: Literal["train", "development", "heldout", "test"],
    seed: int,
    success_floor: float = 0.75,
) -> TrainingTrace:
    """Project a PolicyMatchResult/asdict row; extra raw fields are never copied.

    Caller owns the match's seed/split provenance. Observation is regenerated
    from that training seed, never from untrusted narrative or final answers.
    """
    if not 0 <= success_floor <= 1:
        raise ValueError("success_floor must be finite and within [0,1]")
    for key, expected in (("seed", seed), ("split", split), ("scenario", "grid_ctf")):
        if key in raw and raw[key] != expected:
            raise ValueError(f"trace {key} conflicts with declared provenance")
    score = raw.get("score")
    if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= score <= 1:
        raise ValueError("trace requires a finite numeric score")
    if type(raw.get("had_illegal_actions")) is not bool or not isinstance(raw.get("errors"), list):
        raise ValueError("trace requires explicit legality and error evidence")
    successful = score >= success_floor and not raw["had_illegal_actions"] and not raw["errors"]
    if raw.get("moves_played") != 1:
        raise ValueError("v1 only consumes completed one-action GridCTF traces")
    return TrainingTrace(
        trace_id=trace_id,
        group_id=group_id,
        split=split,
        seed=seed,
        observation=grid_observation(seed),
        score=float(score),
        successful=successful,
        source_sha256=stable_digest(dict(raw)),
        operations=("observe", "validate_action", "execute_action", "verify_outcome"),
    )


def select_procedure(
    pool: Sequence[TrainingTrace],
    *,
    selected_ids: Sequence[str],
    counterexample_ids: Sequence[str] = (),
    exclusions: SelectionExclusions = NO_SELECTION_EXCLUSIONS,
) -> SelectedProcedure:
    by_id = {trace.trace_id: trace for trace in pool}
    ids = [*selected_ids, *counterexample_ids]
    if len(ids) > 32:
        raise ValueError("v1 synthesis selects at most 32 training traces")
    if len(by_id) != len(pool) or len(set(ids)) != len(ids):
        raise ValueError("trace IDs must be unique across selections")
    protected_groups = set(exclusions.group_ids) | {t.group_id for t in pool if t.split in {"heldout", "test"}}
    protected_inputs = set(exclusions.input_hashes) | {
        stable_digest(t.observation.model_dump()) for t in pool if t.split in {"heldout", "test"}
    }
    protected_seeds = set(exclusions.seeds) | {t.seed for t in pool if t.split in {"heldout", "test"}}
    successes: list[TrainingTrace] = []
    references: list[TraceEvidence] = []
    for trace_id in ids:
        trace = by_id[trace_id]
        evidence = trace.evidence(counterexample=trace_id in counterexample_ids)
        if (
            trace.split != "train"
            or trace_id in exclusions.trace_ids
            or trace.group_id in protected_groups
            or evidence.input_sha256 in protected_inputs
            or trace.seed in protected_seeds
        ):
            raise ValueError("selection overlaps protected or non-training material")
        if trace_id in selected_ids:
            if not trace.successful:
                raise ValueError("selected procedure evidence must be successful training traces")
            if trace.observation.contract != SCENARIO_VERSION or trace.observation.combined_limit != 1.4:
                raise ValueError("successful training trace uses an unsupported contract")
            successes.append(trace)
        elif trace.successful:
            raise ValueError("training counterexamples must have failed the success criterion")
        references.append(evidence)
    if len({t.group_id for t in successes}) < 2 or len({t.seed for t in successes}) < 2:
        raise ValueError("a reusable procedure needs at least two distinct successful training groups/seeds")
    operations = tuple(op for op in successes[0].operations if all(op in t.operations for t in successes))
    if not operations:
        raise ValueError("selected traces have no recurring operations")
    return SelectedProcedure(
        traces=tuple(references), operations=operations, observations=tuple(t.observation for t in successes)
    )


class CandidateCase(FrozenContract):
    case_id: str = Field(min_length=1)
    group_id: str = Field(min_length=1)
    seed: int = Field(strict=True)
    lane: Literal["fresh", "counterexample"]
    observation_json: str

    @classmethod
    def grid(cls, seed: int, *, shifted: bool = False) -> CandidateCase:
        return cls(
            case_id=f"grid-{seed}",
            group_id=f"seed-{seed}",
            seed=seed,
            lane="counterexample" if shifted else "fresh",
            observation_json=json_payload(grid_observation(seed, shifted=shifted)),
        )
