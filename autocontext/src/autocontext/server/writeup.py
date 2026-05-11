from __future__ import annotations

from typing import Any

from autocontext.analytics.artifact_rendering import render_markdown_document_html
from autocontext.analytics.run_trace import TraceStore
from autocontext.analytics.trace_reporter import ReportStore, TraceReporter
from autocontext.storage.artifacts import ArtifactStore
from autocontext.storage.sqlite_store import SQLiteStore


def generate_writeup(
    run_id: str,
    sqlite: SQLiteStore,
    artifacts: ArtifactStore,
) -> str:
    """Assemble a markdown writeup, preferring persisted trace-grounded reports."""
    analytics_root = artifacts.knowledge_root / "analytics"
    report_store = ReportStore(analytics_root)
    persisted = report_store.latest_writeup_for_run(run_id)
    if persisted is not None:
        return persisted.to_markdown()

    trace = TraceStore(analytics_root).load(f"trace-{run_id}")
    if trace is not None:
        writeup = TraceReporter().generate_writeup(trace)
        report_store.persist_writeup(writeup)
        return writeup.to_markdown()

    return _generate_legacy_writeup(run_id, sqlite, artifacts)


def generate_writeup_html(
    run_id: str,
    sqlite: SQLiteStore,
    artifacts: ArtifactStore,
) -> str:
    """Assemble an HTML writeup from the same structured source as Markdown."""
    analytics_root = artifacts.knowledge_root / "analytics"
    report_store = ReportStore(analytics_root)
    persisted = report_store.latest_writeup_for_run(run_id)
    if persisted is not None:
        return persisted.to_html()

    trace = TraceStore(analytics_root).load(f"trace-{run_id}")
    if trace is not None:
        writeup = TraceReporter().generate_writeup(trace)
        report_store.persist_writeup(writeup)
        return writeup.to_html()

    markdown = _generate_legacy_writeup(run_id, sqlite, artifacts)
    return render_markdown_document_html(f"Run Summary: {run_id}", markdown)


def _generate_legacy_writeup(
    run_id: str,
    sqlite: SQLiteStore,
    artifacts: ArtifactStore,
) -> str:
    """Assemble a markdown writeup from existing run artifacts."""
    # 1. Get run info
    with sqlite.connect() as conn:
        run_row = conn.execute(
            "SELECT run_id, scenario, target_generations, status, created_at "
            "FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    if not run_row:
        return f"# Run Summary: {run_id}\n\nNo run data found."

    run: dict[str, Any] = dict(run_row)
    scenario = run["scenario"]

    # 2. Get generation trajectory
    generations = sqlite.get_generation_trajectory(run_id)

    sections: list[str] = []
    sections.append(f"# Run Summary: {run_id}\n")
    sections.append(f"- **Scenario**: {scenario}")
    sections.append(f"- **Target generations**: {run['target_generations']}")
    sections.append(f"- **Status**: {run['status']}")
    sections.append(f"- **Created**: {run['created_at']}")
    sections.append("")

    # 3. Score trajectory section
    sections.append("## Score Trajectory\n")
    if generations:
        sections.append("| Gen | Best Score | Elo | Delta | Gate |")
        sections.append("|-----|-----------|-----|-------|------|")
        for gen in generations:
            sections.append(
                f"| {gen['generation_index']} "
                f"| {gen['best_score']:.2f} "
                f"| {gen['elo']:.0f} "
                f"| {gen['delta']:+.4f} "
                f"| {gen['gate_decision']} |"
            )
    else:
        sections.append("No completed generations.")
    sections.append("")

    # 4. Gate decisions summary
    sections.append("## Gate Decisions\n")
    if generations:
        for gen in generations:
            sections.append(f"- Generation {gen['generation_index']}: **{gen['gate_decision']}**")
    else:
        sections.append("No gate decisions recorded.")
    sections.append("")

    # 5. Best strategy excerpt
    strategy_history = sqlite.get_strategy_score_history(run_id)
    if strategy_history:
        best = max(strategy_history, key=lambda s: s["best_score"])
        sections.append("## Best Strategy\n")
        sections.append(f"Generation {best['generation_index']} (score: {best['best_score']:.2f}):\n")
        content = best["content"]
        if len(content) > 500:
            content = content[:500] + "..."
        sections.append(f"```\n{content}\n```")
        sections.append("")

    # 6. Playbook excerpt
    playbook = artifacts.read_playbook(scenario)
    if playbook and "No playbook yet" not in playbook:
        sections.append("## Playbook\n")
        excerpt = playbook[:1000] if len(playbook) > 1000 else playbook
        sections.append(excerpt)
        sections.append("")

    return "\n".join(sections)
