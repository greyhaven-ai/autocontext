from __future__ import annotations

import json
from pathlib import Path

from autocontext.config import AppSettings
from autocontext.loop import GenerationRunner


def test_single_generation_persists_metadata_and_artifacts(tmp_path: Path) -> None:
    settings = AppSettings(
        db_path=tmp_path / "runs" / "autocontext.sqlite3",
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        event_stream_path=tmp_path / "runs" / "events.ndjson",
        seed_base=2000,
        agent_provider="deterministic",
        matches_per_generation=2,
    )
    runner = GenerationRunner(settings)
    migrations_dir = Path(__file__).resolve().parents[1] / "migrations"
    runner.migrate(migrations_dir)

    run_id = "test_run_1"
    summary = runner.run(scenario_name="grid_ctf", generations=1, run_id=run_id)
    assert summary.run_id == run_id
    assert summary.generations_executed == 1

    metrics_path = tmp_path / "runs" / run_id / "generations" / "gen_1" / "metrics.json"
    replay_files = list((tmp_path / "runs" / run_id / "generations" / "gen_1" / "replays").glob("*.json"))
    analysis_path = tmp_path / "knowledge" / "grid_ctf" / "analysis" / "gen_1.md"
    assert metrics_path.exists()
    assert replay_files
    assert analysis_path.exists()
    payload = json.loads(metrics_path.read_text(encoding="utf-8"))
    assert payload["generation_index"] == 1
    assert "elo" in payload

    # Coach history should exist as audit trail
    coach_history_path = tmp_path / "knowledge" / "grid_ctf" / "coach_history.md"
    assert coach_history_path.exists()
    assert "generation_1" in coach_history_path.read_text(encoding="utf-8")

    # Skills should be a proper Claude Code Skill directory
    skill_dir = tmp_path / "skills" / "grid-ctf-ops"
    skill_path = skill_dir / "SKILL.md"
    assert skill_path.exists()
    skills_content = skill_path.read_text(encoding="utf-8")
    # Proper YAML frontmatter for Claude Code discovery
    assert "name: grid-ctf-ops" in skills_content
    assert "description:" in skills_content
    # Prescriptive lesson bullets, not metrics dump
    assert "## Operational Lessons" in skills_content
    assert "wins=" not in skills_content
    assert "elo=" not in skills_content
    # References to bundled resources (progressive disclosure)
    assert "playbook.md" in skills_content
    assert "knowledge/grid_ctf/" in skills_content
    # Playbook bundled alongside SKILL.md
    bundled_playbook = skill_dir / "playbook.md"
    assert bundled_playbook.exists()
    assert "Strategy Updates" in bundled_playbook.read_text(encoding="utf-8")

    # Playbook should be a clean replacement (no ## generation_N headings)
    playbook_path = tmp_path / "knowledge" / "grid_ctf" / "playbook.md"
    assert playbook_path.exists()
    playbook_content = playbook_path.read_text(encoding="utf-8")
    assert "## generation_" not in playbook_content


def test_playbook_not_updated_on_rollback(tmp_path: Path) -> None:
    # Threshold 0.4: gen 1 advances (delta ≈ 0.5 from 0.0), gen 2 rolls back
    # (delta ≈ 0 since scores are similar).
    settings = AppSettings(
        db_path=tmp_path / "runs" / "autocontext.sqlite3",
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        event_stream_path=tmp_path / "runs" / "events.ndjson",
        seed_base=2000,
        agent_provider="deterministic",
        matches_per_generation=2,
        backpressure_min_delta=0.4,
        max_retries=0,
    )
    runner = GenerationRunner(settings)
    migrations_dir = Path(__file__).resolve().parents[1] / "migrations"
    runner.migrate(migrations_dir)

    run_id = "rollback_run"
    summary = runner.run(scenario_name="grid_ctf", generations=2, run_id=run_id)
    assert summary.generations_executed == 2

    playbook_path = tmp_path / "knowledge" / "grid_ctf" / "playbook.md"
    assert playbook_path.exists()
    playbook_content = playbook_path.read_text(encoding="utf-8")
    # Gen 1 advances (first gen always does since previous_best=0), gen 2 rolls back.
    # Playbook should only reflect gen 1's content (not updated by gen 2).
    assert "Strategy Updates" in playbook_content

    # Skills should be a proper Skill with failure lesson for gen 2
    skill_dir = tmp_path / "skills" / "grid-ctf-ops"
    skills_content = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    assert "name: grid-ctf-ops" in skills_content
    assert "ROLLBACK" in skills_content
    # Bundled playbook should exist (from gen 1 advance)
    assert (skill_dir / "playbook.md").exists()


def test_resume_is_idempotent_for_existing_generation(tmp_path: Path) -> None:
    settings = AppSettings(
        db_path=tmp_path / "runs" / "autocontext.sqlite3",
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        event_stream_path=tmp_path / "runs" / "events.ndjson",
        agent_provider="deterministic",
    )
    runner = GenerationRunner(settings)
    migrations_dir = Path(__file__).resolve().parents[1] / "migrations"
    runner.migrate(migrations_dir)

    run_id = "resume_run"
    first = runner.run(scenario_name="grid_ctf", generations=1, run_id=run_id)
    second = runner.run(scenario_name="grid_ctf", generations=1, run_id=run_id)
    assert first.generations_executed == 1
    assert second.generations_executed == 0
