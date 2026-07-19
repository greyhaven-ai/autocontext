from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from autocontext.harness.core.events import EventStreamEmitter
from autocontext.server.protocol import PROTOCOL_VERSION, export_json_schema, parse_client_message


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


def test_safe_stop_capability_is_typescript_only_with_shared_schema() -> None:
    contract = _contract()
    safe_stop = contract["safe_stop_extension"]

    assert safe_stop["capability"] == "safe_run_stop_v1"
    assert safe_stop["advertised_runtimes"] == ["typescript"]
    assert safe_stop["requires_transcript_protocol_version"] == 1
    assert safe_stop["command"] == "stop"
    assert safe_stop["required_command_fields"] == ["client_run_id", "command_id"]
    assert safe_stop["terminal_event"] == "run_stopped"
    assert safe_stop["terminal_arbitration"] == "first_terminal_outcome_wins"
    assert safe_stop["python_support"] == "schema_only"
    assert "stop" in contract["shared_client_messages"]


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
