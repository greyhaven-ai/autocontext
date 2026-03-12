"""WebSocket protocol models for the AutoContext TUI ↔ Server boundary.

This module is the single source of truth for the protocol. All message types
that flow over ``/ws/interactive`` are defined here as Pydantic models.

Use :func:`export_json_schema` to produce a JSON Schema document suitable for
cross-language validation (e.g. by the TypeScript TUI).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

PROTOCOL_VERSION = 1

# ---------------------------------------------------------------------------
# Nested / shared models
# ---------------------------------------------------------------------------


class ScenarioInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str


class ExecutorResources(BaseModel):
    model_config = ConfigDict(extra="forbid")

    docker_image: str
    cpu_cores: int
    memory_gb: int
    disk_gb: int
    timeout_minutes: int


class ExecutorInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str
    available: bool
    description: str
    resources: ExecutorResources | None = None


class StrategyParam(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str


class ScoringComponent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    weight: float


# ---------------------------------------------------------------------------
# Server -> Client messages
# ---------------------------------------------------------------------------


class HelloMsg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["hello"] = "hello"
    protocol_version: int = PROTOCOL_VERSION


class EventMsg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["event"] = "event"
    event: str
    payload: dict[str, Any]


class StateMsg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["state"] = "state"
    paused: bool
    generation: int = 0
    phase: str = ""


class ChatResponseMsg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["chat_response"] = "chat_response"
    role: str
    text: str


class EnvironmentsMsg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["environments"] = "environments"
    scenarios: list[ScenarioInfo]
    executors: list[ExecutorInfo]
    current_executor: str
    agent_provider: str


class RunAcceptedMsg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["run_accepted"] = "run_accepted"
    run_id: str
    scenario: str
    generations: int


class AckMsg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["ack"] = "ack"
    action: str
    decision: str | None = None


class ErrorMsg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["error"] = "error"
    message: str


class ScenarioGeneratingMsg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["scenario_generating"] = "scenario_generating"
    name: str


class ScenarioPreviewMsg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["scenario_preview"] = "scenario_preview"
    name: str
    display_name: str
    description: str
    strategy_params: list[StrategyParam]
    scoring_components: list[ScoringComponent]
    constraints: list[str]
    win_threshold: float


class ScenarioReadyMsg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["scenario_ready"] = "scenario_ready"
    name: str
    test_scores: list[float]


class ScenarioErrorMsg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["scenario_error"] = "scenario_error"
    message: str
    stage: str


ServerMessage = Annotated[
    HelloMsg
    | EventMsg
    | StateMsg
    | ChatResponseMsg
    | EnvironmentsMsg
    | RunAcceptedMsg
    | AckMsg
    | ErrorMsg
    | ScenarioGeneratingMsg
    | ScenarioPreviewMsg
    | ScenarioReadyMsg
    | ScenarioErrorMsg,
    Field(discriminator="type"),
]


# ---------------------------------------------------------------------------
# Client -> Server messages
# ---------------------------------------------------------------------------


class PauseCmd(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["pause"] = "pause"


class ResumeCmd(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["resume"] = "resume"


class InjectHintCmd(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["inject_hint"] = "inject_hint"
    text: str = Field(min_length=1)


class OverrideGateCmd(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["override_gate"] = "override_gate"
    decision: Literal["advance", "retry", "rollback"]


class ChatAgentCmd(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["chat_agent"] = "chat_agent"
    role: str
    message: str = Field(min_length=1)


class StartRunCmd(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["start_run"] = "start_run"
    scenario: str
    generations: int = Field(gt=0)


class ListScenariosCmd(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["list_scenarios"] = "list_scenarios"


class CreateScenarioCmd(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["create_scenario"] = "create_scenario"
    description: str = Field(min_length=1)


class ConfirmScenarioCmd(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["confirm_scenario"] = "confirm_scenario"


class ReviseScenarioCmd(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["revise_scenario"] = "revise_scenario"
    feedback: str = Field(min_length=1)


class CancelScenarioCmd(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["cancel_scenario"] = "cancel_scenario"


ClientMessage = Annotated[
    PauseCmd
    | ResumeCmd
    | InjectHintCmd
    | OverrideGateCmd
    | ChatAgentCmd
    | StartRunCmd
    | ListScenariosCmd
    | CreateScenarioCmd
    | ConfirmScenarioCmd
    | ReviseScenarioCmd
    | CancelScenarioCmd,
    Field(discriminator="type"),
]


# ---------------------------------------------------------------------------
# Event payloads
# ---------------------------------------------------------------------------


class RunStartedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    scenario: str


class GenerationStartedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    generation: int


class AgentsStartedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    generation: int
    roles: list[str]


class RoleCompletedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    generation: int
    role: str
    latency_ms: int
    tokens: int


class TournamentStartedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    generation: int
    matches: int


class MatchCompletedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    generation: int
    match_index: int
    score: float


class TournamentCompletedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    generation: int
    mean_score: float
    best_score: float
    wins: int
    losses: int


class GateDecidedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    generation: int
    decision: str
    delta: float


class CuratorStartedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    generation: int


class CuratorCompletedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    generation: int
    decision: str


class GenerationCompletedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    generation: int
    mean_score: float
    best_score: float
    elo: float
    gate_decision: str
    created_tools: list[str]


class RunCompletedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    completed_generations: int


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

_client_adapter: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)


def parse_client_message(raw: dict[str, Any]) -> ClientMessage:
    """Validate and parse a raw dict into a typed client message.

    Raises ``ValidationError`` if the dict does not match any known message type.
    """
    return _client_adapter.validate_python(raw)


def export_json_schema() -> dict[str, Any]:
    """Export the full protocol as JSON Schema for cross-language validation."""
    return {
        "protocol_version": PROTOCOL_VERSION,
        "server_messages": TypeAdapter(ServerMessage).json_schema(),
        "client_messages": TypeAdapter(ClientMessage).json_schema(),
    }
