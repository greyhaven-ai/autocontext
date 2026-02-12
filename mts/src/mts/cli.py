from __future__ import annotations

import json
import logging
from pathlib import Path

import typer
import uvicorn
from rich.console import Console
from rich.table import Table

from mts.config import load_settings
from mts.loop.generation_runner import GenerationRunner
from mts.storage import SQLiteStore

app = typer.Typer(help="MTS control-plane CLI")
console = Console()


def _runner() -> GenerationRunner:
    settings = load_settings()
    runner = GenerationRunner(settings)
    runner.migrate(Path(__file__).resolve().parents[2] / "migrations")
    return runner


@app.command()
def run(
    scenario: str = typer.Option("grid_ctf", "--scenario"),
    gens: int = typer.Option(1, "--gens", min=1),
    run_id: str | None = typer.Option(None, "--run-id"),
) -> None:
    """Run generation loop."""

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    summary = _runner().run(scenario_name=scenario, generations=gens, run_id=run_id)
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


@app.command("mcp-serve")
def mcp_serve() -> None:
    """Start MTS MCP server on stdio for Claude Code integration."""

    try:
        from mts.mcp.server import run_server
    except ImportError:
        console.print("[red]MCP dependencies not installed. Run: uv sync --extra mcp[/red]")
        raise typer.Exit(code=1) from None
    run_server()


if __name__ == "__main__":
    app()
