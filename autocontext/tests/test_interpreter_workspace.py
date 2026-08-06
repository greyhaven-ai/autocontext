"""Tests for the persistent interpreter workspace (AC-901)."""

from __future__ import annotations

import pytest

from autocontext.execution.interpreter_workspace import (
    InterpreterWorkspace,
    WorkspaceSnapshot,
    WorkspaceVariable,
)


def test_state_persists_across_run_calls() -> None:
    ws = InterpreterWorkspace()
    first = ws.run("x = 5")
    assert first.error is None
    second = ws.run("x + 1")
    assert second.error is None
    assert "6" in second.stdout


def test_variables_lists_seeded_and_assigned_only() -> None:
    ws = InterpreterWorkspace(seed={"pool": [1, 2, 3]})
    ws.run("best = 'abc'")
    ws.run("_scratch = 1")
    names = {v.name for v in ws.variables()}
    assert names == {"pool", "best"}
    # ReplWorker infrastructure never leaks into the listing.
    assert "answer" not in names
    assert "json" not in names
    assert "peek" not in names


def test_variables_metadata_fields() -> None:
    ws = InterpreterWorkspace(seed={"pool": [1, 2, 3]})
    ws.run("count = 7")
    by_name = {v.name: v for v in ws.variables()}
    pool = by_name["pool"]
    assert isinstance(pool, WorkspaceVariable)
    assert pool.type_name == "list"
    assert pool.size == 3
    assert pool.summary == "[1, 2, 3]"
    count = by_name["count"]
    assert count.type_name == "int"
    assert count.size is None
    assert count.summary == "7"


def test_variables_sorted_and_summary_truncated() -> None:
    ws = InterpreterWorkspace()
    ws.run("zeta = 1")
    ws.run(f"alpha = {'x' * 500!r}")
    listed = ws.variables()
    assert [v.name for v in listed] == ["alpha", "zeta"]
    assert len(listed[0].summary) <= 120


def test_render_markdown_bounded() -> None:
    ws = InterpreterWorkspace()
    for i in range(25):
        ws.run(f"var_{i:02d} = {i}")
    rendered = ws.render_markdown(max_vars=20)
    assert "- var_00 (int): 0" in rendered
    assert "var_19" in rendered
    assert "- var_20" not in rendered
    assert "... and 5 more" in rendered


def test_render_markdown_empty_workspace() -> None:
    ws = InterpreterWorkspace()
    assert ws.render_markdown() == ""


def test_snapshot_restore_round_trip_is_deep() -> None:
    ws = InterpreterWorkspace(seed={"pool": [[1], [2]]})
    snap = ws.snapshot()

    fresh = InterpreterWorkspace()
    fresh.restore(snap)
    result = fresh.run("pool[0][0]")
    assert "1" in result.stdout

    # Mutating the restored copy touches neither the snapshot nor the source.
    fresh.run("pool[0][0] = 99")
    assert snap.variables["pool"][0][0] == 1
    source = ws.run("pool[0][0]")
    assert "1" in source.stdout


def test_snapshot_skips_non_copyable_values() -> None:
    ws = InterpreterWorkspace()
    ws.run("gen = (i for i in range(3))")
    ws.run("ok = [1, 2]")
    snap = ws.snapshot()
    assert isinstance(snap, WorkspaceSnapshot)
    assert "gen" in snap.skipped
    assert snap.variables["ok"] == [1, 2]
    assert "gen" not in snap.variables


def test_restore_replaces_existing_user_variables() -> None:
    ws = InterpreterWorkspace(seed={"stale": 1})
    ws.restore(WorkspaceSnapshot(variables={"fresh": 2}, skipped=()))
    names = {v.name for v in ws.variables()}
    assert names == {"fresh"}


def test_error_result_keeps_workspace_usable() -> None:
    ws = InterpreterWorkspace()
    result = ws.run("1/0")
    assert result.error is not None and "ZeroDivisionError" in result.error
    after = ws.run("2 + 2")
    assert after.error is None
    assert "4" in after.stdout


def test_close_is_deterministic_teardown() -> None:
    ws = InterpreterWorkspace(seed={"pool": [1]})
    ws.close()
    with pytest.raises(RuntimeError, match="closed"):
        ws.run("1 + 1")
    with pytest.raises(RuntimeError, match="closed"):
        ws.snapshot()
    with pytest.raises(RuntimeError, match="closed"):
        ws.restore(WorkspaceSnapshot(variables={}, skipped=()))
    # Idempotent close.
    ws.close()


def test_timeout_configured_through_worker() -> None:
    ws = InterpreterWorkspace(timeout_seconds=0.2)
    result = ws.run("while True:\n    pass")
    assert result.error is not None
