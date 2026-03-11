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


if __name__ == "__main__":
    app()
