"""`autoctx self-improve` command: the ReST-EM self-improving training loop."""

from __future__ import annotations

import logging
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

logger = logging.getLogger(__name__)


def register_self_improve_command(app: typer.Typer, console: Console) -> None:
    @app.command("self-improve")
    def self_improve(
        scenario: str = typer.Option("grid_ctf", "--scenario", help="Scenario to train on"),
        data: str = typer.Option("training_data.jsonl", "--data", help="Seed JSONL training data"),
        output_dir: str = typer.Option("runs/self_improve", "--output-dir", help="Output directory"),
        rounds: int = typer.Option(3, "--rounds", help="Number of generate->filter->retrain rounds"),
        samples_per_round: int = typer.Option(16, "--samples-per-round", help="Samples generated per round"),
        elite_fraction: float = typer.Option(0.25, "--elite-fraction", help="Top fraction of samples to keep"),
        train_steps: int = typer.Option(100, "--train-steps", help="Training steps per round"),
        batch_size: int = typer.Option(
            4, "--batch-size", help="Training batch size (mlxlm needs the validation split >= this; lower it for small seeds)"
        ),
        score_conditioned: bool = typer.Option(False, "--score-conditioned", help="Score-conditioned generation"),
        backend: str = typer.Option(
            "mlx", "--backend", help="SFT backend: mlx (from-scratch GPT) | mlxlm (LoRA on a pretrained base)"
        ),
        base_model: str = typer.Option("", "--base-model", help="mlxlm: pretrained base model (empty = backend default)"),
        fine_tune_type: str = typer.Option("lora", "--fine-tune-type", help="mlxlm: lora | dora | full"),
        num_layers: int = typer.Option(8, "--num-layers", help="mlxlm: layers to fine-tune"),
        json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
    ) -> None:
        """Run the ReST-EM loop: train, sample, keep the elite, append, retrain (mlx or mlxlm)."""
        from autocontext.cli import _write_json_stderr, _write_json_stdout
        from autocontext.training.autoresearch.self_improve import run_self_improving_loop

        if not 0.0 < elite_fraction <= 1.0:
            raise typer.BadParameter(f"--elite-fraction must be in (0, 1], got {elite_fraction}")
        if rounds < 1:
            raise typer.BadParameter(f"--rounds must be a positive integer, got {rounds}")
        if samples_per_round < 1:
            raise typer.BadParameter(f"--samples-per-round must be a positive integer, got {samples_per_round}")
        if train_steps < 1:
            raise typer.BadParameter(f"--train-steps must be a positive integer, got {train_steps}")
        if batch_size < 1:
            raise typer.BadParameter(f"--batch-size must be a positive integer, got {batch_size}")
        if backend not in ("mlx", "mlxlm"):
            raise typer.BadParameter(f"--backend must be mlx|mlxlm (the SFT backends), got {backend!r}")

        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        try:
            result = run_self_improving_loop(
                scenario_name=scenario,
                data_path=Path(data),
                output_dir=Path(output_dir),
                rounds=rounds,
                samples_per_round=samples_per_round,
                elite_fraction=elite_fraction,
                train_steps=train_steps,
                batch_size=batch_size,
                score_conditioned=score_conditioned,
                backend=backend,
                base_model=base_model,
                fine_tune_type=fine_tune_type,
                num_layers=num_layers,
            )
        except Exception as exc:
            logger.debug("cli_self_improve: caught Exception", exc_info=True)
            if json_output:
                _write_json_stderr(str(exc))
            else:
                console.print(f"[red]Self-improving loop failed:[/red] {exc}")
            raise typer.Exit(code=1) from exc

        if json_output:
            _write_json_stdout(result)
            return
        table = Table(title=f"Self-Improving Loop ({result['scenario']})")
        for col in ("Round", "avg_score", "samples", "elite", "dataset_size"):
            table.add_column(col)
        for h in result["history"]:
            table.add_row(
                str(h["round"]),
                f"{h['avg_score']:.4f}",
                str(h["num_samples"]),
                str(h["num_elite"]),
                str(h["dataset_size"]),
            )
        console.print(table)
        console.print(f"[green]Best avg_score:[/green] {result['best_avg_score']:.4f} | final dataset: {result['final_dataset']}")
        # The model to ship is the best-scoring pass, not blindly the final one (the loop can
        # peak early and decay). Highlight it; show the final all-data model only as a footnote.
        if result.get("best_model_dir"):
            console.print(
                f"[bold green]Best model (ship this):[/bold green] {result['best_model_dir']} "
                f"(avg_score {result['best_avg_score']:.4f}, from {result.get('best_round')})"
            )
        if result.get("final_model_dir") and result.get("best_round") != "final":
            console.print(
                f"[dim]Final all-data model:[/dim] {result['final_model_dir']} "
                f"(avg_score {result['final_avg_score']:.4f}, trained on all {result['final_dataset_size']} records)"
            )
