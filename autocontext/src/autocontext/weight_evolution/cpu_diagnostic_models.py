"""Pinned, label-free plans for separate exhaustive CPU practice diagnostics."""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import Field, ValidationInfo, field_validator, model_validator

from autocontext.weight_evolution.artifacts import canonical_digest
from autocontext.weight_evolution.checkpoint_candidates import MAX_CANDIDATES, TensorCandidateSpec
from autocontext.weight_evolution.cpu_campaign_models import CAMPAIGN_POLICIES, CampaignPolicy
from autocontext.weight_evolution.cpu_models import CpuDecoding, CpuInferenceRequest, CpuModelContract, CpuWorkerLimits
from autocontext.weight_evolution.cpu_search_models import SEARCH_MECHANISMS, CpuSearchMechanism, CpuSearchOption
from autocontext.weight_evolution.models import Digest, FrozenModel, Identifier

MAX_DIAGNOSTIC_FILE_BYTES = 16 * 1024 * 1024
DIAGNOSTIC_METERS = ("proposals", "materializations", "inference_calls", "input_tokens", "output_tokens")


class _DiagnosticModel(FrozenModel):
    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())

    @field_validator("schema_version", "max_proposals", "gpu_execution_deferred", "promotion_eligible",
                     mode="before", check_fields=False)
    @classmethod
    def literal_types(cls, value: Any, info: ValidationInfo) -> Any:
        expected = int if info.field_name in {"schema_version", "max_proposals"} else bool
        if type(value) is not expected:
            raise ValueError(f"{info.field_name} has the wrong literal type")
        return value


class CpuDiagnosticCase(_DiagnosticModel):
    """The worker-visible practice input; scorer labels remain in the source."""

    case_id: Identifier = Field(max_length=64)
    prompt: str = Field(min_length=1, max_length=8192)


class CpuDiagnosticSourceRun(_DiagnosticModel):
    run_id: Identifier = Field(max_length=128)
    policy: CampaignPolicy
    study_digest: Digest
    selection_digest: Digest
    summary_semantic_digest: Digest


class CpuDiagnosticReference(_DiagnosticModel):
    reference_id: Identifier = Field(max_length=97)
    task_id: Identifier = Field(max_length=64)
    repeat_id: Identifier = Field(max_length=32)
    inference_seed: int = Field(ge=0, le=2**32 - 1)
    source_runs: tuple[CpuDiagnosticSourceRun, ...] = Field(min_length=3, max_length=3)
    model: CpuModelContract
    candidates: tuple[TensorCandidateSpec, ...] = Field(min_length=2, max_length=MAX_CANDIDATES)
    options: tuple[CpuSearchOption, ...] = Field(min_length=3, max_length=48)
    baseline_decoding: CpuDecoding
    limits: CpuWorkerLimits
    practice: tuple[CpuDiagnosticCase, ...] = Field(min_length=2, max_length=16)
    practice_cases_digest: Digest
    source_options_digest: Digest

    @property
    def baseline(self) -> TensorCandidateSpec:
        return next(candidate for candidate in self.candidates if candidate.program is None)

    def options_for(self, mechanism: CpuSearchMechanism) -> tuple[CpuSearchOption, ...]:
        return tuple(option for option in self.options if option.mechanism == mechanism)

    @property
    def required_usage(self) -> dict[str, int]:
        """Baseline once, then every declared single-surface option once."""
        calls = (1 + len(self.options)) * len(self.practice)
        base_tokens = sum(len(case.prompt.split()) for case in self.practice)
        prefix_tokens = sum(len((option.context or "").split()) for option in self.options)
        return {"proposals": 0, "materializations": len(self.candidates), "inference_calls": calls,
                "input_tokens": (1 + len(self.options)) * base_tokens + len(self.practice) * prefix_tokens,
                "output_tokens": calls}

    @model_validator(mode="after")
    def bounded_reference(self) -> Self:
        if self.reference_id != f"{self.task_id}.{self.repeat_id}":
            raise ValueError("reference identity must compose its task and repeat")
        if tuple(run.policy for run in self.source_runs) != CAMPAIGN_POLICIES:
            raise ValueError("reference requires all three source policies in pinned order")
        if any(run.run_id != f"{self.reference_id}.{run.policy}" for run in self.source_runs):
            raise ValueError("reference source run differs from its paired identity")
        if len({candidate.candidate_id.casefold() for candidate in self.candidates}) != len(self.candidates):
            raise ValueError("reference candidate IDs alias")
        if len({candidate.output_sha256 for candidate in self.candidates}) != len(self.candidates):
            raise ValueError("reference candidates repeat checkpoint bytes")
        bases = [candidate for candidate in self.candidates if candidate.program is None]
        if len(bases) != 1 or any(candidate.contract != bases[0].contract for candidate in self.candidates):
            raise ValueError("reference requires one baseline and one shared immutable checkpoint contract")
        if self.baseline_decoding.mode != "greedy" or self.baseline_decoding.seed != self.inference_seed:
            raise ValueError("reference baseline must preserve greedy decoding and paired inference seed")
        if len({option.option_id.casefold() for option in self.options}) != len(self.options):
            raise ValueError("reference option IDs alias")
        if any(option.option_id.casefold() == "a" for option in self.options):
            raise ValueError("reference option IDs must not alias the baseline score key A")
        if self.source_options_digest != canonical_digest([option.to_dict() for option in self.options]):
            raise ValueError("reference options differ from their source digest")
        edited = {candidate.candidate_id for candidate in self.candidates if candidate.program is not None}
        weight_options = [option.candidate_id for option in self.options_for("W")]
        if len(set(weight_options)) != len(weight_options) or set(weight_options) != edited:
            raise ValueError("reference weight options must cover every edited checkpoint exactly once")
        for mechanism in SEARCH_MECHANISMS:
            options = self.options_for(mechanism)
            if not 1 <= len(options) <= 16:
                raise ValueError("reference requires bounded options for every single surface")
            if len({(option.direction, option.step) for option in options}) != len(options):
                raise ValueError("reference direction and step coordinates repeat")
            for direction in {option.direction for option in options}:
                steps = [option.step for option in options if option.direction == direction]
                if steps != list(range(1, len(steps) + 1)):
                    raise ValueError("reference directions must retain contiguous declared steps")
        if len({case.case_id.casefold() for case in self.practice}) != len(self.practice):
            raise ValueError("reference practice case IDs alias")
        if len({tuple(case.prompt.split()) for case in self.practice}) != len(self.practice):
            raise ValueError("reference practice inputs repeat")
        for option in (None, *self.options):
            decoding = option.decoding if option is not None and option.decoding is not None else self.baseline_decoding
            if option is not None and option.mechanism == "D":
                if decoding.mode != "sample" or decoding.seed != self.inference_seed:
                    raise ValueError("reference decoding option changes the pinned evaluation seed")
            prefix = option.context if option is not None and option.context is not None else ""
            for case in self.practice:
                CpuInferenceRequest(request_id=case.case_id, model=self.model,
                                    checkpoint_sha256=self.baseline.output_sha256,
                                    checkpoint_size_bytes=self.baseline.output_size_bytes,
                                    prompt=f"{prefix} {case.prompt}".strip(), decoding=decoding, limits=self.limits)
        return self


class CpuDiagnosticBudget(_DiagnosticModel):
    max_references: int = Field(ge=1, le=256)
    max_proposals: Literal[0] = 0
    max_materializations: int = Field(ge=1, le=2048)
    max_inference_calls: int = Field(ge=1, le=200704)
    max_input_tokens: int = Field(ge=1, le=12845056)
    max_output_tokens: int = Field(ge=1, le=200704)


class CpuDiagnosticSpec(_DiagnosticModel):
    schema_version: Literal[1] = 1
    study_kind: Literal["cpu_search_diagnostic"] = "cpu_search_diagnostic"
    gpu_execution_deferred: Literal[True] = True
    promotion_eligible: Literal[False] = False
    diagnostic_id: Identifier = Field(max_length=128)
    campaign_digest: Digest
    runtime_digest: Digest
    campaign_report_digest: Digest
    campaign_ledger_digest: Digest
    policies: tuple[CampaignPolicy, ...] = CAMPAIGN_POLICIES
    scorer: Literal["exact-label-v1"] = "exact-label-v1"
    reference_rule: Literal["complete-practice-baseline-and-all-single-surface-options-v1"] = (
        "complete-practice-baseline-and-all-single-surface-options-v1"
    )
    selection_rule: Literal["reference-is-diagnostic-only-no-search-reselection"] = (
        "reference-is-diagnostic-only-no-search-reselection"
    )
    references: tuple[CpuDiagnosticReference, ...] = Field(min_length=2, max_length=256)
    budget: CpuDiagnosticBudget
    implementation_digest: Digest

    @property
    def required_usage(self) -> dict[str, int]:
        return {"references": len(self.references),
                **{meter: sum(ref.required_usage[meter] for ref in self.references) for meter in DIAGNOSTIC_METERS}}

    @model_validator(mode="after")
    def complete_design(self) -> Self:
        if self.policies != CAMPAIGN_POLICIES:
            raise ValueError("diagnostic must preserve the three source policies")
        if len({ref.reference_id.casefold() for ref in self.references}) != len(self.references):
            raise ValueError("diagnostic reference IDs alias after composition or case folding")
        run_ids = [run.run_id.casefold() for ref in self.references for run in ref.source_runs]
        if len(set(run_ids)) != len(run_ids):
            raise ValueError("diagnostic source runs repeat")
        if any(value > getattr(self.budget, f"max_{meter}") for meter, value in self.required_usage.items()):
            raise ValueError("diagnostic budget cannot reserve the complete exhaustive reference schedule")
        return self


class CpuDiagnosticLock(_DiagnosticModel):
    schema_version: Literal[1] = 1
    kind: Literal["cpu_search_diagnostic_plan"] = "cpu_search_diagnostic_plan"
    spec_sha256: Digest
    spec_size_bytes: int = Field(gt=0, le=MAX_DIAGNOSTIC_FILE_BYTES)
