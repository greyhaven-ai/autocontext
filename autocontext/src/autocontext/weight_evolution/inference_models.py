"""Data-only requests and raw evidence for the pinned Qwen inference worker."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import Field, field_validator, model_validator

from autocontext.weight_evolution.models import Digest, FrozenModel, _unique_object


def content_digest(value: dict[str, Any]) -> str:
    """Hash canonical JSON using the same representation as the reproduction lock."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def text_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class InferenceProblem(FrozenModel):
    problem_id: str = Field(min_length=1, max_length=512)
    text: str = Field(min_length=1, max_length=131072)

    @field_validator("problem_id")
    @classmethod
    def canonical_id(cls, value: str) -> str:
        if value != value.strip() or any(ord(char) < 32 for char in value):
            raise ValueError("problem identity must be nonblank, trimmed and contain no control characters")
        return value

    @field_validator("text")
    @classmethod
    def nonblank_text(cls, value: str) -> str:
        if not value.strip() or "\0" in value:
            raise ValueError("problem text must be nonblank and contain no NUL")
        return value


class InferenceRequest(FrozenModel):
    schema_version: Literal[1] = 1
    variant: Literal["base", "published_patch"]
    dataset: Literal["math500", "gsm8k", "ifeval"]
    # Snapshot identity is supplied by the caller; this milestone does not certify
    # dataset provenance or full benchmark membership. request.digest additionally
    # binds every actual input, its order, and the mode so labels cannot alias runs.
    dataset_sha256: Digest
    problems: tuple[InferenceProblem, ...] = Field(min_length=1, max_length=2048)
    mode: Literal["reproduction", "smoke"] = "reproduction"
    smoke_max_new_tokens: int | None = Field(default=None, ge=1, lt=32768)

    @model_validator(mode="after")
    def complete_request(self) -> Self:
        if len({problem.problem_id for problem in self.problems}) != len(self.problems):
            raise ValueError("duplicate problem identity")
        if (self.mode == "smoke") != (self.smoke_max_new_tokens is not None):
            raise ValueError("only smoke requests require a reduced smoke_max_new_tokens")
        return self

    @property
    def digest(self) -> str:
        return content_digest(self.to_dict())


class GenerationSample(FrozenModel):
    problem_id: str
    sample_index: int = Field(ge=0)
    seed: Literal[0] = 0
    rendered_prompt: str
    prompt_sha256: Digest
    prompt_token_ids: tuple[int, ...]
    input_tokens: int = Field(ge=1)
    text: str
    response_sha256: Digest
    token_ids: tuple[int, ...]
    output_tokens: int = Field(ge=0)
    finish_reason: Literal["stop", "length"]
    stop_reason: int | str | None

    @model_validator(mode="after")
    def validate_evidence(self) -> Self:
        if self.input_tokens != len(self.prompt_token_ids) or self.output_tokens != len(self.token_ids):
            raise ValueError("token counts differ from token evidence")
        if any(token < 0 for token in (*self.prompt_token_ids, *self.token_ids)):
            raise ValueError("token IDs must be nonnegative")
        if self.prompt_sha256 != text_digest(self.rendered_prompt) or self.response_sha256 != text_digest(self.text):
            raise ValueError("text hash differs from raw evidence")
        return self


class InferenceResult(FrozenModel):
    schema_version: Literal[1] = 1
    status: Literal["completed"] = "completed"
    execution_kind: Literal["vllm", "fixture"]
    lock_digest: Digest
    runtime_digest: Digest
    runtime_image: str
    runtime_environment: dict[str, Any]
    checkpoint_receipt_digest: Digest
    checkpoint_sha256: Digest
    tensor_content_digest: Digest
    request_digest: Digest
    request: InferenceRequest
    max_new_tokens: int = Field(ge=1, le=32768)
    max_model_len: Literal[34816] = 34816
    samples_per_problem: int = Field(ge=1, le=4)
    sampling: dict[str, int | float | bool | None]
    elapsed_seconds: float = Field(ge=0, allow_inf_nan=False)
    hardware: dict[str, str]
    samples: tuple[GenerationSample, ...]
    benchmark_score: None = None

    @model_validator(mode="after")
    def complete_samples(self) -> Self:
        if self.request_digest != self.request.digest:
            raise ValueError("request identity differs from evidence")
        expected_count = 4 if self.request.dataset == "math500" else 2
        expected_cap = self.request.smoke_max_new_tokens or 32768
        if self.samples_per_problem != expected_count or self.max_new_tokens != expected_cap:
            raise ValueError("result sampling budget differs from request protocol")
        expected_sampling = {
            "n": expected_count, "temperature": 0.6, "top_p": 0.95, "top_k": 20,
            "presence_penalty": 1.5, "max_tokens": expected_cap, "seed": 0,
            "stop": None, "stop_token_ids": None, "ignore_eos": False,
        }
        if self.sampling != expected_sampling:
            raise ValueError("result sampling differs from pinned think35 protocol")
        expected = {(problem.problem_id, index) for problem in self.request.problems for index in range(self.samples_per_problem)}
        actual = [(sample.problem_id, sample.sample_index) for sample in self.samples]
        if len(set(actual)) != len(actual) or set(actual) != expected:
            raise ValueError("generation evidence has duplicate, missing, or unexpected samples")
        if any(sample.output_tokens > self.max_new_tokens for sample in self.samples):
            raise ValueError("generation evidence exceeds the token cap")
        if any(sample.input_tokens > 2048 for sample in self.samples):
            raise ValueError("generation evidence exceeds the pinned input allowance")
        if any(sample.finish_reason == "length" and sample.output_tokens != self.max_new_tokens for sample in self.samples):
            raise ValueError("length termination did not reach the output token cap")
        return self

    @property
    def digest(self) -> str:
        return content_digest(self.to_dict())


def load_inference_request(path: Path) -> InferenceRequest:
    """Read a bounded regular file; reject symlinks and ambiguous/coerced JSON."""
    try:
        absolute = path.absolute()
        if any(parent.is_symlink() for parent in (absolute, *absolute.parents)):
            raise ValueError("request path must not traverse symlinks")
        descriptor = os.open(absolute, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            os.close(descriptor)
            raise ValueError("request must be a regular file")
        with os.fdopen(descriptor, "rb") as stream:
            data = stream.read(16 * 1024 * 1024 + 1)
        if len(data) > 16 * 1024 * 1024:
            raise ValueError("inference request exceeds 16 MiB")
        json.loads(data, object_pairs_hook=_unique_object)
        return InferenceRequest.model_validate_json(data)
    except (OSError, ValueError) as exc:
        raise ValueError(f"invalid inference request: {exc}") from exc
