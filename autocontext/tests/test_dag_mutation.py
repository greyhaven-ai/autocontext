"""Tests for RoleDAG mutation methods (MTS-27)."""

from __future__ import annotations

import pytest
from conftest import make_base_dag

from autocontext.harness.orchestration.dag import RoleDAG
from autocontext.harness.orchestration.types import RoleSpec


def test_add_role_appends() -> None:
    dag = make_base_dag()
    dag.add_role(RoleSpec(name="critic", depends_on=("analyst",)))
    assert "critic" in dag.roles
    dag.validate()


def test_add_role_duplicate_raises() -> None:
    dag = make_base_dag()
    with pytest.raises(ValueError, match="already exists"):
        dag.add_role(RoleSpec(name="analyst", depends_on=("translator",)))


def test_add_role_cycle_detected_on_construction() -> None:
    """A DAG constructed with a cycle is caught by validate()."""
    dag = RoleDAG([
        RoleSpec(name="a", depends_on=("c",)),
        RoleSpec(name="b", depends_on=("a",)),
        RoleSpec(name="c", depends_on=("b",)),
    ])
    with pytest.raises(ValueError, match="[Cc]ycle"):
        dag.validate()


def test_add_role_self_dep_raises() -> None:
    """A role that depends on itself is rejected by add_role."""
    dag = RoleDAG([RoleSpec(name="a")])
    with pytest.raises(ValueError, match="depends on itself"):
        dag.add_role(RoleSpec(name="b", depends_on=("b",)))


def test_add_role_missing_dep_raises() -> None:
    dag = make_base_dag()
    with pytest.raises(ValueError, match="unknown role"):
        dag.add_role(RoleSpec(name="critic", depends_on=("nonexistent",)))


def test_remove_role() -> None:
    dag = make_base_dag()
    dag.remove_role("architect")
    assert "architect" not in dag.roles
    dag.validate()


def test_remove_role_unknown_raises() -> None:
    dag = make_base_dag()
    with pytest.raises(ValueError, match="not found"):
        dag.remove_role("nonexistent")


def test_remove_role_with_dependents_raises() -> None:
    dag = make_base_dag()
    with pytest.raises(ValueError, match="depended on by"):
        dag.remove_role("analyst")


def test_execution_batches_after_mutation() -> None:
    dag = make_base_dag()
    dag.add_role(RoleSpec(name="critic", depends_on=("coach",)))
    batches = dag.execution_batches()
    flat = [name for batch in batches for name in batch]
    assert flat.index("critic") > flat.index("coach")
