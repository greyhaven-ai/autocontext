"""Tests for applying DAG changes in the orchestrator (MTS-27)."""
from __future__ import annotations

from conftest import make_base_dag


def test_apply_add_role() -> None:
    """apply_dag_changes adds a new role to the DAG."""
    from mts.agents.orchestrator import apply_dag_changes

    dag = make_base_dag()
    changes = [{"action": "add_role", "name": "critic", "depends_on": ["analyst"]}]
    applied, skipped = apply_dag_changes(dag, changes)
    assert applied == 1
    assert skipped == 0
    assert "critic" in dag.roles


def test_apply_remove_role() -> None:
    """apply_dag_changes removes a role from the DAG."""
    from mts.agents.orchestrator import apply_dag_changes

    dag = make_base_dag()
    changes = [{"action": "remove_role", "name": "architect"}]
    applied, skipped = apply_dag_changes(dag, changes)
    assert applied == 1
    assert "architect" not in dag.roles


def test_apply_invalid_change_skipped() -> None:
    """Invalid changes (e.g., removing a depended-upon role) are skipped."""
    from mts.agents.orchestrator import apply_dag_changes

    dag = make_base_dag()
    changes = [{"action": "remove_role", "name": "analyst"}]  # coach depends on analyst
    applied, skipped = apply_dag_changes(dag, changes)
    assert applied == 0
    assert skipped == 1
    assert "analyst" in dag.roles  # Unchanged


def test_apply_multiple_changes() -> None:
    """Multiple changes are applied in order."""
    from mts.agents.orchestrator import apply_dag_changes

    dag = make_base_dag()
    changes = [
        {"action": "remove_role", "name": "architect"},
        {"action": "add_role", "name": "critic", "depends_on": ["analyst"]},
    ]
    applied, skipped = apply_dag_changes(dag, changes)
    assert applied == 2
    assert "architect" not in dag.roles
    assert "critic" in dag.roles
