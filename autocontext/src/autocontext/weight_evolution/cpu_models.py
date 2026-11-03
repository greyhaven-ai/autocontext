"""Strict contracts for a bounded, trusted CPU inference integration model.

This is a token-sum linear classifier, not Qwen. Requests contain only model
inputs and execution settings; labels and scorer authority stay outside them.
"""

from __future__ import annotations

import math
from typing import Annotated, Any, Literal, Self

from pydantic import Field, ValidationInfo, field_validator, model_validator

from autocontext.weight_evolution.artifacts import canonical_digest
from autocontext.weight_evolution.models import Digest, FrozenModel, Identifier

MAX_CPU_CHECKPOINT_BYTES = 4 * 1024 * 1024
MAX_CPU_PROMPT_BYTES = 8192
MAX_CPU_INPUT_TOKENS = 64
MAX_CPU_VOCABULARY = 32
MAX_CPU_OUTPUT_TOKENS = 8
CpuToken = Annotated[str, Field(min_length=1, max_length=64)]
CpuTokenId = Annotated[int, Field(ge=0, lt=MAX_CPU_VOCABULARY)]
CpuLogit = Annotated[float, Field(allow_inf_nan=False)]


class _CpuModel(FrozenModel):
    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())

    @field_validator("schema_version", "max_output_tokens", "model_execution_performed", "gpu_execution_deferred",
                     "promotion_eligible", mode="before", check_fields=False)
    @classmethod
    def literal_types(cls, value: Any, info: ValidationInfo) -> Any:
        # Pydantic's Literal comparison otherwise accepts 1 for True and True
        # for 1, despite strict=True. Evidence flags must preserve JSON types.
        integer_literals = {"schema_version", "max_output_tokens"}
        boolean_literals = {"model_execution_performed", "gpu_execution_deferred", "promotion_eligible"}
        if info.field_name in integer_literals and type(value) is not int:
            raise ValueError(f"{info.field_name} must be an integer")
        if info.field_name in boolean_literals and type(value) is not bool:
            raise ValueError(f"{info.field_name} must be a boolean")
        return value


class CpuModelContract(_CpuModel):
    schema_version: Literal[1] = 1
    architecture: Literal["token-sum-linear-v1"] = "token-sum-linear-v1"
    vocabulary: tuple[CpuToken, ...] = Field(min_length=1, max_length=MAX_CPU_VOCABULARY)
    output_tokens: tuple[CpuToken, ...] = Field(min_length=2, max_length=MAX_CPU_OUTPUT_TOKENS)

    @model_validator(mode="after")
    def unambiguous_tokens(self) -> Self:
        for tokens in (self.vocabulary, self.output_tokens):
            if len(set(tokens)) != len(tokens):
                raise ValueError("model token inventories must be unique")
            if any(any(char.isspace() or not char.isprintable() for char in token) for token in tokens):
                raise ValueError("model tokens must be printable and whitespace-free")
        return self


class CpuDecoding(_CpuModel):
    mode: Literal["greedy", "sample"] = "greedy"
    temperature: float = Field(default=1.0, gt=0.0, le=100.0, allow_inf_nan=False)
    top_k: int = Field(default=0, ge=0, le=MAX_CPU_OUTPUT_TOKENS)
    seed: int = Field(default=0, ge=0, le=2**32 - 1)


class CpuWorkerLimits(_CpuModel):
    memory_mb: int = Field(default=512, ge=256, le=2048)
    wall_seconds: float = Field(default=5.0, ge=0.1, le=30.0, allow_inf_nan=False)
    max_input_tokens: int = Field(default=MAX_CPU_INPUT_TOKENS, ge=1, le=MAX_CPU_INPUT_TOKENS)
    max_output_tokens: Literal[1] = 1


class CpuInferenceRequest(_CpuModel):
    schema_version: Literal[1] = 1
    request_id: Identifier = Field(max_length=128)
    model: CpuModelContract
    checkpoint_sha256: Digest
    checkpoint_size_bytes: int = Field(ge=10, le=MAX_CPU_CHECKPOINT_BYTES)
    prompt: str = Field(min_length=1, max_length=MAX_CPU_PROMPT_BYTES)
    decoding: CpuDecoding = Field(default_factory=CpuDecoding)
    limits: CpuWorkerLimits = Field(default_factory=CpuWorkerLimits)

    @model_validator(mode="after")
    def bounded_input(self) -> Self:
        if len(self.prompt.encode("utf-8")) > MAX_CPU_PROMPT_BYTES:
            raise ValueError("CPU prompt exceeds its UTF-8 byte cap")
        tokens = self.prompt.split()
        if not 1 <= len(tokens) <= self.limits.max_input_tokens:
            raise ValueError("CPU prompt exceeds the nonempty input-token bound")
        if any(token not in self.model.vocabulary for token in tokens):
            raise ValueError("CPU prompt contains an unknown vocabulary token")
        if self.decoding.top_k > len(self.model.output_tokens):
            raise ValueError("top_k exceeds the model output vocabulary")
        return self


class CpuInferenceResult(_CpuModel):
    schema_version: Literal[1] = 1
    execution_kind: Literal["cpu_inference"] = "cpu_inference"
    device: Literal["cpu"] = "cpu"
    model_execution_performed: Literal[True] = True
    gpu_execution_deferred: Literal[True] = True
    promotion_eligible: Literal[False] = False
    request_digest: Digest
    checkpoint_sha256: Digest
    checkpoint_size_bytes: int = Field(ge=10, le=MAX_CPU_CHECKPOINT_BYTES)
    input_tokens: tuple[CpuTokenId, ...] = Field(min_length=1, max_length=MAX_CPU_INPUT_TOKENS)
    logits: tuple[CpuLogit, ...] = Field(min_length=2, max_length=MAX_CPU_OUTPUT_TOKENS)
    output_token_id: int = Field(ge=0, lt=MAX_CPU_OUTPUT_TOKENS)
    text: CpuToken

    @model_validator(mode="after")
    def bounded_output(self) -> Self:
        if self.output_token_id >= len(self.logits):
            raise ValueError("output token is outside the logit inventory")
        if any(not math.isfinite(logit) for logit in self.logits):
            raise ValueError("CPU logits must be finite")
        if any(char.isspace() or not char.isprintable() for char in self.text):
            raise ValueError("CPU output must be one printable token")
        return self
