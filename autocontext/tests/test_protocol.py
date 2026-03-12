"""Conformance tests for the WebSocket protocol models."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from autocontext.server.protocol import (
    PROTOCOL_VERSION,
    AckMsg,
    AgentsStartedPayload,
    CancelScenarioCmd,
    ChatAgentCmd,
    ChatResponseMsg,
    ConfirmScenarioCmd,
    CreateScenarioCmd,
    CuratorCompletedPayload,
    CuratorStartedPayload,
    EnvironmentsMsg,
    ErrorMsg,
    EventMsg,
    GateDecidedPayload,
    GenerationCompletedPayload,
    GenerationStartedPayload,
    HelloMsg,
    InjectHintCmd,
    ListScenariosCmd,
    MatchCompletedPayload,
    OverrideGateCmd,
    PauseCmd,
    ResumeCmd,
    ReviseScenarioCmd,
    RoleCompletedPayload,
    RunAcceptedMsg,
    RunCompletedPayload,
    RunStartedPayload,
    ScenarioErrorMsg,
    ScenarioGeneratingMsg,
    ScenarioInfo,
    ScenarioPreviewMsg,
    ScenarioReadyMsg,
    ScoringComponent,
    StartRunCmd,
    StateMsg,
    StrategyParam,
    TournamentCompletedPayload,
    TournamentStartedPayload,
    export_json_schema,
    parse_client_message,
)


def _find_schema_path() -> Path | None:
    """Walk up from this file to locate protocol/autocontext-protocol.json at the repo root."""
    current = Path(__file__).resolve().parent
    for _ in range(5):
        candidate = current / "protocol" / "autocontext-protocol.json"
        if candidate.exists():
            return candidate
        current = current.parent
    return None


class TestSchemaConformance:
    def test_protocol_models_match_schema_file(self) -> None:
        """Verify that Pydantic models produce the same JSON Schema as the committed file."""
        schema_path = _find_schema_path()
        if schema_path is None:
            pytest.skip("protocol/autocontext-protocol.json not found — run from repo root")
        schema = export_json_schema()
        committed = json.loads(schema_path.read_text(encoding="utf-8"))
        assert schema == committed, (
            "protocol/autocontext-protocol.json is out of date. "
            "Regenerate with: uv run python -c "
            '"from autocontext.server.protocol import export_json_schema; import json; '
            'print(json.dumps(export_json_schema(), indent=2))" > ../protocol/autocontext-protocol.json'
        )


class TestHelloMsg:
    def test_defaults(self) -> None:
        msg = HelloMsg()
        assert msg.type == "hello"
        assert msg.protocol_version == PROTOCOL_VERSION

    def test_round_trip(self) -> None:
        msg = HelloMsg()
        d = msg.model_dump()
        assert d == {"type": "hello", "protocol_version": PROTOCOL_VERSION}
        restored = HelloMsg(**d)
        assert restored == msg


class TestServerMessageRoundTrip:
    """Verify every server message type can be serialized and deserialized."""

    @pytest.mark.parametrize(
        "model,kwargs",
        [
            (HelloMsg, {}),
            (EventMsg, {"event": "run_started", "payload": {"run_id": "r1"}}),
            (StateMsg, {"paused": True, "generation": 3, "phase": "agents"}),
            (ChatResponseMsg, {"role": "analyst", "text": "hello"}),
            (
                EnvironmentsMsg,
                {
                    "scenarios": [{"name": "grid_ctf", "description": "CTF game"}],
                    "executors": [{"mode": "local", "available": True, "description": "Local"}],
                    "current_executor": "local",
                    "agent_provider": "deterministic",
                },
            ),
            (RunAcceptedMsg, {"run_id": "r1", "scenario": "grid_ctf", "generations": 5}),
            (AckMsg, {"action": "inject_hint"}),
            (AckMsg, {"action": "override_gate", "decision": "advance"}),
            (ErrorMsg, {"message": "something failed"}),
            (ScenarioGeneratingMsg, {"name": "test_scenario"}),
            (
                ScenarioPreviewMsg,
                {
                    "name": "test",
                    "display_name": "Test",
                    "description": "A test",
                    "strategy_params": [{"name": "x", "description": "param"}],
                    "scoring_components": [{"name": "s", "description": "score", "weight": 1.0}],
                    "constraints": ["x > 0"],
                    "win_threshold": 0.5,
                },
            ),
            (ScenarioReadyMsg, {"name": "test", "test_scores": [0.5, 0.7]}),
            (ScenarioErrorMsg, {"message": "failed", "stage": "generation"}),
        ],
        ids=lambda x: x.__name__ if isinstance(x, type) else "",
    )
    def test_round_trip(self, model: type, kwargs: dict) -> None:
        instance = model(**kwargs)
        d = instance.model_dump()
        assert "type" in d
        restored = model(**d)
        assert restored == instance


class TestClientMessageParsing:
    """Verify parse_client_message handles all known types and rejects unknowns."""

    @pytest.mark.parametrize(
        "raw,expected_type",
        [
            ({"type": "pause"}, PauseCmd),
            ({"type": "resume"}, ResumeCmd),
            ({"type": "inject_hint", "text": "try X"}, InjectHintCmd),
            ({"type": "override_gate", "decision": "advance"}, OverrideGateCmd),
            ({"type": "chat_agent", "role": "analyst", "message": "hello"}, ChatAgentCmd),
            ({"type": "start_run", "scenario": "grid_ctf", "generations": 3}, StartRunCmd),
            ({"type": "list_scenarios"}, ListScenariosCmd),
            ({"type": "create_scenario", "description": "A game"}, CreateScenarioCmd),
            ({"type": "confirm_scenario"}, ConfirmScenarioCmd),
            ({"type": "revise_scenario", "feedback": "change X"}, ReviseScenarioCmd),
            ({"type": "cancel_scenario"}, CancelScenarioCmd),
        ],
    )
    def test_valid_messages(self, raw: dict, expected_type: type) -> None:
        msg = parse_client_message(raw)
        assert isinstance(msg, expected_type)

    def test_unknown_type_rejected(self) -> None:
        with pytest.raises(ValidationError):
            parse_client_message({"type": "unknown_type"})

    def test_missing_type_rejected(self) -> None:
        with pytest.raises(ValidationError):
            parse_client_message({"text": "no type field"})

    def test_extra_fields_rejected(self) -> None:
        with pytest.raises(ValidationError):
            parse_client_message({"type": "pause", "extra": "not allowed"})

    def test_invalid_gate_decision_rejected(self) -> None:
        with pytest.raises(ValidationError):
            parse_client_message({"type": "override_gate", "decision": "invalid"})

    @pytest.mark.parametrize(
        "raw",
        [
            {"type": "inject_hint", "text": ""},
            {"type": "chat_agent", "role": "analyst", "message": ""},
            {"type": "create_scenario", "description": ""},
            {"type": "revise_scenario", "feedback": ""},
        ],
    )
    def test_empty_required_strings_rejected(self, raw: dict[str, object]) -> None:
        with pytest.raises(ValidationError):
            parse_client_message(raw)

    @pytest.mark.parametrize("generations", [0, -1])
    def test_non_positive_generations_rejected(self, generations: int) -> None:
        with pytest.raises(ValidationError):
            parse_client_message({"type": "start_run", "scenario": "grid_ctf", "generations": generations})


class TestEventPayloads:
    """Verify each event payload model validates its expected shape."""

    @pytest.mark.parametrize(
        "model,kwargs",
        [
            (RunStartedPayload, {"run_id": "r1", "scenario": "grid_ctf"}),
            (GenerationStartedPayload, {"run_id": "r1", "generation": 1}),
            (AgentsStartedPayload, {"run_id": "r1", "generation": 1, "roles": ["competitor", "analyst"]}),
            (RoleCompletedPayload, {"run_id": "r1", "generation": 1, "role": "analyst", "latency_ms": 1200, "tokens": 500}),
            (TournamentStartedPayload, {"run_id": "r1", "generation": 1, "matches": 3}),
            (MatchCompletedPayload, {"run_id": "r1", "generation": 1, "match_index": 0, "score": 0.75}),
            (
                TournamentCompletedPayload,
                {"run_id": "r1", "generation": 1, "mean_score": 0.6, "best_score": 0.8, "wins": 2, "losses": 1},
            ),
            (GateDecidedPayload, {"run_id": "r1", "generation": 1, "decision": "advance", "delta": 0.05}),
            (CuratorStartedPayload, {"run_id": "r1", "generation": 1}),
            (CuratorCompletedPayload, {"run_id": "r1", "generation": 1, "decision": "accept"}),
            (
                GenerationCompletedPayload,
                {
                    "run_id": "r1",
                    "generation": 1,
                    "mean_score": 0.6,
                    "best_score": 0.8,
                    "elo": 1050.0,
                    "gate_decision": "advance",
                    "created_tools": ["tool_a.py"],
                },
            ),
            (RunCompletedPayload, {"run_id": "r1", "completed_generations": 5}),
        ],
    )
    def test_validates(self, model: type, kwargs: dict) -> None:
        instance = model(**kwargs)
        d = instance.model_dump()
        restored = model(**d)
        assert restored == instance

    def test_extra_fields_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RunStartedPayload(run_id="r1", scenario="grid_ctf", extra="bad")  # type: ignore[call-arg]


class TestNestedModels:
    def test_scenario_info(self) -> None:
        info = ScenarioInfo(name="grid_ctf", description="Capture the Flag")
        assert info.model_dump() == {"name": "grid_ctf", "description": "Capture the Flag"}

    def test_strategy_param(self) -> None:
        param = StrategyParam(name="x", description="a param")
        assert param.model_dump() == {"name": "x", "description": "a param"}

    def test_scoring_component(self) -> None:
        comp = ScoringComponent(name="s", description="score", weight=0.5)
        assert comp.model_dump() == {"name": "s", "description": "score", "weight": 0.5}
