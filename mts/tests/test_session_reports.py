"""Tests for AR-5 Cross-Session Reports."""
from __future__ import annotations

import time
from pathlib import Path

from mts.config.settings import AppSettings, load_settings
from mts.scenarios.base import Observation
from mts.storage.artifacts import ArtifactStore

# ── Settings ───────────────────────────────────────────────────────────


class TestSessionReportSettings:
    def test_session_reports_enabled_defaults_true(self) -> None:
        settings = AppSettings()
        assert settings.session_reports_enabled is True

    def test_load_settings_reads_session_reports_env(self, monkeypatch: object) -> None:
        monkeypatch.setenv("MTS_SESSION_REPORTS_ENABLED", "false")  # type: ignore[attr-defined]
        settings = load_settings()
        assert settings.session_reports_enabled is False


# ── SessionReport dataclass ────────────────────────────────────────────


class TestSessionReport:
    def test_session_report_to_markdown(self) -> None:
        from mts.knowledge.report import SessionReport

        report = SessionReport(
            run_id="run_001",
            scenario="grid_ctf",
            start_score=0.3000,
            end_score=0.7500,
            start_elo=1000.0,
            end_elo=1150.0,
            total_generations=5,
            duration_seconds=185.0,
            gate_counts={"advance": 3, "retry": 1, "rollback": 1},
            top_improvements=[
                {"gen": 2, "delta": 0.2, "description": "Score improved to 0.5000"},
                {"gen": 4, "delta": 0.15, "description": "Score improved to 0.7500"},
            ],
            dead_ends_found=2,
            exploration_mode="linear",
        )
        md = report.to_markdown()

        assert "# Session Report: run_001" in md
        assert "grid_ctf" in md
        assert "3m 5s" in md
        assert "0.3000" in md
        assert "0.7500" in md
        assert "1000.0" in md
        assert "1150.0" in md
        assert "3 advances" in md
        assert "1 retries" in md
        assert "1 rollbacks" in md
        assert "| Gen | Delta | Description |" in md
        assert "2 dead ends identified" in md
        assert "linear" in md

    def test_session_report_empty_improvements(self) -> None:
        from mts.knowledge.report import SessionReport

        report = SessionReport(
            run_id="run_empty",
            scenario="othello",
            start_score=0.0,
            end_score=0.0,
            start_elo=1000.0,
            end_elo=1000.0,
            total_generations=0,
            duration_seconds=0.0,
        )
        md = report.to_markdown()

        assert "No significant improvements recorded." in md

    def test_session_report_duration_formatting(self) -> None:
        from mts.knowledge.report import SessionReport

        # Under 60 seconds -- no minutes prefix
        report_short = SessionReport(
            run_id="r1",
            scenario="s1",
            start_score=0.0,
            end_score=0.0,
            start_elo=1000.0,
            end_elo=1000.0,
            total_generations=0,
            duration_seconds=45.0,
        )
        md_short = report_short.to_markdown()
        assert "45s" in md_short
        assert "0m" not in md_short

        # Over 60 seconds -- minutes and seconds
        report_long = SessionReport(
            run_id="r2",
            scenario="s2",
            start_score=0.0,
            end_score=0.0,
            start_elo=1000.0,
            end_elo=1000.0,
            total_generations=0,
            duration_seconds=130.0,
        )
        md_long = report_long.to_markdown()
        assert "2m 10s" in md_long


# ── generate_session_report ────────────────────────────────────────────


class TestGenerateSessionReport:
    def test_generate_from_trajectory(self) -> None:
        from mts.knowledge.report import generate_session_report

        rows: list[dict[str, object]] = [
            {"generation_index": 1, "best_score": 0.3, "elo": 1000, "delta": 0.0, "gate_decision": "advance"},
            {"generation_index": 2, "best_score": 0.5, "elo": 1050, "delta": 0.2, "gate_decision": "advance"},
            {"generation_index": 3, "best_score": 0.7, "elo": 1100, "delta": 0.2, "gate_decision": "advance"},
        ]
        report = generate_session_report(
            run_id="run_traj",
            scenario="grid_ctf",
            trajectory_rows=rows,
            duration_seconds=60.0,
        )
        assert report.run_id == "run_traj"
        assert report.scenario == "grid_ctf"
        assert report.start_score == 0.3
        assert report.end_score == 0.7
        assert report.start_elo == 1000
        assert report.end_elo == 1100
        assert report.total_generations == 3
        assert report.duration_seconds == 60.0
        assert len(report.top_improvements) == 2  # two rows with delta > 0

    def test_generate_empty_trajectory(self) -> None:
        from mts.knowledge.report import generate_session_report

        report = generate_session_report(
            run_id="run_empty",
            scenario="othello",
            trajectory_rows=[],
            duration_seconds=10.0,
            dead_ends_found=3,
        )
        assert report.start_score == 0.0
        assert report.end_score == 0.0
        assert report.start_elo == 1000.0
        assert report.end_elo == 1000.0
        assert report.total_generations == 0
        assert report.dead_ends_found == 3

    def test_generate_gate_counts(self) -> None:
        from mts.knowledge.report import generate_session_report

        rows: list[dict[str, object]] = [
            {"generation_index": 1, "best_score": 0.3, "elo": 1000, "delta": 0.0, "gate_decision": "advance"},
            {"generation_index": 2, "best_score": 0.3, "elo": 1000, "delta": 0.0, "gate_decision": "retry"},
            {"generation_index": 3, "best_score": 0.3, "elo": 1000, "delta": 0.0, "gate_decision": "rollback"},
            {"generation_index": 4, "best_score": 0.5, "elo": 1050, "delta": 0.2, "gate_decision": "advance"},
            {"generation_index": 5, "best_score": 0.4, "elo": 1020, "delta": -0.1, "gate_decision": "rollback"},
        ]
        report = generate_session_report(
            run_id="run_gc",
            scenario="grid_ctf",
            trajectory_rows=rows,
        )
        assert report.gate_counts["advance"] == 2
        assert report.gate_counts["retry"] == 1
        assert report.gate_counts["rollback"] == 2


# ── ArtifactStore report methods ───────────────────────────────────────


class TestArtifactStoreReports:
    def _make_store(self, tmp_path: Path) -> ArtifactStore:
        return ArtifactStore(
            runs_root=tmp_path / "runs",
            knowledge_root=tmp_path / "knowledge",
            skills_root=tmp_path / "skills",
            claude_skills_path=tmp_path / ".claude" / "skills",
        )

    def test_write_read_session_report(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        content = "# Session Report: run_001\nTest content"
        store.write_session_report("grid_ctf", "run_001", content)
        result = store.read_latest_session_reports("grid_ctf", max_reports=2)
        assert "run_001" in result
        assert "Test content" in result

    def test_read_latest_reports_ordering(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        store.write_session_report("grid_ctf", "run_old", "# Old Report")
        # Ensure modification time differs
        time.sleep(0.05)
        store.write_session_report("grid_ctf", "run_mid", "# Mid Report")
        time.sleep(0.05)
        store.write_session_report("grid_ctf", "run_new", "# New Report")

        result = store.read_latest_session_reports("grid_ctf", max_reports=2)
        # Should contain the two most recent
        assert "New Report" in result
        assert "Mid Report" in result
        assert "Old Report" not in result


# ── Prompt bundle integration ──────────────────────────────────────────


def _obs() -> Observation:
    return Observation(narrative="test", state={}, constraints=[])


class TestPromptBundleReports:
    def test_prompt_bundle_includes_session_reports(self) -> None:
        from mts.prompts.templates import build_prompt_bundle

        bundle = build_prompt_bundle(
            scenario_rules="rules",
            strategy_interface="interface",
            evaluation_criteria="criteria",
            previous_summary="summary",
            observation=_obs(),
            current_playbook="playbook",
            available_tools="tools",
            session_reports="# Session Report: run_001\nSome content",
        )
        assert "Prior session reports:" in bundle.competitor
        assert "run_001" in bundle.competitor
        assert "Prior session reports:" in bundle.analyst

    def test_prompt_bundle_empty_reports_omitted(self) -> None:
        from mts.prompts.templates import build_prompt_bundle

        bundle = build_prompt_bundle(
            scenario_rules="rules",
            strategy_interface="interface",
            evaluation_criteria="criteria",
            previous_summary="summary",
            observation=_obs(),
            current_playbook="playbook",
            available_tools="tools",
            session_reports="",
        )
        assert "Prior session reports:" not in bundle.competitor
        assert "Prior session reports:" not in bundle.analyst
