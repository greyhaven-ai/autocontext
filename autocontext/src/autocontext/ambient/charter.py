"""charter models: the ambient daemon's only policy input.

Guardrail floors are enforced here so no charter file can disable them
(spec: docs/ambient-trainer-design.md, "The charter").
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

AutonomyLevel = Literal["propose", "train", "full"]
DeploymentTier = Literal["oss", "hosted-box"]
SourceKind = Literal["autocontext", "otel", "proxy", "full-box"]
TrainingMethod = Literal["sft-distill", "rlvr-experimental"]

_FLOOR_MESSAGE = "guardrail floor: this protection cannot be disabled through the charter"


class GuardrailConfig(BaseModel):
    # validate_assignment closes the silent post-construction bypass
    # (attribute assignment re-runs the floor validators). model_construct
    # and model_copy(update=...) still skip validation by pydantic design;
    # never use them on charter models with untrusted input.
    model_config = ConfigDict(validate_assignment=True)

    frozen_anchor: bool = True
    provenance_quarantine: bool = True
    asymmetric_trainability: bool = True
    drift_canaries: bool = True
    min_frontier_fraction: float = Field(default=0.2, ge=0.05, le=1.0)

    @field_validator("frozen_anchor", "provenance_quarantine", "asymmetric_trainability", "drift_canaries")
    @classmethod
    def _floor(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError(_FLOOR_MESSAGE)
        return value


class CharterSource(BaseModel):
    name: str = Field(min_length=1)
    kind: SourceKind
    enabled: bool = True
    redaction_profile: str = "default"


class CharterTarget(BaseModel):
    name: str = Field(min_length=1)
    kind: Literal["role", "task_family"]
    selector: str = Field(min_length=1)
    base_model: str = Field(min_length=1)
    method: TrainingMethod = "sft-distill"
    min_dataset_records: int = Field(ge=1)
    eval_suite: str = Field(min_length=1)
    autonomy: AutonomyLevel | None = None


class CharterBudgets(BaseModel):
    gpu_hours_per_window: float = Field(gt=0)
    window_hours: int = Field(ge=1)
    disk_quota_gb: float = Field(gt=0)
    priority: Literal["serving", "training"] = "serving"


class Charter(BaseModel):
    tier: DeploymentTier
    control_surface: Literal["local", "autowork"] = "local"
    autonomy: AutonomyLevel = "propose"
    sources: list[CharterSource]
    targets: list[CharterTarget]
    budgets: CharterBudgets
    guardrails: GuardrailConfig = Field(default_factory=GuardrailConfig)

    @model_validator(mode="after")
    def _full_box_requires_hosted(self) -> Charter:
        for source in self.sources:
            if source.kind == "full-box" and self.tier != "hosted-box":
                raise ValueError("full-box sources require the hosted-box tier")
        return self

    @model_validator(mode="after")
    def _target_names_unique(self) -> Charter:
        # policy lookups and proposal keying treat target name as a unique key
        names = [target.name for target in self.targets]
        duplicates = {name for name in names if names.count(name) > 1}
        if duplicates:
            raise ValueError(f"duplicate target names: {sorted(duplicates)}")
        return self
