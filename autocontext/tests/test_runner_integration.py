from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from autocontext.config import AppSettings
from autocontext.context_bundles import ComponentKind, ContextBundleStore
from autocontext.extensions import HookEvents, HookResult
from autocontext.loop import GenerationRunner
from autocontext.loop.generation_pipeline import GenerationPipeline


def test_single_generation_persists_metadata_and_artifacts(tmp_path: Path) -> None:
    settings = AppSettings(
        db_path=tmp_path / "runs" / "autocontext.sqlite3",
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
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

    event_stream_path = tmp_path / "runs" / "events.ndjson"
    events = [json.loads(line) for line in event_stream_path.read_text(encoding="utf-8").splitlines()]
    run_started = next(event for event in events if event["event"] == "run_started")
    assert run_started["payload"] == {
        "run_id": run_id,
        "scenario": "grid_ctf",
        "target_generations": 1,
    }

    run_completed = next(event for event in events if event["event"] == "run_completed")
    assert run_completed["payload"] == {
        "run_id": run_id,
        "completed_generations": 1,
        "best_score": summary.best_score,
        "elo": summary.current_elo,
        "session_report_path": str(tmp_path / "knowledge" / "grid_ctf" / "reports" / f"{run_id}.md"),
        "dead_ends_found": 0,
    }

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
    assert "No playbook yet" in bundled_playbook.read_text(encoding="utf-8")

    # The coach's replacement is present, but only in the candidate namespace
    # until matched context trials confirm it.
    bundle_store = ContextBundleStore(tmp_path / "knowledge")
    candidate_digest = next(
        path.parent.name
        for path in (tmp_path / "knowledge" / "grid_ctf" / "context_bundles" / "candidates").glob("*/record.json")
        if '"lifecycle": "proposed"' in path.read_text(encoding="utf-8")
    )
    candidate = bundle_store.load_bundle("grid_ctf", candidate_digest)
    assert any("Strategy Updates" in component.content for component in candidate.components_of_kind(ComponentKind.PLAYBOOK))

    # Playbook should be a clean replacement (no ## generation_N headings)
    playbook_path = tmp_path / "knowledge" / "grid_ctf" / "playbook.md"
    assert playbook_path.exists()
    playbook_content = playbook_path.read_text(encoding="utf-8")
    assert "## generation_" not in playbook_content


def test_playbook_candidate_not_activated_on_rollback(tmp_path: Path) -> None:
    # Threshold 0.4: gen 1 advances (delta ≈ 0.5 from 0.0), gen 2 rolls back
    # (delta ≈ 0 since scores are similar).
    settings = AppSettings(
        db_path=tmp_path / "runs" / "autocontext.sqlite3",
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
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
    # Neither strategy result is matched evidence for a context mutation.
    assert "No playbook yet" in playbook_content

    # Skills should be a proper Skill with failure lesson for gen 2
    skill_dir = tmp_path / "skills" / "grid-ctf-ops"
    skills_content = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    assert "name: grid-ctf-ops" in skills_content
    assert "ROLLBACK" in skills_content
    # Bundled playbook is the active baseline, not an unconfirmed proposal.
    assert (skill_dir / "playbook.md").exists()


def test_enabled_context_promotion_fails_fast_without_hard_bounded_executor(tmp_path: Path) -> None:
    settings = AppSettings(
        db_path=tmp_path / "runs" / "autocontext.sqlite3",
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
        event_stream_path=tmp_path / "runs" / "events.ndjson",
        agent_provider="deterministic",
        matches_per_generation=1,
        context_bundle_promotion_enabled=True,
        context_bundle_promotion_min_screen_pairs=1,
        context_bundle_promotion_min_confirmation_pairs=2,
        context_bundle_promotion_max_confirmation_pairs=2,
        context_bundle_promotion_min_heldout_pairs=1,
        context_bundle_promotion_min_independent_blocks=2,
    )
    runner = GenerationRunner(settings)
    runner.migrate(Path(__file__).resolve().parents[1] / "migrations")
    scenario = runner._scenario("grid_ctf")
    with pytest.raises(RuntimeError, match="cannot prove killable"):
        runner._context_promotion_for_generation(
            "grid_ctf",
            scenario,
            "live-context-run",
            1,
        )


def test_context_promotion_rejects_auditor_matching_actual_live_proposer(tmp_path: Path) -> None:
    settings = AppSettings(
        db_path=tmp_path / "runs" / "autocontext.sqlite3",
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
        event_stream_path=tmp_path / "runs" / "events.ndjson",
        agent_provider="deterministic",
        context_bundle_promotion_enabled=True,
        campaign_auditor_enabled=True,
        campaign_auditor_provider="deterministic",
        campaign_auditor_model="claude-opus-5",
        campaign_auditor_proposer_provider="different-declared-route",
        campaign_auditor_proposer_model="different-model",
    )
    runner = GenerationRunner(settings)

    with pytest.raises(ValueError, match="every proposer route"):
        runner._context_promotion_for_generation(
            "grid_ctf",
            runner._scenario("grid_ctf"),
            "live-context-run",
            1,
        )


def test_generation_fails_fast_when_auditor_has_no_live_checkpoint_producer(tmp_path: Path) -> None:
    settings = AppSettings(
        db_path=tmp_path / "runs" / "autocontext.sqlite3",
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
        event_stream_path=tmp_path / "runs" / "events.ndjson",
        agent_provider="deterministic",
        context_bundle_promotion_enabled=False,
        campaign_auditor_enabled=True,
        campaign_auditor_provider="deterministic",
        campaign_auditor_model="independent-auditor",
    )
    runner = GenerationRunner(settings)

    with pytest.raises(ValueError, match="requires context_bundle_promotion_enabled for generation runs"):
        runner._context_promotion_for_generation(
            "grid_ctf",
            runner._scenario("grid_ctf"),
            "live-context-run",
            1,
        )


def test_context_proposer_routes_capture_actual_endpoint_identity(tmp_path: Path) -> None:
    from autocontext.loop.context_promotion_runtime import _proposer_routes
    from autocontext.storage.artifacts import ArtifactStore

    settings = AppSettings(
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
        coach_provider="openai",
        architect_provider="openai-compatible",
        coach_base_url="HTTPS://API.OPENAI.COM:443/v1/",
        architect_base_url="https://api.openai.com/v1",
        model_coach="shared-model",
        model_architect="shared-model",
    )
    artifacts = ArtifactStore(
        settings.runs_root,
        settings.knowledge_root,
        settings.skills_root,
        settings.claude_skills_path,
    )

    routes = _proposer_routes(
        settings,
        artifacts=artifacts,
        scenario_name="grid_ctf",
        generation_index=1,
    )

    assert len(routes) == 1
    assert routes[0].backend_identity == "endpoint:https://api.openai.com/v1"
    assert routes[0].model == "shared-model"


def test_context_promotion_auditor_checks_tier_resolved_proposer_model(tmp_path: Path) -> None:
    settings = AppSettings(
        db_path=tmp_path / "runs" / "autocontext.sqlite3",
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
        event_stream_path=tmp_path / "runs" / "events.ndjson",
        agent_provider="deterministic",
        tier_routing_enabled=True,
        tier_haiku_model="tier-fast",
        tier_sonnet_model="tier-coach",
        tier_opus_model="tier-architect",
        context_bundle_promotion_enabled=True,
        campaign_auditor_enabled=True,
        campaign_auditor_provider="deterministic",
        campaign_auditor_model="tier-fast",
        campaign_auditor_proposer_provider="different-declared-route",
        campaign_auditor_proposer_model="different-model",
    )
    runner = GenerationRunner(settings)

    with pytest.raises(ValueError, match="every proposer route"):
        runner._context_promotion_for_generation(
            "grid_ctf",
            runner._scenario("grid_ctf"),
            "live-context-run",
            1,
        )


def test_run_completed_omits_session_report_path_when_reports_disabled(tmp_path: Path) -> None:
    settings = AppSettings(
        db_path=tmp_path / "runs" / "autocontext.sqlite3",
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
        event_stream_path=tmp_path / "runs" / "events.ndjson",
        seed_base=2000,
        agent_provider="deterministic",
        matches_per_generation=2,
        session_reports_enabled=False,
    )
    runner = GenerationRunner(settings)
    migrations_dir = Path(__file__).resolve().parents[1] / "migrations"
    runner.migrate(migrations_dir)

    run_id = "test_run_no_report"
    summary = runner.run(scenario_name="grid_ctf", generations=1, run_id=run_id)

    event_stream_path = tmp_path / "runs" / "events.ndjson"
    events = [json.loads(line) for line in event_stream_path.read_text(encoding="utf-8").splitlines()]
    run_completed = next(event for event in events if event["event"] == "run_completed")
    assert run_completed["payload"] == {
        "run_id": run_id,
        "completed_generations": 1,
        "best_score": summary.best_score,
        "elo": summary.current_elo,
        "session_report_path": None,
        "dead_ends_found": 0,
    }


def test_resume_is_idempotent_for_existing_generation(tmp_path: Path) -> None:
    settings = AppSettings(
        db_path=tmp_path / "runs" / "autocontext.sqlite3",
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
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


def _reentry_runner(tmp_path: Path) -> GenerationRunner:
    settings = AppSettings(
        db_path=tmp_path / "runs" / "autocontext.sqlite3",
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
        event_stream_path=tmp_path / "runs" / "events.ndjson",
        audit_log_path=tmp_path / "runs" / "audit.ndjson",
        agent_provider="deterministic",
        matches_per_generation=1,
    )
    runner = GenerationRunner(settings)
    runner.migrate(Path(__file__).resolve().parents[1] / "migrations")
    return runner


def _event_names(tmp_path: Path) -> list[str]:
    lines = (tmp_path / "runs" / "events.ndjson").read_text(encoding="utf-8").splitlines()
    return [json.loads(line)["event"] for line in lines]


def _snapshot_rows(tmp_path: Path, run_id: str) -> int:
    conn = sqlite3.connect(tmp_path / "runs" / "autocontext.sqlite3")
    try:
        return int(conn.execute("SELECT COUNT(*) FROM knowledge_snapshots WHERE run_id = ?", (run_id,)).fetchone()[0])
    finally:
        conn.close()


@pytest.mark.slow
def test_rerun_of_a_completed_run_leaves_its_knowledge_and_reports_untouched(tmp_path: Path) -> None:
    # A no-op re-entry reran the post-run tail: it re-snapshotted the scenario's current playbook under
    # this run, appended snapshot rows and drift output, and rewrote the session report.
    runner = _reentry_runner(tmp_path)
    run_id = "done_run"
    first = runner.run(scenario_name="grid_ctf", generations=1, run_id=run_id)
    knowledge = tmp_path / "knowledge"

    def observed() -> dict[str, object]:
        return {
            "snapshot_rows": _snapshot_rows(tmp_path, run_id),
            "snapshot_playbook": (knowledge / "grid_ctf" / "snapshots" / run_id / "playbook.md").read_bytes(),
            "drift_snapshots": len(list((knowledge / "analytics" / "drift_snapshots").glob("*.json"))),
            "drift_warnings": len(list((knowledge / "analytics" / "drift_warnings").glob("*.json"))),
            "session_report": (knowledge / "grid_ctf" / "reports" / f"{run_id}.md").read_text(encoding="utf-8"),
            "events": _event_names(tmp_path),
        }

    before = observed()
    playbook = knowledge / "grid_ctf" / "playbook.md"
    playbook.write_text(playbook.read_text(encoding="utf-8") + "\nA later run changed this playbook.\n", encoding="utf-8")

    summary = runner.run(scenario_name="grid_ctf", generations=1, run_id=run_id)

    assert (summary.generations_executed, summary.best_score, summary.current_elo) == (0, first.best_score, first.current_elo)
    assert (before["snapshot_rows"], before["drift_snapshots"]) == (1, 1)
    assert observed() == before
    assert runner.sqlite.get_run(run_id)["status"] == "completed"


@pytest.mark.slow
@pytest.mark.parametrize(
    ("status", "generations", "executed"),
    [("completed", 2, 1), ("running", 1, 0), ("failed", 1, 0)],
)
def test_reentry_that_extends_or_recovers_a_run_still_runs_the_tail_once(
    tmp_path: Path, status: str, generations: int, executed: int
) -> None:
    # Only a run completed at its target on entry skips the tail. One extended past its target, or left
    # 'running' or 'failed' by a crash after its last generation, still needs its report, snapshot and receipt.
    runner = _reentry_runner(tmp_path)
    run_id = "tail_run"
    runner.run(scenario_name="grid_ctf", generations=1, run_id=run_id)
    if status == "running":
        runner.sqlite.mark_run_running(run_id)
    elif status == "failed":
        runner.sqlite.mark_run_failed(run_id)
    report = tmp_path / "knowledge" / "grid_ctf" / "reports" / f"{run_id}.md"
    report.unlink()

    summary = runner.run(scenario_name="grid_ctf", generations=generations, run_id=run_id)

    assert summary.generations_executed == executed
    assert runner.sqlite.get_run(run_id)["status"] == "completed"
    assert report.exists()
    assert _snapshot_rows(tmp_path, run_id) == 2
    assert _event_names(tmp_path).count("run_completed") == 2


@pytest.mark.slow
@pytest.mark.parametrize("cut_short", ["blocked_resume", "interrupted"])
def test_run_cut_short_after_its_last_generation_still_gets_its_tail_on_the_next_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cut_short: str
) -> None:
    # Recovery marked a 'running' run whose generations were all done 'completed' before its tail had run,
    # so the next resume took it for finished and never wrote its report, snapshot or run_completed.
    runner = _reentry_runner(tmp_path)
    run_id = "cut_run"
    report = tmp_path / "knowledge" / "grid_ctf" / "reports" / f"{run_id}.md"
    if cut_short == "blocked_resume":
        # A process killed after its last generation, then a resume that RUN_START refused.
        runner.run(scenario_name="grid_ctf", generations=1, run_id=run_id)
        report.unlink()
        runner.sqlite.mark_run_running(run_id)
        runner.hook_bus.on(HookEvents.RUN_START, lambda event: HookResult(block=True, reason="policy says no"))
        with pytest.raises(RuntimeError, match="blocked run_start"):
            runner.run(scenario_name="grid_ctf", generations=1, run_id=run_id)
    else:
        # Interrupted after generation 1 was persisted, before the run was marked completed.
        interrupts = [KeyboardInterrupt()]

        def interrupt_once(*_args: object) -> None:
            if interrupts:
                raise interrupts.pop()

        monkeypatch.setattr(runner, "_safe_generate_run_trace_artifacts", interrupt_once)
        with pytest.raises(KeyboardInterrupt):
            runner.run(scenario_name="grid_ctf", generations=1, run_id=run_id)
    assert runner.sqlite.get_run(run_id)["status"] == "failed"
    receipts = _event_names(tmp_path).count("run_completed")

    summary = _reentry_runner(tmp_path).run(scenario_name="grid_ctf", generations=1, run_id=run_id)

    assert summary.generations_executed == 0
    assert runner.sqlite.get_run(run_id)["status"] == "completed"
    assert report.exists()
    assert _event_names(tmp_path).count("run_completed") == receipts + 1


def _record_run_ends(runner: GenerationRunner) -> list[dict]:
    ends: list[dict] = []
    runner.hook_bus.on(HookEvents.RUN_END, lambda event: ends.append(dict(event.payload)))
    return ends


@pytest.mark.slow
@pytest.mark.parametrize("interrupted", [False, True])
def test_resumed_run_reports_its_durable_generation_total(tmp_path: Path, interrupted: bool) -> None:
    # run_completed, RUN_END and the checkpoint counted only this invocation's generations (2, not 3).
    runner = _reentry_runner(tmp_path)
    run_id = "total_run"
    runner.run(scenario_name="grid_ctf", generations=1, run_id=run_id)
    if interrupted:
        # A process killed during generation 2 of 3.
        runner.sqlite.mark_run_running(run_id, target_generations=3)
        runner.sqlite.upsert_generation(
            run_id, 2, mean_score=0.0, best_score=0.0, elo=1000.0, wins=0, losses=0, gate_decision="running", status="running"
        )
    ends = _record_run_ends(runner)

    summary = runner.run(scenario_name="grid_ctf", generations=3, run_id=run_id)

    assert summary.generations_executed == 2
    lines = (tmp_path / "runs" / "events.ndjson").read_text(encoding="utf-8").splitlines()
    receipts = [event["payload"] for event in map(json.loads, lines) if event["event"] == "run_completed"]
    assert receipts[-1]["completed_generations"] == 3
    assert [(end["status"], end["completed_generations"]) for end in ends] == [("completed", 3)]
    checkpoints = runner.artifacts.mutation_log.read("grid_ctf", mutation_types=["checkpoint"])
    assert [checkpoint.generation for checkpoint in checkpoints] == [1, 3]


@pytest.mark.slow
def test_failed_extension_reports_its_durable_generation_total_on_run_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = _reentry_runner(tmp_path)
    run_id = "failing_run"
    runner.run(scenario_name="grid_ctf", generations=1, run_id=run_id)
    original = GenerationPipeline.run_generation

    def fail_generation_3(self, ctx):  # type: ignore[no-untyped-def]
        if ctx.generation == 3:
            raise RuntimeError("injected generation 3 failure")
        return original(self, ctx)

    monkeypatch.setattr(GenerationPipeline, "run_generation", fail_generation_3)
    ends = _record_run_ends(runner)

    with pytest.raises(RuntimeError, match="injected generation 3 failure"):
        runner.run(scenario_name="grid_ctf", generations=3, run_id=run_id)

    assert [(end["status"], end["completed_generations"]) for end in ends] == [("failed", 2)]
