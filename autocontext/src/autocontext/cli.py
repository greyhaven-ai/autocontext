from __future__ import annotations

import dataclasses
import json
import logging
import os
import sys
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn

import typer
import uvicorn
from rich.console import Console
from rich.table import Table

from autocontext.agents.orchestrator import AgentOrchestrator
from autocontext.cli_ambient import ambient_app
from autocontext.cli_analytics import register_analytics_command
from autocontext.cli_capabilities import register_capabilities_command
from autocontext.cli_hermes import register_hermes_command
from autocontext.cli_improve import register_improve_command
from autocontext.cli_investigate import run_investigate_command
from autocontext.cli_mission import register_mission_command
from autocontext.cli_new_scenario import register_new_scenario_command
from autocontext.cli_package_commands import register_package_commands
from autocontext.cli_probes import register_probes_command
from autocontext.cli_queue import register_queue_command
from autocontext.cli_role_runtime import resolve_role_runtime
from autocontext.cli_run_inspect import register_run_inspect_commands
from autocontext.cli_runtime_overrides import (
    apply_judge_runtime_overrides,
    format_runtime_provider_error,
)
from autocontext.cli_self_improve import register_self_improve_command
from autocontext.cli_share import register_share_command
from autocontext.cli_solve import register_solve_command
from autocontext.cli_train import register_train_command
from autocontext.cli_worker import register_worker_command
from autocontext.config import load_settings
from autocontext.config.presets import VALID_PRESET_NAMES
from autocontext.config.settings import AppSettings
from autocontext.execution.agent_task_completion import (
    TaskConfig,
    build_evaluator_guardrail_payload,
    compute_effective_met_threshold,
)
from autocontext.execution.improvement_loop import ImprovementLoop
from autocontext.extensions import active_hook_bus
from autocontext.loop.generation_runner import GenerationRunner
from autocontext.loop.runner_hooks import initialize_hook_bus
from autocontext.providers.base import ProviderError
from autocontext.scenarios import SCENARIO_REGISTRY
from autocontext.scenarios.agent_task import AgentTaskInterface
from autocontext.simplicity import append_simplicity_guidance, simplicity_mode_metadata
from autocontext.storage import ArtifactStore, SQLiteStore, artifact_store_from_settings
from autocontext.util.json_io import read_json

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from autocontext.extensions import HookBus
    from autocontext.providers.base import LLMProvider


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
    optimizer_metadata: dict[str, str] | None = None


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
    return artifact_store_from_settings(
        settings,
        enable_buffered_writes=True,
    )


def _write_json_stdout(payload: object) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")


def _write_json_stderr(message: str) -> None:
    sys.stderr.write(json.dumps({"error": message}) + "\n")


@contextmanager
def cli_error_boundary(json_output: bool, *, action: str) -> Iterator[None]:
    """Convert KeyboardInterrupt/Exception into the CLI's JSON-or-Rich exit convention.

    `run` and `resume` both need identical KeyboardInterrupt -> exit(1) and
    Exception -> exit(1) handling, differing only in the action verb used in
    the interrupted-message text (e.g. "run interrupted" / "Run interrupted.",
    "resume interrupted" / "Resume interrupted.").
    """
    try:
        yield
    except KeyboardInterrupt:
        if json_output:
            _write_json_stderr(f"{action} interrupted")
        else:
            console.print(f"[yellow]{action.capitalize()} interrupted.[/yellow]")
        raise typer.Exit(code=1) from None
    except Exception as exc:
        logger.debug("cli: caught Exception", exc_info=True)
        if json_output:
            _write_json_stderr(str(exc))
        else:
            console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(code=1) from exc


def _check_json_exit(result: dict[str, Any]) -> None:
    """Raise SystemExit(1) if JSON result has status=failed (AC-520)."""
    if isinstance(result, dict) and result.get("status") == "failed":
        raise SystemExit(1)


def _exit_provider_error(
    exc: ProviderError,
    *,
    provider_name: str,
    settings: AppSettings,
    json_output: bool,
    ndjson_output: bool = False,
) -> NoReturn:
    message = format_runtime_provider_error(exc, provider_name=provider_name, settings=settings)
    if ndjson_output:
        # AC-752 (P2 follow-up): under --ndjson, stdout is contract-bound to be
        # newline-delimited JSON. Emit a single structured error event on
        # stdout so ndjson consumers don't get a non-JSON line in the stream.
        typer.echo(json.dumps({"event": "error", "message": message}))
    elif json_output:
        _write_json_stderr(message)
    else:
        console.print(f"[red]{message}[/red]")
    raise typer.Exit(code=1) from exc


def _is_agent_task(scenario_name: str) -> bool:
    """Check if a scenario should use the direct agent-task execution path."""
    if scenario_name not in SCENARIO_REGISTRY:
        return False
    from autocontext.scenarios.families import detect_family

    family = detect_family(SCENARIO_REGISTRY[scenario_name]())
    if family is None:
        return False
    return issubclass(family.interface_class, AgentTaskInterface)


def _resolve_simulation_runtime(settings: AppSettings) -> tuple[LLMProvider, str]:
    """Resolve the architect-style runtime used for simulation spec generation.

    Simulations are authoring/spec-generation tasks, so they should follow the
    configured architect runtime surface rather than the judge provider.
    """
    return _resolve_role_runtime(settings, role="architect")


def _resolve_role_runtime(
    settings: AppSettings,
    *,
    role: str,
    scenario_name: str = "",
    hook_bus: HookBus | None = None,
) -> tuple[LLMProvider, str]:
    return resolve_role_runtime(
        settings,
        role=role,
        scenario_name=scenario_name,
        sqlite=_sqlite_from_settings(settings),
        artifacts=_artifacts_from_settings(settings),
        hook_bus=hook_bus,
        orchestrator_cls=AgentOrchestrator,
    )


def _resolve_investigation_runtime(
    settings: AppSettings,
    *,
    role: str,
) -> tuple[LLMProvider, str]:
    return _resolve_role_runtime(settings, role=role)


def _resolve_agent_task_runtime(
    settings: AppSettings,
    scenario_name: str,
    *,
    hook_bus: HookBus | None = None,
) -> tuple[LLMProvider, str]:
    """Resolve the effective competitor runtime for direct agent-task execution."""
    return _resolve_role_runtime(settings, role="competitor", scenario_name=scenario_name, hook_bus=hook_bus)


def _run_agent_task(
    scenario_name: str,
    settings: AppSettings,
    max_rounds: int,
    run_id: str | None,
) -> AgentTaskRunSummary:
    """Execute an agent-task scenario through ImprovementLoop."""
    sqlite = _sqlite_from_settings(settings)
    hook_bus, _loaded_extensions = initialize_hook_bus(settings)
    cls = SCENARIO_REGISTRY[scenario_name]
    instance = cls()
    # Runtime-validated: _is_agent_task() already confirmed this
    task: AgentTaskInterface = instance

    if settings.extensions:
        provider, provider_model = _resolve_agent_task_runtime(settings, scenario_name, hook_bus=hook_bus)
    else:
        provider, provider_model = _resolve_agent_task_runtime(settings, scenario_name)
    state = task.prepare_context(task.initial_state())
    context_errors = task.validate_context(state)
    if context_errors:
        raise ValueError(f"Context validation failed: {'; '.join(context_errors)}")
    prompt = append_simplicity_guidance(task.get_task_prompt(state), settings.simplicity_mode)

    with active_hook_bus(hook_bus):
        initial_output = provider.complete(
            system_prompt="Complete the task precisely.",
            user_prompt=prompt,
            model=provider_model,
        ).text

    loop = ImprovementLoop(
        task=task,
        max_rounds=max_rounds,
        metadata=(simplicity_mode_metadata(settings.simplicity_mode) if settings.simplicity_mode != "off" else None),
    )
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
        with active_hook_bus(hook_bus):
            result = loop.run(initial_output=initial_output, state=state)
    except Exception:
        logger.debug("cli: caught Exception", exc_info=True)
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

    # AC-848: apply the same guardrail-adjusted effective_met_threshold that
    # execution/task_runner.py::_process_task applies, via the shared
    # agent_task_completion helpers, so the two execution paths cannot drift.
    # There is no queued objective_verification config for a direct scenario
    # run, so only the evaluator guardrail (judge_samples / bias probes) can
    # veto here.
    guardrail_config = TaskConfig(
        judge_samples=settings.judge_samples,
        judge_temperature=settings.judge_temperature,
        judge_disagreement_threshold=settings.judge_disagreement_threshold,
        judge_bias_probes_enabled=settings.judge_bias_probes_enabled,
    )
    # The guardrail re-runs the live judge, so it must run inside the same
    # hook-bus context as the loop's judge calls (above), and it must see the
    # same prepared/validated ``state`` the loop ran against -- not a fresh
    # ``task.initial_state()`` that would drop any injected context.
    with active_hook_bus(hook_bus):
        evaluator_guardrail = build_evaluator_guardrail_payload(
            task,
            result.best_output,
            guardrail_config,
            state=state,
        )
    effective_met_threshold = compute_effective_met_threshold(
        result.met_threshold,
        None,
        evaluator_guardrail,
    )
    # When a guardrail vetoes a loop that met the threshold, the persisted
    # termination_reason/gate_decision must not still claim "threshold_met".
    # Mirror the ``"threshold_met" if effective else "max_rounds"`` derivation
    # in execution/task_runner.py::_run_task_multi_generation.
    effective_termination_reason = (
        "max_rounds" if result.met_threshold and not effective_met_threshold else result.termination_reason
    )

    sqlite.append_agent_output(active_run_id, 1, "competitor", result.best_output)
    sqlite.upsert_generation(
        active_run_id,
        1,
        mean_score=result.best_score,
        best_score=result.best_score,
        elo=0.0,
        wins=0,
        losses=0,
        gate_decision=effective_termination_reason,
        status="completed",
        duration_seconds=(result.duration_ms / 1000.0) if result.duration_ms is not None else None,
        evaluator_epoch=getattr(result, "evaluator_epoch", None),
    )

    return AgentTaskRunSummary(
        run_id=active_run_id,
        scenario=scenario_name,
        best_score=result.best_score,
        best_output=result.best_output,
        total_rounds=result.total_rounds,
        met_threshold=effective_met_threshold,
        termination_reason=effective_termination_reason,
        optimizer_metadata=getattr(result, "metadata", {}) or None,
    )


@app.command()
def run(
    scenario_text: str | None = typer.Argument(None, help="Scenario to run"),
    scenario: str = typer.Option("", "--scenario"),
    gens: int | None = typer.Option(None, "--gens", min=1),
    iterations: int | None = typer.Option(None, "--iterations", min=1, help="Plain-language alias for --gens"),
    run_id: str | None = typer.Option(None, "--run-id"),
    serve: bool = typer.Option(False, "--serve", help="Start interactive server alongside generation loop"),
    port: int = typer.Option(8000, "--port", help="Server port (only used with --serve)"),
    preset: str | None = typer.Option(None, "--preset", help=_PRESET_HELP),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """Run generation loop."""
    scenario = scenario.strip() or (scenario_text or "").strip() or "grid_ctf"
    gens = gens if gens is not None else iterations if iterations is not None else 1

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
        with cli_error_boundary(json_output, action="run"):
            task_summary = _run_agent_task(scenario, settings, max_rounds=gens, run_id=run_id)
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
        with cli_error_boundary(json_output, action="run"):
            summary = _runner(preset).run(scenario_name=scenario, generations=gens, run_id=run_id)
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

    with cli_error_boundary(json_output, action="resume"):
        summary = _runner().run(scenario_name=scenario, generations=gens, run_id=run_id)
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
    payload = read_json(replay_files[0])
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
    store = _sqlite_from_settings(settings)
    rows = store.list_runs(limit=20)

    if json_output:
        result = rows
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
    store = _sqlite_from_settings(settings)
    rows = store.run_status(run_id)

    if json_output:
        generations = []
        for row in rows:
            generations.append(
                {
                    "generation": row["generation_index"],
                    "mean_score": row["mean_score"],
                    "best_score": row["best_score"],
                    "elo": row["elo"],
                    "wins": row["wins"],
                    "losses": row["losses"],
                    "gate_decision": row["gate_decision"],
                    "status": row["status"],
                }
            )
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


def _run_http_serve(host: str, port: int) -> None:
    """Backend for `autoctx serve` and `autoctx serve http`.

    Extracted so the canonical sub-Typer group can route bare
    `autoctx serve` (legacy form) and `autoctx serve http`
    (explicit subcommand) through the same code path.
    """
    uvicorn.run("autocontext.server.app:app", host=host, port=port, reload=False)


# AC-697 slice 6: `serve` is a sub-Typer group with `invoke_without_command`
# so the legacy `autoctx serve [--host ...] [--port ...]` form continues
# to start the HTTP API, while the canonical `serve mcp` path the slice-1
# contract pins now exists as a registered subcommand.

# PR #1001 review (P2): the bare callback parses --host/--port options
# whenever they appear before the subcommand. The previous design discarded
# those parsed values when typer saw a subcommand, breaking
# `autoctx serve --host 0.0.0.0 --port 9001 http` (callback values
# ignored, http subcommand used its own defaults). Mirror of the PR #998
# queue fix: stash callback values on `ctx.obj` and merge in the http
# subcommand. Subcommand-explicit > callback-explicit > default.
_SERVE_DEFAULTS: dict[str, Any] = {"host": "127.0.0.1", "port": 8000}


def _merge_serve_options(ctx: typer.Context, **subcommand_values: Any) -> dict[str, Any]:
    """Return effective serve options, merging callback values from ctx.obj."""
    callback_values: dict[str, Any] = (ctx.obj or {}) if isinstance(ctx.obj, dict) else {}
    merged: dict[str, Any] = {}
    for key, default in _SERVE_DEFAULTS.items():
        sub_val = subcommand_values.get(key, default)
        cb_val = callback_values.get(key, default)
        if sub_val != default:
            merged[key] = sub_val
        elif cb_val != default:
            merged[key] = cb_val
        else:
            merged[key] = default
    return merged


_serve_app = typer.Typer(invoke_without_command=True, help="Serve API or MCP endpoints.")


@_serve_app.callback(invoke_without_command=True)
def _serve_root(
    ctx: typer.Context,
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8000, "--port"),
) -> None:
    """`autoctx serve [--host ...] [--port ...]` -> HTTP (legacy form).

    Options passed to the callback (before the subcommand) are stashed
    on `ctx.obj` so subcommands can merge them with their own explicit
    values; this preserves legacy forms like
    `autoctx serve --host 0.0.0.0 --port 9001 http` that put flags
    before the subcommand.
    """
    ctx.obj = {"host": host, "port": port}
    if ctx.invoked_subcommand is not None:
        return
    _run_http_serve(host, port)


@_serve_app.command("http")
def _serve_http(
    ctx: typer.Context,
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8000, "--port"),
) -> None:
    """Serve HTTP API and WebSocket stream."""
    merged = _merge_serve_options(ctx, host=host, port=port)
    _run_http_serve(merged["host"], merged["port"])


@_serve_app.command("mcp")
def _serve_mcp() -> None:
    """Start the MCP server on stdio (canonical path for `mcp-serve`)."""
    try:
        from autocontext.mcp.server import run_server
    except ImportError:
        console.print("[red]MCP dependencies not installed. Run: uv sync --extra mcp[/red]")
        raise typer.Exit(code=1) from None
    run_server()


app.add_typer(_serve_app, name="serve")
app.add_typer(ambient_app, name="ambient")


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
        "AUTOCONTEXT_RLM_ENABLED=false",
        "--baseline",
        help="Comma-separated KEY=VALUE env overrides for baseline",
    ),
    treatment: str = typer.Option(
        "AUTOCONTEXT_RLM_ENABLED=true",
        "--treatment",
        help="Comma-separated KEY=VALUE env overrides for treatment",
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
    provider_override: str = typer.Option("", "--provider", help="Provider override"),
    runs: int = typer.Option(1, "--runs", min=1, help="Number of runs"),
    max_steps: int = typer.Option(0, "--max-steps", help="Max steps per run (0 = auto)"),
    save_as: str = typer.Option("", "--save-as", help="Name for saved simulation"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Run a plain-language simulation with sweeps and analysis."""
    from autocontext.simulation.engine import SimulationEngine

    settings = load_settings()
    if provider_override:
        settings = settings.model_copy(
            update={"agent_provider": provider_override, "architect_provider": provider_override},
        )

    if bool(compare_left) != bool(compare_right):
        console.print("[red]--compare-left and --compare-right must be provided together[/red]")
        raise typer.Exit(code=1)

    # Parse variables
    parsed_vars: dict[str, Any] = {}
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
    parsed_sweep: list[dict[str, Any]] | None = None
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

    runtime_provider, runtime_model = _resolve_simulation_runtime(settings)

    def _llm_fn(system: str, user: str) -> str:
        result = runtime_provider.complete(system, user, model=runtime_model)
        return result.text

    engine = SimulationEngine(llm_fn=_llm_fn, knowledge_root=settings.knowledge_root)

    # Export mode
    if export_id:
        from autocontext.simulation.export import export_simulation

        result = export_simulation(id=export_id, knowledge_root=settings.knowledge_root, format=export_format)
        if json_output:
            _write_json_stdout(result)
            _check_json_exit(result)
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
            _check_json_exit(result)
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
            _check_json_exit(result)
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
        _check_json_exit(result)
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
def investigate(
    description: str = typer.Option("", "--description", "-d", help="Plain-language problem to investigate"),
    max_steps: int = typer.Option(8, "--max-steps", min=1, help="Maximum investigation steps"),
    hypotheses: int = typer.Option(5, "--hypotheses", min=1, help="Maximum hypotheses to generate"),
    save_as: str = typer.Option("", "--save-as", help="Name for the saved investigation"),
    browser_url: str = typer.Option("", "--browser-url", help="Optional browser URL to capture before investigation"),
    mode: str = typer.Option("synthetic", "--mode", help="Investigation mode: synthetic or iterative"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Run a plain-language investigation with evidence and hypotheses."""
    run_investigate_command(
        description=description,
        max_steps=max_steps,
        hypotheses=hypotheses,
        save_as=save_as,
        browser_url=browser_url,
        mode=mode,
        json_output=json_output,
        console=console,
        load_settings_fn=load_settings,
        resolve_investigation_runtime=_resolve_investigation_runtime,
        write_json_stdout=_write_json_stdout,
        write_json_stderr=_write_json_stderr,
        check_json_exit=_check_json_exit,
    )


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
            _write_json_stdout(
                {
                    "fired": True,
                    "condition_id": condition_id,
                    "alert": alert,
                }
            )
        else:
            detail = alert.get("detail", "") if alert else ""
            console.print(f"[green]Alert fired:[/green] {detail}")
    else:
        if json_output:
            _write_json_stdout(
                {
                    "fired": False,
                    "condition_id": condition_id,
                    "timeout_seconds": timeout,
                }
            )
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
    timeout: float | None = typer.Option(
        None,
        "--timeout",
        min=1.0,
        help="Override runtime timeout in seconds for CLI-backed providers",
    ),
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
) -> None:
    """One-shot evaluation of agent output against a rubric."""
    from autocontext.execution.judge import LLMJudge

    settings = apply_judge_runtime_overrides(
        load_settings(),
        provider_name=provider,
        model=model,
        timeout=timeout,
    )

    try:
        from autocontext.providers.registry import get_provider

        judge_provider = get_provider(settings)
        llm_judge = LLMJudge(
            provider=judge_provider,
            model=settings.judge_model,
            rubric=rubric,
        )
        result = llm_judge.evaluate(task_prompt=task_prompt, agent_output=output)
    except ProviderError as exc:
        _exit_provider_error(
            exc,
            provider_name=settings.judge_provider,
            settings=settings,
            json_output=json_output,
        )

    if json_output:
        _write_json_stdout(
            {
                "score": result.score,
                "reasoning": result.reasoning,
                "dimension_scores": result.dimension_scores,
            }
        )
    else:
        console.print(f"[bold]Score:[/bold] {result.score:.4f}")
        console.print(f"[bold]Reasoning:[/bold] {result.reasoning}")


register_analytics_command(app, console=console)
register_capabilities_command(app, console=console)
register_hermes_command(app, console=console)
register_improve_command(app, console=console)
register_mission_command(app, console=console)
register_new_scenario_command(app, console=console)
register_package_commands(app, console)
register_run_inspect_commands(app, console=console)
register_solve_command(app, console=console)
register_probes_command(app, console=console)
register_queue_command(app, console=console)
register_self_improve_command(app, console)
register_share_command(app, console=console)
register_train_command(app, console)
register_worker_command(app, console=console)


if __name__ == "__main__":
    app()
