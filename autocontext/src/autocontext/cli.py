from __future__ import annotations

import dataclasses
import json
import logging
import os
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import typer
import uvicorn
from rich.console import Console
from rich.table import Table

from autocontext.agents.orchestrator import AgentOrchestrator
from autocontext.config import load_settings
from autocontext.config.presets import VALID_PRESET_NAMES
from autocontext.config.settings import AppSettings
from autocontext.execution.improvement_loop import ImprovementLoop
from autocontext.loop.generation_runner import GenerationRunner
from autocontext.scenarios import SCENARIO_REGISTRY
from autocontext.scenarios.agent_task import AgentTaskInterface
from autocontext.storage import ArtifactStore, SQLiteStore

if TYPE_CHECKING:
    from autocontext.providers.base import LLMProvider
    from autocontext.training.runner import TrainingConfig, TrainingResult


@dataclass(slots=True)
class AgentTaskRunSummary:
    """Result summary for an agent-task execution via the CLI."""

    run_id: str
    scenario: str
    best_score: float
    best_output: str
    total_rounds: int
    met_threshold: bool
    termination_reason: str


@dataclass(slots=True)
class SolveRunSummary:
    """Result summary for solve-on-demand via the CLI."""

    job_id: str
    status: str
    description: str
    scenario_name: str | None
    generations: int
    progress: int
    output_path: str | None
    result: dict[str, object] | None

app = typer.Typer(help="autocontext control-plane CLI", invoke_without_command=True)
console = Console()

_PRESET_HELP = f"Apply a named preset ({', '.join(sorted(VALID_PRESET_NAMES))}). Overrides AUTOCONTEXT_PRESET env var."


@app.callback()
def _main_callback(ctx: typer.Context) -> None:
    """Show the banner when invoked without a subcommand."""
    if ctx.invoked_subcommand is None:
        from autocontext.banner import print_banner_rich

        print_banner_rich()


def _apply_preset_env(preset: str | None) -> None:
    """Set AUTOCONTEXT_PRESET env var from CLI flag so load_settings() picks it up."""
    if preset is not None:
        os.environ["AUTOCONTEXT_PRESET"] = preset


def _runner(preset: str | None = None) -> GenerationRunner:
    _apply_preset_env(preset)
    settings = load_settings()
    runner = GenerationRunner(settings)
    runner.migrate(Path(__file__).resolve().parents[2] / "migrations")
    return runner


def _sqlite_from_settings(settings: AppSettings) -> SQLiteStore:
    sqlite = SQLiteStore(settings.db_path)
    sqlite.migrate(Path(__file__).resolve().parents[2] / "migrations")
    return sqlite


def _artifacts_from_settings(settings: AppSettings) -> ArtifactStore:
    return ArtifactStore(
        settings.runs_root,
        settings.knowledge_root,
        settings.skills_root,
        settings.claude_skills_path,
        max_playbook_versions=settings.playbook_max_versions,
        enable_buffered_writes=True,
    )


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


def _write_json_stdout(payload: object) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")


def _write_json_stderr(message: str) -> None:
    sys.stderr.write(json.dumps({"error": message}) + "\n")


def _is_agent_task(scenario_name: str) -> bool:
    """Check if a scenario should use the direct agent-task execution path."""
    if scenario_name not in SCENARIO_REGISTRY:
        return False
    from autocontext.scenarios.families import detect_family

    family = detect_family(SCENARIO_REGISTRY[scenario_name]())
    if family is None:
        return False
    return issubclass(family.interface_class, AgentTaskInterface)


def _resolve_agent_task_runtime(settings: AppSettings, scenario_name: str) -> tuple[LLMProvider, str]:
    """Resolve the effective competitor runtime for direct agent-task execution."""
    from autocontext.providers.callable_wrapper import CallableProvider

    sqlite = _sqlite_from_settings(settings)
    artifacts = _artifacts_from_settings(settings)
    orchestrator = AgentOrchestrator.from_settings(settings, artifacts=artifacts, sqlite=sqlite)
    client, model = orchestrator.resolve_role_execution(
        "competitor",
        generation=1,
        scenario_name=scenario_name,
    )
    resolved_model = model or settings.model_competitor or settings.agent_default_model

    def _llm_fn(system_prompt: str, user_prompt: str) -> str:
        response = client.generate(
            model=resolved_model,
            prompt=f"{system_prompt}\n\n{user_prompt}" if system_prompt else user_prompt,
            max_tokens=4096,
            temperature=0.0,
            role="competitor",
        )
        return response.text

    return CallableProvider(_llm_fn, model_name=resolved_model), resolved_model


def _run_agent_task(
    scenario_name: str,
    settings: AppSettings,
    max_rounds: int,
    run_id: str | None,
) -> AgentTaskRunSummary:
    """Execute an agent-task scenario through ImprovementLoop."""
    sqlite = _sqlite_from_settings(settings)
    cls = SCENARIO_REGISTRY[scenario_name]
    instance = cls()
    # Runtime-validated: _is_agent_task() already confirmed this
    task: AgentTaskInterface = instance  # type: ignore[assignment]

    provider, provider_model = _resolve_agent_task_runtime(settings, scenario_name)
    state = task.prepare_context(task.initial_state())
    context_errors = task.validate_context(state)
    if context_errors:
        raise ValueError(f"Context validation failed: {'; '.join(context_errors)}")
    prompt = task.get_task_prompt(state)

    initial_output = provider.complete(
        system_prompt="Complete the task precisely.",
        user_prompt=prompt,
        model=provider_model,
    ).text

    loop = ImprovementLoop(task=task, max_rounds=max_rounds)
    active_run_id = run_id or f"task_{uuid.uuid4().hex[:12]}"
    sqlite.create_run(
        active_run_id,
        scenario_name,
        1,
        "agent_task",
        agent_provider=settings.agent_provider,
    )
    sqlite.upsert_generation(
        active_run_id,
        1,
        mean_score=0.0,
        best_score=0.0,
        elo=0.0,
        wins=0,
        losses=0,
        gate_decision="running",
        status="running",
    )
    sqlite.append_agent_output(active_run_id, 1, "competitor_initial", initial_output)

    try:
        result = loop.run(initial_output=initial_output, state=state)
    except Exception:
        sqlite.upsert_generation(
            active_run_id,
            1,
            mean_score=0.0,
            best_score=0.0,
            elo=0.0,
            wins=0,
            losses=0,
            gate_decision="failed",
            status="failed",
        )
        raise

    sqlite.append_agent_output(active_run_id, 1, "competitor", result.best_output)
    sqlite.upsert_generation(
        active_run_id,
        1,
        mean_score=result.best_score,
        best_score=result.best_score,
        elo=0.0,
        wins=0,
        losses=0,
        gate_decision=result.termination_reason,
        status="completed",
        duration_seconds=(result.duration_ms / 1000.0) if result.duration_ms is not None else None,
    )

    return AgentTaskRunSummary(
        run_id=active_run_id,
        scenario=scenario_name,
        best_score=result.best_score,
        best_output=result.best_output,
        total_rounds=result.total_rounds,
        met_threshold=result.met_threshold,
        termination_reason=result.termination_reason,
    )


@app.command()
def run(
    scenario: str = typer.Option("grid_ctf", "--scenario"),
    gens: int = typer.Option(1, "--gens", min=1),
    run_id: str | None = typer.Option(None, "--run-id"),
    serve: bool = typer.Option(False, "--serve", help="Start interactive server alongside generation loop"),
    port: int = typer.Option(8000, "--port", help="Server port (only used with --serve)"),
    preset: str | None = typer.Option(None, "--preset", help=_PRESET_HELP),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Run generation loop."""

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if serve and json_output:
        _write_json_stderr("--json cannot be used with --serve")
        raise typer.Exit(code=2)

    if preset and not json_output:
        console.print(f"[dim]Active preset: {preset}[/dim]")

    # Agent-task scenario detection (AC-231)
    if _is_agent_task(scenario):
        if serve:
            msg = "--serve is not supported for agent-task scenarios"
            if json_output:
                _write_json_stderr(msg)
            else:
                console.print(f"[red]{msg}[/red]")
            raise typer.Exit(code=2)

        _apply_preset_env(preset)
        settings = load_settings()
        try:
            task_summary = _run_agent_task(scenario, settings, max_rounds=gens, run_id=run_id)
        except KeyboardInterrupt:
            if json_output:
                _write_json_stderr("run interrupted")
            else:
                console.print("[yellow]Run interrupted.[/yellow]")
            raise typer.Exit(code=1) from None
        except Exception as exc:
            if json_output:
                _write_json_stderr(str(exc))
            else:
                console.print(f"[red]Error: {exc}[/red]")
            raise typer.Exit(code=1) from exc
        if json_output:
            _write_json_stdout(dataclasses.asdict(task_summary))
        else:
            table = Table(title="Agent Task Result")
            table.add_column("Run ID")
            table.add_column("Scenario")
            table.add_column("Best Score")
            table.add_column("Rounds")
            table.add_column("Threshold Met")
            table.add_column("Termination")
            table.add_row(
                task_summary.run_id,
                task_summary.scenario,
                f"{task_summary.best_score:.4f}",
                str(task_summary.total_rounds),
                str(task_summary.met_threshold),
                task_summary.termination_reason,
            )
            console.print(table)
        return

    if serve:
        from autocontext.loop.controller import LoopController
        from autocontext.server.app import create_app

        runner = _runner(preset)
        controller = LoopController()
        runner.controller = controller

        def _loop_target() -> None:
            runner.run(scenario_name=scenario, generations=gens, run_id=run_id)

        loop_thread = threading.Thread(target=_loop_target, daemon=True)
        loop_thread.start()

        interactive_app = create_app(controller=controller, events=runner.events)
        console.print(f"[green]Interactive server started on port {port}[/green]")
        console.print(f"[dim]API: http://localhost:{port}/api/runs | WS: ws://localhost:{port}/ws/interactive[/dim]")
        uvicorn.run(interactive_app, host="127.0.0.1", port=int(port), log_level="info")
    else:
        try:
            summary = _runner(preset).run(scenario_name=scenario, generations=gens, run_id=run_id)
        except KeyboardInterrupt:
            if json_output:
                _write_json_stderr("run interrupted")
            else:
                console.print("[yellow]Run interrupted.[/yellow]")
            raise typer.Exit(code=1) from None
        except Exception as exc:
            if json_output:
                _write_json_stderr(str(exc))
            else:
                console.print(f"[red]Error: {exc}[/red]")
            raise typer.Exit(code=1) from exc
        if json_output:
            _write_json_stdout(dataclasses.asdict(summary))
        else:
            table = Table(title="autocontext Run Summary")
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
def resume(
    run_id: str = typer.Argument(...),
    scenario: str = typer.Option("grid_ctf"),
    gens: int = typer.Option(1),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Resume an existing run idempotently."""

    try:
        summary = _runner().run(scenario_name=scenario, generations=gens, run_id=run_id)
    except KeyboardInterrupt:
        if json_output:
            _write_json_stderr("resume interrupted")
        else:
            console.print("[yellow]Resume interrupted.[/yellow]")
        raise typer.Exit(code=1) from None
    except Exception as exc:
        if json_output:
            _write_json_stderr(str(exc))
        else:
            console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(code=1) from exc
    if json_output:
        _write_json_stdout(dataclasses.asdict(summary))
    else:
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
def list_runs(
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """List recent runs."""

    settings = load_settings()
    store = SQLiteStore(settings.db_path)
    with store.connect() as conn:
        rows = conn.execute(
            "SELECT run_id, scenario, target_generations, executor_mode, status, created_at "
            "FROM runs ORDER BY created_at DESC LIMIT 20"
        ).fetchall()

    if json_output:
        result = [dict(row) for row in rows]
        sys.stdout.write(json.dumps(result) + "\n")
    else:
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
def status(
    run_id: str = typer.Argument(...),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Show generation status for a run."""

    settings = load_settings()
    store = SQLiteStore(settings.db_path)
    with store.connect() as conn:
        rows = conn.execute(
            "SELECT generation_index, mean_score, best_score, elo, wins, losses, gate_decision, status "
            "FROM generations WHERE run_id = ? ORDER BY generation_index ASC",
            (run_id,),
        ).fetchall()

    if json_output:
        generations = []
        for row in rows:
            generations.append({
                "generation": row["generation_index"],
                "mean_score": row["mean_score"],
                "best_score": row["best_score"],
                "elo": row["elo"],
                "wins": row["wins"],
                "losses": row["losses"],
                "gate_decision": row["gate_decision"],
                "status": row["status"],
            })
        sys.stdout.write(json.dumps({"run_id": run_id, "generations": generations}) + "\n")
    else:
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
    """Serve HTTP API and WebSocket stream."""

    uvicorn.run("autocontext.server.app:app", host=host, port=port, reload=False)


@app.command()
def ecosystem(
    scenario: str = typer.Option("grid_ctf", "--scenario"),
    cycles: int = typer.Option(3, "--cycles", min=1),
    gens_per_cycle: int = typer.Option(3, "--gens-per-cycle", min=1),
    provider_a: str = typer.Option("anthropic", "--provider-a"),
    provider_b: str = typer.Option("agent_sdk", "--provider-b"),
    rlm_a: bool = typer.Option(True, "--rlm-a/--no-rlm-a"),
    rlm_b: bool = typer.Option(False, "--rlm-b/--no-rlm-b"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Run ecosystem loop alternating provider modes across cycles."""

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from autocontext.loop.ecosystem_runner import EcosystemConfig, EcosystemPhase, EcosystemRunner

    settings = load_settings()
    phases = [
        EcosystemPhase(provider=provider_a, rlm_enabled=rlm_a, generations=gens_per_cycle),
        EcosystemPhase(provider=provider_b, rlm_enabled=rlm_b, generations=gens_per_cycle),
    ]
    config = EcosystemConfig(scenario=scenario, cycles=cycles, gens_per_cycle=gens_per_cycle, phases=phases)
    eco_runner = EcosystemRunner(settings, config)
    eco_runner.migrate(Path(__file__).resolve().parents[2] / "migrations")
    summary = eco_runner.run()

    if json_output:
        runs_data = []
        for rs in summary.run_summaries:
            runs_data.append(dataclasses.asdict(rs))
        traj_data = [{"run_id": rid, "best_score": score} for rid, score in summary.score_trajectory()]
        sys.stdout.write(json.dumps({"runs": runs_data, "trajectory": traj_data}) + "\n")
    else:
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

        score_traj = summary.score_trajectory()
        traj_table = Table(title="Score Trajectory")
        traj_table.add_column("Run ID")
        traj_table.add_column("Best Score")
        for run_id_val, score in score_traj:
            traj_table.add_row(run_id_val, f"{score:.4f}")
        console.print(traj_table)


@app.command()
def tui(
    port: int = typer.Option(8000, "--port", help="Server port"),
) -> None:
    """Start the interactive API/WebSocket server for a separate terminal UI client."""

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    from autocontext.loop.controller import LoopController
    from autocontext.loop.events import EventStreamEmitter
    from autocontext.server.app import create_app
    from autocontext.server.run_manager import RunManager

    settings = load_settings()
    controller = LoopController()
    events = EventStreamEmitter(settings.event_stream_path)
    run_manager = RunManager(controller, events, settings)

    interactive_app = create_app(controller=controller, events=events, run_manager=run_manager)

    # AC-467: standalone tui/ removed — server is API-only.
    # Interactive TUI is available via the TS package: autoctx tui
    console.print(f"[green]Interactive server on port {port}[/green]")
    console.print(f"[dim]API: http://localhost:{port}/api/runs[/dim]")
    console.print(f"[dim]WebSocket: ws://localhost:{port}/ws/interactive[/dim]")
    console.print("[dim]For interactive TUI, use the TypeScript package: npx autoctx tui[/dim]")

    uvicorn.run(interactive_app, host="127.0.0.1", port=int(port), log_level="info")


@app.command("ab-test")
def ab_test(
    scenario: str = typer.Option("grid_ctf", "--scenario", help="Scenario to test"),
    baseline: str = typer.Option(
        "AUTOCONTEXT_RLM_ENABLED=false", "--baseline", help="Comma-separated KEY=VALUE env overrides for baseline",
    ),
    treatment: str = typer.Option(
        "AUTOCONTEXT_RLM_ENABLED=true", "--treatment", help="Comma-separated KEY=VALUE env overrides for treatment",
    ),
    runs: int = typer.Option(5, "--runs", min=1, help="Runs per condition"),
    gens: int = typer.Option(3, "--gens", min=1, help="Generations per run"),
    seed: int = typer.Option(42, "--seed", help="Random seed for condition ordering"),
) -> None:
    """Run paired A/B test comparing two autocontext configurations."""
    from autocontext.evaluation.ab_runner import ABTestConfig, ABTestRunner
    from autocontext.evaluation.ab_stats import mcnemar_test

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
    """Start autocontext MCP server on stdio for Claude Code integration."""

    try:
        from autocontext.mcp.server import run_server
    except ImportError:
        console.print("[red]MCP dependencies not installed. Run: uv sync --extra mcp[/red]")
        raise typer.Exit(code=1) from None
    run_server()


def _run_training(config: TrainingConfig, *, json_output: bool = False) -> TrainingResult:
    """Run the training loop. Extracted for testability."""
    from autocontext.training.runner import TrainingRunner

    runner = TrainingRunner(config, work_dir=Path("runs") / f"train_{config.scenario}")
    if not json_output:
        console.print(f"[green]Training workspace:[/green] {runner.work_dir}")
        console.print(
            f"[dim]scenario={config.scenario} budget={config.time_budget}s max_experiments={config.max_experiments}[/dim]"
        )
    return runner.run()


@app.command()
def train(
    scenario: str = typer.Option("grid_ctf", "--scenario", help="Scenario to train on"),
    data: str = typer.Option("training_data.jsonl", "--data", help="Path to JSONL training data"),
    time_budget: int = typer.Option(300, "--time-budget", help="Training time budget in seconds"),
    max_experiments: int = typer.Option(0, "--max-experiments", help="Max iterations (0 = unlimited)"),
    memory_limit: int = typer.Option(16384, "--memory-limit", help="Peak memory cap in MB"),
    backend: str = typer.Option("mlx", "--backend", help="Training backend to publish and activate (mlx, cuda)"),
    agent_provider: str = typer.Option("anthropic", "--agent-provider", help="LLM provider for training agent"),
    agent_model: str = typer.Option("", "--agent-model", help="Model for training agent (empty = provider default)"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Launch the autoresearch-style training loop."""
    from autocontext.training.runner import TrainingConfig

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    config = TrainingConfig(
        scenario=scenario,
        data_path=Path(data),
        time_budget=time_budget,
        max_experiments=max_experiments,
        memory_limit_mb=memory_limit,
        backend=backend,
        agent_provider=agent_provider,
        agent_model=agent_model,
    )

    try:
        result = _run_training(config, json_output=json_output)
    except KeyboardInterrupt:
        if not json_output:
            console.print("\n[yellow]Training interrupted.[/yellow]")
        raise typer.Exit(code=1) from None
    except Exception as exc:
        if json_output:
            _write_json_stderr(str(exc))
        else:
            console.print(f"[red]Training failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    if json_output:
        _write_json_stdout({
            "scenario": result.scenario,
            "total_experiments": result.total_experiments,
            "kept_count": result.kept_count,
            "discarded_count": result.discarded_count,
            "best_score": result.best_score,
            "checkpoint_path": str(result.checkpoint_path) if result.checkpoint_path else None,
            "published_model_id": result.published_model_id,
        })
    else:
        # Summary
        table = Table(title="Training Summary")
        table.add_column("Metric")
        table.add_column("Value")
        table.add_row("Scenario", result.scenario)
        table.add_row("Total experiments", str(result.total_experiments))
        table.add_row("Kept / Discarded", f"{result.kept_count} / {result.discarded_count}")
        table.add_row("Best score", f"{result.best_score:.4f}")
        table.add_row("Checkpoint", str(result.checkpoint_path) if result.checkpoint_path else "(none)")
        if result.published_model_id:
            table.add_row("Published model", result.published_model_id)
        console.print(table)


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

    from autocontext.storage.artifacts import ArtifactStore
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
    from autocontext.scenarios.templates import TemplateLoader

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

    from autocontext.scenarios import SCENARIO_REGISTRY
    from autocontext.scenarios.custom.registry import load_all_custom_scenarios

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
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Export a portable strategy package for a scenario."""
    from autocontext.knowledge.export import export_strategy_package
    from autocontext.mcp.tools import MtsToolContext
    from autocontext.storage.artifacts import ArtifactStore

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
        if json_output:
            _write_json_stderr(str(exc))
        else:
            console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    output_path = Path(output) if output else Path(f"{scenario}_package.json")
    pkg.to_file(output_path)

    if json_output:
        _write_json_stdout({
            "scenario": scenario,
            "output_path": str(output_path),
            "best_score": pkg.best_score,
            "lessons_count": len(pkg.lessons),
            "harness_count": len(pkg.harness),
        })
    else:
        console.print(f"[green]Exported {scenario} package to {output_path}[/green]")
        console.print(f"[dim]best_score={pkg.best_score:.4f} lessons={len(pkg.lessons)} harness={len(pkg.harness)}[/dim]")


@app.command()
def simulate(
    description: str = typer.Option("", "--description", "-d", help="Plain-language description of what to simulate"),
    variables: str = typer.Option("", "--variables", help="Variable overrides (key=val,key2=val2)"),
    sweep: str = typer.Option("", "--sweep", help="Sweep spec (key=min:max:step)"),
    replay_id: str = typer.Option("", "--replay", help="Replay a saved simulation by name"),
    compare_left: str = typer.Option("", "--compare-left", help="Left simulation for comparison"),
    compare_right: str = typer.Option("", "--compare-right", help="Right simulation for comparison"),
    export_id: str = typer.Option("", "--export", help="Export a saved simulation"),
    export_format: str = typer.Option("json", "--format", help="Export format: json, markdown, csv"),
    runs: int = typer.Option(1, "--runs", min=1, help="Number of runs"),
    max_steps: int = typer.Option(0, "--max-steps", help="Max steps per run (0 = auto)"),
    save_as: str = typer.Option("", "--save-as", help="Name for saved simulation"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Run a plain-language simulation with sweeps and analysis."""
    from autocontext.simulation.engine import SimulationEngine

    settings = load_settings()

    if bool(compare_left) != bool(compare_right):
        console.print("[red]--compare-left and --compare-right must be provided together[/red]")
        raise typer.Exit(code=1)

    # Parse variables
    parsed_vars: dict[str, object] = {}
    if variables:
        for pair in variables.split(","):
            parts = pair.split("=", 1)
            if len(parts) == 2:
                key, val = parts[0].strip(), parts[1].strip()
                try:
                    parsed_vars[key] = float(val) if "." in val else int(val)
                except ValueError:
                    parsed_vars[key] = val

    # Parse sweep
    parsed_sweep: list[dict[str, object]] | None = None
    if sweep:
        parsed_sweep = []
        for pair in sweep.split(","):
            parts = pair.split("=", 1)
            if len(parts) == 2:
                name, range_str = parts[0].strip(), parts[1].strip()
                range_parts = range_str.split(":")
                if len(range_parts) == 3:
                    mn, mx, st = float(range_parts[0]), float(range_parts[1]), float(range_parts[2])
                    vals = []
                    v = mn
                    while v <= mx + st / 2:
                        vals.append(round(v, 4))
                        v += st
                    parsed_sweep.append({"name": name, "values": vals})

    def _llm_fn(system: str, user: str) -> str:
        from autocontext.providers.registry import get_provider
        provider = get_provider(settings)
        model = settings.model_architect or provider.default_model()
        result = provider.complete(system, user, model=model)
        return result.text

    engine = SimulationEngine(llm_fn=_llm_fn, knowledge_root=settings.knowledge_root)

    # Export mode
    if export_id:
        from autocontext.simulation.export import export_simulation
        result = export_simulation(id=export_id, knowledge_root=settings.knowledge_root, format=export_format)
        if json_output:
            _write_json_stdout(result)
        elif result["status"] == "failed":
            console.print(f"[red]Export failed:[/red] {result.get('error')}")
            raise typer.Exit(code=1)
        else:
            console.print(f"[green]Exported:[/green] {result['output_path']}")
        return

    # Compare mode
    if compare_left and compare_right:
        result = engine.compare(left=compare_left, right=compare_right)
        if json_output:
            _write_json_stdout(result)
        elif result["status"] == "failed":
            console.print(f"[red]Compare failed:[/red] {result.get('error')}")
            raise typer.Exit(code=1)
        else:
            console.print(f"Compare: {result['summary']}")
        return

    # Replay mode
    if replay_id:
        result = engine.replay(
            id=replay_id,
            variables=parsed_vars if parsed_vars else None,
            max_steps=max_steps if max_steps > 0 else None,
        )
        if json_output:
            _write_json_stdout(result)
        elif result["status"] == "failed":
            console.print(f"[red]Replay failed:[/red] {result.get('error')}")
            raise typer.Exit(code=1)
        else:
            console.print(
                f"Replay: {result['name']} "
                f"(original: {result.get('original_score', 0):.2f}, "
                f"replay: {result['summary']['score']:.2f}, "
                f"delta: {result.get('score_delta', 0):.4f})"
            )
        return

    # Run mode
    if not description:
        console.print("[red]--description, --replay, --compare-left/--compare-right, or --export is required[/red]")
        raise typer.Exit(code=1)

    result = engine.run(
        description=description,
        variables=parsed_vars if parsed_vars else None,
        sweep=parsed_sweep,
        runs=runs,
        max_steps=max_steps if max_steps > 0 else None,
        save_as=save_as if save_as else None,
    )

    if json_output:
        _write_json_stdout(result)
    elif result["status"] == "failed":
        console.print(f"[red]Simulation failed:[/red] {result.get('error')}")
        raise typer.Exit(code=1)
    else:
        console.print(f"[bold]Simulation:[/bold] {result['name']} (family: {result['family']})")
        console.print(f"Score: {result['summary']['score']:.4f}")
        console.print(f"Reasoning: {result['summary']['reasoning']}")
        if result.get("sweep"):
            console.print(f"Sweep: {result['sweep']['runs']} runs")
        console.print("\n[dim]Assumptions:[/dim]")
        for a in result.get("assumptions", []):
            console.print(f"  - {a}")
        console.print("\n[dim]Warnings:[/dim]")
        for w in result.get("warnings", []):
            console.print(f"  ⚠ {w}")
        console.print(f"\nArtifacts: {result['artifacts']['scenario_dir']}")


@app.command()
def solve(
    description: str = typer.Option(..., "--description", help="Natural-language scenario/problem description"),
    gens: int = typer.Option(5, "--gens", min=1, max=50, help="Generations to run for the solve"),
    output: str = typer.Option("", "--output", help="Optional JSON file path for the solved package"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Create a scenario on demand, run it, and export the solved package."""
    from autocontext.knowledge.solver import SolveManager

    settings = load_settings()
    manager = SolveManager(settings)

    try:
        job = manager.solve_sync(description=description, generations=gens)
    except KeyboardInterrupt:
        if json_output:
            _write_json_stderr("solve interrupted")
        else:
            console.print("[red]Solve interrupted[/red]")
        raise typer.Exit(code=1) from None
    except Exception as exc:
        if json_output:
            _write_json_stderr(str(exc))
        else:
            console.print(f"[red]Solve failed:[/red] {exc}")
        raise typer.Exit(code=1) from None

    if job.status != "completed" or job.result is None:
        message = job.error or "solve did not complete successfully"
        if json_output:
            _write_json_stderr(message)
        else:
            console.print(f"[red]Solve failed:[/red] {message}")
        raise typer.Exit(code=1)

    output_path: str | None = None
    if output:
        output_file = Path(output)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(json.dumps(job.result.to_dict(), indent=2), encoding="utf-8")
        output_path = str(output_file)

    summary = SolveRunSummary(
        job_id=job.job_id,
        status=job.status,
        description=job.description,
        scenario_name=job.scenario_name,
        generations=job.generations,
        progress=job.progress,
        output_path=output_path,
        result=job.result.to_dict(),
    )

    if json_output:
        _write_json_stdout(dataclasses.asdict(summary))
        return

    table = Table(title="Solve Result")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("Job ID", job.job_id)
    table.add_row("Status", job.status)
    table.add_row("Scenario", job.scenario_name or "unknown")
    table.add_row("Generations", str(job.generations))
    table.add_row("Progress", str(job.progress))
    if output_path is not None:
        table.add_row("Output", output_path)
    console.print(table)


@app.command("import-package")
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
    from autocontext.storage.artifacts import ArtifactStore

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
    if migrations_dir.exists():
        sqlite.migrate(migrations_dir)
    artifacts = ArtifactStore(
        runs_root=settings.runs_root,
        knowledge_root=Path(knowledge_root) if knowledge_root else settings.knowledge_root,
        skills_root=Path(skills_root) if skills_root else settings.skills_root,
        claude_skills_path=Path(claude_skills_path) if claude_skills_path else settings.claude_skills_path,
    )

    result = import_strategy_package(artifacts, pkg, sqlite=sqlite, conflict_policy=policy)

    if json_output:
        _write_json_stdout({
            "scenario_name": result.scenario_name,
            "playbook_written": result.playbook_written,
            "hints_written": result.hints_written,
            "skill_written": result.skill_written,
            "harness_written": result.harness_written,
            "harness_skipped": result.harness_skipped,
            "conflict_policy": result.conflict_policy,
        })
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


@app.command()
def wait(
    condition_id: str = typer.Argument(..., help="Monitor condition ID to wait on"),
    timeout: float = typer.Option(30.0, "--timeout", help="Timeout in seconds"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Wait for a monitor condition to fire (AC-209 integration)."""
    settings = load_settings()
    store = SQLiteStore(settings.db_path)
    migrations_dir = Path(__file__).resolve().parents[2] / "migrations"
    if migrations_dir.exists():
        store.migrate(migrations_dir)

    # Check condition exists
    condition = store.get_monitor_condition(condition_id)
    if condition is None:
        msg = f"Monitor condition '{condition_id}' not found"
        if json_output:
            _write_json_stderr(msg)
        else:
            console.print(f"[red]{msg}[/red]")
        raise typer.Exit(code=1)

    deadline = time.monotonic() + timeout
    alert = store.get_latest_monitor_alert(condition_id)
    while alert is None and time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        time.sleep(min(0.1, max(remaining, 0.0)))
        alert = store.get_latest_monitor_alert(condition_id)

    fired = alert is not None

    if fired:
        if json_output:
            _write_json_stdout({
                "fired": True,
                "condition_id": condition_id,
                "alert": alert,
            })
        else:
            detail = alert.get("detail", "") if alert else ""
            console.print(f"[green]Alert fired:[/green] {detail}")
    else:
        if json_output:
            _write_json_stdout({
                "fired": False,
                "condition_id": condition_id,
                "timeout_seconds": timeout,
            })
        else:
            console.print(f"[yellow]Timed out after {timeout}s waiting for condition {condition_id}[/yellow]")
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# Backported from TS package (AC-382)
# ---------------------------------------------------------------------------


@app.command()
def judge(
    task_prompt: str = typer.Option(..., "--task-prompt", "-p", help="The task prompt"),
    output: str = typer.Option(..., "--output", "-o", help="The agent output to evaluate"),
    rubric: str = typer.Option(..., "--rubric", "-r", help="Evaluation rubric"),
    provider: str = typer.Option("", "--provider", help="Provider override"),
    model: str = typer.Option("", "--model", help="Model override"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """One-shot evaluation of agent output against a rubric."""
    from autocontext.execution.judge import LLMJudge

    settings = load_settings()
    if provider:
        settings = settings.model_copy(update={"judge_provider": provider})
    if model:
        settings = settings.model_copy(update={"judge_model": model})

    from autocontext.providers.registry import get_provider
    judge_provider = get_provider(settings)
    llm_judge = LLMJudge(
        provider=judge_provider,
        model=settings.judge_model,
        rubric=rubric,
    )
    result = llm_judge.evaluate(task_prompt=task_prompt, agent_output=output)

    if json_output:
        _write_json_stdout({
            "score": result.score,
            "reasoning": result.reasoning,
            "dimension_scores": result.dimension_scores,
        })
    else:
        console.print(f"[bold]Score:[/bold] {result.score:.4f}")
        console.print(f"[bold]Reasoning:[/bold] {result.reasoning}")


@app.command()
def improve(
    task_prompt: str = typer.Option(..., "--task-prompt", "-p", help="The task prompt"),
    rubric: str = typer.Option(..., "--rubric", "-r", help="Evaluation rubric"),
    initial_output: str = typer.Option("", "--output", "-o", help="Starting output to improve"),
    max_rounds: int = typer.Option(5, "--rounds", "-n", help="Maximum improvement rounds"),
    threshold: float = typer.Option(0.9, "--threshold", "-t", help="Quality threshold to stop"),
    provider_override: str = typer.Option("", "--provider", help="Provider override"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Run multi-round improvement loop on agent output.

    Creates a simple agent task from the prompt and rubric, then runs
    the improvement loop with judge-guided iteration.
    """
    from autocontext.execution.improvement_loop import ImprovementLoop
    from autocontext.execution.task_runner import SimpleAgentTask
    from autocontext.providers.registry import get_provider as get_judge_provider

    settings = load_settings()
    if provider_override:
        settings = settings.model_copy(update={"judge_provider": provider_override})

    provider = get_judge_provider(settings)
    task = SimpleAgentTask(
        task_prompt=task_prompt,
        rubric=rubric,
        provider=provider,
        model=settings.judge_model,
    )
    state = task.initial_state()
    loop = ImprovementLoop(
        task=task,
        max_rounds=max_rounds,
        quality_threshold=threshold,
    )
    starting_output = initial_output or task.generate_output(state)
    result = loop.run(initial_output=starting_output, state=state)

    if json_output:
        _write_json_stdout({
            "best_score": result.best_score,
            "best_round": result.best_round,
            "total_rounds": result.total_rounds,
            "met_threshold": result.met_threshold,
            "best_output": result.best_output,
        })
    else:
        console.print(f"[bold]Best score:[/bold] {result.best_score:.4f} (round {result.best_round})")
        console.print(f"[bold]Rounds:[/bold] {result.total_rounds}")
        console.print(f"[bold]Met threshold:[/bold] {result.met_threshold}")


@app.command()
def queue(
    spec: str = typer.Option(..., "--spec", "-s", help="Task spec name"),
    priority: int = typer.Option(0, "--priority", help="Task priority"),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Add a task to the background runner queue."""
    from autocontext.execution.task_runner import enqueue_task

    settings = load_settings()
    store = _sqlite_from_settings(settings)
    migrations_dir = Path(__file__).resolve().parents[2] / "migrations"
    if migrations_dir.exists():
        store.migrate(migrations_dir)

    task_id = enqueue_task(store=store, spec_name=spec, priority=priority)

    if json_output:
        _write_json_stdout({"task_id": task_id, "spec_name": spec, "status": "queued"})
    else:
        console.print(f"Queued task {task_id} for spec '{spec}' (priority {priority})")


if __name__ == "__main__":
    app()
