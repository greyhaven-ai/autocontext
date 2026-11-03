"""Bounded contracts for practice-driven search with actual CPU inference.

These are separate from the frozen M6--M8 fixture contracts. The search space
is declared before execution, while candidate outcomes come from model runs.
"""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import Field, ValidationInfo, field_validator, model_validator

from autocontext.weight_evolution.artifacts import canonical_digest
from autocontext.weight_evolution.checkpoint_candidates import MAX_CANDIDATES, TensorCandidateSpec
from autocontext.weight_evolution.cpu_models import (
    MAX_CPU_CHECKPOINT_BYTES,
    MAX_CPU_PROMPT_BYTES,
    CpuDecoding,
    CpuModelContract,
    CpuWorkerLimits,
)
from autocontext.weight_evolution.cpu_study import CpuCase
from autocontext.weight_evolution.models import Digest, FrozenModel, Identifier

CpuSearchMechanism = Literal["C", "W", "D"]
SEARCH_MECHANISMS: tuple[CpuSearchMechanism, ...] = ("C", "W", "D")
MAX_SEARCH_BUNDLE_BYTES = 1024 * 1024


class _SearchModel(FrozenModel):
    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())

    @field_validator("schema_version", "gpu_execution_deferred", "promotion_eligible", mode="before", check_fields=False)
    @classmethod
    def exact_literal_types(cls, value: Any, info: ValidationInfo) -> Any:
        expected = int if info.field_name == "schema_version" else bool
        if type(value) is not expected:
            raise ValueError(f"{info.field_name} has the wrong literal type")
        return value


class CpuSearchOption(_SearchModel):
    option_id: Identifier = Field(max_length=64)
    mechanism: CpuSearchMechanism
    direction: Identifier = Field(max_length=64)
    step: int = Field(ge=1, le=16)
    context: str | None = Field(default=None, min_length=1, max_length=1024)
    candidate_id: Identifier | None = Field(default=None, max_length=128)
    decoding: CpuDecoding | None = None

    @model_validator(mode="after")
    def exactly_one_surface(self) -> Self:
        fields = {"C": self.context, "W": self.candidate_id, "D": self.decoding}
        if fields[self.mechanism] is None or any(value is not None for key, value in fields.items() if key != self.mechanism):
            raise ValueError("search option must change exactly its mechanism's surface")
        if self.context is not None and not self.context.split():
            raise ValueError("context option must contain tokens")
        return self


class CpuSearchBudget(_SearchModel):
    max_proposals: int = Field(default=32, ge=1, le=48)
    max_materializations: int = Field(default=8, ge=1, le=MAX_CANDIDATES)
    max_inference_calls: int = Field(default=512, ge=1, le=2048)
    max_input_tokens: int = Field(default=32768, ge=1, le=131072)
    max_output_tokens: int = Field(default=512, ge=1, le=2048)


class CpuSearchPolicy(_SearchModel):
    """Versioned proposal order, independent of the paired evaluation seed."""

    name: Literal["adaptive-v1", "fixed-order-v1", "seeded-random-v1"] = "adaptive-v1"
    order_seed: int = Field(default=0, ge=0, le=2**32 - 1)

    @model_validator(mode="after")
    def meaningful_seed(self) -> Self:
        if self.name != "seeded-random-v1" and self.order_seed != 0:
            raise ValueError("only seeded-random-v1 accepts a nonzero ordering seed")
        return self


class CpuSearchSpec(_SearchModel):
    schema_version: Literal[1] = 1
    study_kind: Literal["adaptive_cpu_inference"] = "adaptive_cpu_inference"
    gpu_execution_deferred: Literal[True] = True
    promotion_eligible: Literal[False] = False
    study_id: Identifier = Field(max_length=128)
    model: CpuModelContract
    candidates: tuple[TensorCandidateSpec, ...] = Field(min_length=2, max_length=MAX_CANDIDATES)
    options: tuple[CpuSearchOption, ...] = Field(min_length=3, max_length=48)
    rounds: int = Field(ge=1, le=MAX_CANDIDATES - 1)
    policy: CpuSearchPolicy = Field(default_factory=CpuSearchPolicy)
    baseline_decoding: CpuDecoding
    evaluation_seed: int = Field(ge=0, le=2**32 - 1)
    limits: CpuWorkerLimits
    budget: CpuSearchBudget
    cases: tuple[CpuCase, ...] = Field(min_length=6, max_length=48)
    minimum_confirmation_gain: float = Field(default=0.1, gt=0.0, le=1.0, allow_inf_nan=False)
    implementation_digest: Digest

    @property
    def baseline(self) -> TensorCandidateSpec:
        return next(candidate for candidate in self.candidates if candidate.program is None)

    def options_for(self, mechanism: CpuSearchMechanism) -> tuple[CpuSearchOption, ...]:
        return tuple(option for option in self.options if option.mechanism == mechanism)

    @property
    def required_usage(self) -> dict[str, int]:
        """Conservative reservation for all search, restoration and held-out work.

        Each arm gets its own baseline, equal trial count and restoration probe.
        Practice CW is one additional composition probe; both later roles have
        five arms. The maximum context is charged in this preflight upper bound
        even for requests that will not use context. Actual claims use real sizes.
        """
        prefix = max(len(option.context.split()) for option in self.options if option.context is not None)
        counts = {role: sum(case.role == role for case in self.cases) for role in ("practice", "confirmation", "audit")}
        token_counts = {role: sum(len(case.prompt.split()) + prefix for case in self.cases if case.role == role)
                        for role in counts}
        practice_batches = 3 * (self.rounds + 2) + 1
        calls = practice_batches * counts["practice"] + 5 * (counts["confirmation"] + counts["audit"])
        tokens = practice_batches * token_counts["practice"] + 5 * (token_counts["confirmation"] + token_counts["audit"])
        return {"proposals": 3 * self.rounds, "materializations": len(self.candidates), "inference_calls": calls,
                "input_tokens": tokens, "output_tokens": calls}

    @model_validator(mode="after")
    def bounded_design(self) -> Self:
        if len({candidate.candidate_id for candidate in self.candidates}) != len(self.candidates):
            raise ValueError("search candidates repeat an ID")
        if len({candidate.output_sha256 for candidate in self.candidates}) != len(self.candidates):
            raise ValueError("search candidates repeat checkpoint bytes")
        bases = [candidate for candidate in self.candidates if candidate.program is None]
        if len(bases) != 1:
            raise ValueError("search requires exactly one base checkpoint")
        if any(candidate.contract.digest != bases[0].contract.digest for candidate in self.candidates):
            raise ValueError("search candidates must share one immutable input contract")
        if self.baseline_decoding.mode != "greedy" or self.baseline_decoding.seed != self.evaluation_seed:
            raise ValueError("baseline decoding must be greedy with the pinned evaluation seed")
        if self.baseline_decoding.top_k > len(self.model.output_tokens):
            raise ValueError("baseline decoding exceeds the output vocabulary")
        if len({option.option_id for option in self.options}) != len(self.options):
            raise ValueError("search options repeat an ID")
        edited = {candidate.candidate_id for candidate in self.candidates if candidate.program is not None}
        weight_choices = [option.candidate_id for option in self.options_for("W")]
        if len(set(weight_choices)) != len(weight_choices) or set(weight_choices) != edited:
            raise ValueError("weight options must reference every distinct edited checkpoint exactly once")
        for mechanism in SEARCH_MECHANISMS:
            choices = self.options_for(mechanism)
            if not self.rounds <= len(choices) <= 16:
                raise ValueError("each mechanism needs enough bounded options for equal search rounds")
            if len({(option.direction, option.step) for option in choices}) != len(choices):
                raise ValueError("search direction/step coordinates must be unique per mechanism")
            for direction in {option.direction for option in choices}:
                steps = [option.step for option in choices if option.direction == direction]
                if steps != list(range(1, len(steps) + 1)):
                    raise ValueError("each search direction must declare contiguous ascending steps from one")
            if mechanism == "C" and len({tuple((option.context or "").split()) for option in choices}) != len(choices):
                raise ValueError("context options repeat tokenized content")
            if mechanism == "D":
                decodings = [option.decoding for option in choices]
                if len({value.digest for value in decodings if value is not None}) != len(decodings):
                    raise ValueError("decoding options repeat settings")
                for decoding in decodings:
                    assert decoding is not None
                    if (decoding.mode != "sample" or decoding.seed != self.evaluation_seed
                            or decoding.top_k > len(self.model.output_tokens)):
                        raise ValueError("decoding options must sample with the fixed seed and bounded top-k")
        if len({case.case_id for case in self.cases}) != len(self.cases):
            raise ValueError("case IDs must be globally unique")
        if any(len(case.case_id) > 64 for case in self.cases):
            raise ValueError("CPU search case IDs must leave room for bounded request IDs")
        if len({tuple(case.prompt.split()) for case in self.cases}) != len(self.cases):
            raise ValueError("split prompts must be distinct after tokenization")
        for role in ("practice", "confirmation", "audit"):
            if not 2 <= sum(case.role == role for case in self.cases) <= 16:
                raise ValueError("each role requires 2..16 cases")
        if any(case.expected not in self.model.output_tokens for case in self.cases):
            raise ValueError("case label is outside the model output vocabulary")
        contexts = [option.context for option in self.options if option.context is not None]
        prompts = [*contexts, *(case.prompt for case in self.cases)]
        if any(not text.split() or any(token not in self.model.vocabulary for token in text.split()) for text in prompts):
            raise ValueError("CPU search prompts contain empty or unknown tokens")
        if any(len(context.split()) + len(case.prompt.split()) > self.limits.max_input_tokens
               for context in contexts for case in self.cases):
            raise ValueError("rendered search prompt exceeds the input-token limit")
        if any(len(f"{context} {case.prompt}".encode()) > MAX_CPU_PROMPT_BYTES
               for context in contexts for case in self.cases):
            raise ValueError("rendered search prompt exceeds the UTF-8 byte limit")
        if any(value > getattr(self.budget, f"max_{name}") for name, value in self.required_usage.items()):
            raise ValueError("search budget cannot reserve complete trials, restoration, confirmation and audit")
        return self


class CpuSearchBundleLock(_SearchModel):
    schema_version: Literal[1] = 1
    kind: Literal["cpu_adaptive_bundle"] = "cpu_adaptive_bundle"
    spec_sha256: Digest
    spec_size_bytes: int = Field(gt=0, le=MAX_SEARCH_BUNDLE_BYTES)
    base_sha256: Digest
    base_size_bytes: int = Field(gt=0, le=MAX_CPU_CHECKPOINT_BYTES)
