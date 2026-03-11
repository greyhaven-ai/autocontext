"""Tests for protocol parity between server (Python) and TUI (TypeScript).

MTS-142: Ensure the TUI protocol.ts is generated/validated from the server
protocol.py JSON Schema, so protocol drift is caught automatically.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from mts.server.protocol import export_json_schema


def _repo_root() -> Path:
    """Walk up from this file to find the repo root (contains tui/)."""
    current = Path(__file__).resolve().parent
    for _ in range(5):
        if (current / "tui").exists():
            return current
        current = current.parent
    pytest.skip("Could not locate repo root with tui/ directory")
    raise RuntimeError("unreachable")  # pragma: no cover


def _protocol_schema_path() -> Path:
    """Return the path to the committed protocol JSON schema file."""
    return _repo_root() / "protocol" / "mts-protocol.json"


class TestProtocolSchemaExport:
    """Verify the server can export its schema as JSON."""

    def test_export_contains_protocol_version(self) -> None:
        schema = export_json_schema()
        assert "protocol_version" in schema
        assert isinstance(schema["protocol_version"], int)

    def test_export_contains_server_messages(self) -> None:
        schema = export_json_schema()
        assert "server_messages" in schema
        assert "$defs" in schema["server_messages"] or "anyOf" in schema["server_messages"]

    def test_export_contains_client_messages(self) -> None:
        schema = export_json_schema()
        assert "client_messages" in schema
        assert "$defs" in schema["client_messages"] or "anyOf" in schema["client_messages"]


class TestProtocolSchemaFile:
    """Verify the committed protocol/mts-protocol.json matches the live schema."""

    def test_schema_file_exists(self) -> None:
        path = _protocol_schema_path()
        assert path.exists(), (
            f"protocol/mts-protocol.json not found at {path}. "
            "Run: python scripts/generate_protocol.py"
        )

    def test_schema_file_matches_live(self) -> None:
        path = _protocol_schema_path()
        if not path.exists():
            pytest.skip("protocol/mts-protocol.json not found")
        committed = json.loads(path.read_text(encoding="utf-8"))
        live = export_json_schema()
        assert committed == live, (
            "protocol/mts-protocol.json is out of date. "
            "Regenerate with: python scripts/generate_protocol.py"
        )


class TestProtocolGenerationScript:
    """Verify the generation script can run and produces valid output."""

    def test_generation_script_exists(self) -> None:
        script = _repo_root() / "scripts" / "generate_protocol.py"
        assert script.exists(), "scripts/generate_protocol.py not found"

    def test_generation_script_check_mode(self) -> None:
        """The script's --check flag should exit 0 when schemas are in sync."""
        script = _repo_root() / "scripts" / "generate_protocol.py"
        if not script.exists():
            pytest.skip("scripts/generate_protocol.py not found")
        result = subprocess.run(
            [sys.executable, str(script), "--check"],
            capture_output=True,
            text=True,
            cwd=str(_repo_root()),
        )
        assert result.returncode == 0, (
            f"Protocol parity check failed:\n{result.stdout}\n{result.stderr}"
        )


class TestScenarioErrorMsgStage:
    """MTS-142 acceptance: ScenarioErrorMsg.stage is in the exported schema."""

    def test_scenario_error_has_stage_field(self) -> None:
        schema = export_json_schema()
        server_defs = schema["server_messages"].get("$defs", {})
        error_schema = server_defs.get("ScenarioErrorMsg", {})
        props = error_schema.get("properties", {})
        assert "stage" in props, (
            "ScenarioErrorMsg must have a 'stage' property in the JSON Schema"
        )

    def test_scenario_error_stage_is_string(self) -> None:
        schema = export_json_schema()
        server_defs = schema["server_messages"].get("$defs", {})
        error_schema = server_defs.get("ScenarioErrorMsg", {})
        stage_prop = error_schema.get("properties", {}).get("stage", {})
        assert stage_prop.get("type") == "string"


class TestProtocolSingleSourceOfTruth:
    """Verify that adding a new server message requires only changing protocol.py."""

    def test_all_server_message_types_in_schema(self) -> None:
        """Every server message type literal should appear in the schema."""
        expected_types = {
            "hello", "event", "state", "chat_response",
            "environments", "run_accepted", "ack", "error",
            "scenario_generating", "scenario_preview",
            "scenario_ready", "scenario_error",
        }

        schema = export_json_schema()
        server_defs = schema["server_messages"].get("$defs", {})
        found_types: set[str] = set()
        for _def_name, def_schema in server_defs.items():
            props = def_schema.get("properties", {})
            type_prop = props.get("type", {})
            if "const" in type_prop:
                found_types.add(type_prop["const"])

        assert expected_types <= found_types, (
            f"Missing message types in schema: {expected_types - found_types}"
        )
