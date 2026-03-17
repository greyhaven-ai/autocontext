"""Schema-evolution scenario family with stale-context evaluation (AC-252).

Scenarios where schemas, upstream state, or constraints change mid-run
or between generations. Agents must detect invalidated assumptions,
discard stale context, and adapt. Evaluated on stale-assumption detection
rate, recovery quality, and mutation adaptation.
"""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from typing import Any

from autocontext.scenarios.simulation import SimulationInterface


@dataclass(slots=True)
class SchemaMutation:
    """A single schema or state mutation applied to the environment."""

    version: int
    description: str
    fields_added: list[str]
    fields_removed: list[str]
    fields_modified: dict[str, str]  # field_name -> "old_type -> new_type"
    breaking: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "description": self.description,
            "fields_added": self.fields_added,
            "fields_removed": self.fields_removed,
            "fields_modified": self.fields_modified,
            "breaking": self.breaking,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SchemaMutation:
        return cls(
            version=data["version"],
            description=data["description"],
            fields_added=data.get("fields_added", []),
            fields_removed=data.get("fields_removed", []),
            fields_modified=data.get("fields_modified", {}),
            breaking=data["breaking"],
        )


@dataclass(slots=True)
class ContextValidity:
    """Whether a prior assumption is still valid after mutations."""

    assumption: str
    still_valid: bool
    invalidated_by_version: int | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "assumption": self.assumption,
            "still_valid": self.still_valid,
            "invalidated_by_version": self.invalidated_by_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContextValidity:
        return cls(
            assumption=data["assumption"],
            still_valid=data["still_valid"],
            invalidated_by_version=data.get("invalidated_by_version"),
        )


@dataclass(slots=True)
class SchemaEvolutionResult:
    """Result of evaluating a schema-evolution scenario."""

    score: float
    reasoning: str
    dimension_scores: dict[str, float]
    mutations_applied: int
    stale_assumptions_detected: int
    stale_assumptions_missed: int
    recovery_actions_taken: int
    recovery_actions_successful: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "reasoning": self.reasoning,
            "dimension_scores": self.dimension_scores,
            "mutations_applied": self.mutations_applied,
            "stale_assumptions_detected": self.stale_assumptions_detected,
            "stale_assumptions_missed": self.stale_assumptions_missed,
            "recovery_actions_taken": self.recovery_actions_taken,
            "recovery_actions_successful": self.recovery_actions_successful,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SchemaEvolutionResult:
        return cls(
            score=data["score"],
            reasoning=data["reasoning"],
            dimension_scores=data["dimension_scores"],
            mutations_applied=data["mutations_applied"],
            stale_assumptions_detected=data["stale_assumptions_detected"],
            stale_assumptions_missed=data["stale_assumptions_missed"],
            recovery_actions_taken=data["recovery_actions_taken"],
            recovery_actions_successful=data["recovery_actions_successful"],
        )


class SchemaEvolutionInterface(SimulationInterface):
    """Contract for schema-evolution / stale-context scenarios.

    Extends SimulationInterface with schema versioning, mutation tracking,
    context validity checking, and adaptation evaluation. Agents are judged
    on detecting and discarding stale assumptions after schema changes.
    """

    @abstractmethod
    def get_mutations(self) -> list[SchemaMutation]:
        """Return all known schema mutations for this scenario."""

    @abstractmethod
    def get_schema_version(self, state: dict[str, Any]) -> int:
        """Return the current schema version from state."""

    @abstractmethod
    def get_mutation_log(self, state: dict[str, Any]) -> list[SchemaMutation]:
        """Return the log of mutations applied so far."""

    @abstractmethod
    def apply_mutation(
        self, state: dict[str, Any], mutation: SchemaMutation
    ) -> dict[str, Any]:
        """Apply a schema mutation and return the updated state."""

    @abstractmethod
    def check_context_validity(
        self, state: dict[str, Any], assumptions: list[str]
    ) -> list[ContextValidity]:
        """Check which prior assumptions are still valid after mutations."""

    @abstractmethod
    def evaluate_adaptation(self, state: dict[str, Any]) -> SchemaEvolutionResult:
        """Evaluate how well the agent adapted to schema changes."""
