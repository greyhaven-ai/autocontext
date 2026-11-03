"""Frozen A/C/W/CW/D comparison contracts for offline synthetic fixtures only.

These contracts deliberately do not weaken the two-checkpoint reproduction
protocol. UTF-8 byte counters exercise budgets; they are not Qwen token counts.
"""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import Field, field_validator, model_validator

from autocontext.weight_evolution.artifacts import canonical_digest, load_small_json
from autocontext.weight_evolution.datasets import EvaluationProblem, overlapping_problem_ids
from autocontext.weight_evolution.inference_models import text_digest
from autocontext.weight_evolution.models import Digest, FrozenModel, Identifier

ConditionId = Literal["A", "C", "W", "CW", "D"]
DatasetName = Literal["math500", "gsm8k", "ifeval"]
CONDITION_IDS: tuple[ConditionId, ...] = ("A", "C", "W", "CW", "D")
CONTRAST_PAIRS: tuple[tuple[ConditionId, ConditionId], ...] = (("C", "A"), ("W", "A"), ("CW", "C"), ("CW", "W"), ("D", "A"))


class _DigestModel(FrozenModel):
    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())


class ComparisonCase(_DigestModel):
    dataset: DatasetName
    problem: EvaluationProblem

    @model_validator(mode="after")
    def task_kind(self) -> Self:
        if (self.dataset == "ifeval") != bool(self.problem.instructions):
            raise ValueError("case scoring payload differs from its dataset protocol")
        return self


class DevelopmentProvenance(_DigestModel):
    origin: Literal["synthetic_fixture"] = "synthetic_fixture"
    cases: tuple[ComparisonCase, ...] = Field(min_length=1, max_length=1000)


class ArtifactSource(_DigestModel):
    kind: Literal["baseline_fixture", "development_fixture"]
    source_problem_ids: tuple[str, ...] = ()
    declared_search_calls: int = Field(default=0, ge=0, le=1000000)
    declared_search_units: int = Field(default=0, ge=0, le=1000000000)

    @model_validator(mode="after")
    def source_scope(self) -> Self:
        if len(set(self.source_problem_ids)) != len(self.source_problem_ids):
            raise ValueError("duplicate artifact source problem")
        if self.kind == "baseline_fixture" and (
            self.source_problem_ids or self.declared_search_calls or self.declared_search_units
        ):
            raise ValueError("baseline fixtures cannot declare development work")
        if self.kind == "development_fixture" and (
            not self.source_problem_ids or self.declared_search_calls < 1 or self.declared_search_units < 1
        ):
            raise ValueError("development fixtures require source IDs and declared mock search usage")
        return self


class CheckpointArtifact(_DigestModel):
    artifact_id: Identifier
    representation: Literal["synthetic_weight_descriptor"] = "synthetic_weight_descriptor"
    content: str = Field(min_length=1, max_length=4096)
    source: ArtifactSource

    @property
    def content_digest(self) -> str:
        return text_digest(self.content)


class ContextArtifact(_DigestModel):
    artifact_id: Identifier
    text: str = Field(max_length=32768)
    source: ArtifactSource

    @property
    def content_digest(self) -> str:
        return text_digest(self.text)


class DecodingArtifact(_DigestModel):
    artifact_id: Identifier
    temperature: float = Field(ge=0, le=2, allow_inf_nan=False)
    top_p: float = Field(gt=0, le=1, allow_inf_nan=False)
    top_k: int = Field(ge=1, le=1000)
    presence_penalty: float = Field(ge=-2, le=2, allow_inf_nan=False)
    source: ArtifactSource

    def settings(self) -> dict[str, int | float]:
        return {name: getattr(self, name) for name in ("temperature", "top_p", "top_k", "presence_penalty")}

    @property
    def content_digest(self) -> str:
        return canonical_digest(self.settings())


class ComparisonArtifacts(_DigestModel):
    base_checkpoint: CheckpointArtifact
    edited_checkpoint: CheckpointArtifact
    base_context: ContextArtifact
    learned_context: ContextArtifact
    base_decoding: DecodingArtifact
    selected_decoding: DecodingArtifact

    @model_validator(mode="after")
    def interventions(self) -> Self:
        baseline = (self.base_checkpoint, self.base_context, self.base_decoding)
        developed = (self.edited_checkpoint, self.learned_context, self.selected_decoding)
        if any(a.source.kind != "baseline_fixture" for a in baseline):
            raise ValueError("baseline artifacts must be baseline fixtures")
        if any(a.source.kind != "development_fixture" for a in developed):
            raise ValueError("interventions must declare development fixture provenance")
        if len({a.artifact_id for a in (*baseline, *developed)}) != 6:
            raise ValueError("artifact labels must be unique")
        if (self.base_checkpoint.content_digest == self.edited_checkpoint.content_digest
                or self.base_context.content_digest == self.learned_context.content_digest
                or self.base_decoding.content_digest == self.selected_decoding.content_digest):
            raise ValueError("C, W and D must each change their permitted artifact")
        return self


class ComparisonSampleCounts(FrozenModel):
    math500: int = Field(default=4, ge=1, le=16)
    gsm8k: int = Field(default=2, ge=1, le=16)
    ifeval: int = Field(default=2, ge=1, le=16)


class ComparisonProtocol(_DigestModel):
    runtime_digest: Digest
    tokenizer_digest: Digest
    scorer_digest: Digest
    seeds: tuple[int, ...] = Field(min_length=1, max_length=16)
    samples_per_problem: ComparisonSampleCounts = ComparisonSampleCounts()
    max_new_tokens: int = Field(ge=1, le=32768)
    math_prompt_suffix: str = Field(max_length=4096)
    renderer_version: Literal["fixture-context-user-v1"] = "fixture-context-user-v1"
    tokenizer_kind: Literal["utf8-bytes-v1"] = "utf8-bytes-v1"

    @field_validator("seeds")
    @classmethod
    def unique_seeds(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if len(set(value)) != len(value) or any(seed < 0 or seed > 2**32 - 1 for seed in value):
            raise ValueError("seeds must be distinct unsigned 32-bit integers")
        return value


class ComparisonBudgets(FrozenModel):
    max_input_units_per_sample: int = Field(gt=0, le=1048576)
    max_generated_units_per_condition: int = Field(gt=0, le=1000000000)
    max_total_generated_units: int = Field(gt=0, le=5000000000)
    max_total_input_units: int = Field(gt=0, le=5000000000)
    max_total_wall_seconds: float = Field(gt=0, le=86400, allow_inf_nan=False)
    max_search_calls_per_mechanism: int = Field(gt=0, le=1000000)
    max_search_units_per_mechanism: int = Field(gt=0, le=1000000000)


class ComparisonCondition(_DigestModel):
    condition_id: ConditionId
    checkpoint_digest: Digest
    context_digest: Digest
    decoding_digest: Digest
    declared_search_calls: int = Field(ge=0)
    declared_search_units: int = Field(ge=0)
    search_attribution: Literal["baseline_zero", "single_mechanism", "sum_of_C_and_W_shared_artifacts"]


class ComparisonContrast(FrozenModel):
    contrast_id: str
    treatment: ConditionId
    control: ConditionId


class ComparisonCell(_DigestModel):
    condition_id: ConditionId
    dataset: DatasetName
    problem_id: str
    sample_index: int = Field(ge=0)
    seed: int = Field(ge=0)
    checkpoint_digest: Digest
    context_digest: Digest
    decoding_digest: Digest
    runtime_digest: Digest
    tokenizer_digest: Digest
    scorer_digest: Digest
    dataset_digest: Digest
    renderer_version: Literal["fixture-context-user-v1"] = "fixture-context-user-v1"
    tokenizer_kind: Literal["utf8-bytes-v1"] = "utf8-bytes-v1"
    context_text: str
    user_text: str
    max_new_tokens: int = Field(ge=1, le=32768)

    @property
    def rendered_prompt(self) -> str:
        return f"<fixture-context>\n{self.context_text}\n</fixture-context>\n<fixture-user>\n{self.user_text}\n</fixture-user>\n"

    @property
    def input_units(self) -> int:
        return len(self.rendered_prompt.encode("utf-8"))


def _conditions(artifacts: ComparisonArtifacts) -> tuple[ComparisonCondition, ...]:
    result = []
    for name in CONDITION_IDS:
        weight = artifacts.edited_checkpoint if name in {"W", "CW"} else artifacts.base_checkpoint
        context = artifacts.learned_context if name in {"C", "CW"} else artifacts.base_context
        decoding = artifacts.selected_decoding if name == "D" else artifacts.base_decoding
        sources = [a.source for a in (weight, context, decoding) if a.source.kind == "development_fixture"]
        result.append(ComparisonCondition(
            condition_id=name, checkpoint_digest=weight.content_digest, context_digest=context.content_digest,
            decoding_digest=decoding.content_digest, declared_search_calls=sum(s.declared_search_calls for s in sources),
            declared_search_units=sum(s.declared_search_units for s in sources),
            search_attribution="baseline_zero" if name == "A" else
            "sum_of_C_and_W_shared_artifacts" if name == "CW" else "single_mechanism",
        ))
    return tuple(result)


def _cells(cases: tuple[ComparisonCase, ...], artifacts: ComparisonArtifacts,
           protocol: ComparisonProtocol) -> tuple[ComparisonCell, ...]:
    count = 5 * len(protocol.seeds) * sum(getattr(protocol.samples_per_problem, c.dataset) for c in cases)
    if count > 100000:
        raise ValueError("comparison cell count exceeds the supported offline fixture bound")
    conditions = _conditions(artifacts)
    dataset_digests = {name: canonical_digest([c.to_dict() for c in cases if c.dataset == name])
                       for name in {c.dataset for c in cases}}
    result = []
    # Rotate first condition by problem while preserving identical coverage.
    for ordinal, case in enumerate(cases):
        shift = ordinal % len(conditions)
        for condition in (*conditions[shift:], *conditions[:shift]):
            context = artifacts.learned_context if condition.condition_id in {"C", "CW"} else artifacts.base_context
            text = case.problem.text + (protocol.math_prompt_suffix if case.dataset != "ifeval" else "")
            for seed in protocol.seeds:
                for index in range(getattr(protocol.samples_per_problem, case.dataset)):
                    result.append(ComparisonCell(
                        condition_id=condition.condition_id, dataset=case.dataset, problem_id=case.problem.problem_id,
                        sample_index=index, seed=seed, checkpoint_digest=condition.checkpoint_digest,
                        context_digest=condition.context_digest, decoding_digest=condition.decoding_digest,
                        runtime_digest=protocol.runtime_digest, tokenizer_digest=protocol.tokenizer_digest,
                        scorer_digest=protocol.scorer_digest, dataset_digest=dataset_digests[case.dataset],
                        context_text=context.text, user_text=text, max_new_tokens=protocol.max_new_tokens,
                    ))
    return tuple(result)


class ComparisonPlan(_DigestModel):
    schema_version: Literal[1] = 1
    execution_mode: Literal["offline_fixture"] = "offline_fixture"
    study_id: Identifier
    development: DevelopmentProvenance
    cases: tuple[ComparisonCase, ...] = Field(min_length=1, max_length=1000)
    artifacts: ComparisonArtifacts
    protocol: ComparisonProtocol
    budgets: ComparisonBudgets
    conditions: tuple[ComparisonCondition, ...] = Field(min_length=5, max_length=5)
    cells: tuple[ComparisonCell, ...] = Field(min_length=5, max_length=100000)
    schedule_version: Literal["rotating-condition-problem-seed-sample-v1"] = "rotating-condition-problem-seed-sample-v1"
    factorial_interaction: Literal["CW-C-W+A"] = "CW-C-W+A"

    @model_validator(mode="after")
    def complete_study(self) -> Self:
        practice, evaluation = [c.problem for c in self.development.cases], [c.problem for c in self.cases]
        practice_ids, evaluation_ids = [p.problem_id for p in practice], [p.problem_id for p in evaluation]
        if len(set(practice_ids)) != len(practice_ids) or len(set(evaluation_ids)) != len(evaluation_ids):
            raise ValueError("duplicate problem identity within a study split")
        for split in (practice, evaluation):
            prompts = {" ".join(unicodedata.normalize("NFKC", p.text).casefold().split()) for p in split}
            if len(prompts) != len(split):
                raise ValueError("duplicate normalized prompt within a study split")
        if set(practice_ids) & set(evaluation_ids) or overlapping_problem_ids(practice, evaluation):
            raise ValueError("development/evaluation problem IDs or normalized prompts overlap")
        if self.conditions != _conditions(self.artifacts) or self.cells != _cells(self.cases, self.artifacts, self.protocol):
            raise ValueError("condition permissions or paired cell schedule differ from frozen artifacts/protocol")
        for artifact in (self.artifacts.learned_context, self.artifacts.edited_checkpoint, self.artifacts.selected_decoding):
            source = artifact.source
            if not set(source.source_problem_ids) <= set(practice_ids):
                raise ValueError("artifact provenance must reference the common development pool only")
            if (source.declared_search_calls > self.budgets.max_search_calls_per_mechanism
                    or source.declared_search_units > self.budgets.max_search_units_per_mechanism):
                raise ValueError("declared fixture search usage exceeds the shared mechanism budget")
        if any(c.input_units > self.budgets.max_input_units_per_sample for c in self.cells):
            raise ValueError("fixture input exceeds the per-sample budget")
        per_condition = len(self.cells) // 5 * self.protocol.max_new_tokens
        if (per_condition > self.budgets.max_generated_units_per_condition
                or 5 * per_condition > self.budgets.max_total_generated_units
                or sum(c.input_units for c in self.cells) > self.budgets.max_total_input_units):
            raise ValueError("paired study exceeds frozen input/output reservations")
        return self

    @property
    def contrasts(self) -> tuple[ComparisonContrast, ...]:
        return tuple(ComparisonContrast(contrast_id=f"{t}-{c}", treatment=t, control=c)
                     for t, c in CONTRAST_PAIRS)

    def resource_summary(self) -> dict[str, Any]:
        sources = (self.artifacts.learned_context.source, self.artifacts.edited_checkpoint.source,
                   self.artifacts.selected_decoding.source)
        return {"plan_digest": self.digest, "execution_mode": self.execution_mode,
                "model_execution_performed": False, "gpu_execution_deferred": True,
                "cases": len(self.cases), "cells": len(self.cells), "condition_count": 5,
                "fixture_counter_unit": "utf8_bytes_not_model_tokens",
                "reserved_generated_units": len(self.cells) * self.protocol.max_new_tokens,
                "reserved_input_units": sum(c.input_units for c in self.cells),
                "declared_unique_search_calls": sum(s.declared_search_calls for s in sources),
                "declared_unique_search_units": sum(s.declared_search_units for s in sources),
                "search_usage_kind": "declared_fixture_not_measured_optimization",
                "cw_cost_note": "CW reuses C and W artifacts; attribute C+W cost without charging discovery twice."}


def build_comparison_plan(*, study_id: str, cases: tuple[ComparisonCase, ...], artifacts: ComparisonArtifacts,
                          protocol: ComparisonProtocol, budgets: ComparisonBudgets,
                          development: DevelopmentProvenance) -> ComparisonPlan:
    return ComparisonPlan(study_id=study_id, cases=cases, artifacts=artifacts, protocol=protocol, budgets=budgets,
                          development=development, conditions=_conditions(artifacts), cells=_cells(cases, artifacts, protocol))


def projected_requests(plan: ComparisonPlan) -> tuple[ComparisonCell, ...]:
    """Return frozen fixture requests containing no evaluation gold/instructions."""
    return plan.cells


def load_comparison_plan(path: Path) -> ComparisonPlan:
    return ComparisonPlan.model_validate_json(json.dumps(load_small_json(path, max_bytes=32 * 1024 * 1024)))
