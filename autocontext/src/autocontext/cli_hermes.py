from __future__ import annotations

import importlib
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import typer

from autocontext.cli_hermes_runners import (
    run_hermes_export_dataset_command,
    run_hermes_export_skill_command,
    run_hermes_ingest_curator_command,
    run_hermes_ingest_sessions_command,
    run_hermes_ingest_trajectories_command,
    run_hermes_inspect_command,
    run_hermes_recommend_command,
    run_hermes_train_advisor_command,
    run_hermes_validate_skill_command,
)

if TYPE_CHECKING:
    from rich.console import Console


def _cli_attr(dependency_module: str, name: str) -> Any:
    return getattr(importlib.import_module(dependency_module), name)


def register_hermes_command(
    app: typer.Typer,
    *,
    console: Console,
    dependency_module: str = "autocontext.cli",
) -> None:
    hermes_app = typer.Typer(help="Hermes Agent integration helpers")

    @hermes_app.command("inspect")
    def inspect(
        home: Annotated[
            Path | None,
            typer.Option("--home", help="Hermes home directory (default: HERMES_HOME or ~/.hermes)"),
        ] = None,
        json_output: Annotated[bool, typer.Option("--json", help="Output structured JSON")] = False,
    ) -> None:
        """Read Hermes skill usage and Curator reports without mutating Hermes."""

        run_hermes_inspect_command(
            home=home,
            json_output=json_output,
            console=console,
            write_json_stdout=_cli_attr(dependency_module, "_write_json_stdout"),
        )

    @hermes_app.command("export-skill")
    def export_skill(
        output: Annotated[
            Path | None,
            typer.Option("--output", help="Write the Hermes SKILL.md to this path; omit to print it"),
        ] = None,
        force: Annotated[bool, typer.Option("--force", help="Overwrite --output and any existing references")] = False,
        with_references: Annotated[
            bool,
            typer.Option(
                "--with-references",
                help="Also write progressive-disclosure references next to SKILL.md (AC-702)",
            ),
        ] = False,
        json_output: Annotated[bool, typer.Option("--json", help="Output structured JSON")] = False,
    ) -> None:
        """Emit the first-class Hermes autocontext skill."""

        run_hermes_export_skill_command(
            output=output,
            force=force,
            with_references=with_references,
            json_output=json_output,
            console=console,
            write_json_stdout=_cli_attr(dependency_module, "_write_json_stdout"),
            write_json_stderr=_cli_attr(dependency_module, "_write_json_stderr"),
        )

    @hermes_app.command("ingest-curator")
    def ingest_curator(
        home: Annotated[
            Path | None,
            typer.Option("--home", help="Hermes home directory (default: HERMES_HOME or ~/.hermes)"),
        ] = None,
        output: Annotated[
            Path,
            typer.Option("--output", help="Destination JSONL path for ProductionTrace entries"),
        ] = Path("hermes-curator-traces.jsonl"),
        since: Annotated[
            str | None,
            typer.Option("--since", help="ISO-8601 timestamp; skip curator runs strictly before this"),
        ] = None,
        limit: Annotated[
            int | None,
            typer.Option("--limit", help="Maximum number of traces to write"),
        ] = None,
        include_llm_final: Annotated[
            bool,
            typer.Option(
                "--include-llm-final",
                help="Attach the curator's LLM final summary as an assistant message (off by default for privacy)",
            ),
        ] = False,
        include_tool_args: Annotated[
            bool,
            typer.Option(
                "--include-tool-args",
                help="Attach raw tool-call args (off by default to avoid leaking sensitive arguments)",
            ),
        ] = False,
        json_output: Annotated[bool, typer.Option("--json", help="Output structured JSON")] = False,
    ) -> None:
        """Ingest Hermes curator reports into ProductionTrace JSONL (AC-704)."""

        run_hermes_ingest_curator_command(
            home=home,
            output=output,
            since=since,
            limit=limit,
            include_llm_final=include_llm_final,
            include_tool_args=include_tool_args,
            json_output=json_output,
            console=console,
            write_json_stdout=_cli_attr(dependency_module, "_write_json_stdout"),
        )

    @hermes_app.command("export-dataset")
    def export_dataset_cmd(
        kind: Annotated[
            str,
            typer.Option(
                "--kind",
                help="Dataset kind: curator-decisions (shipped); other kinds documented but not yet implemented",
            ),
        ] = "curator-decisions",
        home: Annotated[
            Path | None,
            typer.Option("--home", help="Hermes home directory (default: HERMES_HOME or ~/.hermes)"),
        ] = None,
        output: Annotated[
            Path,
            typer.Option("--output", help="Destination JSONL path for training examples"),
        ] = Path("hermes-curator-decisions.jsonl"),
        since: Annotated[
            str | None,
            typer.Option("--since", help="ISO-8601 timestamp; skip curator runs strictly before this"),
        ] = None,
        limit: Annotated[
            int | None,
            typer.Option("--limit", help="Maximum number of examples to write"),
        ] = None,
        json_output: Annotated[bool, typer.Option("--json", help="Output structured JSON")] = False,
    ) -> None:
        """Export Hermes curator decisions as training JSONL (AC-705)."""

        run_hermes_export_dataset_command(
            kind=kind,
            home=home,
            output=output,
            since=since,
            limit=limit,
            json_output=json_output,
            console=console,
            write_json_stdout=_cli_attr(dependency_module, "_write_json_stdout"),
        )

    @hermes_app.command("ingest-trajectories")
    def ingest_trajectories(
        input_path: Annotated[
            Path,
            typer.Option(
                "--input",
                help="Source JSONL file (trajectory_samples.jsonl, failed_trajectories.jsonl, or batch export)",
            ),
        ],
        output: Annotated[
            Path,
            typer.Option("--output", help="Destination JSONL path for redacted trajectories"),
        ] = Path("hermes-trajectories-redacted.jsonl"),
        redact: Annotated[
            str,
            typer.Option(
                "--redact",
                help="Redaction mode: off | standard (default) | strict. 'strict' requires --user-patterns.",
            ),
        ] = "standard",
        user_patterns_json: Annotated[
            str | None,
            typer.Option(
                "--user-patterns",
                help="JSON array of {name, pattern} regex objects for --redact strict",
            ),
        ] = None,
        limit: Annotated[
            int | None,
            typer.Option("--limit", help="Maximum number of trajectories to write"),
        ] = None,
        dry_run: Annotated[
            bool,
            typer.Option(
                "--dry-run",
                help="Count and redact but do not write the output file (AC-706 privacy preview)",
            ),
        ] = False,
        json_output: Annotated[bool, typer.Option("--json", help="Output structured JSON")] = False,
    ) -> None:
        """Ingest a Hermes trajectory JSONL with explicit redaction (AC-706 slice 1)."""

        run_hermes_ingest_trajectories_command(
            input_path=input_path,
            output=output,
            redact=redact,
            user_patterns_json=user_patterns_json,
            limit=limit,
            dry_run=dry_run,
            json_output=json_output,
            console=console,
            write_json_stdout=_cli_attr(dependency_module, "_write_json_stdout"),
            write_json_stderr=_cli_attr(dependency_module, "_write_json_stderr"),
        )

    @hermes_app.command("ingest-sessions")
    def ingest_sessions(
        home: Annotated[
            Path | None,
            typer.Option("--home", help="Hermes home directory (default: HERMES_HOME or ~/.hermes)"),
        ] = None,
        output: Annotated[
            Path,
            typer.Option("--output", help="Destination JSONL path for ProductionTrace entries"),
        ] = Path("hermes-sessions.jsonl"),
        redact: Annotated[
            str,
            typer.Option(
                "--redact",
                help="Redaction mode: off | standard (default) | strict. 'strict' requires --user-patterns.",
            ),
        ] = "standard",
        user_patterns_json: Annotated[
            str | None,
            typer.Option(
                "--user-patterns",
                help="JSON array of {name, pattern} regex objects for --redact strict",
            ),
        ] = None,
        since: Annotated[
            str | None,
            typer.Option("--since", help="ISO-8601 timestamp; skip sessions strictly before this"),
        ] = None,
        limit: Annotated[
            int | None,
            typer.Option("--limit", help="Maximum number of session traces to write"),
        ] = None,
        dry_run: Annotated[
            bool,
            typer.Option(
                "--dry-run",
                help="Count and redact but do not write the output file (AC-706 privacy preview)",
            ),
        ] = False,
        json_output: Annotated[bool, typer.Option("--json", help="Output structured JSON")] = False,
    ) -> None:
        """Ingest Hermes session DB into ProductionTrace JSONL (AC-706 slice 2)."""

        run_hermes_ingest_sessions_command(
            home=home,
            output=output,
            redact=redact,
            user_patterns_json=user_patterns_json,
            since=since,
            limit=limit,
            dry_run=dry_run,
            json_output=json_output,
            console=console,
            write_json_stdout=_cli_attr(dependency_module, "_write_json_stdout"),
            write_json_stderr=_cli_attr(dependency_module, "_write_json_stderr"),
        )

    @hermes_app.command("train-advisor")
    def train_advisor(
        data: Annotated[
            Path,
            typer.Option(
                "--data",
                help="AC-705 curator-decisions JSONL to train and evaluate on",
            ),
        ],
        baseline: Annotated[
            bool,
            typer.Option(
                "--baseline",
                help="Train the majority-class baseline (AC-708 slice 1)",
            ),
        ] = False,
        logistic: Annotated[
            bool,
            typer.Option(
                "--logistic",
                help="Train the pure-Python logistic-regression advisor (AC-708 slice 2a)",
            ),
        ] = False,
        mlx: Annotated[
            bool,
            typer.Option(
                "--mlx",
                help="Train the MLX-backed logistic-regression advisor (AC-708 slice 2b; "
                "requires `pip install autocontext[mlx]`)",
            ),
        ] = False,
        output: Annotated[
            Path | None,
            typer.Option(
                "--output",
                help="Optional metrics JSON destination; --json prints to stdout regardless",
            ),
        ] = None,
        checkpoint: Annotated[
            Path | None,
            typer.Option(
                "--checkpoint",
                help="Optional advisor-checkpoint destination (--logistic or --mlx only; recommend --advisor consumes it)",
            ),
        ] = None,
        json_output: Annotated[bool, typer.Option("--json", help="Output structured JSON")] = False,
    ) -> None:
        """Train + evaluate a Hermes curator advisor (AC-708)."""

        run_hermes_train_advisor_command(
            data=data,
            baseline=baseline,
            logistic=logistic,
            mlx=mlx,
            output=output,
            checkpoint=checkpoint,
            json_output=json_output,
            console=console,
            write_json_stdout=_cli_attr(dependency_module, "_write_json_stdout"),
            write_json_stderr=_cli_attr(dependency_module, "_write_json_stderr"),
        )

    @hermes_app.command("recommend")
    def recommend_cmd(
        home: Annotated[
            Path | None,
            typer.Option("--home", help="Hermes home directory (default: HERMES_HOME or ~/.hermes)"),
        ] = None,
        baseline_from: Annotated[
            Path | None,
            typer.Option(
                "--baseline-from",
                help="AC-705 curator-decisions JSONL to train a baseline advisor from",
            ),
        ] = None,
        advisor_path: Annotated[
            Path | None,
            typer.Option(
                "--advisor",
                help="Trained advisor checkpoint (AC-708 slice 2a; produced by train-advisor --logistic --checkpoint)",
            ),
        ] = None,
        output: Annotated[
            Path,
            typer.Option("--output", help="Destination JSONL for the recommendations"),
        ] = Path("hermes-recommendations.jsonl"),
        include_protected: Annotated[
            bool,
            typer.Option(
                "--include-protected",
                help="Surface recommendations for pinned/bundled/hub skills tagged status=protected",
            ),
        ] = False,
        json_output: Annotated[bool, typer.Option("--json", help="Output structured JSON")] = False,
    ) -> None:
        """Emit read-only advisor recommendations against a live Hermes home (AC-709)."""

        run_hermes_recommend_command(
            home=home,
            baseline_from=baseline_from,
            advisor_path=advisor_path,
            output=output,
            include_protected=include_protected,
            json_output=json_output,
            console=console,
            write_json_stdout=_cli_attr(dependency_module, "_write_json_stdout"),
            write_json_stderr=_cli_attr(dependency_module, "_write_json_stderr"),
        )

    @hermes_app.command("validate-skill")
    def validate_skill_cmd(
        output: Annotated[
            Path | None,
            typer.Option(
                "--output",
                help="Optional markdown report destination; --json prints to stdout regardless",
            ),
        ] = None,
        json_output: Annotated[bool, typer.Option("--json", help="Output structured JSON")] = False,
    ) -> None:
        """Validate the rendered Hermes autocontext SKILL.md (AC-711)."""

        run_hermes_validate_skill_command(
            output=output,
            json_output=json_output,
            console=console,
            write_json_stdout=_cli_attr(dependency_module, "_write_json_stdout"),
            write_json_stderr=_cli_attr(dependency_module, "_write_json_stderr"),
        )

    app.add_typer(hermes_app, name="hermes")
