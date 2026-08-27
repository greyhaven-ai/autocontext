from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from autocontext.harness.core.events import EventStreamEmitter
from autocontext.server.protocol import (
    PROTOCOL_VERSION,
    SERVER_CAPABILITIES,
    export_json_schema,
    parse_client_message,
)


def _contract() -> dict[str, Any]:
    contract_path = Path(__file__).resolve().parents[2] / "docs" / "websocket-protocol-contract.json"
    return json.loads(contract_path.read_text(encoding="utf-8"))


def _message_types(schema: dict[str, Any]) -> set[str]:
    found: set[str] = set()
    for definition in schema.get("$defs", {}).values():
        type_field = definition.get("properties", {}).get("type", {})
        if isinstance(type_field.get("const"), str):
            found.add(type_field["const"])
    return found


def _runtime_only_types(contract: dict[str, Any], key: str) -> set[str]:
    return {item["type"] for item in contract[key]}


def test_python_websocket_protocol_matches_shared_contract() -> None:
    contract = _contract()
    exported = export_json_schema()

    assert PROTOCOL_VERSION == contract["protocol_version"]
    assert _message_types(exported["server_messages"]) == set(contract["shared_server_messages"])
    assert _message_types(exported["client_messages"]) == set(contract["shared_client_messages"])


def test_python_protocol_excludes_typescript_only_messages() -> None:
    contract = _contract()
    exported = export_json_schema()

    assert _message_types(exported["server_messages"]).isdisjoint(
        _runtime_only_types(contract, "typescript_only_server_messages"),
    )
    assert _message_types(exported["client_messages"]).isdisjoint(
        _runtime_only_types(contract, "typescript_only_client_messages"),
    )


def test_python_protocol_forbids_unknown_top_level_client_fields() -> None:
    assert _contract()["top_level_unknown_field_policy"] == "forbid"

    with pytest.raises(ValidationError):
        parse_client_message({"type": "pause", "unexpected": True})


def test_safe_stop_capability_advertised_by_python_and_typescript() -> None:
    contract = _contract()
    safe_stop = contract["safe_stop_extension"]

    assert safe_stop["capability"] == "safe_run_stop_v1"
    assert safe_stop["advertised_runtimes"] == ["typescript", "python"]
    assert safe_stop["command"] == "stop"
    assert safe_stop["required_command_fields"] == ["client_run_id", "command_id"]
    assert safe_stop["terminal_event"] == "run_stopped"
    assert safe_stop["terminal_arbitration"] == "first_terminal_outcome_wins"
    assert safe_stop["base_idempotency"] == "live"
    assert safe_stop["python_support"] == "supported"
    # Base cooperative stop is shared; durable reconnect-after-terminal replay
    # stays transcript-gated and TypeScript-only.
    durable = safe_stop["durable_reconnect_replay"]
    assert durable["requires_transcript_protocol_version"] == 1
    assert durable["advertised_runtimes"] == ["typescript"]
    assert "stop" in contract["shared_client_messages"]


def test_agent_task_plan_capability_remains_typescript_only() -> None:
    extension = _contract()["agent_task_plan_extension"]

    assert extension["capability"] == "agent_task_plan_v1"
    assert extension["advertised_runtimes"] == ["typescript"]
    assert extension["requires_transcript_protocol_version"] == 1
    assert extension["python_support"] == "deferred_until_durable_transcript_metadata_is_available"
    assert "agent_task_plan_v1" not in SERVER_CAPABILITIES


def test_agent_progress_note_capability_remains_typescript_only() -> None:
    extension = _contract()["agent_progress_note_extension"]

    assert extension["capability"] == "agent_progress_notes_v1"
    assert extension["advertised_runtimes"] == ["typescript"]
    assert extension["requires_transcript_protocol_version"] == 1
    assert extension["event"] == "agent_progress_note"
    assert extension["payload"]["kinds"] == [
        "intent",
        "discovery",
        "decision",
        "verification",
        "blocker",
    ]
    assert extension["payload"]["copy_limits"]["text_characters"] == 480
    assert extension["payload"]["evidence_targets"]["maximum_items"] == 5
    assert extension["payload"]["evidence_targets"]["id"]["maximum_characters"] == 200
    assert extension["retention"]["max_serialized_event_bytes"] == 4096
    assert extension["fixture"]["run_id"] == extension["fixture"]["payload"]["run_id"]
    assert extension["python_support"] == ("deferred_until_durable_transcript_metadata_is_available")
    assert "agent_progress_notes_v1" not in SERVER_CAPABILITIES


def test_system_map_projection_remains_typescript_only_and_redacted() -> None:
    extension = _contract()["system_map_projection_extension"]

    assert extension["capability"] == "system_map_projection_v1"
    assert extension["advertised_runtimes"] == ["typescript"]
    assert extension["endpoint"] == "/ws/events"
    assert extension["query"] == {
        "projection": "system-map",
        "optional_run_scope": "run_id",
        "optional_view": {
            "parameter": "view",
            "values": ["execution", "context", "activation", "routing"],
            "default": "execution",
        },
    }
    assert extension["event"] == "system_map_transfer"
    assert extension["channel"] == "cockpit"
    assert {"traceId", "spanId", "spanName", "spanPhase", "startedAt"}.issubset(
        extension["payload"]["required_fields"]
    )
    assert extension["trace"] == {
        "version": 1,
        "source": "EventStreamRecord.trace",
        "phases": ["start", "complete", "instant"],
        "paired_boundaries": ["run", "generation", "role", "tournament", "persistence"],
        "raw_payload_copied": False,
    }
    assert extension["safety"] == {
        "allowlisted_summary_fields_only": True,
        "raw_prompts_allowed": False,
        "raw_model_or_tool_io_allowed": False,
        "credentials_allowed": False,
        "bounded_replay": True,
    }
    assert extension["python_support"] == "deferred"
    assert "system_map_projection_v1" not in SERVER_CAPABILITIES


def test_image_attachment_capability_remains_typescript_only() -> None:
    extension = _contract()["image_attachment_extension"]

    assert extension["capability"] == "image_attachments_v1"
    assert extension["advertised_runtimes"] == ["typescript"]
    assert extension["additive_commands"] == ["chat_agent", "inject_hint"]
    assert extension["limits"]["max_attachments"] == 4
    assert extension["python_support"] == "deferred"
    assert "image_attachments_v1" not in SERVER_CAPABILITIES


def test_python_event_stream_envelope_matches_shared_contract(tmp_path: Path) -> None:
    contract = _contract()["event_stream_envelope"]
    event_path = tmp_path / "events.ndjson"
    emitter = EventStreamEmitter(event_path)

    emitter.emit("run_started", {"run_id": "run_1"}, channel="generation")

    line = json.loads(event_path.read_text(encoding="utf-8").strip())
    assert sorted(line) == sorted(contract["required_fields"])
    assert line["v"] == contract["version"]
    assert line["seq"] == 1
    assert line["channel"] in contract["fields"]["channel"]["known_values"]
    assert isinstance(line["payload"], dict)
