"""Tests for AC-292: verification dataset registry, provenance, and oracle feedback.

Covers: DatasetProvenance, VerificationDataset, DatasetRegistry,
VerificationRunRecord, OracleRevisionFeedback, oracle_to_revision_feedback.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_items() -> list[Any]:
    from autocontext.execution.objective_verification import GroundTruthItem

    return [
        GroundTruthItem(
            item_id="item-1",
            description="Warfarin + Aspirin bleeding risk",
            match_keywords=[["warfarin"], ["aspirin"]],
            weight="high",
        ),
        GroundTruthItem(
            item_id="item-2",
            description="Metformin + Lisinopril hypotension",
            match_keywords=[["metformin"], ["lisinopril"]],
            weight="moderate",
        ),
    ]


def _make_provenance() -> Any:
    from autocontext.execution.verification_dataset import DatasetProvenance

    return DatasetProvenance(
        source="FDA Drug Interaction Database",
        curator="operator-alice",
        version="1.0.0",
        domain="drug_interaction",
        updated_at="2026-03-16T12:00:00Z",
        notes="Curated from FDA label data",
    )


# ===========================================================================
# DatasetProvenance
# ===========================================================================


class TestDatasetProvenance:
    def test_construction(self) -> None:
        prov = _make_provenance()
        assert prov.version == "1.0.0"
        assert prov.curator == "operator-alice"

    def test_roundtrip(self) -> None:
        from autocontext.execution.verification_dataset import DatasetProvenance

        prov = _make_provenance()
        d = prov.to_dict()
        restored = DatasetProvenance.from_dict(d)
        assert restored.source == "FDA Drug Interaction Database"
        assert restored.domain == "drug_interaction"


# ===========================================================================
# VerificationDataset
# ===========================================================================


class TestVerificationDataset:
    def test_construction(self) -> None:
        from autocontext.execution.verification_dataset import VerificationDataset

        ds = VerificationDataset(
            dataset_id="ds-l19-v1",
            name="L19 Drug Interactions",
            provenance=_make_provenance(),
            items=_make_items(),
            claim_patterns=[r"^\d+\."],
        )
        assert ds.dataset_id == "ds-l19-v1"
        assert len(ds.items) == 2

    def test_roundtrip(self) -> None:
        from autocontext.execution.verification_dataset import VerificationDataset

        ds = VerificationDataset(
            dataset_id="ds-test",
            name="Test Dataset",
            provenance=_make_provenance(),
            items=_make_items(),
        )
        d = ds.to_dict()
        restored = VerificationDataset.from_dict(d)
        assert restored.dataset_id == "ds-test"
        assert len(restored.items) == 2
        assert restored.provenance.version == "1.0.0"

    def test_build_oracle(self) -> None:
        from autocontext.execution.verification_dataset import VerificationDataset

        ds = VerificationDataset(
            dataset_id="ds-test",
            name="Test",
            provenance=_make_provenance(),
            items=_make_items(),
        )
        oracle = ds.build_oracle()
        result = oracle.evaluate("Warfarin and Aspirin have a bleeding interaction.")
        assert result.found_count >= 1


# ===========================================================================
# DatasetRegistry
# ===========================================================================


class TestDatasetRegistry:
    def test_register_and_load(self, tmp_path: Path) -> None:
        from autocontext.execution.verification_dataset import (
            DatasetRegistry,
            VerificationDataset,
        )

        registry = DatasetRegistry(tmp_path)
        ds = VerificationDataset(
            dataset_id="ds-1",
            name="Test",
            provenance=_make_provenance(),
            items=_make_items(),
        )
        registry.register(ds)

        loaded = registry.load("ds-1")
        assert loaded is not None
        assert loaded.name == "Test"

    def test_load_missing(self, tmp_path: Path) -> None:
        from autocontext.execution.verification_dataset import DatasetRegistry

        registry = DatasetRegistry(tmp_path)
        assert registry.load("nonexistent") is None

    def test_list_datasets(self, tmp_path: Path) -> None:
        from autocontext.execution.verification_dataset import (
            DatasetRegistry,
            VerificationDataset,
        )

        registry = DatasetRegistry(tmp_path)
        for i in range(3):
            registry.register(VerificationDataset(
                dataset_id=f"ds-{i}",
                name=f"Dataset {i}",
                provenance=_make_provenance(),
                items=_make_items(),
            ))
        assert len(registry.list_datasets()) == 3

    def test_version_update(self, tmp_path: Path) -> None:
        from autocontext.execution.verification_dataset import (
            DatasetProvenance,
            DatasetRegistry,
            VerificationDataset,
        )

        registry = DatasetRegistry(tmp_path)
        ds_v1 = VerificationDataset(
            dataset_id="ds-1",
            name="Test v1",
            provenance=DatasetProvenance(
                source="test", curator="alice", version="1.0.0",
                domain="test", updated_at="2026-03-16T12:00:00Z",
            ),
            items=_make_items(),
        )
        registry.register(ds_v1)

        ds_v2 = VerificationDataset(
            dataset_id="ds-1",
            name="Test v2",
            provenance=DatasetProvenance(
                source="test", curator="alice", version="2.0.0",
                domain="test", updated_at="2026-03-16T13:00:00Z",
            ),
            items=_make_items(),
        )
        registry.register(ds_v2)

        loaded = registry.load("ds-1")
        assert loaded is not None
        assert loaded.provenance.version == "2.0.0"
        original = registry.load("ds-1", version="1.0.0")
        assert original is not None
        assert original.name == "Test v1"
        assert registry.list_versions("ds-1") == ["1.0.0", "2.0.0"]

    def test_rejects_overwrite_of_existing_version(self, tmp_path: Path) -> None:
        from autocontext.execution.verification_dataset import (
            DatasetProvenance,
            DatasetRegistry,
            VerificationDataset,
        )

        registry = DatasetRegistry(tmp_path)
        registry.register(VerificationDataset(
            dataset_id="ds-1",
            name="Test v1",
            provenance=DatasetProvenance(
                source="test", curator="alice", version="1.0.0",
                domain="test", updated_at="2026-03-16T12:00:00Z",
            ),
            items=_make_items(),
        ))

        with pytest.raises(ValueError, match="Refusing to overwrite"):
            registry.register(VerificationDataset(
                dataset_id="ds-1",
                name="Changed snapshot",
                provenance=DatasetProvenance(
                    source="test", curator="alice", version="1.0.0",
                    domain="test", updated_at="2026-03-16T12:00:00Z",
                ),
                items=[],
            ))


# ===========================================================================
# VerificationRunRecord
# ===========================================================================


class TestVerificationRunRecord:
    def test_construction(self) -> None:
        from autocontext.execution.verification_dataset import VerificationRunRecord

        record = VerificationRunRecord(
            run_id="run-42",
            dataset_id="ds-l19-v1",
            dataset_version="1.0.0",
            rubric_score=0.85,
            objective_recall=0.67,
            objective_precision=0.80,
            created_at="2026-03-16T14:00:00Z",
        )
        assert record.dataset_id == "ds-l19-v1"

    def test_roundtrip(self) -> None:
        from autocontext.execution.verification_dataset import VerificationRunRecord

        record = VerificationRunRecord(
            run_id="run-1", dataset_id="ds-1", dataset_version="1.0.0",
            rubric_score=0.9, objective_recall=0.8, objective_precision=0.85,
            created_at="2026-03-16T14:00:00Z",
        )
        d = record.to_dict()
        restored = VerificationRunRecord.from_dict(d)
        assert restored.run_id == "run-1"
        assert restored.objective_recall == 0.8


# ===========================================================================
# OracleRevisionFeedback + oracle_to_revision_feedback
# ===========================================================================


class TestOracleRevisionFeedback:
    def test_construction(self) -> None:
        from autocontext.execution.verification_dataset import OracleRevisionFeedback

        fb = OracleRevisionFeedback(
            missed_items=["item-2: Metformin + Lisinopril"],
            false_positives=["Ibuprofen + Acetaminophen (not in oracle)"],
            weight_mismatches=["item-1: expected high, got moderate"],
            revision_prompt_context="Focus on identifying Metformin + Lisinopril interaction.",
        )
        assert len(fb.missed_items) == 1
        assert len(fb.false_positives) == 1

    def test_is_empty_when_no_issues(self) -> None:
        from autocontext.execution.verification_dataset import OracleRevisionFeedback

        fb = OracleRevisionFeedback(
            missed_items=[], false_positives=[],
            weight_mismatches=[], revision_prompt_context="",
        )
        assert fb.is_empty()

    def test_roundtrip(self) -> None:
        from autocontext.execution.verification_dataset import OracleRevisionFeedback

        fb = OracleRevisionFeedback(
            missed_items=["item-1"],
            false_positives=["item-x"],
            weight_mismatches=[],
            revision_prompt_context="Add the missed item.",
            metadata={"source": "oracle"},
        )
        restored = OracleRevisionFeedback.from_dict(fb.to_dict())
        assert restored.missed_items == ["item-1"]
        assert restored.metadata["source"] == "oracle"


class TestLiveResolutionHelpers:
    def test_resolve_dataset_reference_builds_live_config(self, tmp_path: Path) -> None:
        from autocontext.execution.verification_dataset import (
            DatasetRegistry,
            VerificationDataset,
            resolve_objective_verification_config,
        )

        registry = DatasetRegistry(tmp_path)
        registry.register(VerificationDataset(
            dataset_id="l19-core",
            name="L19 Core",
            provenance=_make_provenance(),
            items=_make_items(),
            claim_patterns=[r"^\d+\."],
        ))

        config, dataset = resolve_objective_verification_config(
            {"dataset_id": "l19-core", "dataset_version": "1.0.0"},
            registry,
        )

        assert config is not None
        assert dataset is not None
        assert len(config.ground_truth) == 2
        assert config.metadata["dataset_id"] == "l19-core"
        assert config.metadata["dataset_version"] == "1.0.0"
        assert config.claim_patterns == [r"^\d+\."]

    def test_not_empty_when_has_misses(self) -> None:
        from autocontext.execution.verification_dataset import OracleRevisionFeedback

        fb = OracleRevisionFeedback(
            missed_items=["item-1"],
            false_positives=[], weight_mismatches=[],
            revision_prompt_context="",
        )
        assert not fb.is_empty()


class TestOracleToRevisionFeedback:
    def test_converts_misses_to_feedback(self) -> None:
        from autocontext.execution.objective_verification import ItemMatchDetail, OracleResult
        from autocontext.execution.verification_dataset import oracle_to_revision_feedback

        result = OracleResult(
            total_known=3, found_count=1, claimed_count=2,
            false_positive_count=1, recall=0.33, precision=0.5,
            weight_agreement=None,
            item_details=[
                ItemMatchDetail(item_id="item-1", found=True, weight="high",
                                weight_matched=True, matched_in="line1"),
                ItemMatchDetail(item_id="item-2", found=False, weight="moderate",
                                weight_matched=False, matched_in=""),
                ItemMatchDetail(item_id="item-3", found=False, weight="high",
                                weight_matched=False, matched_in=""),
            ],
        )
        feedback = oracle_to_revision_feedback(result)
        assert len(feedback.missed_items) == 2
        assert feedback.revision_prompt_context != ""

    def test_perfect_result_empty_feedback(self) -> None:
        from autocontext.execution.objective_verification import ItemMatchDetail, OracleResult
        from autocontext.execution.verification_dataset import oracle_to_revision_feedback

        result = OracleResult(
            total_known=2, found_count=2, claimed_count=2,
            false_positive_count=0, recall=1.0, precision=1.0,
            weight_agreement=1.0,
            item_details=[
                ItemMatchDetail(item_id="item-1", found=True, weight="high",
                                weight_matched=True, matched_in="line1"),
                ItemMatchDetail(item_id="item-2", found=True, weight="moderate",
                                weight_matched=True, matched_in="line2"),
            ],
        )
        feedback = oracle_to_revision_feedback(result)
        assert feedback.is_empty()

    def test_weight_mismatch_feedback(self) -> None:
        from autocontext.execution.objective_verification import ItemMatchDetail, OracleResult
        from autocontext.execution.verification_dataset import oracle_to_revision_feedback

        result = OracleResult(
            total_known=1, found_count=1, claimed_count=1,
            false_positive_count=0, recall=1.0, precision=1.0,
            weight_agreement=0.0,
            item_details=[
                ItemMatchDetail(item_id="item-1", found=True, weight="high",
                                weight_matched=False, matched_in="line1"),
            ],
        )
        feedback = oracle_to_revision_feedback(result)
        assert len(feedback.weight_mismatches) == 1
