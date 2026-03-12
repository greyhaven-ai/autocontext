from __future__ import annotations

import json
from pathlib import Path

import pytest

from autocontext.rlm.context_loader import ContextLoader
from autocontext.storage.artifacts import ArtifactStore
from autocontext.storage.sqlite_store import SQLiteStore


@pytest.fixture()
def store_pair(tmp_path: Path) -> tuple[ArtifactStore, SQLiteStore]:
    runs = tmp_path / "runs"
    knowledge = tmp_path / "knowledge"
    skills = tmp_path / "skills"
    claude_skills = tmp_path / ".claude" / "skills"
    artifacts = ArtifactStore(runs, knowledge, skills, claude_skills)

    db_path = tmp_path / "test.sqlite3"
    sqlite = SQLiteStore(db_path)
    migrations_dir = Path(__file__).resolve().parent.parent / "migrations"
    sqlite.migrate(migrations_dir)
    return artifacts, sqlite


class TestLoadForAnalyst:
    def test_populates_expected_variables(self, store_pair: tuple[ArtifactStore, SQLiteStore]) -> None:
        artifacts, sqlite = store_pair
        loader = ContextLoader(artifacts, sqlite)

        # Seed some data
        sqlite.create_run("r1", "grid_ctf", 3, "local")
        sqlite.upsert_generation("r1", 1, 0.5, 0.6, 1010.0, 2, 1, "advance", "completed")
        sqlite.insert_match("r1", 1, 1001, 0.55, True, "[]")
        sqlite.insert_match("r1", 1, 1002, 0.65, True, "[]")

        # Create replay and metrics artifacts
        gen_dir = artifacts.generation_dir("r1", 1)
        (gen_dir / "replays").mkdir(parents=True)
        (gen_dir / "replays" / "grid_ctf_1.json").write_text(
            json.dumps({"scenario": "grid_ctf", "timeline": []}), encoding="utf-8"
        )
        artifacts.write_json(gen_dir / "metrics.json", {"mean_score": 0.5, "best_score": 0.6})

        # Create playbook
        artifacts.append_markdown(
            artifacts.knowledge_root / "grid_ctf" / "playbook.md",
            "Keep defensive anchor.",
            heading="generation_1",
        )

        ctx = loader.load_for_analyst(
            "r1", "grid_ctf", 1,
            scenario_rules="Test rules",
            strategy_interface="Test interface",
            current_strategy={"aggression": 0.5},
        )

        assert "replays" in ctx.variables
        assert len(ctx.variables["replays"]) == 1
        assert "metrics_history" in ctx.variables
        assert len(ctx.variables["metrics_history"]) == 1
        assert "match_scores" in ctx.variables
        assert len(ctx.variables["match_scores"]) == 2
        assert "playbook" in ctx.variables
        assert "defensive" in ctx.variables["playbook"]
        assert ctx.variables["scenario_rules"] == "Test rules"
        assert ctx.variables["current_strategy"]["aggression"] == 0.5
        assert "prior_analyses" in ctx.variables
        assert "operational_lessons" in ctx.variables
        assert isinstance(ctx.variables["operational_lessons"], str)
        assert "replays" in ctx.summary
        assert "operational_lessons" in ctx.summary


class TestLoadForArchitect:
    def test_includes_existing_tools(self, store_pair: tuple[ArtifactStore, SQLiteStore]) -> None:
        artifacts, sqlite = store_pair
        loader = ContextLoader(artifacts, sqlite)

        sqlite.create_run("r1", "grid_ctf", 2, "local")

        # Create a tool file
        artifacts.persist_tools("grid_ctf", 1, [
            {"name": "threat_assessor", "description": "Risk estimator", "code": "def run(x): return x"},
        ])

        ctx = loader.load_for_architect("r1", "grid_ctf", 1, scenario_rules="Rules here")

        assert "existing_tools" in ctx.variables
        assert "threat_assessor" in ctx.variables["existing_tools"]
        assert "def run" in ctx.variables["existing_tools"]["threat_assessor"]
        assert ctx.variables["scenario_rules"] == "Rules here"
        assert "threat_assessor" in ctx.summary

    def test_empty_when_no_data(self, store_pair: tuple[ArtifactStore, SQLiteStore]) -> None:
        artifacts, sqlite = store_pair
        loader = ContextLoader(artifacts, sqlite)

        sqlite.create_run("r1", "othello", 1, "local")

        ctx = loader.load_for_architect("r1", "othello", 1)

        assert ctx.variables["existing_tools"] == {}
        assert ctx.variables["replays"] == []
        assert ctx.variables["metrics_history"] == []
        assert ctx.variables["match_scores"] == []
        assert "operational_lessons" in ctx.variables
        assert ctx.variables["operational_lessons"] == ""
        assert "operational_lessons" in ctx.summary
