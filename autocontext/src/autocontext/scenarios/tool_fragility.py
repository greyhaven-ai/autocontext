"""Tool-fragility scenario family with environment-drift evaluation (AC-254).

Scenarios where tools, APIs, or environment contracts drift while the core
task stays the same. Agents must detect broken tools, changed interfaces,
and degraded environments. Evaluation separates routing, instruction,
runtime/tool, and stale-context failures.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Any

from pydantic import BaseModel

from autocontext.scenarios.simulation import SimulationInterface

FAILURE_CLASSES = frozenset({
    "routing_failure",
    "stale_instruction_failure",
    "tool_failure",
    "stale_context_failure",
})


class ToolContract(BaseModel):
    """Describes a tool/API contract at a specific version."""

    tool_name: str
    version: int
    input_schema: dict[str, str]
    output_schema: dict[str, str]
    description: str

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ToolContract:
        return cls.model_validate(data)


class ToolDrift(BaseModel):
    """Records a change in a tool's contract."""

    tool_name: str
    from_version: int
    to_version: int
    description: str
    drift_type: str  # "schema_change", "additive_change", "removal", "behavior_change"
    breaking: bool

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ToolDrift:
        return cls.model_validate(data)


class FailureAttribution(BaseModel):
    """Attributes a failure to a specific class."""

    step: int
    failure_class: str  # one of FAILURE_CLASSES
    description: str
    tool_name: str
    recoverable: bool

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FailureAttribution:
        return cls.model_validate(data)


class ToolFragilityResult(BaseModel):
    """Result of evaluating a tool-fragility scenario."""

    score: float
    reasoning: str
    dimension_scores: dict[str, float]
    drifts_injected: int
    drifts_detected: int
    drifts_adapted: int
    wasted_attempts: int
    failure_attributions: list[FailureAttribution]

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ToolFragilityResult:
        return cls.model_validate(data)


class ToolFragilityInterface(SimulationInterface):
    """Contract for tool-fragility / environment-drift scenarios.

    Extends SimulationInterface with tool contract management, drift injection,
    failure attribution, and fragility evaluation. Agents are judged on
    adaptation quality and wasted attempts when tools change.
    """

    @abstractmethod
    def get_tool_contracts(self, state: dict[str, Any]) -> list[ToolContract]:
        """Return current tool contracts in the environment."""

    @abstractmethod
    def get_drift_log(self, state: dict[str, Any]) -> list[ToolDrift]:
        """Return the log of tool drifts applied so far."""

    @abstractmethod
    def inject_drift(
        self, state: dict[str, Any], drift: ToolDrift
    ) -> dict[str, Any]:
        """Inject a tool drift and return the updated state."""

    @abstractmethod
    def attribute_failure(
        self, state: dict[str, Any], step: int, error: str
    ) -> FailureAttribution:
        """Attribute a failure to a specific class."""

    @abstractmethod
    def evaluate_fragility(self, state: dict[str, Any]) -> ToolFragilityResult:
        """Evaluate how well the agent adapted to tool/environment changes."""
