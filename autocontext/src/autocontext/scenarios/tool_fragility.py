"""Tool-fragility scenario family with environment-drift evaluation (AC-254).

Scenarios where tools, APIs, or environment contracts drift while the core
task stays the same. Agents must detect broken tools, changed interfaces,
and degraded environments. Evaluation separates routing, instruction,
runtime/tool, and stale-context failures.
"""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from typing import Any

from autocontext.scenarios.simulation import SimulationInterface

FAILURE_CLASSES = frozenset({
    "routing_failure",
    "stale_instruction_failure",
    "tool_failure",
    "stale_context_failure",
})


@dataclass(slots=True)
class ToolContract:
    """Describes a tool/API contract at a specific version."""

    tool_name: str
    version: int
    input_schema: dict[str, str]
    output_schema: dict[str, str]
    description: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "version": self.version,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ToolContract:
        return cls(
            tool_name=data["tool_name"],
            version=data["version"],
            input_schema=data.get("input_schema", {}),
            output_schema=data.get("output_schema", {}),
            description=data["description"],
        )


@dataclass(slots=True)
class ToolDrift:
    """Records a change in a tool's contract."""

    tool_name: str
    from_version: int
    to_version: int
    description: str
    drift_type: str  # "schema_change", "additive_change", "removal", "behavior_change"
    breaking: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "from_version": self.from_version,
            "to_version": self.to_version,
            "description": self.description,
            "drift_type": self.drift_type,
            "breaking": self.breaking,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ToolDrift:
        return cls(
            tool_name=data["tool_name"],
            from_version=data["from_version"],
            to_version=data["to_version"],
            description=data["description"],
            drift_type=data["drift_type"],
            breaking=data["breaking"],
        )


@dataclass(slots=True)
class FailureAttribution:
    """Attributes a failure to a specific class."""

    step: int
    failure_class: str  # one of FAILURE_CLASSES
    description: str
    tool_name: str
    recoverable: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "failure_class": self.failure_class,
            "description": self.description,
            "tool_name": self.tool_name,
            "recoverable": self.recoverable,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FailureAttribution:
        return cls(
            step=data["step"],
            failure_class=data["failure_class"],
            description=data["description"],
            tool_name=data["tool_name"],
            recoverable=data["recoverable"],
        )


@dataclass(slots=True)
class ToolFragilityResult:
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
        return {
            "score": self.score,
            "reasoning": self.reasoning,
            "dimension_scores": self.dimension_scores,
            "drifts_injected": self.drifts_injected,
            "drifts_detected": self.drifts_detected,
            "drifts_adapted": self.drifts_adapted,
            "wasted_attempts": self.wasted_attempts,
            "failure_attributions": [fa.to_dict() for fa in self.failure_attributions],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ToolFragilityResult:
        return cls(
            score=data["score"],
            reasoning=data["reasoning"],
            dimension_scores=data["dimension_scores"],
            drifts_injected=data["drifts_injected"],
            drifts_detected=data["drifts_detected"],
            drifts_adapted=data["drifts_adapted"],
            wasted_attempts=data["wasted_attempts"],
            failure_attributions=[
                FailureAttribution.from_dict(fa) for fa in data["failure_attributions"]
            ],
        )


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
