"""Pinned measured-workflow capacity and immutable returned operation evidence."""

from __future__ import annotations

import json
import math
from typing import Any, Literal, Self

from pydantic import Field, ValidationInfo, field_validator, model_validator

from autocontext.weight_evolution.artifacts import canonical_digest
from autocontext.weight_evolution.cpu_failure_v2_models import CpuFailureContractV2, CpuFailureRecordV2
from autocontext.weight_evolution.cpu_measured_models import CpuMeasuredBatchOutcome
from autocontext.weight_evolution.models import Digest, FrozenModel, Identifier

EXECUTION_METERS = ("proposals", "materializations", "inference_calls", "input_tokens", "output_tokens")
MAX_EXECUTION_OPERATIONS = 256
MAX_OPERATION_OUTCOME_BYTES = 768 * 1024
CpuExecutionScope = Literal["search", "reference"]
CpuOperationKind = Literal["materialize", "proposal", "checkpoint-verification", "inference"]


def validate_execution_json(value: Any, maximum: int) -> None:
    count = 0
    characters = 0

    def visit(item: Any, depth: int, active: set[int]) -> None:
        nonlocal count, characters
        count += 1
        if count > 32768 or depth > 32:
            raise ValueError("operation JSON exceeds its structural bound")
        kind = type(item)
        if kind is str:
            characters += len(item)
        elif item is None or kind is bool:
            pass
        elif kind is int:
            if item.bit_length() > 128:
                raise ValueError("operation integer exceeds its bounded JSON representation")
        elif kind is float:
            if not math.isfinite(item):
                raise ValueError("operation JSON contains a nonfinite value")
        elif kind in {dict, list}:
            if id(item) in active:
                raise ValueError("operation JSON contains a cycle")
            active.add(id(item))
            if kind is dict:
                for key, child in item.items():
                    if type(key) is not str:
                        raise ValueError("operation JSON requires string keys")
                    visit(key, depth + 1, active)
                    visit(child, depth + 1, active)
            else:
                for child in item:
                    visit(child, depth + 1, active)
            active.remove(id(item))
        else:
            raise ValueError("operation payload is not exact JSON data")
        if characters > maximum:
            raise ValueError("operation JSON exceeds its text bound")

    visit(value, 0, set())
    if len(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()) > maximum:
        raise ValueError("operation JSON exceeds its serialized byte bound")

class _ExecutionModel(FrozenModel):
    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())

    @field_validator("schema_version", "gpu_execution_deferred", "promotion_eligible", "retry_permitted",
                     "refund_permitted", mode="before", check_fields=False)
    @classmethod
    def exact_literals(cls, value: Any, info: ValidationInfo) -> Any:
        if type(value) is not (int if info.field_name == "schema_version" else bool):
            raise ValueError("execution contract literal has the wrong exact type")
        return value


class CpuExecutionUsage(_ExecutionModel):
    proposals: int = Field(default=0, ge=0, le=48)
    materializations: int = Field(default=0, ge=0, le=8)
    inference_calls: int = Field(default=0, ge=0, le=2048)
    input_tokens: int = Field(default=0, ge=0, le=131072)
    output_tokens: int = Field(default=0, ge=0, le=2048)


class CpuExecutionContract(_ExecutionModel):
    schema_version: Literal[1] = 1
    kind: Literal["measured-cpu-workflow-contract-v1"] = "measured-cpu-workflow-contract-v1"
    scope: CpuExecutionScope
    design_digest: Digest
    workflow_digest: Digest
    workflow_implementation_digest: Digest
    implementation_digest: Digest
    native_runtime_digest: Digest
    measured_profile_digest: Digest
    failure_contract: CpuFailureContractV2 = Field(default_factory=CpuFailureContractV2)
    capacity: CpuExecutionUsage
    retry_permitted: Literal[False] = False
    refund_permitted: Literal[False] = False
    gpu_execution_deferred: Literal[True] = True
    promotion_eligible: Literal[False] = False

    @model_validator(mode="after")
    def complete_capacity(self) -> Self:
        if self.scope == "search" and self.workflow_digest != self.design_digest:
            raise ValueError("search design and workflow must identify the same native study")
        if self.scope == "reference" and self.capacity.proposals:
            raise ValueError("exhaustive references cannot reserve proposals")
        if (not self.capacity.materializations or not self.capacity.inference_calls
                or self.capacity.output_tokens != self.capacity.inference_calls
                or self.capacity.input_tokens < self.capacity.inference_calls):
            raise ValueError("workflow capacity must reserve complete checkpoint and one-token inference coverage")
        if self.scope == "search" and not self.capacity.proposals:
            raise ValueError("search capacity must reserve proposals")
        return self


class CpuOperationClaim(_ExecutionModel):
    """New claim grammar; never presented as a native M11--M15 journal entry."""

    schema_version: Literal[1] = 1
    contract_digest: Digest
    index: int = Field(ge=0, lt=MAX_EXECUTION_OPERATIONS)
    kind: CpuOperationKind
    label: Identifier = Field(max_length=128)
    input: dict[str, Any]
    reserved: CpuExecutionUsage
    previous_result_digest: Digest

    @field_validator("input", mode="before")
    @classmethod
    def bounded_input(cls, value: Any) -> Any:
        validate_execution_json(value, 512 * 1024)
        return value


class CpuOperationOutcome(_ExecutionModel):
    schema_version: Literal[1] = 1
    kind: Literal["measured-cpu-operation-outcome-v1"] = "measured-cpu-operation-outcome-v1"
    contract_digest: Digest
    operation_claim_digest: Digest
    operation_index: int = Field(ge=0, lt=MAX_EXECUTION_OPERATIONS)
    operation_kind: CpuOperationKind
    operation_label: Identifier = Field(max_length=128)
    operation_input_digest: Digest
    reserved: CpuExecutionUsage
    status: Literal["completed", "failed"]
    measured: CpuMeasuredBatchOutcome | None = None
    value: dict[str, Any] | None = None
    failure: CpuFailureRecordV2 | None = None
    retry_permitted: Literal[False] = False
    refund_permitted: Literal[False] = False
    gpu_execution_deferred: Literal[True] = True
    promotion_eligible: Literal[False] = False

    @field_validator("value", mode="before")
    @classmethod
    def bounded_value(cls, value: Any) -> Any:
        if value is not None:
            validate_execution_json(value, 64 * 1024)
        return value

    @model_validator(mode="after")
    def returned_evidence_only(self) -> Self:
        if self.operation_kind == "inference":
            if (self.measured is None or self.value is not None or self.failure is not None
                    or self.status != self.measured.status):
                raise ValueError("inference outcome requires its actual measured return, with the identical status")
        elif self.measured is not None:
            raise ValueError("noninference operations cannot invent process measurements")
        elif self.status == "completed":
            if self.value is None or self.failure is not None:
                raise ValueError("completed noninference outcome requires exactly its returned value")
        elif self.failure is None or self.value is not None:
            raise ValueError("failed noninference outcome requires exactly its observed exception")
        if self.failure is not None:
            if (self.failure.native_journal_kind != "absent" or self.failure.observation_kind != "live-exception"
                    or any(value is not None for value in self.failure.observations.to_dict().values())):
                raise ValueError("noninference failure must have live evidence and no invented journal or process observations")
        size = len(json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False).encode())
        if size > MAX_OPERATION_OUTCOME_BYTES:
            raise ValueError("operation outcome exceeds its serialized byte bound")
        return self
