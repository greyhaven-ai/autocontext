"""Generic objective verification harness for verifiable domains (AC-282).

Provides a domain-agnostic oracle framework for measuring objective
correctness alongside LLM rubric scores. Any domain with ground-truth
items (drug interactions, proof steps, factual claims, code correctness
checks, etc.) can plug in by creating GroundTruthItem instances and
configuring a KeywordMatchOracle or implementing the ObjectiveOracle ABC.

Key types:
- GroundTruthItem: a single verifiable item with match keywords and weight
- ObjectiveOracle: abstract interface for domain-specific evaluation
- KeywordMatchOracle: configurable oracle using keyword matching
- OracleResult: precision, recall, weight agreement, false positives
- OracleComparison: compares rubric score vs objective metrics
- compare_oracle_vs_rubric(): structured comparison function
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class GroundTruthItem:
    """A single verifiable item that should appear in correct output.

    Domain-agnostic: works for drug interactions, proof steps, factual
    claims, code patterns, or any verifiable assertion.

    Attributes:
        item_id: unique identifier for this item
        description: human-readable description of what should be present
        match_keywords: list of keyword groups — at least one keyword from
            each group must appear nearby in the output for a match.
            For a drug interaction: [["warfarin", "coumadin"], ["aspirin"]]
            means (warfarin OR coumadin) AND aspirin must co-occur.
        weight: importance weight (e.g., "high", "moderate", "low")
        category: optional domain category (e.g., "interaction", "proof_step")
        metadata: arbitrary domain-specific data
    """

    item_id: str
    description: str
    match_keywords: list[list[str]]
    weight: str = "moderate"
    category: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "description": self.description,
            "match_keywords": self.match_keywords,
            "weight": self.weight,
            "category": self.category,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GroundTruthItem:
        return cls(
            item_id=data["item_id"],
            description=data.get("description", ""),
            match_keywords=data.get("match_keywords", []),
            weight=data.get("weight", "moderate"),
            category=data.get("category", ""),
            metadata=data.get("metadata", {}),
        )


@dataclass(slots=True)
class ItemMatchDetail:
    """Detail about whether a single ground-truth item was found."""

    item_id: str
    found: bool
    weight: str
    weight_matched: bool
    matched_in: str  # the text fragment where match occurred, or ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "found": self.found,
            "weight": self.weight,
            "weight_matched": self.weight_matched,
            "matched_in": self.matched_in,
        }


@dataclass(slots=True)
class OracleResult:
    """Objective verification metrics."""

    total_known: int
    found_count: int
    claimed_count: int
    false_positive_count: int
    recall: float
    precision: float
    weight_agreement: float | None
    item_details: list[ItemMatchDetail]
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_known": self.total_known,
            "found_count": self.found_count,
            "claimed_count": self.claimed_count,
            "false_positive_count": self.false_positive_count,
            "recall": self.recall,
            "precision": self.precision,
            "weight_agreement": self.weight_agreement,
            "item_details": [d.to_dict() for d in self.item_details],
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OracleResult:
        return cls(
            total_known=data.get("total_known", 0),
            found_count=data.get("found_count", 0),
            claimed_count=data.get("claimed_count", 0),
            false_positive_count=data.get("false_positive_count", 0),
            recall=data.get("recall", 0.0),
            precision=data.get("precision", 0.0),
            weight_agreement=data.get("weight_agreement"),
            item_details=[
                ItemMatchDetail(**d) for d in data.get("item_details", [])
            ],
            metadata=data.get("metadata", {}),
        )


@dataclass(slots=True)
class OracleComparison:
    """Comparison of rubric score vs objective metrics."""

    rubric_score: float
    objective_recall: float
    objective_precision: float
    weight_agreement: float | None
    false_positive_rate: float
    rubric_objective_gap: float
    metadata: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> str:
        lines = [
            f"Rubric score: {self.rubric_score:.2f}",
            f"Objective recall: {self.objective_recall:.2f}",
            f"Objective precision: {self.objective_precision:.2f}",
            f"False positive rate: {self.false_positive_rate:.2f}",
            f"Rubric-objective gap: {self.rubric_objective_gap:.2f}",
        ]
        if self.weight_agreement is not None:
            lines.append(f"Weight agreement: {self.weight_agreement:.2f}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rubric_score": self.rubric_score,
            "objective_recall": self.objective_recall,
            "objective_precision": self.objective_precision,
            "weight_agreement": self.weight_agreement,
            "false_positive_rate": self.false_positive_rate,
            "rubric_objective_gap": self.rubric_objective_gap,
            "metadata": self.metadata,
        }


@dataclass(slots=True)
class ObjectiveVerificationConfig:
    """Serializable config for running an objective oracle in live task paths."""

    ground_truth: list[GroundTruthItem]
    claim_patterns: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ground_truth": [item.to_dict() for item in self.ground_truth],
            "claim_patterns": self.claim_patterns,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ObjectiveVerificationConfig:
        return cls(
            ground_truth=[
                GroundTruthItem.from_dict(item)
                for item in data.get("ground_truth", [])
            ],
            claim_patterns=data.get("claim_patterns", []),
            metadata=data.get("metadata", {}),
        )

    def build_oracle(self) -> KeywordMatchOracle:
        compiled = [re.compile(pattern, re.MULTILINE) for pattern in self.claim_patterns]
        return KeywordMatchOracle(self.ground_truth, claim_patterns=compiled)


class ObjectiveOracle(ABC):
    """Abstract interface for domain-specific objective evaluation."""

    @abstractmethod
    def evaluate(self, output: str) -> OracleResult:
        """Evaluate agent output against ground truth."""


# Weight-word aliases that the keyword oracle checks nearby found items.
_WEIGHT_ALIASES: dict[str, list[str]] = {
    "low": ["low", "minor", "mild", "minimal"],
    "moderate": ["moderate", "medium", "notable"],
    "high": ["high", "major", "severe", "significant"],
    "critical": ["critical", "life-threatening", "contraindicated", "dangerous"],
}


class KeywordMatchOracle(ObjectiveOracle):
    """Configurable oracle that matches ground-truth items via keyword co-occurrence.

    For each GroundTruthItem, checks that at least one keyword from each
    keyword group appears within a configurable window of text lines.
    Works for drug interactions, factual claims, proof steps, code patterns, etc.
    """

    def __init__(
        self,
        ground_truth: list[GroundTruthItem],
        *,
        claim_patterns: list[re.Pattern[str]] | None = None,
        window_lines: int = 3,
    ) -> None:
        self._items = ground_truth
        self._claim_patterns = claim_patterns or []
        self._window = window_lines

    def evaluate(self, output: str) -> OracleResult:
        output_lower = output.lower()
        lines = output_lower.splitlines()

        details: list[ItemMatchDetail] = []
        found_count = 0
        weight_matches = 0
        weight_checks = 0

        for item in self._items:
            match_line = self._find_item_in_output(item, lines)
            found = match_line is not None

            weight_matched = False
            if found and match_line is not None:
                found_count += 1
                weight_matched = self._check_weight_near(item.weight, lines, match_line)
                weight_checks += 1
                if weight_matched:
                    weight_matches += 1

            details.append(ItemMatchDetail(
                item_id=item.item_id,
                found=found,
                weight=item.weight,
                weight_matched=weight_matched,
                matched_in=lines[match_line].strip() if found and match_line is not None else "",
            ))

        claim_count_source = "patterns" if self._claim_patterns else "heuristic"
        claimed_count = max(found_count, self._count_claims(output))
        false_positives = max(0, claimed_count - found_count)

        total = len(self._items)
        recall = found_count / total if total > 0 else 0.0
        precision = found_count / claimed_count if claimed_count > 0 else 0.0
        weight_agreement = weight_matches / weight_checks if weight_checks > 0 else None

        return OracleResult(
            total_known=total,
            found_count=found_count,
            claimed_count=claimed_count,
            false_positive_count=false_positives,
            recall=round(recall, 4),
            precision=round(precision, 4),
            weight_agreement=round(weight_agreement, 4) if weight_agreement is not None else None,
            item_details=details,
            metadata={"claim_count_source": claim_count_source},
        )

    def _find_item_in_output(
        self, item: GroundTruthItem, lines: list[str],
    ) -> int | None:
        """Find a ground-truth item via keyword co-occurrence within a line window."""
        for i, _line in enumerate(lines):
            window_text = " ".join(
                lines[max(0, i - self._window): i + self._window + 1]
            )
            if self._all_groups_match(item.match_keywords, window_text):
                return i
        return None

    @staticmethod
    def _all_groups_match(keyword_groups: list[list[str]], text: str) -> bool:
        """Check that at least one keyword from each group appears in text."""
        for group in keyword_groups:
            if not any(kw.lower() in text for kw in group):
                return False
        return True

    def _check_weight_near(self, weight: str, lines: list[str], line_idx: int) -> bool:
        """Check if weight-related words appear near the matched line."""
        aliases = _WEIGHT_ALIASES.get(weight.lower(), [])
        if not aliases:
            return False
        window_text = " ".join(
            lines[max(0, line_idx - self._window): line_idx + self._window + 1]
        )
        return any(alias in window_text for alias in aliases)

    def _count_claims(self, output: str) -> int:
        """Count how many claims the output makes."""
        if not self._claim_patterns:
            return self._estimate_claims(output)
        count = 0
        for pattern in self._claim_patterns:
            count += len(pattern.findall(output))
        return max(count, 0)

    @staticmethod
    def _estimate_claims(output: str) -> int:
        """Heuristic fallback so precision is not silently collapsed to recall."""
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        bullet_lines = [
            line for line in lines
            if re.match(r"^(\d+[\).\s]|[-*•]\s)", line)
        ]
        if bullet_lines:
            return len(bullet_lines)
        if len(lines) > 1:
            return len(lines)
        sentences = [
            segment.strip()
            for segment in re.split(r"(?<=[.!?])\s+", output)
            if segment.strip()
        ]
        return len(sentences)


def compare_oracle_vs_rubric(
    rubric_score: float,
    oracle_result: OracleResult,
) -> OracleComparison:
    """Compare rubric-based score against objective oracle metrics."""
    false_positive_rate = (
        oracle_result.false_positive_count / oracle_result.claimed_count
        if oracle_result.claimed_count > 0
        else 0.0
    )
    # Objective verification exceeding the rubric score is not a risky gap.
    gap = max(0.0, rubric_score - oracle_result.recall)

    return OracleComparison(
        rubric_score=rubric_score,
        objective_recall=oracle_result.recall,
        objective_precision=oracle_result.precision,
        weight_agreement=oracle_result.weight_agreement,
        false_positive_rate=round(false_positive_rate, 4),
        rubric_objective_gap=round(gap, 4),
    )


def run_objective_verification(
    *,
    output: str,
    rubric_score: float,
    config: ObjectiveVerificationConfig,
) -> dict[str, Any]:
    """Run objective verification and package both raw metrics and comparison."""
    oracle_result = config.build_oracle().evaluate(output)
    comparison = compare_oracle_vs_rubric(rubric_score, oracle_result)
    return {
        "oracle_result": oracle_result.to_dict(),
        "comparison": comparison.to_dict(),
        "config_metadata": config.metadata,
    }
