"""Tests for AC-189: Portable strategy package export/import.

Tests for StrategyPackage model, import logic, export wrapper, and
full roundtrip export→import cycle.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from autocontext.knowledge.export import SkillPackage
from autocontext.storage.artifacts import ArtifactStore
from autocontext.storage.sqlite_store import SQLiteStore

# ── Helpers ──────────────────────────────────────────────────────────────


def _make_skill_package(**overrides: object) -> SkillPackage:
    """Create a minimal SkillPackage for testing."""
    defaults = {
        "scenario_name": "grid_ctf",
        "display_name": "Grid Ctf",
        "description": "Capture the flag on a grid.",
        "playbook": "## Strategy\n\nBe aggressive.",
        "lessons": ["Scout borders early", "Defend flag with 2 units"],
        "best_strategy": {"aggression": 0.7, "defense": 0.3},
        "best_score": 0.85,
        "best_elo": 1650.0,
        "hints": "Focus on early scouting.",
        "harness": {"flag_placement": "def validate(s): return True"},
        "metadata": {"completed_runs": 5, "has_snapshot": True},
    }
    defaults.update(overrides)
    return SkillPackage(**defaults)


def _make_artifacts(tmp_path: Path) -> ArtifactStore:
    """Create ArtifactStore in a temp directory."""
    return ArtifactStore(
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
    )


def _make_sqlite(tmp_path: Path) -> SQLiteStore:
    db_path = tmp_path / "runs" / "autocontext.sqlite3"
    db = SQLiteStore(db_path)
    migrations_dir = Path(__file__).resolve().parents[1] / "migrations"
    if migrations_dir.exists():
        db.migrate(migrations_dir)
    return db


# ── StrategyPackage model tests ──────────────────────────────────────────


class TestStrategyPackageModel:
    def test_minimal_valid_package(self) -> None:
        from autocontext.knowledge.package import StrategyPackage

        pkg = StrategyPackage(scenario_name="grid_ctf")
        assert pkg.scenario_name == "grid_ctf"
        assert pkg.format_version == 1
        assert pkg.playbook == ""
        assert pkg.lessons == []
        assert pkg.best_strategy is None
        assert pkg.best_score == 0.0
        assert pkg.best_elo == 1500.0

    def test_full_roundtrip_json(self) -> None:
        from autocontext.knowledge.package import StrategyPackage

        pkg = StrategyPackage(
            scenario_name="grid_ctf",
            display_name="Grid CTF",
            description="Capture the flag.",
            playbook="## Strategy",
            lessons=["lesson 1"],
            best_strategy={"aggression": 0.8},
            best_score=0.9,
            best_elo=1700.0,
            hints="Be aggressive.",
            harness={"validator": "def check(s): pass"},
        )
        json_str = pkg.to_json()
        restored = StrategyPackage.from_json(json_str)
        assert restored.scenario_name == "grid_ctf"
        assert restored.best_strategy == {"aggression": 0.8}
        assert restored.harness == {"validator": "def check(s): pass"}

    def test_from_dict_rejects_missing_scenario(self) -> None:
        from autocontext.knowledge.package import StrategyPackage

        with pytest.raises(ValidationError):
            StrategyPackage.from_dict({})

    def test_from_file_roundtrip(self, tmp_path: Path) -> None:
        from autocontext.knowledge.package import StrategyPackage

        pkg = StrategyPackage(scenario_name="othello", playbook="## Othello strat")
        out = tmp_path / "pkg.json"
        pkg.to_file(out)
        assert out.exists()
        restored = StrategyPackage.from_file(out)
        assert restored.scenario_name == "othello"
        assert restored.playbook == "## Othello strat"

    def test_display_name_auto_generated(self) -> None:
        from autocontext.knowledge.package import StrategyPackage

        pkg = StrategyPackage(scenario_name="grid_ctf")
        assert pkg.display_name == "Grid Ctf"

    def test_metadata_defaults(self) -> None:
        from autocontext.knowledge.package import StrategyPackage

        pkg = StrategyPackage(scenario_name="grid_ctf")
        assert pkg.metadata.created_at != ""
        assert pkg.metadata.completed_runs == 0

    def test_format_version_field(self) -> None:
        from autocontext.knowledge.package import PACKAGE_FORMAT_VERSION, StrategyPackage

        pkg = StrategyPackage(scenario_name="grid_ctf")
        assert pkg.format_version == PACKAGE_FORMAT_VERSION


# ── from_skill_package tests ─────────────────────────────────────────────


class TestFromSkillPackage:
    def test_game_scenario_conversion(self) -> None:
        from autocontext.knowledge.package import StrategyPackage

        skill = _make_skill_package()
        pkg = StrategyPackage.from_skill_package(skill)
        assert pkg.scenario_name == "grid_ctf"
        assert pkg.playbook == "## Strategy\n\nBe aggressive."
        assert pkg.best_score == 0.85
        assert pkg.lessons == ["Scout borders early", "Defend flag with 2 units"]

    def test_agent_task_conversion(self) -> None:
        from autocontext.knowledge.package import StrategyPackage

        skill = _make_skill_package(
            task_prompt="Write a summary.",
            judge_rubric="Score 1-5 on clarity.",
        )
        pkg = StrategyPackage.from_skill_package(skill)
        assert pkg.task_prompt == "Write a summary."
        assert pkg.judge_rubric == "Score 1-5 on clarity."

    def test_harness_preserved(self) -> None:
        from autocontext.knowledge.package import StrategyPackage

        skill = _make_skill_package(harness={"v1": "code1", "v2": "code2"})
        pkg = StrategyPackage.from_skill_package(skill)
        assert pkg.harness == {"v1": "code1", "v2": "code2"}

    def test_source_run_id_propagated(self) -> None:
        from autocontext.knowledge.package import StrategyPackage

        skill = _make_skill_package()
        pkg = StrategyPackage.from_skill_package(skill, source_run_id="run_abc")
        assert pkg.metadata.source_run_id == "run_abc"

    def test_mts_version_populated(self) -> None:
        from autocontext import __version__
        from autocontext.knowledge.package import StrategyPackage

        skill = _make_skill_package()
        pkg = StrategyPackage.from_skill_package(skill)
        assert pkg.metadata.mts_version == __version__


# ── to_skill_package tests ───────────────────────────────────────────────


class TestToSkillPackage:
    def test_roundtrip_game_scenario(self) -> None:
        from autocontext.knowledge.package import StrategyPackage

        skill = _make_skill_package()
        pkg = StrategyPackage.from_skill_package(skill)
        restored = pkg.to_skill_package()
        assert restored.scenario_name == skill.scenario_name
        assert restored.playbook == skill.playbook
        assert restored.best_score == skill.best_score
        assert restored.harness == skill.harness

    def test_roundtrip_agent_task(self) -> None:
        from autocontext.knowledge.package import StrategyPackage

        skill = _make_skill_package(task_prompt="Do X.", judge_rubric="Rate Y.")
        pkg = StrategyPackage.from_skill_package(skill)
        restored = pkg.to_skill_package()
        assert restored.task_prompt == "Do X."
        assert restored.judge_rubric == "Rate Y."


# ── import_strategy_package tests ────────────────────────────────────────


class TestImportStrategyPackage:
    def test_import_writes_playbook_on_empty(self, tmp_path: Path) -> None:
        from autocontext.knowledge.package import ConflictPolicy, StrategyPackage, import_strategy_package

        artifacts = _make_artifacts(tmp_path)
        pkg = StrategyPackage(scenario_name="grid_ctf", playbook="## Imported playbook")
        result = import_strategy_package(artifacts, pkg, conflict_policy=ConflictPolicy.MERGE)
        assert result.playbook_written is True
        assert artifacts.read_playbook("grid_ctf") == "## Imported playbook\n"

    def test_import_merge_skips_existing_playbook(self, tmp_path: Path) -> None:
        from autocontext.knowledge.package import ConflictPolicy, StrategyPackage, import_strategy_package

        artifacts = _make_artifacts(tmp_path)
        artifacts.write_playbook("grid_ctf", "## Existing playbook")
        pkg = StrategyPackage(scenario_name="grid_ctf", playbook="## New playbook")
        result = import_strategy_package(artifacts, pkg, conflict_policy=ConflictPolicy.MERGE)
        assert result.playbook_written is False
        assert "Existing" in artifacts.read_playbook("grid_ctf")

    def test_import_overwrite_replaces_playbook(self, tmp_path: Path) -> None:
        from autocontext.knowledge.package import ConflictPolicy, StrategyPackage, import_strategy_package

        artifacts = _make_artifacts(tmp_path)
        artifacts.write_playbook("grid_ctf", "## Old playbook")
        pkg = StrategyPackage(scenario_name="grid_ctf", playbook="## Replacement")
        result = import_strategy_package(artifacts, pkg, conflict_policy=ConflictPolicy.OVERWRITE)
        assert result.playbook_written is True
        assert "Replacement" in artifacts.read_playbook("grid_ctf")

    def test_import_skip_never_overwrites(self, tmp_path: Path) -> None:
        from autocontext.knowledge.package import ConflictPolicy, StrategyPackage, import_strategy_package

        artifacts = _make_artifacts(tmp_path)
        artifacts.write_playbook("grid_ctf", "## Keep me")
        pkg = StrategyPackage(scenario_name="grid_ctf", playbook="## Drop me")
        result = import_strategy_package(artifacts, pkg, conflict_policy=ConflictPolicy.SKIP)
        assert result.playbook_written is False
        assert "Keep me" in artifacts.read_playbook("grid_ctf")

    def test_import_writes_hints(self, tmp_path: Path) -> None:
        from autocontext.knowledge.package import ConflictPolicy, StrategyPackage, import_strategy_package

        artifacts = _make_artifacts(tmp_path)
        pkg = StrategyPackage(scenario_name="grid_ctf", hints="Scout borders early.")
        result = import_strategy_package(artifacts, pkg, conflict_policy=ConflictPolicy.MERGE)
        assert result.hints_written is True
        assert "Scout borders" in artifacts.read_hints("grid_ctf")

    def test_import_merge_appends_hints(self, tmp_path: Path) -> None:
        from autocontext.knowledge.package import ConflictPolicy, StrategyPackage, import_strategy_package

        artifacts = _make_artifacts(tmp_path)
        artifacts.write_hints("grid_ctf", "Existing hint.")
        pkg = StrategyPackage(scenario_name="grid_ctf", hints="New hint.")
        result = import_strategy_package(artifacts, pkg, conflict_policy=ConflictPolicy.MERGE)
        assert result.hints_written is True
        content = artifacts.read_hints("grid_ctf")
        assert "Existing hint" in content
        assert "New hint" in content

    def test_import_writes_harness_validators(self, tmp_path: Path) -> None:
        from autocontext.knowledge.package import ConflictPolicy, StrategyPackage, import_strategy_package

        artifacts = _make_artifacts(tmp_path)
        pkg = StrategyPackage(
            scenario_name="grid_ctf",
            harness={"flag_check": "def validate(s): return True"},
        )
        result = import_strategy_package(artifacts, pkg, conflict_policy=ConflictPolicy.MERGE)
        assert "flag_check" in result.harness_written

    def test_import_merge_skips_existing_harness(self, tmp_path: Path) -> None:
        from autocontext.knowledge.package import ConflictPolicy, StrategyPackage, import_strategy_package

        artifacts = _make_artifacts(tmp_path)
        artifacts.write_harness("grid_ctf", "existing_validator", "def old(): pass")
        pkg = StrategyPackage(
            scenario_name="grid_ctf",
            harness={"existing_validator": "def new(): pass", "new_validator": "def fresh(): pass"},
        )
        result = import_strategy_package(artifacts, pkg, conflict_policy=ConflictPolicy.MERGE)
        assert "existing_validator" in result.harness_skipped
        assert "new_validator" in result.harness_written

    def test_import_overwrite_replaces_harness(self, tmp_path: Path) -> None:
        from autocontext.knowledge.package import ConflictPolicy, StrategyPackage, import_strategy_package

        artifacts = _make_artifacts(tmp_path)
        artifacts.write_harness("grid_ctf", "validator", "def old(): pass")
        pkg = StrategyPackage(
            scenario_name="grid_ctf",
            harness={"validator": "def new(): pass"},
        )
        result = import_strategy_package(artifacts, pkg, conflict_policy=ConflictPolicy.OVERWRITE)
        assert "validator" in result.harness_written
        source = artifacts.read_harness("grid_ctf", "validator")
        assert "new" in source

    def test_import_writes_skill_md(self, tmp_path: Path) -> None:
        from autocontext.knowledge.package import ConflictPolicy, StrategyPackage, import_strategy_package

        artifacts = _make_artifacts(tmp_path)
        pkg = StrategyPackage(
            scenario_name="grid_ctf",
            description="A CTF game.",
            playbook="## Strategy",
            lessons=["Lesson 1"],
        )
        result = import_strategy_package(artifacts, pkg, conflict_policy=ConflictPolicy.MERGE)
        assert result.skill_written is True
        skill_path = artifacts.skills_root / "grid-ctf-ops" / "SKILL.md"
        assert skill_path.exists()
        content = skill_path.read_text(encoding="utf-8")
        assert "Lesson 1" in content

    def test_import_skip_preserves_existing_skill_md(self, tmp_path: Path) -> None:
        from autocontext.knowledge.package import ConflictPolicy, StrategyPackage, import_strategy_package

        artifacts = _make_artifacts(tmp_path)
        skill_dir = artifacts.skills_root / "grid-ctf-ops"
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_path = skill_dir / "SKILL.md"
        skill_path.write_text("# Existing skill\n\n## Operational Lessons\n\n- Keep me\n", encoding="utf-8")

        pkg = StrategyPackage(
            scenario_name="grid_ctf",
            description="A CTF game.",
            lessons=["Imported lesson"],
        )
        result = import_strategy_package(artifacts, pkg, conflict_policy=ConflictPolicy.SKIP)
        assert result.skill_written is False
        assert skill_path.read_text(encoding="utf-8") == "# Existing skill\n\n## Operational Lessons\n\n- Keep me\n"

    def test_import_merge_keeps_existing_skill_and_adds_lessons(self, tmp_path: Path) -> None:
        from autocontext.knowledge.package import ConflictPolicy, StrategyPackage, import_strategy_package

        artifacts = _make_artifacts(tmp_path)
        skill_dir = artifacts.skills_root / "grid-ctf-ops"
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_path = skill_dir / "SKILL.md"
        skill_path.write_text(
            "---\nname: grid-ctf-knowledge\ndescription: existing\n---\n\n"
            "# Existing Skill\n\n## Operational Lessons\n\n- Keep me\n\n## Playbook\n\nOld\n",
            encoding="utf-8",
        )

        pkg = StrategyPackage(
            scenario_name="grid_ctf",
            description="A CTF game.",
            lessons=["Imported lesson"],
        )
        result = import_strategy_package(artifacts, pkg, conflict_policy=ConflictPolicy.MERGE)
        assert result.skill_written is True
        content = skill_path.read_text(encoding="utf-8")
        assert "# Existing Skill" in content
        assert "- Keep me" in content
        assert "- Imported lesson" in content

    def test_import_persists_snapshot_and_strategy_for_reexport(self, tmp_path: Path) -> None:
        from autocontext.knowledge.export import export_strategy_package
        from autocontext.knowledge.package import ConflictPolicy, StrategyPackage, import_strategy_package

        artifacts = _make_artifacts(tmp_path)
        sqlite = _make_sqlite(tmp_path)
        pkg = StrategyPackage(
            scenario_name="grid_ctf",
            playbook="## Imported playbook",
            best_strategy={"aggression": 0.9},
            best_score=0.88,
            best_elo=1700.0,
            metadata={"completed_runs": 5, "has_snapshot": True, "source_run_id": "run_abc"},
        )
        result = import_strategy_package(
            artifacts,
            pkg,
            sqlite=sqlite,
            conflict_policy=ConflictPolicy.MERGE,
        )
        assert result.snapshot_written is True
        assert sqlite.count_completed_runs("grid_ctf") == 1
        snapshot = sqlite.get_best_knowledge_snapshot("grid_ctf")
        assert snapshot is not None
        assert snapshot["best_score"] == pytest.approx(0.88)
        assert sqlite.get_best_competitor_output("grid_ctf") == '{"aggression": 0.9}'

        ctx = MagicMock()
        ctx.artifacts = artifacts
        ctx.sqlite = sqlite
        with patch("autocontext.knowledge.export.SCENARIO_REGISTRY", self._mock_registry()):
            exported = export_strategy_package(ctx, "grid_ctf")
        assert exported.best_strategy == {"aggression": 0.9}
        assert exported.best_score == pytest.approx(0.88)
        assert exported.best_elo == pytest.approx(1700.0)
        assert exported.metadata.completed_runs == 5
        assert exported.metadata.source_run_id == snapshot["run_id"]

    def test_import_result_reports_actions(self, tmp_path: Path) -> None:
        from autocontext.knowledge.package import ConflictPolicy, ImportResult, StrategyPackage, import_strategy_package

        artifacts = _make_artifacts(tmp_path)
        pkg = StrategyPackage(
            scenario_name="grid_ctf",
            playbook="## Playbook",
            hints="Hints here.",
            harness={"v1": "code"},
        )
        result = import_strategy_package(artifacts, pkg, conflict_policy=ConflictPolicy.MERGE)
        assert isinstance(result, ImportResult)
        assert result.scenario_name == "grid_ctf"
        assert result.conflict_policy == "merge"

    def test_import_empty_package_safe(self, tmp_path: Path) -> None:
        from autocontext.knowledge.package import ConflictPolicy, StrategyPackage, import_strategy_package

        artifacts = _make_artifacts(tmp_path)
        pkg = StrategyPackage(scenario_name="grid_ctf")
        result = import_strategy_package(artifacts, pkg, conflict_policy=ConflictPolicy.MERGE)
        assert result.playbook_written is False
        assert result.hints_written is False
        assert result.harness_written == []

    @staticmethod
    def _mock_registry() -> dict[str, object]:
        class ScenarioStub:
            def describe_rules(self) -> str:
                return "Rules"

        return {"grid_ctf": ScenarioStub}


# ── export_strategy_package tests ────────────────────────────────────────


class TestExportStrategyPackage:
    @pytest.fixture()
    def tool_ctx(self, tmp_path: Path) -> MagicMock:
        """Minimal MtsToolContext mock for export tests."""
        from autocontext.mcp.tools import MtsToolContext

        ctx = MagicMock(spec=MtsToolContext)
        ctx.artifacts = _make_artifacts(tmp_path)
        ctx.artifacts.write_playbook("grid_ctf", "## Test playbook")
        ctx.artifacts.write_hints("grid_ctf", "Test hints")
        ctx.sqlite = MagicMock()
        ctx.sqlite.get_best_knowledge_snapshot.return_value = {
            "best_score": 0.75, "best_elo": 1600.0, "run_id": "run_123",
        }
        ctx.sqlite.get_best_competitor_output.return_value = '{"aggression": 0.5}'
        ctx.sqlite.count_completed_runs.return_value = 3
        return ctx

    def _mock_registry(self) -> dict:
        scenario = MagicMock()
        scenario.describe_rules = MagicMock(return_value="Rules")
        # No agent task interface
        del scenario.get_task_prompt
        del scenario.get_rubric
        return {"grid_ctf": lambda: scenario}

    def test_export_produces_valid_package(self, tool_ctx: MagicMock) -> None:
        from autocontext.knowledge.package import StrategyPackage

        with patch("autocontext.knowledge.export.SCENARIO_REGISTRY", self._mock_registry()):
            from autocontext.knowledge.export import export_strategy_package
            pkg = export_strategy_package(tool_ctx, "grid_ctf")
        assert isinstance(pkg, StrategyPackage)
        assert pkg.scenario_name == "grid_ctf"

    def test_export_includes_format_version(self, tool_ctx: MagicMock) -> None:
        from autocontext.knowledge.package import PACKAGE_FORMAT_VERSION

        with patch("autocontext.knowledge.export.SCENARIO_REGISTRY", self._mock_registry()):
            from autocontext.knowledge.export import export_strategy_package
            pkg = export_strategy_package(tool_ctx, "grid_ctf")
        assert pkg.format_version == PACKAGE_FORMAT_VERSION

    def test_export_includes_mts_version(self, tool_ctx: MagicMock) -> None:
        from autocontext import __version__

        with patch("autocontext.knowledge.export.SCENARIO_REGISTRY", self._mock_registry()):
            from autocontext.knowledge.export import export_strategy_package
            pkg = export_strategy_package(tool_ctx, "grid_ctf")
        assert pkg.metadata.mts_version == __version__

    def test_export_includes_source_run_id(self, tool_ctx: MagicMock) -> None:
        with patch("autocontext.knowledge.export.SCENARIO_REGISTRY", self._mock_registry()):
            from autocontext.knowledge.export import export_strategy_package
            pkg = export_strategy_package(tool_ctx, "grid_ctf")
        assert pkg.metadata.source_run_id == "run_123"


# ── Full roundtrip tests ────────────────────────────────────────────────


class TestExportImportRoundtrip:
    def test_roundtrip_preserves_all_fields(self, tmp_path: Path) -> None:
        from autocontext.knowledge.package import StrategyPackage

        original = StrategyPackage(
            scenario_name="grid_ctf",
            display_name="Grid CTF",
            description="A game.",
            playbook="## Be aggressive",
            lessons=["Scout early", "Defend flag"],
            best_strategy={"aggression": 0.9},
            best_score=0.88,
            best_elo=1700.0,
            hints="Play fast.",
            harness={"check_flag": "def validate(s): return True"},
        )
        # Export to file
        pkg_file = tmp_path / "export.json"
        original.to_file(pkg_file)

        # Import from file
        restored = StrategyPackage.from_file(pkg_file)
        assert restored.scenario_name == original.scenario_name
        assert restored.playbook == original.playbook
        assert restored.lessons == original.lessons
        assert restored.best_strategy == original.best_strategy
        assert restored.hints == original.hints
        assert restored.harness == original.harness

    def test_roundtrip_harness_content_intact(self, tmp_path: Path) -> None:
        from autocontext.knowledge.package import ConflictPolicy, StrategyPackage, import_strategy_package

        harness_code = (
            "def validate_strategy(strategy, scenario):\n"
            "    if strategy['aggression'] > 1:\n"
            "        return False, ['too aggressive']\n"
            "    return True, []\n"
        )
        pkg = StrategyPackage(
            scenario_name="grid_ctf",
            harness={"aggression_check": harness_code},
        )
        pkg_file = tmp_path / "pkg.json"
        pkg.to_file(pkg_file)
        restored = StrategyPackage.from_file(pkg_file)

        # Import into artifacts
        artifacts = _make_artifacts(tmp_path)
        import_strategy_package(artifacts, restored, conflict_policy=ConflictPolicy.MERGE)

        # Read back harness source
        source = artifacts.read_harness("grid_ctf", "aggression_check")
        assert source == harness_code
