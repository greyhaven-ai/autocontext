"""Schema-evolution scenario family with stale-context evaluation (AC-252).

Scenarios where schemas, upstream state, or constraints change mid-run
or between generations. Agents must detect invalidated assumptions,
discard stale context, and adapt. Evaluated on stale-assumption detection
rate, recovery quality, and mutation adaptation.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Any

from pydantic import BaseModel

from autocontext.scenarios.simulation import SimulationInterface


class SchemaMutation(BaseModel):
    """A single schema or state mutation applied to the environment."""

    version: int
    description: str
    fields_added: list[str]
    fields_removed: list[str]
    fields_modified: dict[str, str]  # field_name -> "old_type -> new_type"
    breaking: bool

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SchemaMutation:
        return cls.model_validate(data)


class ContextValidity(BaseModel):
    """Whether a prior assumption is still valid after mutations."""

    assumption: str
    still_valid: bool
    invalidated_by_version: int | None

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContextValidity:
        return cls.model_validate(data)


class SchemaEvolutionResult(BaseModel):
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
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SchemaEvolutionResult:
        return cls.model_validate(data)


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
