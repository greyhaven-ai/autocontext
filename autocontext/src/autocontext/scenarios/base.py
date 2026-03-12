from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field


class Observation(BaseModel):
    """Scenario observation carrying tool state and Claude-readable narrative."""

    narrative: str
    state: dict[str, Any] = Field(default_factory=dict)
    constraints: list[str] = Field(default_factory=list)


class Result(BaseModel):
    """Outcome envelope consumed by control plane scoring and analysis."""

    score: float
    winner: str | None = None
    summary: str
    replay: list[dict[str, Any]] = Field(default_factory=list)
    metrics: dict[str, float] = Field(default_factory=dict)
    validation_errors: list[str] = Field(default_factory=list)

    @property
    def passed_validation(self) -> bool:
        return len(self.validation_errors) == 0


class ReplayEnvelope(BaseModel):
    """Replay payload emitted by the data plane."""

    scenario: str
    seed: int
    narrative: str
    timeline: list[dict[str, Any]] = Field(default_factory=list)


class GenerationMetrics(BaseModel):
    """Persisted generation summary for reporting and backpressure."""

    generation_index: int
    mean_score: float
    best_score: float
    elo: float
    wins: int
    losses: int
    runs: int
    gate_decision: str


@dataclass(slots=True)
class ExecutionLimits:
    timeout_seconds: float = 10.0
    max_memory_mb: int = 512
    network_access: bool = False


class ScenarioInterface(ABC):
    """Blueprint-compatible pluggable scenario interface."""

    name: str

    @abstractmethod
    def describe_rules(self) -> str:
        """Return natural-language rules for the scenario."""

    @abstractmethod
    def describe_strategy_interface(self) -> str:
        """Return expected JSON strategy schema description."""

    @abstractmethod
    def describe_evaluation_criteria(self) -> str:
        """Return score criteria and optimization objectives."""

    @abstractmethod
    def initial_state(self, seed: int | None = None) -> dict[str, Any]:
        """Create deterministic initial state."""

    @abstractmethod
    def get_observation(self, state: Mapping[str, Any], player_id: str) -> Observation:
        """Return the player observation from current state."""

    @abstractmethod
    def validate_actions(self, state: Mapping[str, Any], player_id: str, actions: Mapping[str, Any]) -> tuple[bool, str]:
        """Validate actions prior to stepping scenario."""

    @abstractmethod
    def step(self, state: Mapping[str, Any], actions: Mapping[str, Any]) -> dict[str, Any]:
        """Advance state using provided actions."""

    @abstractmethod
    def is_terminal(self, state: Mapping[str, Any]) -> bool:
        """Check terminal condition."""

    @abstractmethod
    def get_result(self, state: Mapping[str, Any]) -> Result:
        """Build final result payload from terminal state."""

    @abstractmethod
    def replay_to_narrative(self, replay: list[dict[str, Any]]) -> str:
        """Render replay data into concise narrative text."""

    @abstractmethod
    def render_frame(self, state: Mapping[str, Any]) -> dict[str, Any]:
        """Render state frame for UI consumers."""

    def enumerate_legal_actions(self, state: Mapping[str, Any]) -> list[dict[str, Any]] | None:
        """Return all legal actions from the current state.

        Returns None if enumeration is not supported for this scenario (default).
        An empty list means no legal moves are available (e.g. must pass).
        Each action dict should have at minimum ``{"action": str, "description": str}``.
        """
        return None

    def seed_tools(self) -> dict[str, str]:
        return {}

    def custom_backpressure(self, result: Result) -> dict[str, float]:
        return {"score": result.score}

    def execute_match(self, strategy: Mapping[str, Any], seed: int) -> Result:
        """Default single-step execution for strategy scoring."""

        state = self.initial_state(seed=seed)
        valid, reason = self.validate_actions(state, "challenger", strategy)
        if not valid:
            return Result(
                score=0.0,
                winner="incumbent",
                summary="strategy rejected during validation",
                replay=[{"event": "validation_failed", "reason": reason}],
                metrics={"valid": 0.0},
                validation_errors=[reason],
            )
        next_state = self.step(state, strategy)
        if not self.is_terminal(next_state):
            # Scenarios can override for multi-turn games; baseline marks one-step complete.
            next_state = {**dict(next_state), "terminal": True}
        return self.get_result(next_state)
