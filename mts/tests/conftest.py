"""Shared test fixtures and helpers."""
from __future__ import annotations

from mts.harness.orchestration.dag import RoleDAG
from mts.harness.orchestration.types import RoleSpec


def make_base_dag() -> RoleDAG:
    """Standard 5-role MTS DAG used across multiple test modules."""
    return RoleDAG([
        RoleSpec(name="competitor"),
        RoleSpec(name="translator", depends_on=("competitor",)),
        RoleSpec(name="analyst", depends_on=("translator",)),
        RoleSpec(name="architect", depends_on=("translator",)),
        RoleSpec(name="coach", depends_on=("analyst",)),
    ])
