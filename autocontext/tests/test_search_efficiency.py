"""Search stays fresh while avoiding per-scenario SQL and repeated file reads."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from autocontext.config import AppSettings
from autocontext.knowledge.search import _build_search_index, search_strategies
from autocontext.mcp.tools import MtsToolContext


@pytest.fixture
def ctx(tmp_path: Path) -> MtsToolContext:
    context = MtsToolContext(AppSettings(
        db_path=tmp_path / "runs.db", knowledge_root=tmp_path / "knowledge", skills_root=tmp_path / "skills",
        runs_root=tmp_path / "runs", claude_skills_path=tmp_path / "claude",
    ))
    context.sqlite.create_run("run", "grid_ctf", 3, "local")
    context.sqlite.mark_run_completed("run")
    return context


def test_search_opens_one_connection_and_reuses_files(ctx: MtsToolContext, monkeypatch: pytest.MonkeyPatch) -> None:
    connect = MagicMock(wraps=ctx.sqlite.connect)
    read = MagicMock(wraps=ctx.artifacts.read_playbook)
    monkeypatch.setattr(ctx.sqlite, "connect", connect)
    monkeypatch.setattr(ctx.artifacts, "read_playbook", read)
    first = search_strategies(ctx, "capture flag")
    assert first
    assert connect.call_count == read.call_count == 1
    assert search_strategies(ctx, "capture flag") == first
    assert connect.call_count == 2
    assert read.call_count == 1
    ctx.artifacts.write_playbook("grid_ctf", "quasar tactics")
    assert search_strategies(ctx, "quasar")
    assert read.call_count == 2


def test_external_edits_deletions_and_skill_lessons_invalidate(ctx: MtsToolContext) -> None:
    directory = ctx.artifacts._scenario_dir("grid_ctf")
    directory.mkdir(parents=True)
    hints = directory / "hints.md"
    hints.write_text("quasar")
    assert search_strategies(ctx, "quasar")
    hints.write_text("nebula")
    assert not search_strategies(ctx, "quasar")
    assert search_strategies(ctx, "nebula")
    hints.unlink()
    assert not search_strategies(ctx, "nebula")
    skill = ctx.artifacts._skill_dir("grid_ctf") / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("## Operational Lessons\n- quasar\n")
    assert search_strategies(ctx, "quasar")
    skill.write_text("## Operational Lessons\n- nebula\n")
    assert not search_strategies(ctx, "quasar")
    assert search_strategies(ctx, "nebula")


def test_database_changes_are_visible_after_warming_cache(ctx: MtsToolContext) -> None:
    assert _build_search_index(ctx)[0]["completed_runs"] == 1
    ctx.sqlite.create_run("run2", "grid_ctf", 3, "local")
    ctx.sqlite.mark_run_completed("run2")
    with ctx.sqlite.connection() as conn:
        conn.execute(
            "INSERT INTO knowledge_snapshots(scenario, run_id, best_score, best_elo, playbook_hash) VALUES (?, ?, ?, ?, ?)",
            ("grid_ctf", "run2", 0.9, 1700.0, "hash"),
        )
    entry = _build_search_index(ctx)[0]
    assert entry["completed_runs"] == 2
    assert entry["best_score"] == 0.9
    assert entry["best_elo"] == 1700.0
    with ctx.sqlite.connection() as conn:
        conn.execute("UPDATE runs SET status = 'running'")
    assert _build_search_index(ctx) == []


def test_empty_query_does_no_io(ctx: MtsToolContext, monkeypatch: pytest.MonkeyPatch) -> None:
    connect = MagicMock(side_effect=AssertionError("unexpected SQL"))
    monkeypatch.setattr(ctx.sqlite, "connect", connect)
    assert search_strategies(ctx, "the and") == []
    assert search_strategies(ctx, "flag", top_k=0) == []


def test_cache_does_not_cross_artifact_stores(ctx: MtsToolContext, tmp_path: Path) -> None:
    ctx.artifacts.write_playbook("grid_ctf", "quasar")
    assert search_strategies(ctx, "quasar")
    other = MtsToolContext(ctx.settings.model_copy(update={"knowledge_root": tmp_path / "other"}))
    assert not search_strategies(other, "quasar")


def test_structured_hint_state_changes_invalidate_cached_search(ctx: MtsToolContext) -> None:
    import json

    from autocontext.domain.hints import HintManager, HintVolumePolicy

    ctx.artifacts.write_hint_manager("grid_ctf", HintManager.from_hint_text("- quasar", policy=HintVolumePolicy()))
    assert search_strategies(ctx, "quasar")
    # Change only structured state; leave the old flat hints snapshot in place.
    state = ctx.artifacts._scenario_dir("grid_ctf") / "hint_state.json"
    state.write_text(json.dumps(HintManager.from_hint_text("- nebula", policy=HintVolumePolicy()).to_dict()))
    assert not search_strategies(ctx, "quasar")
    assert search_strategies(ctx, "nebula")
