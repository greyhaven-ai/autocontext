"""Verification dataset registry, provenance, and oracle feedback (AC-292).

Manages versioned ground-truth datasets for objective verification,
tracks provenance per run, and converts oracle misses into structured
revision feedback for the learning loop.

Key types:
- DatasetProvenance: source, curator, version, domain metadata
- VerificationDataset: versioned collection of GroundTruthItems
- DatasetRegistry: JSON-file registry for datasets
- VerificationRunRecord: provenance record linking run to dataset
- OracleRevisionFeedback: structured feedback from oracle misses
- oracle_to_revision_feedback(): converts OracleResult into feedback
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from autocontext.execution.objective_verification import (
    GroundTruthItem,
    KeywordMatchOracle,
    ObjectiveVerificationConfig,
    OracleResult,
)


@dataclass(slots=True)
class DatasetProvenance:
    """Provenance metadata for a verification dataset."""

    source: str
    curator: str
    version: str
    domain: str
    updated_at: str
    notes: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "curator": self.curator,
            "version": self.version,
            "domain": self.domain,
            "updated_at": self.updated_at,
            "notes": self.notes,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DatasetProvenance:
        return cls(
            source=data.get("source", ""),
            curator=data.get("curator", ""),
            version=data.get("version", ""),
            domain=data.get("domain", ""),
            updated_at=data.get("updated_at", ""),
            notes=data.get("notes", ""),
            metadata=data.get("metadata", {}),
        )


@dataclass(slots=True)
class VerificationDataset:
    """Versioned collection of ground-truth items with provenance."""

    dataset_id: str
    name: str
    provenance: DatasetProvenance
    items: list[GroundTruthItem]
    claim_patterns: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def build_oracle(self) -> KeywordMatchOracle:
        """Build a KeywordMatchOracle from this dataset."""
        compiled = [re.compile(p, re.MULTILINE) for p in self.claim_patterns]
        return KeywordMatchOracle(self.items, claim_patterns=compiled)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "name": self.name,
            "provenance": self.provenance.to_dict(),
            "items": [item.to_dict() for item in self.items],
            "claim_patterns": self.claim_patterns,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VerificationDataset:
        return cls(
            dataset_id=data["dataset_id"],
            name=data.get("name", ""),
            provenance=DatasetProvenance.from_dict(data.get("provenance", {})),
            items=[GroundTruthItem.from_dict(i) for i in data.get("items", [])],
            claim_patterns=data.get("claim_patterns", []),
            metadata=data.get("metadata", {}),
        )


class DatasetRegistry:
    """JSON-file registry for verification datasets."""

    def __init__(self, root: Path) -> None:
        self._dir = root / "verification_datasets"
        self._dir.mkdir(parents=True, exist_ok=True)

    def _dataset_dir(self, dataset_id: str) -> Path:
        return self._dir / dataset_id

    def _version_path(self, dataset_id: str, version: str) -> Path:
        safe_version = version.replace("/", "__")
        return self._dataset_dir(dataset_id) / f"{safe_version}.json"

    def register(self, dataset: VerificationDataset) -> Path:
        path = self._version_path(dataset.dataset_id, dataset.provenance.version)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(dataset.to_dict(), indent=2)
        if path.exists():
            existing = path.read_text(encoding="utf-8")
            if existing != payload:
                msg = (
                    "Refusing to overwrite existing verification dataset snapshot "
                    f"{dataset.dataset_id}@{dataset.provenance.version}"
                )
                raise ValueError(msg)
            return path
        path.write_text(payload, encoding="utf-8")
        return path

    def load(self, dataset_id: str, version: str | None = None) -> VerificationDataset | None:
        if version:
            path = self._version_path(dataset_id, version)
            if not path.exists():
                return None
            return VerificationDataset.from_dict(json.loads(path.read_text(encoding="utf-8")))

        dataset_dir = self._dataset_dir(dataset_id)
        if not dataset_dir.exists():
            return None
        snapshots = [
            VerificationDataset.from_dict(json.loads(path.read_text(encoding="utf-8")))
            for path in sorted(dataset_dir.glob("*.json"))
        ]
        if not snapshots:
            return None
        snapshots.sort(
            key=lambda dataset: (
                dataset.provenance.updated_at,
                dataset.provenance.version,
            ),
        )
        return snapshots[-1]

    def list_versions(self, dataset_id: str) -> list[str]:
        dataset_dir = self._dataset_dir(dataset_id)
        if not dataset_dir.exists():
            return []
        versions: list[str] = []
        for path in sorted(dataset_dir.glob("*.json")):
            dataset = VerificationDataset.from_dict(json.loads(path.read_text(encoding="utf-8")))
            versions.append(dataset.provenance.version)
        return versions

    def list_datasets(self) -> list[VerificationDataset]:
        datasets: list[VerificationDataset] = []
        for dataset_dir in sorted(path for path in self._dir.iterdir() if path.is_dir()):
            dataset = self.load(dataset_dir.name)
            if dataset is not None:
                datasets.append(dataset)
        return datasets


@dataclass(slots=True)
class VerificationRunRecord:
    """Records which dataset/version was used for objective verification on a run."""

    run_id: str
    dataset_id: str
    dataset_version: str
    rubric_score: float
    objective_recall: float
    objective_precision: float
    created_at: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "dataset_id": self.dataset_id,
            "dataset_version": self.dataset_version,
            "rubric_score": self.rubric_score,
            "objective_recall": self.objective_recall,
            "objective_precision": self.objective_precision,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VerificationRunRecord:
        return cls(
            run_id=data["run_id"],
            dataset_id=data.get("dataset_id", ""),
            dataset_version=data.get("dataset_version", ""),
            rubric_score=data.get("rubric_score", 0.0),
            objective_recall=data.get("objective_recall", 0.0),
            objective_precision=data.get("objective_precision", 0.0),
            created_at=data.get("created_at", ""),
            metadata=data.get("metadata", {}),
        )


@dataclass(slots=True)
class OracleRevisionFeedback:
    """Structured feedback from oracle verification for revision loops."""

    missed_items: list[str]
    false_positives: list[str]
    weight_mismatches: list[str]
    revision_prompt_context: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "missed_items": self.missed_items,
            "false_positives": self.false_positives,
            "weight_mismatches": self.weight_mismatches,
            "revision_prompt_context": self.revision_prompt_context,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OracleRevisionFeedback:
        return cls(
            missed_items=list(data.get("missed_items", [])),
            false_positives=list(data.get("false_positives", [])),
            weight_mismatches=list(data.get("weight_mismatches", [])),
            revision_prompt_context=data.get("revision_prompt_context", ""),
            metadata=dict(data.get("metadata", {})),
        )

    def is_empty(self) -> bool:
        return (
            not self.missed_items
            and not self.false_positives
            and not self.weight_mismatches
        )


def oracle_to_revision_feedback(result: OracleResult) -> OracleRevisionFeedback:
    """Convert an OracleResult into structured revision feedback.

    Identifies missed items, false positives, and weight mismatches,
    then composes a revision prompt context for the learning loop.
    """
    missed: list[str] = []
    false_positives: list[str] = []
    weight_mismatches: list[str] = []

    for detail in result.item_details:
        if not detail.found:
            missed.append(f"{detail.item_id} (weight: {detail.weight})")
        elif not detail.weight_matched and detail.found:
            weight_mismatches.append(
                f"{detail.item_id}: expected weight '{detail.weight}' not confirmed"
            )

    if result.false_positive_count > 0:
        false_positives.append(
            f"{result.false_positive_count} claimed item(s) not in ground truth"
        )

    # Build revision context
    parts: list[str] = []
    if missed:
        parts.append("Missed items that should have been identified:")
        for m in missed:
            parts.append(f"  - {m}")
    if weight_mismatches:
        parts.append("Weight/severity mismatches:")
        for w in weight_mismatches:
            parts.append(f"  - {w}")
    if false_positives:
        parts.append("False positive claims:")
        for fp in false_positives:
            parts.append(f"  - {fp}")

    return OracleRevisionFeedback(
        missed_items=missed,
        false_positives=false_positives,
        weight_mismatches=weight_mismatches,
        revision_prompt_context="\n".join(parts),
    )


def resolve_objective_verification_config(
    config_data: dict[str, Any] | None,
    registry: DatasetRegistry | None = None,
) -> tuple[ObjectiveVerificationConfig | None, VerificationDataset | None]:
    """Resolve inline or dataset-backed objective verification config for live paths."""
    if not config_data:
        return None, None

    if config_data.get("ground_truth"):
        return ObjectiveVerificationConfig.from_dict(config_data), None

    dataset_id = str(config_data.get("dataset_id") or "").strip()
    if not dataset_id:
        return ObjectiveVerificationConfig.from_dict(config_data), None
    if registry is None:
        msg = (
            "Objective verification config references a dataset, but no dataset "
            "registry was provided"
        )
        raise ValueError(msg)

    requested_version = str(config_data.get("dataset_version") or "").strip() or None
    dataset = registry.load(dataset_id, version=requested_version)
    if dataset is None:
        version_suffix = f" version '{requested_version}'" if requested_version else ""
        raise ValueError(f"Verification dataset '{dataset_id}'{version_suffix} not found")

    metadata = dict(config_data.get("metadata") or {})
    metadata.update({
        "dataset_id": dataset.dataset_id,
        "dataset_name": dataset.name,
        "dataset_version": dataset.provenance.version,
        "dataset_provenance": dataset.provenance.to_dict(),
    })

    claim_patterns = list(config_data.get("claim_patterns") or dataset.claim_patterns)
    config = ObjectiveVerificationConfig(
        ground_truth=list(dataset.items),
        claim_patterns=claim_patterns,
        metadata=metadata,
    )
    return config, dataset


def enrich_objective_payload(
    payload: dict[str, Any],
    *,
    run_id: str | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Attach revision feedback and dataset provenance records to an oracle payload."""
    enriched = dict(payload)
    oracle_result = OracleResult.from_dict(payload.get("oracle_result", {}))
    feedback = oracle_to_revision_feedback(oracle_result)
    if not feedback.is_empty():
        enriched["revision_feedback"] = feedback.to_dict()

    metadata = dict(payload.get("config_metadata") or {})
    dataset_id = str(metadata.get("dataset_id") or "").strip()
    dataset_version = str(metadata.get("dataset_version") or "").strip()
    if run_id and dataset_id and dataset_version:
        comparison = dict(payload.get("comparison") or {})
        record = VerificationRunRecord(
            run_id=run_id,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            rubric_score=float(comparison.get("rubric_score", 0.0)),
            objective_recall=float(comparison.get("objective_recall", 0.0)),
            objective_precision=float(comparison.get("objective_precision", 0.0)),
            created_at=created_at or "",
            metadata={
                "dataset_name": metadata.get("dataset_name", ""),
                "dataset_provenance": metadata.get("dataset_provenance", {}),
            },
        )
        enriched["verification_run_record"] = record.to_dict()

    return enriched
