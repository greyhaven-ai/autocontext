"""Pinned published aggregate results, kept separate from measured evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, model_validator

from autocontext.weight_evolution.artifacts import load_small_json, regular_file
from autocontext.weight_evolution.inference_models import content_digest
from autocontext.weight_evolution.models import Digest, FrozenModel


class PublishedSource(FrozenModel):
    variant: Literal["base", "published_patch"]
    relative_path: str
    url: str
    sha256: Digest
    size_bytes: int = Field(gt=0, le=65536)

    @model_validator(mode="after")
    def fixed_location(self) -> Self:
        run = "unedited" if self.variant == "base" else "p50c"
        if self.relative_path != f"reference/{run}.json" or self.url != (
            "https://github.com/RikiyaT/lm_understand-logs/blob/"
            f"4d09ba6d6a7e891dd8063974dfe81b3d78e3494f/exp8_math/eval/{run}/merged.json"
        ):
            raise ValueError("published reference must identify the pinned final result")
        return self


class PublishedMetric(FrozenModel):
    dataset: Literal["math500", "gsm8k", "ifeval"]
    variant: Literal["base", "published_patch"]
    accuracy: float = Field(ge=0, le=1, allow_inf_nan=False)
    token_limit_rate: float = Field(ge=0, le=1, allow_inf_nan=False)
    mean_tokens: float = Field(ge=0, le=32768, allow_inf_nan=False)
    n_problems: int = Field(gt=0)


class PublishedResults(FrozenModel):
    schema_version: Literal[1]
    source_revision: Literal["4d09ba6d6a7e891dd8063974dfe81b3d78e3494f"]
    sources: tuple[PublishedSource, ...] = Field(min_length=2, max_length=2)
    metrics: tuple[PublishedMetric, ...] = Field(min_length=6, max_length=6)

    @model_validator(mode="after")
    def complete(self) -> Self:
        if {item.variant for item in self.sources} != {"base", "published_patch"}:
            raise ValueError("published results require both variants")
        expected = {(dataset, variant) for dataset in ("math500", "gsm8k", "ifeval")
                    for variant in ("base", "published_patch")}
        if {(item.dataset, item.variant) for item in self.metrics} != expected:
            raise ValueError("published metrics must cover each dataset and variant exactly once")
        return self

    @property
    def digest(self) -> str:
        return content_digest(self.to_dict())


def load_published_results(path: Path) -> PublishedResults:
    raw = load_small_json(path, max_bytes=65536)
    result = PublishedResults.model_validate_json(json.dumps(raw))
    for source in result.sources:
        with regular_file(path.parent / source.relative_path) as stream:
            data = stream.read(source.size_bytes + 1)
        if len(data) != source.size_bytes or hashlib.sha256(data).hexdigest() != source.sha256:
            raise ValueError("published source bytes differ from their pin")
        records = json.loads(data)
        for metric in (item for item in result.metrics if item.variant == source.variant):
            row = records[metric.dataset]
            if row.get("complete") is not True or (
                row["acc"], row["cut_share"], row["mean_tokens"], row["problems"]
            ) != (metric.accuracy, metric.token_limit_rate, metric.mean_tokens, metric.n_problems):
                raise ValueError("published metric differs from its final source record")
    return result
