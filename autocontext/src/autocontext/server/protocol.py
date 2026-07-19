"""WebSocket protocol models for the autocontext TUI <-> Server boundary.

This module is the single source of truth for the protocol. All message types
that flow over ``/ws/interactive`` are defined here as Pydantic models.

Use :func:`export_json_schema` to produce a JSON Schema document suitable for
cross-language validation (e.g. by the TypeScript TUI).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

PROTOCOL_VERSION = 1


def _is_none(value: object) -> bool:
    return value is None

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


class RunMessageMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_run_id: str | None = Field(default=None, exclude_if=_is_none)
    event_id: str | None = Field(default=None, exclude_if=_is_none)
    sequence: int | None = Field(default=None, exclude_if=_is_none)
    run_id: str | None = Field(default=None, exclude_if=_is_none)
    occurred_at: str | float | None = Field(default=None, exclude_if=_is_none)


class RunCommandMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_run_id: str | None = None
    command_id: str | None = None


# ---------------------------------------------------------------------------
# Server -> Client messages
# ---------------------------------------------------------------------------


class HelloMsg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["hello"] = "hello"
    protocol_version: int = PROTOCOL_VERSION
    transcript_protocol_version: int | None = Field(default=None, exclude_if=_is_none)
    capabilities: list[str] | None = Field(default=None, exclude_if=_is_none)


class EventMsg(RunMessageMetadata):
    model_config = ConfigDict(extra="forbid")

    type: Literal["event"] = "event"
    event: str
    payload: dict[str, Any]


class StateMsg(RunMessageMetadata):
    model_config = ConfigDict(extra="forbid")

    type: Literal["state"] = "state"
    paused: bool
    generation: int = 0
    phase: str = ""


class ChatResponseMsg(RunMessageMetadata):
    model_config = ConfigDict(extra="forbid")

    type: Literal["chat_response"] = "chat_response"
    role: str
    text: str
    command_id: str | None = Field(default=None, exclude_if=_is_none)


class EnvironmentsMsg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["environments"] = "environments"
    scenarios: list[ScenarioInfo]
    executors: list[ExecutorInfo]
    current_executor: str
    agent_provider: str


class RunAcceptedMsg(RunMessageMetadata):
    model_config = ConfigDict(extra="forbid")

    type: Literal["run_accepted"] = "run_accepted"
    run_id: str
    scenario: str
    generations: int
    command_id: str | None = Field(default=None, exclude_if=_is_none)


class AckMsg(RunMessageMetadata):
    model_config = ConfigDict(extra="forbid")

    type: Literal["ack"] = "ack"
    action: str
    decision: str | None = None
    command_id: str | None = Field(default=None, exclude_if=_is_none)


class ErrorMsg(RunMessageMetadata):
    model_config = ConfigDict(extra="forbid")

    type: Literal["error"] = "error"
    message: str
    command_id: str | None = Field(default=None, exclude_if=_is_none)


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


class MonitorAlertMsg(RunMessageMetadata):
    """Pushed to WebSocket clients when a monitor condition fires (AC-209)."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["monitor_alert"] = "monitor_alert"
    alert_id: str
    condition_id: str
    condition_name: str
    condition_type: str
    scope: str
    detail: str


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
    | ScenarioErrorMsg
    | MonitorAlertMsg,
    Field(discriminator="type"),
]


# ---------------------------------------------------------------------------
# Client -> Server messages
# ---------------------------------------------------------------------------


class PauseCmd(RunCommandMetadata):
    model_config = ConfigDict(extra="forbid")

    type: Literal["pause"] = "pause"


class ResumeCmd(RunCommandMetadata):
    model_config = ConfigDict(extra="forbid")

    type: Literal["resume"] = "resume"


class StopCmd(RunCommandMetadata):
    model_config = ConfigDict(extra="forbid")

    type: Literal["stop"] = "stop"
    client_run_id: str = Field(min_length=1, max_length=200)
    command_id: str = Field(min_length=1, max_length=200)


class InjectHintCmd(RunCommandMetadata):
    model_config = ConfigDict(extra="forbid")

    type: Literal["inject_hint"] = "inject_hint"
    text: str = Field(min_length=1)


class OverrideGateCmd(RunCommandMetadata):
    model_config = ConfigDict(extra="forbid")

    type: Literal["override_gate"] = "override_gate"
    decision: Literal["advance", "retry", "rollback"]


class ChatAgentCmd(RunCommandMetadata):
    model_config = ConfigDict(extra="forbid")

    type: Literal["chat_agent"] = "chat_agent"
    role: str
    message: str = Field(min_length=1)


class StartRunCmd(RunCommandMetadata):
    model_config = ConfigDict(extra="forbid")

    type: Literal["start_run"] = "start_run"
    scenario: str
    generations: int = Field(gt=0)
    require_playbook_approval: bool = False


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
    | StopCmd
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
    target_generations: int


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
    best_score: float
    elo: float
    session_report_path: str | None
    dead_ends_found: int


class RunStoppedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    reason: Literal["operator"]
    command_id: str = Field(min_length=1, max_length=200)
    completed_generations: int = Field(ge=0)
    best_score: float | None = Field(default=None, exclude_if=_is_none)


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
