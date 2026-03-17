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

    def register(self, dataset: VerificationDataset) -> Path:
        path = self._dir / f"{dataset.dataset_id}.json"
        path.write_text(json.dumps(dataset.to_dict(), indent=2), encoding="utf-8")
        return path

    def load(self, dataset_id: str) -> VerificationDataset | None:
        path = self._dir / f"{dataset_id}.json"
        if not path.exists():
            return None
        return VerificationDataset.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def list_datasets(self) -> list[VerificationDataset]:
        return [
            VerificationDataset.from_dict(json.loads(p.read_text(encoding="utf-8")))
            for p in sorted(self._dir.glob("*.json"))
        ]


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
