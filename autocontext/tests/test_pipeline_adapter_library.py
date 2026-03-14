"""Tests for DAG extension with dynamic librarian/archivist nodes."""
from __future__ import annotations

from autocontext.agents.pipeline_adapter import build_mts_dag


# ---------------------------------------------------------------------------
# DAG construction
# ---------------------------------------------------------------------------


def test_build_dag_no_books() -> None:
    dag = build_mts_dag()
    role_names = set(dag.roles.keys())
    assert "librarian" not in str(role_names)
    assert "archivist" not in role_names


def test_build_dag_with_books() -> None:
    dag = build_mts_dag(active_books=["clean-arch", "ddd"])
    role_names = set(dag.roles.keys())
    assert "librarian_clean-arch" in role_names
    assert "librarian_ddd" in role_names
    assert "archivist" in role_names


# ---------------------------------------------------------------------------
# Dependency edges
# ---------------------------------------------------------------------------


def test_librarians_depend_on_translator() -> None:
    dag = build_mts_dag(active_books=["clean-arch"])
    spec = dag.roles["librarian_clean-arch"]
    assert "translator" in spec.depends_on


def test_archivist_depends_on_all_librarians() -> None:
    dag = build_mts_dag(active_books=["a", "b", "c"])
    spec = dag.roles["archivist"]
    assert "librarian_a" in spec.depends_on
    assert "librarian_b" in spec.depends_on
    assert "librarian_c" in spec.depends_on


def test_coach_depends_on_archivist_when_books() -> None:
    dag = build_mts_dag(active_books=["clean-arch"])
    spec = dag.roles["coach"]
    assert "archivist" in spec.depends_on


def test_coach_does_not_depend_on_archivist_without_books() -> None:
    dag = build_mts_dag()
    spec = dag.roles["coach"]
    assert "archivist" not in spec.depends_on


# ---------------------------------------------------------------------------
# Execution order
# ---------------------------------------------------------------------------


def test_dag_execution_order_with_books() -> None:
    dag = build_mts_dag(active_books=["clean-arch"])
    batches = dag.execution_batches()
    role_order = [role for batch in batches for role in batch]
    assert role_order.index("translator") < role_order.index("librarian_clean-arch")
    assert role_order.index("librarian_clean-arch") < role_order.index("archivist")
    assert role_order.index("archivist") < role_order.index("coach")
