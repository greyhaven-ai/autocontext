from __future__ import annotations

import dataclasses
import json
import logging
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from autocontext.config import load_settings
from autocontext.config.settings import AppSettings
from autocontext.storage import SQLiteStore, artifact_store_from_settings

logger = logging.getLogger(__name__)


def _write_json_stdout(payload: object) -> None:
    typer.echo(json.dumps(payload))


def _write_json_stderr(message: str) -> None:
    typer.echo(json.dumps({"error": message}), err=True)


console = Console()


def _resolve_export_artifact_roots(
    *,
    settings: AppSettings,
    resolved_db: Path,
    runs_root: str | None,
    knowledge_root: str | None,
    skills_root: str | None,
    claude_skills_path: str | None,
) -> tuple[Path, Path, Path, Path]:
    """Resolve artifact roots that match the DB being exported.

    When exporting from an alternate DB path, default to the DB's workspace
    layout instead of silently mixing it with the current process settings.
    """
    default_runs_root = settings.runs_root
    default_knowledge_root = settings.knowledge_root
    default_skills_root = settings.skills_root
    default_claude_skills_path = settings.claude_skills_path

    using_default_db = resolved_db == settings.db_path
    if using_default_db:
        base_runs_root = default_runs_root
        base_knowledge_root = default_knowledge_root
        base_skills_root = default_skills_root
        base_claude_skills_path = default_claude_skills_path
    else:
        workspace_root = resolved_db.parent.parent if resolved_db.parent.name == "runs" else resolved_db.parent
        base_runs_root = workspace_root / "runs"
        base_knowledge_root = workspace_root / "knowledge"
        base_skills_root = workspace_root / "skills"
        base_claude_skills_path = workspace_root / ".claude" / "skills"

    return (
        Path(runs_root) if runs_root is not None else base_runs_root,
        Path(knowledge_root) if knowledge_root is not None else base_knowledge_root,
        Path(skills_root) if skills_root is not None else base_skills_root,
        Path(claude_skills_path) if claude_skills_path is not None else base_claude_skills_path,
    )


def export_training_data_cmd(
    run_id: str | None = typer.Option(None, "--run-id", help="Export a specific run"),
    scenario: str | None = typer.Option(None, "--scenario", help="Export all runs for a scenario"),
    all_runs: bool = typer.Option(False, "--all-runs", help="Required with --scenario to confirm multi-run export"),
    output: str = typer.Option("", "--output", help="Output JSONL file path"),
    include_matches: bool = typer.Option(False, "--include-matches", help="Include per-match records"),
    kept_only: bool = typer.Option(False, "--kept-only", help="Only export generations that advanced"),
    include_quarantined: bool = typer.Option(
        False,
        "--include-quarantined",
        help="Include generations scored under an unpromoted evaluator epoch (excluded by default)",
    ),
    db_path: str | None = typer.Option(None, "--db-path", help="Override database path"),
    runs_root: str | None = typer.Option(None, "--runs-root", help="Override runs root for artifact lookup"),
    knowledge_root: str | None = typer.Option(None, "--knowledge-root", help="Override knowledge root for playbooks and hints"),
    skills_root: str | None = typer.Option(None, "--skills-root", help="Override skills root for artifact lookup"),
    claude_skills_path: str | None = typer.Option(
        None,
        "--claude-skills-path",
        help="Override Claude skills path for artifact lookup",
    ),
) -> None:
    """Export strategy-level training data as JSONL."""

    from autocontext.training.export import export_training_data

    if not output:
        console.print("[red]--output is required[/red]")
        raise typer.Exit(code=1)

    if run_id is None and scenario is None:
        console.print("[red]Must specify either --run-id or --scenario --all-runs[/red]")
        raise typer.Exit(code=1)

    if scenario is not None and not all_runs and run_id is None:
        console.print("[red]Use --all-runs with --scenario to export all runs for a scenario[/red]")
        raise typer.Exit(code=1)

    settings = load_settings()
    resolved_db = Path(db_path) if db_path is not None else settings.db_path
    sqlite = SQLiteStore(resolved_db)
    resolved_runs_root, resolved_knowledge_root, resolved_skills_root, resolved_claude_skills_path = (
        _resolve_export_artifact_roots(
            settings=settings,
            resolved_db=resolved_db,
            runs_root=runs_root,
            knowledge_root=knowledge_root,
            skills_root=skills_root,
            claude_skills_path=claude_skills_path,
        )
    )

    artifacts = artifact_store_from_settings(
        settings,
        runs_root=resolved_runs_root,
        knowledge_root=resolved_knowledge_root,
        skills_root=resolved_skills_root,
        claude_skills_path=resolved_claude_skills_path,
    )

    output_path = Path(output)
    count = 0
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        for record in export_training_data(
            sqlite,
            artifacts,
            run_id=run_id,
            scenario=scenario,
            include_matches=include_matches,
            kept_only=kept_only,
            include_quarantined=include_quarantined,
        ):
            f.write(json.dumps(dataclasses.asdict(record)) + "\n")
            count += 1

    console.print(f"[green]Exported {count} record(s) to {output_path}[/green]")


def export_cmd(
    run_id_text: str | None = typer.Argument(None, help="Run id to export"),
    scenario: str = typer.Option("", "--scenario", help="Scenario to export"),
    run_id: str | None = typer.Option(None, "--run-id", help="Run id to export"),
    output: str = typer.Option(
        "",
        "--output",
        help=(
            "Output path: strategy JSON file (default: <scenario-or-run-id>_package.json) "
            "or pi-package directory (default: <scenario>-pi-package)"
        ),
    ),
    export_format: str = typer.Option("strategy", "--format", help="Export format: strategy or pi-package"),
    db_path: str | None = typer.Option(None, "--db-path", help="Override database path"),
    runs_root: str | None = typer.Option(None, "--runs-root", help="Override runs root"),
    knowledge_root: str | None = typer.Option(None, "--knowledge-root", help="Override knowledge root"),
    skills_root: str | None = typer.Option(None, "--skills-root", help="Override skills root"),
    claude_skills_path: str | None = typer.Option(None, "--claude-skills-path", help="Override Claude skills path"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Export a portable strategy package for a scenario."""
    from autocontext.knowledge.export import export_strategy_package
    from autocontext.mcp.tools import MtsToolContext

    settings = load_settings()
    resolved_db = Path(db_path) if db_path is not None else settings.db_path
    resolved_runs, resolved_knowledge, resolved_skills, resolved_claude = _resolve_export_artifact_roots(
        settings=settings,
        resolved_db=resolved_db,
        runs_root=runs_root,
        knowledge_root=knowledge_root,
        skills_root=skills_root,
        claude_skills_path=claude_skills_path,
    )

    sqlite = SQLiteStore(resolved_db)
    migrations_dir = Path(__file__).resolve().parents[2] / "migrations"
    sqlite.migrate(migrations_dir)
    artifacts = artifact_store_from_settings(
        settings,
        runs_root=resolved_runs,
        knowledge_root=resolved_knowledge,
        skills_root=resolved_skills,
        claude_skills_path=resolved_claude,
    )
    ctx = MtsToolContext.__new__(MtsToolContext)
    ctx.settings = settings
    ctx.sqlite = sqlite
    ctx.artifacts = artifacts

    source_run_id = run_id.strip() if run_id else None
    scenario_name = scenario.strip()
    if not scenario_name:
        source_run_id = source_run_id or ((run_id_text or "").strip() or None)
        if source_run_id is None:
            message = "--scenario or <run-id> is required"
            if json_output:
                _write_json_stderr(message)
            else:
                console.print(f"[red]{message}[/red]")
            raise typer.Exit(code=1)
        run_row = sqlite.get_run(source_run_id)
        if run_row is None:
            message = f"run '{source_run_id}' not found"
            if json_output:
                _write_json_stderr(message)
            else:
                console.print(f"[red]{message}[/red]")
            raise typer.Exit(code=1)
        scenario_name = str(run_row["scenario"])

    try:
        pkg = export_strategy_package(ctx, scenario_name, source_run_id=source_run_id)
    except ValueError as exc:
        if json_output:
            _write_json_stderr(str(exc))
        else:
            console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    normalized_format = export_format.strip().lower()
    if normalized_format not in {"strategy", "pi-package"}:
        message = "--format must be one of strategy, pi-package"
        if json_output:
            _write_json_stderr(message)
        else:
            console.print(f"[red]{message}[/red]")
        raise typer.Exit(code=1)

    if normalized_format == "pi-package":
        from autocontext.knowledge.pi_package import (
            build_pi_package,
            default_pi_package_output_dir,
            write_pi_package,
        )

        output_path = Path(output) if output else default_pi_package_output_dir(scenario_name)
        written = write_pi_package(build_pi_package(pkg), output_path)
        if json_output:
            _write_json_stdout(
                {
                    "scenario": scenario_name,
                    "format": normalized_format,
                    "output_path": str(output_path),
                    "file_count": len(written.files),
                    "files": [str(path.relative_to(output_path)) for path in written.files],
                }
            )
        else:
            console.print(f"[green]Exported {scenario_name} Pi package to {output_path}[/green]")
            console.print(f"[dim]files={len(written.files)} best_score={pkg.best_score:.4f}[/dim]")
        return

    output_stem = source_run_id or scenario_name
    output_path = Path(output) if output else Path(f"{output_stem}_package.json")
    pkg.to_file(output_path)

    if json_output:
        _write_json_stdout(
            {
                "scenario": scenario_name,
                "format": normalized_format,
                "output_path": str(output_path),
                "best_score": pkg.best_score,
                "lessons_count": len(pkg.lessons),
                "harness_count": len(pkg.harness),
            }
        )
    else:
        console.print(f"[green]Exported {scenario_name} package to {output_path}[/green]")
        console.print(f"[dim]best_score={pkg.best_score:.4f} lessons={len(pkg.lessons)} harness={len(pkg.harness)}[/dim]")


def import_package_cmd(
    package_file: str = typer.Argument(..., help="Path to the strategy package JSON file"),
    scenario: str | None = typer.Option(None, "--scenario", help="Override target scenario name"),
    conflict: str = typer.Option("merge", "--conflict", help="Conflict policy: overwrite, merge, or skip"),
    db_path: str | None = typer.Option(None, "--db-path", help="Override database path"),
    knowledge_root: str | None = typer.Option(None, "--knowledge-root", help="Override knowledge root"),
    skills_root: str | None = typer.Option(None, "--skills-root", help="Override skills root"),
    claude_skills_path: str | None = typer.Option(None, "--claude-skills-path", help="Override Claude skills path"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Import a strategy package into scenario knowledge."""
    from autocontext.knowledge.package import ConflictPolicy, StrategyPackage, import_strategy_package

    pkg_path = Path(package_file)
    if not pkg_path.exists():
        if json_output:
            _write_json_stderr(f"File not found: {pkg_path}")
        else:
            console.print(f"[red]File not found: {pkg_path}[/red]")
        raise typer.Exit(code=1)

    try:
        pkg = StrategyPackage.from_file(pkg_path)
    except Exception as exc:
        logger.debug("cli: caught Exception", exc_info=True)
        if json_output:
            _write_json_stderr(f"Invalid package file: {exc}")
        else:
            console.print(f"[red]Invalid package file: {exc}[/red]")
        raise typer.Exit(code=1) from exc

    if scenario:
        pkg = pkg.model_copy(update={"scenario_name": scenario})

    try:
        policy = ConflictPolicy(conflict)
    except ValueError as exc:
        if json_output:
            _write_json_stderr(f"Invalid conflict policy: {conflict!r}")
        else:
            console.print(f"[red]Invalid conflict policy: {conflict!r}. Use overwrite, merge, or skip.[/red]")
        raise typer.Exit(code=1) from exc

    settings = load_settings()
    resolved_db = Path(db_path) if db_path is not None else settings.db_path
    sqlite = SQLiteStore(resolved_db)
    migrations_dir = Path(__file__).resolve().parents[2] / "migrations"
    sqlite.migrate(migrations_dir)
    artifacts = artifact_store_from_settings(
        settings,
        knowledge_root=Path(knowledge_root) if knowledge_root else None,
        skills_root=Path(skills_root) if skills_root else None,
        claude_skills_path=Path(claude_skills_path) if claude_skills_path else None,
    )

    result = import_strategy_package(artifacts, pkg, sqlite=sqlite, conflict_policy=policy)

    if json_output:
        _write_json_stdout(
            {
                "scenario_name": result.scenario_name,
                "playbook_written": result.playbook_written,
                "hints_written": result.hints_written,
                "skill_written": result.skill_written,
                "harness_written": result.harness_written,
                "harness_skipped": result.harness_skipped,
                "conflict_policy": result.conflict_policy,
            }
        )
    else:
        table = Table(title=f"Import: {result.scenario_name}")
        table.add_column("Item", style="bold")
        table.add_column("Status")
        table.add_row("Playbook", "[green]written[/green]" if result.playbook_written else "[dim]skipped[/dim]")
        table.add_row("Hints", "[green]written[/green]" if result.hints_written else "[dim]skipped[/dim]")
        table.add_row("SKILL.md", "[green]written[/green]" if result.skill_written else "[dim]skipped[/dim]")
        if result.harness_written:
            table.add_row("Harness written", ", ".join(result.harness_written))
        if result.harness_skipped:
            table.add_row("Harness skipped", ", ".join(result.harness_skipped))
        table.add_row("Conflict policy", result.conflict_policy)
        console.print(table)


def register_package_commands(app: typer.Typer, command_console: Console) -> None:
    global console
    console = command_console
    app.command("export-training-data")(export_training_data_cmd)
    app.command("export")(export_cmd)
    app.command("import-package")(import_package_cmd)
