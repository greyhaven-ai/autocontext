from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from autocontext.weight_evolution.reference import load_published_results

EXAMPLE = Path(__file__).resolve().parents[2] / "examples/weight_evolution/qwen_reproduction"


def test_published_results_are_complete_and_bound_to_original_result_bytes() -> None:
    result = load_published_results(EXAMPLE / "published-results.lock.json")
    assert len(result.metrics) == 6
    assert {(metric.dataset, metric.variant) for metric in result.metrics} == {
        (dataset, variant) for dataset in ("math500", "gsm8k", "ifeval") for variant in ("base", "published_patch")
    }
    assert len(result.digest) == 64


@pytest.fixture
def copied_reference(tmp_path: Path) -> Path:
    destination = tmp_path / "published-results.lock.json"
    shutil.copyfile(EXAMPLE / destination.name, destination)
    shutil.copytree(EXAMPLE / "reference", tmp_path / "reference")
    return destination


@pytest.mark.parametrize("mutation", ["source_bytes", "metric", "incomplete", "duplicate_metric", "foreign_source"])
def test_reference_drift_and_missing_coverage_are_rejected(copied_reference: Path, mutation: str) -> None:
    lock = json.loads(copied_reference.read_bytes())
    source = copied_reference.parent / lock["sources"][0]["relative_path"]
    if mutation == "source_bytes":
        source.write_bytes(source.read_bytes() + b" ")
    elif mutation == "metric":
        lock["metrics"][0]["accuracy"] = 0.123
    elif mutation == "incomplete":
        records = json.loads(source.read_bytes())
        records["math500"]["complete"] = False
        data = json.dumps(records).encode()
        source.write_bytes(data)
        lock["sources"][0].update(sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data))
    elif mutation == "duplicate_metric":
        lock["metrics"][1] = lock["metrics"][0]
    else:
        lock["sources"][0]["url"] = "https://example.com/unverified.json"
    copied_reference.write_text(json.dumps(lock))
    with pytest.raises(ValueError):
        load_published_results(copied_reference)
