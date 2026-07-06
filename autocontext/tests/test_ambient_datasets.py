"""tests for the per-target dataset store and manifest."""

from __future__ import annotations

import json
from pathlib import Path

from autocontext.ambient.datasets import DatasetManifest, DatasetStore


def test_load_manifest_defaults_when_missing(tmp_path: Path) -> None:
    store = DatasetStore(tmp_path / "datasets")
    manifest = store.load_manifest("prover")
    assert manifest.target == "prover"
    assert manifest.record_count == 0
    assert manifest.last_record_id == 0


def test_append_records_writes_jsonl_lines(tmp_path: Path) -> None:
    store = DatasetStore(tmp_path / "datasets")
    written = store.append_records("prover", [{"strategy": "a", "score": 1.0}, {"strategy": "b", "score": 0.5}])
    assert written == 2

    lines = store.dataset_path("prover").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["strategy"] for line in lines] == ["a", "b"]

    more = store.append_records("prover", [{"strategy": "c", "score": 0.2}])
    assert more == 1
    assert len(store.dataset_path("prover").read_text(encoding="utf-8").splitlines()) == 3


def test_manifest_round_trip_is_atomic(tmp_path: Path) -> None:
    store = DatasetStore(tmp_path / "datasets")
    manifest = DatasetManifest(target="prover", record_count=3, last_record_id=17, mean_score=0.5, updated_at="t")
    store.save_manifest(manifest)

    assert store.load_manifest("prover") == manifest
    leftovers = [p for p in (tmp_path / "datasets").iterdir() if p.suffix == ".tmp"]
    assert leftovers == []


def test_absorb_updates_incremental_mean_and_counters(tmp_path: Path) -> None:
    store = DatasetStore(tmp_path / "datasets")
    manifest = DatasetManifest(target="prover", record_count=2, mean_score=0.5)

    updated = store.absorb(manifest, appended_scores=[1.0, 1.0], quarantined=1, skipped=2, last_record_id=42)

    assert updated.record_count == 4
    assert updated.mean_score == 0.75
    assert updated.quarantined_total == 1
    assert updated.skipped_total == 2
    assert updated.last_record_id == 42
    assert updated.updated_at != ""
    # absorb returns a new object; the input is untouched
    assert manifest.record_count == 2


def test_absorb_with_no_new_scores_keeps_mean(tmp_path: Path) -> None:
    store = DatasetStore(tmp_path / "datasets")
    manifest = DatasetManifest(target="prover", record_count=2, mean_score=0.5)
    updated = store.absorb(manifest, appended_scores=[], quarantined=0, skipped=1, last_record_id=9)
    assert updated.mean_score == 0.5
    assert updated.record_count == 2
    assert updated.last_record_id == 9
