"""Frozen inputs for the AC-1029 schema-migration experiment."""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, model_validator

from autocontext.artifacts.policy_candidate import FrozenContract, json_payload
from autocontext.context_bundles.models import stable_digest
from autocontext.execution.executable_skills import ProfileV1, _json
from autocontext.execution.skill_routing_models import ModelTarget, RoutingBudget

ARMS = ("baseline", "textual", "executable", "cheap_textual")
Arm = Literal["baseline", "textual", "executable", "cheap_textual"]


class Case(FrozenContract):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    group: str = Field(min_length=1, max_length=80)
    cohort: Literal["supported", "shifted"]
    input_json: str = Field(min_length=1, max_length=8192)

    @property
    def input_digest(self) -> str:
        return stable_digest(_json(self.input_json))

    @model_validator(mode="after")
    def truthful_cohort(self) -> Case:
        data = _json(self.input_json)
        json_payload(data).encode("utf-8", errors="strict")
        try:
            supported = ProfileV1.model_validate(data).schema_version == 1
        except ValueError:
            supported = False
        if supported != (self.cohort == "supported"):
            raise ValueError("case cohort disagrees with the frozen task contract")
        return self


class Corpus(FrozenContract):
    schema_version: Literal["ac1029.corpus.v1"] = "ac1029.corpus.v1"
    training: tuple[Case, ...] = Field(min_length=2, max_length=32)
    development: tuple[Case, ...] = Field(min_length=2, max_length=100)
    heldout: tuple[Case, ...] = Field(min_length=2, max_length=1000)

    @model_validator(mode="after")
    def isolated_splits(self) -> Corpus:
        ids: set[str] = set()
        inputs: set[str] = set()
        groups: set[str] = set()
        for split in (self.training, self.development, self.heldout):
            new_groups = {case.group for case in split}
            if groups & new_groups:
                raise ValueError("group leakage across splits")
            groups |= new_groups
            if {case.cohort for case in split} != {"supported", "shifted"}:
                raise ValueError("every split requires supported and shifted cases")
            for case in split:
                if case.id in ids or case.input_digest in inputs:
                    raise ValueError("duplicate case or input across the corpus")
                ids.add(case.id)
                inputs.add(case.input_digest)
        # A sampling group belongs to one cohort, so stratified resampling is defined.
        for group in groups:
            if len({c.cohort for split in (self.training, self.development, self.heldout)
                    for c in split if c.group == group}) != 1:
                raise ValueError("sampling groups must not mix cohorts")
        return self

    @property
    def training_digest(self) -> str:
        return stable_digest([case.model_dump(mode="json") for case in self.training])


class StudyProtocol(FrozenContract):
    schema_version: Literal["ac1029.protocol.v1"] = "ac1029.protocol.v1"
    request_budget: RoutingBudget = Field(default_factory=lambda: RoutingBudget(max_attempts=2))
    max_evaluation_calls: int = Field(default=400, ge=4, le=10000, strict=True)
    max_evaluation_tokens: int = Field(default=4000000, ge=1, le=100000000, strict=True)
    max_evaluation_model_cost_usd: float = Field(default=100, ge=0)
    evaluation_wall_seconds: float = Field(default=3600, gt=0, le=86400)
    min_cases_per_cohort: int = Field(default=12, ge=2, strict=True)
    min_groups_per_cohort: int = Field(default=4, ge=2, strict=True)
    quality_floor: float = Field(default=.95, ge=0, le=1)
    acceptable_regression: float = Field(default=.02, ge=0, le=1)
    min_skill_coverage: float = Field(default=.5, ge=0, le=1)
    bootstrap_seed: int = Field(default=1029, ge=0, strict=True)
    bootstrap_resamples: int = Field(default=2000, ge=100, le=10000, strict=True)
    repetition_horizons: tuple[int, ...] = (1, 2, 5, 10, 25, 100, 1000)
    shifted_frequencies: tuple[float, ...] = (0, .1, .5, 1)

    @model_validator(mode="after")
    def bounded_design(self) -> StudyProtocol:
        if (not self.repetition_horizons or any(type(n) is not int or not 1 <= n <= 1000000
                                               for n in self.repetition_horizons)
                or sorted(set(self.repetition_horizons)) != list(self.repetition_horizons)):
            raise ValueError("reuse horizons must be increasing positive integers")
        if not self.shifted_frequencies or any(not 0 <= x <= 1 for x in self.shifted_frequencies):
            raise ValueError("shift frequencies must be probabilities")
        if self.request_budget.max_attempts != 2:
            raise ValueError("pilot allows one skill attempt and at most one model dispatch")
        return self


class Models(FrozenContract):
    evidence_kind: Literal["fixture", "live"]
    reference: ModelTarget
    cheaper: ModelTarget

    @model_validator(mode="after")
    def matched_control(self) -> Models:
        if self.evidence_kind == "fixture" and any(t.provider != "openai-compatible" or urlsplit(t.resolved_endpoint).hostname
                                                 not in {"127.0.0.1", "localhost", "::1"}
                                                 for t in (self.reference, self.cheaper)):
            raise ValueError("fixture models must use an explicit loopback HTTP fixture")
        if (self.reference.provider, self.reference.model, self.reference.resolved_endpoint) == (
                self.cheaper.provider, self.cheaper.model, self.cheaper.resolved_endpoint):
            raise ValueError("cheaper control must identify a distinct model")
        for field in ("max_input_tokens", "max_output_tokens", "timeout_seconds"):
            if getattr(self.reference, field) != getattr(self.cheaper, field):
                raise ValueError("all arms require equal declared resource limits")
        if self.cheaper.cost(self.cheaper.max_input_tokens, self.cheaper.max_output_tokens) >= self.reference.cost(
                self.reference.max_input_tokens, self.reference.max_output_tokens):
            raise ValueError("cheaper control requires lower declared maximum model cost")
        return self


class DiscoveryCost(FrozenContract):
    """One-time ledger entry; unknown resources stay unknown, never zero."""

    id: str = Field(min_length=1)
    phase: Literal["discovery", "failed_candidate", "development", "verification", "evaluation", "other"]
    arms: tuple[Arm, ...] = Field(min_length=1)
    seconds: float | None = Field(default=None, ge=0)
    model_calls: int | None = Field(default=None, ge=0, strict=True)
    tokens: int | None = Field(default=None, ge=0, strict=True)
    total_cost_usd: float | None = Field(default=None, ge=0)
    evidence: str = Field(min_length=1)


class LearningRecord(FrozenContract):
    training_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    origin: Literal["fixture", "externally_prepared"]
    method: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    # Costs include unsuccessful attempts; shared work is charged fully to each
    # consumer arm as if independently deployed, never divided by arm count.
    discovery: tuple[DiscoveryCost, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def complete_ledger_shape(self) -> LearningRecord:
        if len({row.id for row in self.discovery}) != len(self.discovery):
            raise ValueError("duplicate discovery ledger entry")
        if {arm for row in self.discovery for arm in row.arms} != set(ARMS):
            raise ValueError("discovery ledger must account for every arm, including explicit zero/unknown")
        if any(len(set(row.arms)) != len(row.arms) for row in self.discovery):
            raise ValueError("duplicate arm in discovery ledger")
        return self
