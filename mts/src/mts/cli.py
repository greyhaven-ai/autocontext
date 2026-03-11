from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
from pathlib import Path

import typer
import uvicorn
from rich.console import Console
from rich.table import Table

from mts.config import load_settings
from mts.config.presets import VALID_PRESET_NAMES
from mts.config.settings import AppSettings
from mts.loop.generation_runner import GenerationRunner
from mts.storage import SQLiteStore

app = typer.Typer(help="MTS control-plane CLI")
console = Console()

_PRESET_HELP = f"Apply a named preset ({', '.join(sorted(VALID_PRESET_NAMES))}). Overrides MTS_PRESET env var."


def _apply_preset_env(preset: str | None) -> None:
    """Set MTS_PRESET env var from CLI flag so load_settings() picks it up."""
    if preset is not None:
        os.environ["MTS_PRESET"] = preset


def _runner(preset: str | None = None) -> GenerationRunner:
    _apply_preset_env(preset)
    settings = load_settings()
    runner = GenerationRunner(settings)
    runner.migrate(Path(__file__).resolve().parents[2] / "migrations")
    return runner


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


def _get_custom_scenarios_dir() -> Path:
    """Return the default directory for scaffolded custom scenarios."""
    return Path("knowledge") / "_custom_scenarios"


@app.command()
def run(
    scenario: str = typer.Option("grid_ctf", "--scenario"),
    gens: int = typer.Option(1, "--gens", min=1),
    run_id: str | None = typer.Option(None, "--run-id"),
    serve: bool = typer.Option(False, "--serve", help="Start interactive server alongside generation loop"),
    port: int = typer.Option(8000, "--port", help="Server port (only used with --serve)"),
    preset: str | None = typer.Option(None, "--preset", help=_PRESET_HELP),
) -> None:
    """Run generation loop."""

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if preset:
        console.print(f"[dim]Active preset: {preset}[/dim]")

    if serve:
        from mts.loop.controller import LoopController
        from mts.server.app import create_app

        runner = _runner(preset)
        controller = LoopController()
        runner.controller = controller

        def _loop_target() -> None:
            runner.run(scenario_name=scenario, generations=gens, run_id=run_id)

        loop_thread = threading.Thread(target=_loop_target, daemon=True)
        loop_thread.start()

        interactive_app = create_app(controller=controller, events=runner.events)
        console.print(f"[green]Interactive server started on port {port}[/green]")
        console.print(f"[dim]Connect TUI: cd tui && bun run start -- --url ws://localhost:{port}/ws/interactive[/dim]")
        uvicorn.run(interactive_app, host="127.0.0.1", port=int(port), log_level="info")
    else:
        summary = _runner(preset).run(scenario_name=scenario, generations=gens, run_id=run_id)
        table = Table(title="MTS Run Summary")
        table.add_column("Run ID")
        table.add_column("Scenario")
        table.add_column("Generations")
        table.add_column("Best Score")
        table.add_column("Elo")
        table.add_row(
            summary.run_id,
            summary.scenario,
            str(summary.generations_executed),
            f"{summary.best_score:.4f}",
            f"{summary.current_elo:.2f}",
        )
        console.print(table)


@app.command()
def resume(run_id: str = typer.Argument(...), scenario: str = typer.Option("grid_ctf"), gens: int = typer.Option(1)) -> None:
    """Resume an existing run idempotently."""

    summary = _runner().run(scenario_name=scenario, generations=gens, run_id=run_id)
    console.print(f"Resumed {summary.run_id} with {summary.generations_executed} executed generation(s).")


@app.command()
def replay(run_id: str = typer.Argument(...), generation: int = typer.Option(1, "--generation")) -> None:
    """Print replay JSON for a generation."""

    settings = load_settings()
    replay_dir = settings.runs_root / run_id / "generations" / f"gen_{generation}" / "replays"
    replay_files = sorted(replay_dir.glob("*.json"))
    if not replay_files:
        raise typer.BadParameter(f"no replay files found under {replay_dir}")
    payload = json.loads(replay_files[0].read_text(encoding="utf-8"))
    console.print_json(json.dumps(payload))


@app.command()
def benchmark(scenario: str = typer.Option("grid_ctf"), runs: int = typer.Option(3, "--runs", min=1)) -> None:
    """Run repeated one-generation trials for quick benchmarking."""

    runner = _runner()
    scores: list[float] = []
    for _ in range(runs):
        summary = runner.run(scenario_name=scenario, generations=1)
        scores.append(summary.best_score)
    mean_score = sum(scores) / len(scores)
    console.print(f"benchmark scenario={scenario} runs={runs} mean_score={mean_score:.4f}")


@app.command("list")
def list_runs() -> None:
    """List recent runs."""

    settings = load_settings()
    store = SQLiteStore(settings.db_path)
    with store.connect() as conn:
        rows = conn.execute(
            "SELECT run_id, scenario, target_generations, executor_mode, status, created_at "
            "FROM runs ORDER BY created_at DESC LIMIT 20"
        ).fetchall()
    table = Table(title="Recent Runs")
    table.add_column("Run ID")
    table.add_column("Scenario")
    table.add_column("Target Gens")
    table.add_column("Executor")
    table.add_column("Status")
    table.add_column("Created At")
    for row in rows:
        table.add_row(
            row["run_id"],
            row["scenario"],
            str(row["target_generations"]),
            row["executor_mode"],
            row["status"],
            row["created_at"],
        )
    console.print(table)


@app.command()
def status(run_id: str = typer.Argument(...)) -> None:
    """Show generation status for a run."""

    settings = load_settings()
    store = SQLiteStore(settings.db_path)
    with store.connect() as conn:
        rows = conn.execute(
            "SELECT generation_index, mean_score, best_score, elo, wins, losses, gate_decision, status "
            "FROM generations WHERE run_id = ? ORDER BY generation_index ASC",
            (run_id,),
        ).fetchall()
    table = Table(title=f"Run Status: {run_id}")
    table.add_column("Gen")
    table.add_column("Mean")
    table.add_column("Best")
    table.add_column("Elo")
    table.add_column("W")
    table.add_column("L")
    table.add_column("Gate")
    table.add_column("Status")
    for row in rows:
        table.add_row(
            str(row["generation_index"]),
            f"{row['mean_score']:.4f}",
            f"{row['best_score']:.4f}",
            f"{row['elo']:.2f}",
            str(row["wins"]),
            str(row["losses"]),
            row["gate_decision"],
            row["status"],
        )
    console.print(table)


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8000, "--port"),
) -> None:
    """Serve dashboard API and websocket stream."""

    uvicorn.run("mts.server.app:app", host=host, port=port, reload=False)


@app.command()
def ecosystem(
    scenario: str = typer.Option("grid_ctf", "--scenario"),
    cycles: int = typer.Option(3, "--cycles", min=1),
    gens_per_cycle: int = typer.Option(3, "--gens-per-cycle", min=1),
    provider_a: str = typer.Option("anthropic", "--provider-a"),
    provider_b: str = typer.Option("agent_sdk", "--provider-b"),
    rlm_a: bool = typer.Option(True, "--rlm-a/--no-rlm-a"),
    rlm_b: bool = typer.Option(False, "--rlm-b/--no-rlm-b"),
) -> None:
    """Run ecosystem loop alternating provider modes across cycles."""

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from mts.loop.ecosystem_runner import EcosystemConfig, EcosystemPhase, EcosystemRunner

    settings = load_settings()
    phases = [
        EcosystemPhase(provider=provider_a, rlm_enabled=rlm_a, generations=gens_per_cycle),
        EcosystemPhase(provider=provider_b, rlm_enabled=rlm_b, generations=gens_per_cycle),
    ]
    config = EcosystemConfig(scenario=scenario, cycles=cycles, gens_per_cycle=gens_per_cycle, phases=phases)
    runner = EcosystemRunner(settings, config)
    runner.migrate(Path(__file__).resolve().parents[2] / "migrations")
    summary = runner.run()

    table = Table(title="Ecosystem Summary")
    table.add_column("Run ID")
    table.add_column("Scenario")
    table.add_column("Provider")
    table.add_column("Gens")
    table.add_column("Best Score")
    table.add_column("Elo")
    for rs in summary.run_summaries:
        with SQLiteStore(settings.db_path).connect() as conn:
            row = conn.execute("SELECT agent_provider FROM runs WHERE run_id = ?", (rs.run_id,)).fetchone()
        provider_label = row["agent_provider"] if row else "?"
        table.add_row(
            rs.run_id,
            rs.scenario,
            provider_label,
            str(rs.generations_executed),
            f"{rs.best_score:.4f}",
            f"{rs.current_elo:.2f}",
        )
    console.print(table)

    trajectory = summary.score_trajectory()
    traj_table = Table(title="Score Trajectory")
    traj_table.add_column("Run ID")
    traj_table.add_column("Best Score")
    for run_id, score in trajectory:
        traj_table.add_row(run_id, f"{score:.4f}")
    console.print(traj_table)


@app.command()
def tui(
    port: int = typer.Option(8000, "--port", help="Server port"),
) -> None:
    """Launch interactive TUI (starts server + Ink terminal UI)."""

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    from mts.loop.controller import LoopController
    from mts.loop.events import EventStreamEmitter
    from mts.server.app import create_app
    from mts.server.run_manager import RunManager

    settings = load_settings()
    controller = LoopController()
    events = EventStreamEmitter(settings.event_stream_path)
    run_manager = RunManager(controller, events, settings)

    interactive_app = create_app(controller=controller, events=events, run_manager=run_manager)

    # Locate the TUI directory
    tui_dir = Path(__file__).resolve().parents[3] / "tui"
    bun_path = shutil.which("bun")
    has_tui = tui_dir.exists() and (tui_dir / "package.json").exists() and bun_path is not None

    if has_tui and bun_path is not None:
        # Start Ink TUI as a child process inheriting the TTY
        ws_url = f"ws://localhost:{port}/ws/interactive"
        tui_proc = subprocess.Popen(
            [bun_path, "run", "start", "--", "--url", ws_url],
            cwd=str(tui_dir),
            stdin=None,
            stdout=None,
            stderr=None,
            env={**os.environ},
        )
        console.print(f"[green]TUI started (PID {tui_proc.pid})[/green]")
    else:
        tui_proc = None
        if not tui_dir.exists():
            console.print("[yellow]TUI directory not found. Starting server only.[/yellow]")
        elif not bun_path:
            console.print("[yellow]bun not found. Starting server only.[/yellow]")
        console.print(f"[dim]Connect manually: cd tui && bun run start -- --url ws://localhost:{port}/ws/interactive[/dim]")

    console.print(f"[green]Interactive server on port {port}[/green]")
    console.print("[dim]Use /run <scenario> <gens> in the TUI to start a run[/dim]")

    try:
        uvicorn.run(interactive_app, host="127.0.0.1", port=int(port), log_level="info")
    finally:
        if tui_proc and tui_proc.poll() is None:
            tui_proc.terminate()


@app.command("ab-test")
def ab_test(
    scenario: str = typer.Option("grid_ctf", "--scenario", help="Scenario to test"),
    baseline: str = typer.Option(
        "MTS_RLM_ENABLED=false", "--baseline", help="Comma-separated KEY=VALUE env overrides for baseline",
    ),
    treatment: str = typer.Option(
        "MTS_RLM_ENABLED=true", "--treatment", help="Comma-separated KEY=VALUE env overrides for treatment",
    ),
    runs: int = typer.Option(5, "--runs", min=1, help="Runs per condition"),
    gens: int = typer.Option(3, "--gens", min=1, help="Generations per run"),
    seed: int = typer.Option(42, "--seed", help="Random seed for condition ordering"),
) -> None:
    """Run paired A/B test comparing two MTS configurations."""
    from mts.evaluation.ab_runner import ABTestConfig, ABTestRunner
    from mts.evaluation.ab_stats import mcnemar_test

    def _parse_env(env_str: str) -> dict[str, str]:
        result: dict[str, str] = {}
        for pair in env_str.split(","):
            pair = pair.strip()
            if "=" in pair:
                k, v = pair.split("=", 1)
                result[k.strip()] = v.strip()
        return result

    baseline_env = _parse_env(baseline)
    treatment_env = _parse_env(treatment)

    config = ABTestConfig(
        scenario=scenario,
        baseline_env=baseline_env,
        treatment_env=treatment_env,
        runs_per_condition=runs,
        generations_per_run=gens,
        seed=seed,
    )

    console.print(f"[bold]A/B Test: {scenario}[/bold]")
    console.print(f"  Baseline:  {baseline_env}")
    console.print(f"  Treatment: {treatment_env}")
    console.print(f"  Runs: {runs}, Gens: {gens}, Seed: {seed}")
    console.print()

    runner = ABTestRunner(config)
    result = runner.run()

    # Results table
    table = Table(title="A/B Test Results")
    table.add_column("Run", justify="right")
    table.add_column("Baseline Score", justify="right")
    table.add_column("Treatment Score", justify="right")
    table.add_column("Winner")
    for i, (b, t) in enumerate(
        zip(result.baseline_scores, result.treatment_scores, strict=True),
    ):
        winner = "Treatment" if t > b else ("Baseline" if b > t else "Tie")
        table.add_row(str(i), f"{b:.4f}", f"{t:.4f}", winner)
    console.print(table)

    console.print(f"\n[bold]Mean delta:[/bold] {result.mean_delta():+.4f}")
    console.print(f"[bold]Treatment wins:[/bold] {result.treatment_wins()}")
    console.print(f"[bold]Baseline wins:[/bold] {result.baseline_wins()}")

    # McNemar's test
    threshold = 0.5
    baseline_passed = [s >= threshold for s in result.baseline_scores]
    treatment_passed = [s >= threshold for s in result.treatment_scores]
    if any(baseline_passed) or any(treatment_passed):
        stats = mcnemar_test(baseline_passed=baseline_passed, treatment_passed=treatment_passed)
        console.print(f"\n[bold]McNemar's p-value:[/bold] {stats.p_value:.4f}")
        if stats.significant:
            console.print("[green]Result is statistically significant (p < 0.05)[/green]")
        else:
            console.print("[yellow]Result is not statistically significant[/yellow]")


@app.command("mcp-serve")
def mcp_serve() -> None:
    """Start MTS MCP server on stdio for Claude Code integration."""

    try:
        from mts.mcp.server import run_server
    except ImportError:
        console.print("[red]MCP dependencies not installed. Run: uv sync --extra mcp[/red]")
        raise typer.Exit(code=1) from None
    run_server()


@app.command()
def train(
    scenario: str = typer.Option("grid_ctf", "--scenario", help="Scenario to train on"),
    data: str = typer.Option("training_data.jsonl", "--data", help="Path to JSONL training data"),
    time_budget: int = typer.Option(300, "--time-budget", help="Training time budget in seconds"),
) -> None:
    """Train an MLX model to distill strategy knowledge (requires MLX)."""

    from mts.training import HAS_MLX

    if not HAS_MLX:
        console.print("[red]MLX is not installed. Install with: uv sync --group dev --extra mlx[/red]")
        raise typer.Exit(code=1)
    console.print(
        "[yellow]The distillation scaffold is installed, but the end-to-end training runner "
        "is not wired yet. Use the exported data path directly until the runner lands.[/yellow]"
    )
    console.print(f"[dim]scenario={scenario} data={data} budget={time_budget}s[/dim]")
    raise typer.Exit(code=2)


@app.command("export-training-data")
def export_training_data_cmd(
    run_id: str | None = typer.Option(None, "--run-id", help="Export a specific run"),
    scenario: str | None = typer.Option(None, "--scenario", help="Export all runs for a scenario"),
    all_runs: bool = typer.Option(False, "--all-runs", help="Required with --scenario to confirm multi-run export"),
    output: str = typer.Option("", "--output", help="Output JSONL file path"),
    include_matches: bool = typer.Option(False, "--include-matches", help="Include per-match records"),
    kept_only: bool = typer.Option(False, "--kept-only", help="Only export generations that advanced"),
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
    import dataclasses

    from mts.storage.artifacts import ArtifactStore
    from mts.training.export import export_training_data

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

    artifacts = ArtifactStore(
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
        ):
            f.write(json.dumps(dataclasses.asdict(record)) + "\n")
            count += 1

    console.print(f"[green]Exported {count} record(s) to {output_path}[/green]")


@app.command("new-scenario")
def new_scenario(
    list_templates: bool = typer.Option(False, "--list", help="List available templates"),
    template: str | None = typer.Option(None, "--template", help="Template to scaffold from"),
    name: str | None = typer.Option(None, "--name", help="Name for the new scenario"),
    judge_model: str | None = typer.Option(None, "--judge-model", help="Override judge model"),
    non_interactive: bool = typer.Option(False, "--non-interactive", help="Use defaults, skip prompts"),
) -> None:
    """Scaffold a new scenario from the template library."""
    from mts.scenarios.templates import TemplateLoader

    loader = TemplateLoader()

    if list_templates:
        templates = loader.list_templates()
        table = Table(title="Available Scenario Templates")
        table.add_column("Name", style="bold")
        table.add_column("Description")
        table.add_column("Output Format")
        table.add_column("Max Rounds", justify="right")
        for t in templates:
            table.add_row(t.name, t.description, t.output_format, str(t.max_rounds))
        console.print(table)
        return

    # Scaffolding mode requires both --template and --name
    if template is None:
        console.print("[red]--template is required when not using --list[/red]")
        raise typer.Exit(code=1)
    if name is None:
        console.print("[red]--name is required when scaffolding a scenario[/red]")
        raise typer.Exit(code=1)

    # Validate template exists
    try:
        loader.get_template(template)
    except KeyError:
        console.print(f"[red]Template '{template}' not found. Use --list to see available templates.[/red]")
        raise typer.Exit(code=1) from None

    # Build overrides
    overrides: dict[str, object] = {}
    if judge_model is not None:
        overrides["judge_model"] = judge_model

    # Scaffold to target directory
    target_dir = _get_custom_scenarios_dir() / name
    try:
        loader.scaffold(template_name=template, target_dir=target_dir, overrides=overrides or None)
    except Exception as e:
        console.print(f"[red]Failed to scaffold scenario: {e}[/red]")
        raise typer.Exit(code=1) from None

    from mts.scenarios import SCENARIO_REGISTRY
    from mts.scenarios.custom.registry import load_all_custom_scenarios

    loaded = load_all_custom_scenarios(target_dir.parent.parent)
    registered = loaded.get(name)
    if registered is not None:
        SCENARIO_REGISTRY[name] = registered

    console.print(f"[green]Scenario '{name}' created from template '{template}'[/green]")
    console.print(f"[dim]Files scaffolded to: {target_dir}[/dim]")
    console.print("[dim]Available to agent-task tooling after scaffold/load via the custom scenario registry.[/dim]")


@app.command("export")
def export_cmd(
    scenario: str = typer.Option(..., "--scenario", help="Scenario to export"),
    output: str = typer.Option("", "--output", help="Output JSON file path (default: <scenario>_package.json)"),
    db_path: str | None = typer.Option(None, "--db-path", help="Override database path"),
    runs_root: str | None = typer.Option(None, "--runs-root", help="Override runs root"),
    knowledge_root: str | None = typer.Option(None, "--knowledge-root", help="Override knowledge root"),
    skills_root: str | None = typer.Option(None, "--skills-root", help="Override skills root"),
    claude_skills_path: str | None = typer.Option(None, "--claude-skills-path", help="Override Claude skills path"),
) -> None:
    """Export a portable strategy package for a scenario."""
    from mts.knowledge.export import export_strategy_package
    from mts.mcp.tools import MtsToolContext
    from mts.storage.artifacts import ArtifactStore

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
    if migrations_dir.exists():
        sqlite.migrate(migrations_dir)
    artifacts = ArtifactStore(
        runs_root=resolved_runs,
        knowledge_root=resolved_knowledge,
        skills_root=resolved_skills,
        claude_skills_path=resolved_claude,
    )
    ctx = MtsToolContext.__new__(MtsToolContext)
    ctx.settings = settings
    ctx.sqlite = sqlite
    ctx.artifacts = artifacts

    try:
        pkg = export_strategy_package(ctx, scenario)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    output_path = Path(output) if output else Path(f"{scenario}_package.json")
    pkg.to_file(output_path)
    console.print(f"[green]Exported {scenario} package to {output_path}[/green]")
    console.print(f"[dim]best_score={pkg.best_score:.4f} lessons={len(pkg.lessons)} harness={len(pkg.harness)}[/dim]")


@app.command("import-package")
def import_package_cmd(
    package_file: str = typer.Argument(..., help="Path to the strategy package JSON file"),
    scenario: str | None = typer.Option(None, "--scenario", help="Override target scenario name"),
    conflict: str = typer.Option("merge", "--conflict", help="Conflict policy: overwrite, merge, or skip"),
    db_path: str | None = typer.Option(None, "--db-path", help="Override database path"),
    knowledge_root: str | None = typer.Option(None, "--knowledge-root", help="Override knowledge root"),
    skills_root: str | None = typer.Option(None, "--skills-root", help="Override skills root"),
    claude_skills_path: str | None = typer.Option(None, "--claude-skills-path", help="Override Claude skills path"),
) -> None:
    """Import a strategy package into scenario knowledge."""
    from mts.knowledge.package import ConflictPolicy, StrategyPackage, import_strategy_package
    from mts.storage.artifacts import ArtifactStore

    pkg_path = Path(package_file)
    if not pkg_path.exists():
        console.print(f"[red]File not found: {pkg_path}[/red]")
        raise typer.Exit(code=1)

    try:
        pkg = StrategyPackage.from_file(pkg_path)
    except Exception as exc:
        console.print(f"[red]Invalid package file: {exc}[/red]")
        raise typer.Exit(code=1) from exc

    if scenario:
        pkg = pkg.model_copy(update={"scenario_name": scenario})

    try:
        policy = ConflictPolicy(conflict)
    except ValueError as exc:
        console.print(f"[red]Invalid conflict policy: {conflict!r}. Use overwrite, merge, or skip.[/red]")
        raise typer.Exit(code=1) from exc

    settings = load_settings()
    resolved_db = Path(db_path) if db_path is not None else settings.db_path
    sqlite = SQLiteStore(resolved_db)
    migrations_dir = Path(__file__).resolve().parents[2] / "migrations"
    if migrations_dir.exists():
        sqlite.migrate(migrations_dir)
    artifacts = ArtifactStore(
        runs_root=settings.runs_root,
        knowledge_root=Path(knowledge_root) if knowledge_root else settings.knowledge_root,
        skills_root=Path(skills_root) if skills_root else settings.skills_root,
        claude_skills_path=Path(claude_skills_path) if claude_skills_path else settings.claude_skills_path,
    )

    result = import_strategy_package(artifacts, pkg, sqlite=sqlite, conflict_policy=policy)

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


if __name__ == "__main__":
    app()
