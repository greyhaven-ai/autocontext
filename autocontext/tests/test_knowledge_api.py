"""Tests for the Strategy Knowledge API — export, search, solver, MCP tools, REST routes."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from autocontext.config import AppSettings
from autocontext.knowledge.export import SkillPackage, _clean_lessons, export_skill_package, list_solved_scenarios
from autocontext.knowledge.search import _keyword_score, _tokenize, search_strategies
from autocontext.mcp.tools import MtsToolContext, export_skill, list_solved
from autocontext.mcp.tools import search_strategies as mcp_search


def _make_settings(tmp_path: Path) -> AppSettings:
    return AppSettings(
        knowledge_root=tmp_path / "knowledge",
        runs_root=tmp_path / "runs",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
        db_path=tmp_path / "test.sqlite3",
    )


def _make_ctx(tmp_path: Path) -> MtsToolContext:
    settings = _make_settings(tmp_path)
    ctx = MtsToolContext(settings)
    # Apply migrations
    migrations_dir = Path(__file__).resolve().parents[1] / "migrations"
    if migrations_dir.exists():
        ctx.sqlite.migrate(migrations_dir)
    return ctx


# -- Lesson cleaning --


class TestCleanLessons:
    def test_removes_rollback_lines(self) -> None:
        raw = [
            "- Generation 3 ROLLBACK after score dropped",
            "- Keep aggression above 0.7 for flag captures",
        ]
        cleaned = _clean_lessons(raw)
        assert len(cleaned) == 1
        assert "aggression" in cleaned[0]

    def test_removes_raw_json_blobs(self) -> None:
        raw = [
            '- {"aggression": 0.8, "defense": 0.3, "path_bias": 0.5}',
            "- Use defensive positioning near base",
        ]
        cleaned = _clean_lessons(raw)
        assert len(cleaned) == 1
        assert "defensive" in cleaned[0]

    def test_strips_score_parentheticals(self) -> None:
        raw = [
            "- High aggression works best (score=0.7486, delta=-0.0161, threshold=0.005)",
        ]
        cleaned = _clean_lessons(raw)
        assert len(cleaned) == 1
        assert "score=" not in cleaned[0]
        assert "aggression" in cleaned[0]

    def test_empty_input(self) -> None:
        assert _clean_lessons([]) == []

    def test_preserves_clean_bullets(self) -> None:
        raw = [
            "- Balance aggression with defense",
            "- Prioritize flag capture over elimination",
        ]
        cleaned = _clean_lessons(raw)
        assert len(cleaned) == 2


# -- SkillPackage --


class TestSkillPackage:
    def test_to_dict_roundtrip(self) -> None:
        pkg = SkillPackage(
            scenario_name="grid_ctf",
            display_name="Grid Ctf",
            description="A test scenario",
            playbook="# Playbook",
            lessons=["lesson one", "lesson two"],
            best_strategy={"aggression": 0.8},
            best_score=0.95,
            best_elo=1600.0,
            hints="Use flanking",
            metadata={"completed_runs": 3},
        )
        d = pkg.to_dict()
        assert d["scenario_name"] == "grid_ctf"
        assert d["best_score"] == 0.95
        assert len(d["lessons"]) == 2
        assert d["best_strategy"]["aggression"] == 0.8

    def test_to_skill_markdown(self) -> None:
        pkg = SkillPackage(
            scenario_name="grid_ctf",
            display_name="Grid Ctf",
            description="A test scenario",
            playbook="# My Playbook",
            lessons=["lesson one"],
            best_strategy={"aggression": 0.8},
            best_score=0.95,
            best_elo=1600.0,
            hints="",
        )
        md = pkg.to_skill_markdown()
        assert "# Grid Ctf" in md
        assert "## Operational Lessons" in md
        assert "lesson one" in md
        assert "## Best Known Strategy" in md
        assert '"aggression"' in md
        assert "## Playbook" in md

    def test_to_skill_markdown_no_strategy(self) -> None:
        pkg = SkillPackage(
            scenario_name="test",
            display_name="Test",
            description="desc",
            playbook="content",
            lessons=[],
            best_strategy=None,
            best_score=0.0,
            best_elo=1500.0,
            hints="",
        )
        md = pkg.to_skill_markdown()
        assert "Best Known Strategy" not in md
        assert "No lessons yet" in md


# -- Export --


class TestExport:
    def test_export_no_data(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)
        pkg = export_skill_package(ctx, "grid_ctf")
        assert pkg.scenario_name == "grid_ctf"
        assert "No playbook yet" in pkg.playbook
        assert pkg.best_score == 0.0
        assert pkg.best_strategy is None

    def test_export_with_playbook(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)
        playbook_dir = tmp_path / "knowledge" / "grid_ctf"
        playbook_dir.mkdir(parents=True)
        (playbook_dir / "playbook.md").write_text("# Evolved Strategy\n\nUse flanking.", encoding="utf-8")
        pkg = export_skill_package(ctx, "grid_ctf")
        assert "Evolved Strategy" in pkg.playbook

    def test_export_unknown_scenario(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)
        with pytest.raises(ValueError, match="Unknown scenario"):
            export_skill_package(ctx, "nonexistent")

    def test_list_solved_empty(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)
        result = list_solved_scenarios(ctx)
        assert result == []

    def test_list_solved_with_completed_run(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)
        ctx.sqlite.create_run("run1", "grid_ctf", 3, "local")
        ctx.sqlite.mark_run_completed("run1")
        result = list_solved_scenarios(ctx)
        assert len(result) == 1
        assert result[0]["name"] == "grid_ctf"
        assert result[0]["completed_runs"] == 1


# -- Search --


class TestSearch:
    def test_tokenize_removes_stopwords(self) -> None:
        tokens = _tokenize("how to optimize resource allocation under constraints")
        assert "how" not in tokens
        assert "to" not in tokens
        assert "optimize" in tokens
        assert "resource" in tokens

    def test_keyword_score_basic(self) -> None:
        entry = {
            "name": "grid_ctf",
            "display_name": "Grid Ctf",
            "description": "A capture the flag grid game with resource allocation",
            "strategy_interface": "",
            "evaluation_criteria": "",
            "lessons": "",
            "playbook_excerpt": "",
            "hints": "",
        }
        score, reasons = _keyword_score(["resource", "allocation"], entry)
        assert score > 0
        assert len(reasons) > 0

    def test_keyword_score_no_match(self) -> None:
        entry = {
            "name": "othello",
            "display_name": "Othello",
            "description": "A board game about flipping discs",
        }
        score, reasons = _keyword_score(["quantum", "teleportation"], entry)
        assert score == 0

    def test_search_no_completed_runs(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)
        results = search_strategies(ctx, "resource optimization")
        assert results == []

    def test_search_with_completed_run(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)
        ctx.sqlite.create_run("run1", "grid_ctf", 3, "local")
        ctx.sqlite.mark_run_completed("run1")
        results = search_strategies(ctx, "capture flag grid")
        # grid_ctf should match since its description/name contains these terms
        assert len(results) >= 1
        assert results[0].scenario_name == "grid_ctf"
        assert results[0].relevance_score > 0

    def test_build_search_index_uses_capability_helpers(self, tmp_path: Path) -> None:
        from autocontext.knowledge.search import _build_search_index

        ctx = _make_ctx(tmp_path)
        ctx.sqlite.create_run("run1", "grid_ctf", 3, "local")
        ctx.sqlite.mark_run_completed("run1")

        with (
            patch("autocontext.knowledge.search.get_description", return_value="adapter description") as get_description,
            patch(
                "autocontext.knowledge.search.resolve_capabilities",
                return_value=SimpleNamespace(is_agent_task=True),
            ) as resolve_caps,
            patch("autocontext.knowledge.search.get_strategy_interface_safe", return_value=None) as get_iface,
            patch("autocontext.knowledge.search.get_evaluation_criteria", return_value="ignored criteria") as get_eval,
            patch("autocontext.knowledge.search.get_task_prompt_safe", return_value="adapter task prompt") as get_prompt,
            patch("autocontext.knowledge.search.get_rubric_safe", return_value="adapter rubric") as get_rubric,
        ):
            entries = _build_search_index(ctx)

        assert entries
        entry = next(e for e in entries if e["name"] == "grid_ctf")
        assert entry["description"] == "adapter description"
        assert entry["strategy_interface"] == ""
        assert entry["evaluation_criteria"] == ""
        assert entry["task_prompt"] == "adapter task prompt"
        assert entry["judge_rubric"] == "adapter rubric"
        get_description.assert_called()
        resolve_caps.assert_called()
        get_iface.assert_called()
        get_eval.assert_not_called()
        get_prompt.assert_called()
        get_rubric.assert_called()


# -- SQLite query methods --


class TestSqliteExtensions:
    def test_count_completed_runs(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)
        assert ctx.sqlite.count_completed_runs("grid_ctf") == 0
        ctx.sqlite.create_run("run1", "grid_ctf", 3, "local")
        assert ctx.sqlite.count_completed_runs("grid_ctf") == 0  # still running
        ctx.sqlite.mark_run_completed("run1")
        assert ctx.sqlite.count_completed_runs("grid_ctf") == 1

    def test_get_best_competitor_output_none(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)
        assert ctx.sqlite.get_best_competitor_output("grid_ctf") is None

    def test_get_best_competitor_output_with_data(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)
        ctx.sqlite.create_run("run1", "grid_ctf", 3, "local")
        ctx.sqlite.upsert_generation("run1", 1, 0.5, 0.6, 1500.0, 2, 1, "advance", "completed")
        ctx.sqlite.upsert_generation("run1", 2, 0.7, 0.85, 1520.0, 3, 0, "advance", "completed")
        ctx.sqlite.append_agent_output("run1", 1, "competitor", '{"aggression": 0.5}')
        ctx.sqlite.append_agent_output("run1", 2, "competitor", '{"aggression": 0.8}')
        result = ctx.sqlite.get_best_competitor_output("grid_ctf")
        assert result is not None
        parsed = json.loads(result)
        assert parsed["aggression"] == 0.8  # gen 2 had higher best_score


# -- MCP tool wrappers --


class TestMcpTools:
    def test_export_skill_tool(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)
        result = export_skill(ctx, "grid_ctf")
        assert result["scenario_name"] == "grid_ctf"

    def test_list_solved_tool(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)
        result = list_solved(ctx)
        assert isinstance(result, list)

    def test_search_strategies_tool(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)
        result = mcp_search(ctx, "grid tactics")
        assert isinstance(result, list)


# -- REST API --


class TestRestApi:
    def test_list_solved_endpoint(self) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from autocontext.server.knowledge_api import router

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        resp = client.get("/api/knowledge/scenarios")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_export_unknown_scenario(self) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from autocontext.server.knowledge_api import router

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        resp = client.get("/api/knowledge/export/nonexistent_xyz")
        assert resp.status_code == 404

    def test_search_endpoint(self) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from autocontext.server.knowledge_api import router

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        resp = client.post("/api/knowledge/search", json={"query": "grid capture"})
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_solve_endpoint(self) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from autocontext.server.knowledge_api import router

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        resp = client.post("/api/knowledge/solve", json={"description": "test game", "generations": 1})
        assert resp.status_code == 200
        data = resp.json()
        assert "job_id" in data
        assert data["status"] == "pending"

    def test_solve_status_not_found(self) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from autocontext.server.knowledge_api import router

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        resp = client.get("/api/knowledge/solve/nonexistent_job")
        assert resp.status_code == 404
