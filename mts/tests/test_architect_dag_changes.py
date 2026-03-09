"""Tests for parsing DAG change directives from architect output (MTS-27)."""
from __future__ import annotations

from mts.agents.architect import parse_dag_changes


def test_parse_no_markers_returns_empty() -> None:
    content = "Some architect output with tools."
    assert parse_dag_changes(content) == []


def test_parse_add_role() -> None:
    content = (
        "Some text\n"
        "<!-- DAG_CHANGES_START -->\n"
        '{"changes": [{"action": "add_role", "name": "critic", "depends_on": ["analyst"]}]}\n'
        "<!-- DAG_CHANGES_END -->\n"
        "More text"
    )
    changes = parse_dag_changes(content)
    assert len(changes) == 1
    assert changes[0]["action"] == "add_role"
    assert changes[0]["name"] == "critic"
    assert changes[0]["depends_on"] == ["analyst"]


def test_parse_remove_role() -> None:
    content = (
        "<!-- DAG_CHANGES_START -->\n"
        '{"changes": [{"action": "remove_role", "name": "architect"}]}\n'
        "<!-- DAG_CHANGES_END -->\n"
    )
    changes = parse_dag_changes(content)
    assert len(changes) == 1
    assert changes[0]["action"] == "remove_role"
    assert changes[0]["name"] == "architect"


def test_parse_multiple_changes() -> None:
    content = (
        "<!-- DAG_CHANGES_START -->\n"
        '{"changes": ['
        '{"action": "remove_role", "name": "architect"},'
        '{"action": "add_role", "name": "critic", "depends_on": ["analyst"]}'
        "]}\n"
        "<!-- DAG_CHANGES_END -->\n"
    )
    changes = parse_dag_changes(content)
    assert len(changes) == 2


def test_parse_malformed_json_returns_empty() -> None:
    content = (
        "<!-- DAG_CHANGES_START -->\n"
        "not valid json\n"
        "<!-- DAG_CHANGES_END -->\n"
    )
    assert parse_dag_changes(content) == []


def test_parse_invalid_action_skipped() -> None:
    content = (
        "<!-- DAG_CHANGES_START -->\n"
        '{"changes": [{"action": "explode", "name": "boom"}]}\n'
        "<!-- DAG_CHANGES_END -->\n"
    )
    assert parse_dag_changes(content) == []
