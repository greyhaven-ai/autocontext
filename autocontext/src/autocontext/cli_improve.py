"""Registration of the `autoctx improve` command.

Extracted from `cli.py` to keep that file under the grandfathered module-size
limit. Mirrors the `register_*_command` pattern used by analytics, hermes,
new-scenario, solve, queue, and worker commands.
"""

from __future__ import annotations

import dataclasses
import importlib
import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import typer

from autocontext.cli_runtime_overrides import apply_judge_runtime_overrides
from autocontext.execution.improvement_events import ImprovementLoopEvent
from autocontext.providers.base import ProviderError

if TYPE_CHECKING:
    from rich.console import Console


def _cli_attr(dependency_module: str, name: str) -> Any:
    """Fetch a symbol from the host CLI module without importing at top level.

    The host CLI module (`autocontext.cli`) defines `load_settings`,
    `_exit_provider_error`, and `_write_json_stdout`. Reaching them through
    `importlib.import_module` keeps this module decoupled and lets tests
    patch those symbols on the host module path.
    """
    return getattr(importlib.import_module(dependency_module), name)


def register_improve_command(
    app: typer.Typer,
    *,
    console: Console,
    dependency_module: str = "autocontext.cli",
) -> None:
    """Register the `improve` command on the host Typer app.

    Splits out of `cli.py` (AC-752 follow-up) so that file stays under its
    grandfathered module-size limit.
    """

    @app.command()
    def improve(  # noqa: D401  -- Typer command surface; keep imperative
        task_prompt: str = typer.Option(..., "--task-prompt", "-p", help="The task prompt"),
        rubric: str = typer.Option(..., "--rubric", "-r", help="Evaluation rubric"),
        initial_output: str = typer.Option("", "--output", "-o", help="Starting output to improve"),
        max_rounds: int = typer.Option(5, "--rounds", "-n", help="Maximum improvement rounds"),
        threshold: float = typer.Option(0.9, "--threshold", "-t", help="Quality threshold to stop"),
        provider_override: str = typer.Option("", "--provider", help="Provider override"),
        timeout: float | None = typer.Option(
            None,
            "--timeout",
            min=1.0,
            help=(
                "Override per-call provider timeout in seconds. For claude-cli this "
                "writes claude_timeout (env: AUTOCONTEXT_CLAUDE_TIMEOUT, default 600s); "
                "for codex it writes codex_timeout; for pi/pi-rpc it writes pi_timeout. "
                "For the overall claude-cli wall-clock budget, see --claude-max-total-seconds."
            ),
        ),
        claude_max_total_seconds: float | None = typer.Option(
            None,
            "--claude-max-total-seconds",
            min=0.0,
            help=(
                "Override the wall-clock ceiling on total claude-cli runtime across all "
                "invocations during this run (env: AUTOCONTEXT_CLAUDE_MAX_TOTAL_SECONDS, "
                "default 0=off). Only applied when the resolved judge provider is claude-cli."
            ),
        ),
        json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
        ndjson_output: bool = typer.Option(
            False,
            "--ndjson",
            help=(
                "Stream per-round events as newline-delimited JSON to stdout (AC-752). "
                "Useful for long-running loops where --json would buffer all output until "
                "completion. Emits one JSON line per event: round_start, judge_done, "
                "verifier_done, round_summary, and a final summary line."
            ),
        ),
        verify_cmd: str = typer.Option(
            "",
            "--verify-cmd",
            help=(
                "External command to verify each round's output (AC-733). "
                "Non-zero exit forces the round score to 0 and feeds the "
                "command's stderr/stdout into the next revision prompt. "
                "Use the literal `{file}` placeholder to receive the output as a "
                "temp-file path; otherwise the output is piped to stdin. "
                "Examples: 'lake env lean {file}', 'mypy {file}', 'cargo check'."
            ),
        ),
        verify_suffix: str = typer.Option(
            ".txt",
            "--verify-suffix",
            help="Suffix for the temp file passed to --verify-cmd (e.g. '.lean', '.py').",
        ),
        verify_timeout: float = typer.Option(
            300.0,
            "--verify-timeout",
            min=1.0,
            help="Timeout in seconds for each --verify-cmd invocation.",
        ),
    ) -> None:
        """Run multi-round improvement loop on agent output.

        Creates a simple agent task from the prompt and rubric, then runs
        the improvement loop with judge-guided iteration.
        """
        from autocontext.execution.improvement_loop import ImprovementLoop
        from autocontext.execution.output_verifier import make_verifier
        from autocontext.execution.task_runner import SimpleAgentTask
        from autocontext.providers.registry import get_provider as get_judge_provider

        load_settings = _cli_attr(dependency_module, "load_settings")
        write_json_stdout = _cli_attr(dependency_module, "_write_json_stdout")
        exit_provider_error = _cli_attr(dependency_module, "_exit_provider_error")

        # AC-752 (P3 follow-up): --json (single final blob) and --ndjson (streaming
        # events) are mutually exclusive output modes. Passing both produces a
        # mixed, un-parseable stream. Reject up front with a clear error.
        if json_output and ndjson_output:
            typer.echo(
                "Error: --json and --ndjson are mutually exclusive output modes; pick one.",
                err=True,
            )
            raise typer.Exit(code=2)

        settings = apply_judge_runtime_overrides(
            load_settings(),
            provider_name=provider_override,
            timeout=timeout,
            claude_max_total_seconds=claude_max_total_seconds,
        )

        try:
            provider = get_judge_provider(settings)
            task = SimpleAgentTask(
                task_prompt=task_prompt,
                rubric=rubric,
                provider=provider,
                model=settings.judge_model,
            )
            state = task.initial_state()
            verifier = make_verifier(
                verify_cmd or None,
                file_suffix=verify_suffix,
                timeout_s=verify_timeout,
            )
            # AC-752: when --ndjson is set, stream per-round events as JSON lines
            # so long-running loops have progress visibility before --json's final
            # blob lands. The event sink writes one compact JSON line per event.
            on_event: Callable[[ImprovementLoopEvent], None] | None = None
            if ndjson_output:

                def _emit_ndjson(event: ImprovementLoopEvent) -> None:
                    payload = {k: v for k, v in dataclasses.asdict(event).items() if v is not None}
                    typer.echo(json.dumps(payload))

                on_event = _emit_ndjson
            loop = ImprovementLoop(
                task=task,
                max_rounds=max_rounds,
                quality_threshold=threshold,
                output_verifier=verifier,
                on_event=on_event,
            )
            starting_output = initial_output or task.generate_output(state)
            result = loop.run(initial_output=starting_output, state=state)
        except ProviderError as exc:
            exit_provider_error(
                exc,
                provider_name=settings.judge_provider,
                settings=settings,
                json_output=json_output,
                ndjson_output=ndjson_output,
            )

        if ndjson_output:
            # Pure newline-delimited JSON on stdout (already streamed via on_event).
            # Suppress the Rich human-readable summary so consumers can parse each
            # stdout line as JSON. --json + --ndjson is rejected up front.
            pass
        elif json_output:
            write_json_stdout(
                {
                    "best_score": result.best_score,
                    "best_round": result.best_round,
                    "total_rounds": result.total_rounds,
                    "met_threshold": result.met_threshold,
                    "best_output": result.best_output,
                }
            )
        else:
            console.print(f"[bold]Best score:[/bold] {result.best_score:.4f} (round {result.best_round})")
            console.print(f"[bold]Rounds:[/bold] {result.total_rounds}")
            console.print(f"[bold]Met threshold:[/bold] {result.met_threshold}")
