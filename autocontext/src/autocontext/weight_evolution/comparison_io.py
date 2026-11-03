"""Bounded local-only loading of the synthetic comparison example bundle."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import Field

from autocontext.weight_evolution.artifacts import load_small_json, regular_file
from autocontext.weight_evolution.comparison_evaluation import FixtureEvidence
from autocontext.weight_evolution.comparison_models import ComparisonPlan
from autocontext.weight_evolution.models import Digest, FrozenModel, _unique_object

MAX_FIXTURE_BYTES = 8 * 1024 * 1024


class ComparisonFixtureLock(FrozenModel):
    schema_version: Literal[1] = 1
    execution_kind: Literal["fixture"] = "fixture"
    model_execution_performed: Literal[False] = False
    study_sha256: Digest
    study_size_bytes: int = Field(gt=0, le=MAX_FIXTURE_BYTES)
    responses_sha256: Digest
    responses_size_bytes: int = Field(gt=0, le=MAX_FIXTURE_BYTES)


def _verified_bytes(path: Path, digest: str, size: int) -> bytes:
    with regular_file(path) as stream:
        data = stream.read(size + 1)
    if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
        raise ValueError(f"comparison fixture size/hash mismatch: {path.name}")
    # Reject repeated object keys before Pydantic can silently pick one value.
    value = json.loads(data, object_pairs_hook=_unique_object)
    json.dumps(value, allow_nan=False)
    return data


def load_comparison_fixture(directory: Path) -> tuple[ComparisonPlan, FixtureEvidence]:
    """Authenticate fixed filenames, then validate the complete fixture models.

    This lock detects accidental/tampered fixture bytes; it is not remote
    execution attestation. There are no URLs, dynamic imports or model calls.
    """
    lock = ComparisonFixtureLock.model_validate_json(json.dumps(load_small_json(directory / "fixtures.lock.json")))
    plan = ComparisonPlan.model_validate_json(
        _verified_bytes(directory / "study.json", lock.study_sha256, lock.study_size_bytes),
    )
    evidence = FixtureEvidence.model_validate_json(
        _verified_bytes(directory / "responses.json", lock.responses_sha256, lock.responses_size_bytes),
    )
    if evidence.plan_digest != plan.digest:
        raise ValueError("comparison responses belong to a different frozen study")
    return plan, evidence
