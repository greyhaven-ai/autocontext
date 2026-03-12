"""Tests for autocontext.harness.orchestration.dag — DAG topological sort and validation."""

from __future__ import annotations

import pytest

from autocontext.harness.orchestration.dag import RoleDAG
from autocontext.harness.orchestration.types import PipelineConfig, RoleSpec


class TestRoleSpec:
    def test_role_spec_construction(self) -> None:
        spec = RoleSpec(name="analyst", depends_on=("competitor",), model="claude-3", max_tokens=4096)
        assert spec.name == "analyst"
        assert spec.depends_on == ("competitor",)
        assert spec.model == "claude-3"
        assert spec.max_tokens == 4096

    def test_role_spec_frozen(self) -> None:
        spec = RoleSpec(name="analyst")
        with pytest.raises(AttributeError):
            spec.name = "other"  # type: ignore[misc]


class TestRoleDAG:
    def test_dag_single_role(self) -> None:
        dag = RoleDAG([RoleSpec(name="solo")])
        batches = dag.execution_batches()
        assert batches == [["solo"]]

    def test_dag_linear_chain(self) -> None:
        roles = [
            RoleSpec(name="A"),
            RoleSpec(name="B", depends_on=("A",)),
            RoleSpec(name="C", depends_on=("B",)),
        ]
        dag = RoleDAG(roles)
        batches = dag.execution_batches()
        assert batches == [["A"], ["B"], ["C"]]

    def test_dag_parallel_independent(self) -> None:
        roles = [RoleSpec(name="A"), RoleSpec(name="B")]
        dag = RoleDAG(roles)
        batches = dag.execution_batches()
        assert batches == [["A", "B"]]

    def test_dag_diamond(self) -> None:
        roles = [
            RoleSpec(name="A"),
            RoleSpec(name="B", depends_on=("A",)),
            RoleSpec(name="C", depends_on=("A",)),
            RoleSpec(name="D", depends_on=("B", "C")),
        ]
        dag = RoleDAG(roles)
        batches = dag.execution_batches()
        assert batches == [["A"], ["B", "C"], ["D"]]

    def test_dag_detects_cycle(self) -> None:
        roles = [
            RoleSpec(name="A", depends_on=("B",)),
            RoleSpec(name="B", depends_on=("A",)),
        ]
        dag = RoleDAG(roles)
        with pytest.raises(ValueError, match="[Cc]ycle"):
            dag.validate()

    def test_dag_detects_missing_dep(self) -> None:
        roles = [RoleSpec(name="A", depends_on=("Z",))]
        dag = RoleDAG(roles)
        with pytest.raises(ValueError, match="unknown role"):
            dag.validate()

    def test_dag_detects_self_dep(self) -> None:
        roles = [RoleSpec(name="A", depends_on=("A",))]
        dag = RoleDAG(roles)
        with pytest.raises(ValueError, match="depends on itself"):
            dag.validate()

    def test_dag_execution_order_deterministic(self) -> None:
        roles = [
            RoleSpec(name="C"),
            RoleSpec(name="A"),
            RoleSpec(name="B"),
        ]
        dag = RoleDAG(roles)
        b1 = dag.execution_batches()
        b2 = dag.execution_batches()
        assert b1 == b2

    def test_pipeline_config_validates_on_init(self) -> None:
        with pytest.raises(ValueError, match="unknown role"):
            PipelineConfig(roles=[RoleSpec(name="A", depends_on=("missing",))])
