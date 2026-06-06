"""`autoctx train` command, extracted from cli.py to respect module-size limits.

Registered onto the main Typer app via ``register_train_command(app, console)``,
matching the sibling ``register_*_command`` modules.
"""

from __future__ import annotations

import logging
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from autocontext.training.autoresearch.sequence_format import BASE_VOCAB_SIZE
from autocontext.training.runner import TrainingConfig, TrainingResult

logger = logging.getLogger(__name__)


def _run_training(config: TrainingConfig, console: Console, *, json_output: bool = False) -> TrainingResult:
    """Run the training loop. Extracted for testability."""
    from autocontext.training.runner import TrainingRunner

    runner = TrainingRunner(config, work_dir=Path("runs") / f"train_{config.scenario}")
    if not json_output:
        console.print(f"[green]Training workspace:[/green] {runner.work_dir}")
        console.print(
            f"[dim]scenario={config.scenario} budget={config.time_budget}s max_experiments={config.max_experiments}[/dim]"
        )
    return runner.run()


def register_train_command(app: typer.Typer, console: Console) -> None:
    @app.command()
    def train(
        scenario: str = typer.Option("grid_ctf", "--scenario", help="Scenario to train on"),
        data: str = typer.Option("training_data.jsonl", "--data", help="Path to JSONL training data"),
        time_budget: int = typer.Option(300, "--time-budget", help="Training time budget in seconds"),
        max_experiments: int = typer.Option(0, "--max-experiments", help="Max iterations (0 = unlimited)"),
        memory_limit: int = typer.Option(16384, "--memory-limit", help="Peak memory cap in MB"),
        backend: str = typer.Option(
            "mlx", "--backend", help="Training backend (mlx, cuda, mlxlm, grpo). grpo = GSPO/GRPO RLVR (needs mlx-lm-lora)"
        ),
        agent_provider: str = typer.Option("anthropic", "--agent-provider", help="LLM provider for training agent"),
        agent_model: str = typer.Option("", "--agent-model", help="Model for training agent (empty = provider default)"),
        val_select: bool = typer.Option(
            False, "--val-select", help="Keep the best-by-validation-loss checkpoint and early-stop (MLX only)"
        ),
        elite_fraction: float = typer.Option(
            1.0, "--elite-fraction", help="Train on only the top fraction of records by score (1.0 = all)"
        ),
        dedupe: bool = typer.Option(
            False, "--dedupe", help="Drop duplicate constructions, keeping the highest-scoring representative"
        ),
        dedupe_near_threshold: float = typer.Option(
            1.0, "--dedupe-near-threshold", help="With --dedupe, also drop near-duplicates at/above this similarity"
        ),
        score_conditioned: bool = typer.Option(
            False, "--score-conditioned", help="Emit a quality control token; generate conditioned on the top bucket"
        ),
        loss_weight_by_score: str = typer.Option(
            "uniform",
            "--loss-weight-by-score",
            help="Reward-weighted regression: scale each example's loss by its score (uniform | linear | softmax; mlx/cuda)",
        ),
        loss_weight_temperature: float = typer.Option(
            1.0, "--loss-weight-temperature", help="softmax loss-weight temperature (lower = sharper toward top scores)"
        ),
        augmenter: str = typer.Option(
            "",
            "--augmenter",
            help="Record augmenter spec 'package.module:function' for symmetry/transform expansion (empty = none)",
        ),
        vocab_size: int = typer.Option(
            BASE_VOCAB_SIZE, "--vocab-size", help="BPE tokenizer target vocab size (mlx/cuda from-scratch backends)"
        ),
        base_model: str = typer.Option("", "--base-model", help="mlxlm backend: pretrained base model (empty = default)"),
        fine_tune_type: str = typer.Option("lora", "--fine-tune-type", help="mlxlm backend: lora | dora | full"),
        num_layers: int = typer.Option(8, "--num-layers", help="mlxlm backend: layers to fine-tune"),
        json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
    ) -> None:
        """Launch the autoresearch-style training loop."""
        from autocontext.cli import _write_json_stderr, _write_json_stdout

        # Fail fast on out-of-range curation values before any workspace/subprocess setup.
        if not 0.0 < elite_fraction <= 1.0:
            raise typer.BadParameter(f"--elite-fraction must be in (0, 1], got {elite_fraction}")
        if not 0.0 < dedupe_near_threshold <= 1.0:
            raise typer.BadParameter(f"--dedupe-near-threshold must be in (0, 1], got {dedupe_near_threshold}")
        if loss_weight_by_score not in ("uniform", "linear", "softmax"):
            raise typer.BadParameter(f"--loss-weight-by-score must be uniform|linear|softmax, got {loss_weight_by_score!r}")
        if loss_weight_by_score == "softmax" and loss_weight_temperature <= 0:
            raise typer.BadParameter(f"--loss-weight-temperature must be > 0 for softmax, got {loss_weight_temperature}")
        if vocab_size < 256:
            raise typer.BadParameter(f"--vocab-size must be >= 256 (the byte-level BPE base), got {vocab_size}")

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
            val_select=val_select,
            elite_fraction=elite_fraction,
            dedupe=dedupe,
            dedupe_near_threshold=dedupe_near_threshold,
            score_conditioned=score_conditioned,
            loss_weight_mode=loss_weight_by_score,
            loss_weight_temperature=loss_weight_temperature,
            augmenter_spec=augmenter,
            vocab_size=vocab_size,
            base_model=base_model,
            fine_tune_type=fine_tune_type,
            num_layers=num_layers,
        )

        try:
            result = _run_training(config, console, json_output=json_output)
        except KeyboardInterrupt:
            if not json_output:
                console.print("\n[yellow]Training interrupted.[/yellow]")
            raise typer.Exit(code=1) from None
        except Exception as exc:
            logger.debug("cli: caught Exception", exc_info=True)
            if json_output:
                _write_json_stderr(str(exc))
            else:
                console.print(f"[red]Training failed:[/red] {exc}")
            raise typer.Exit(code=1) from exc

        if json_output:
            _write_json_stdout(
                {
                    "scenario": result.scenario,
                    "total_experiments": result.total_experiments,
                    "kept_count": result.kept_count,
                    "discarded_count": result.discarded_count,
                    "best_score": result.best_score,
                    "checkpoint_path": str(result.checkpoint_path) if result.checkpoint_path else None,
                    "published_model_id": result.published_model_id,
                }
            )
            return
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
