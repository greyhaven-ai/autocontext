"""AC-697 mission CLI subcommands (slice 4).

Mirrors `ts/src/cli/mission-command-workflow.ts` +
`mission-command-execution.ts` for the `create / run / status / list
/ artifacts` subcommands. The `pause / resume / cancel` lifecycle
subcommands + the contract flip land in slice 5.

All subcommands accept ``--json`` to emit a structured payload to
stdout. Default output is human-readable. Errors route to stderr
via the same pattern as `cli_probes.py` (PR #1009 review fix) so
JSON consumers can safely parse stdout on failure paths.

The CLI delegates to the slice-3 control-plane helpers
(`build_mission_status_payload`, `build_mission_artifacts_payload`,
`run_mission_loop`, `write_mission_checkpoint`) and the slice-2
`MissionManager` + `CodeMissionSpec` + `create_code_mission`
factory. State lives under the same SQLite file the rest of
autocontext uses (`settings.db_path`); checkpoints land under
`<settings.runs_root>/missions/<mission_id>/checkpoints/`.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import typer
from rich.console import Console

from autocontext.config import AppSettings, load_settings
from autocontext.mission import (
    CodeMissionSpec,
    MissionBudget,
    MissionManager,
    build_mission_artifacts_payload,
    build_mission_status_payload,
    create_code_mission,
    run_mission_loop,
    write_mission_checkpoint,
)

__all__ = [
    "MISSION_HELP_TEXT",
    "MissionCreatePlan",
    "MissionRunPlan",
    "register_mission_command",
]


MISSION_HELP_TEXT = """autoctx mission -- Manage verifier-driven missions

Subcommands:
  create     Create a new mission
  run        Advance a mission and write a checkpoint
  status     Show mission details
  list       List all missions
  artifacts  Inspect saved mission checkpoints

Examples:
  autoctx mission create --name "Ship login" --goal "Implement OAuth"
  autoctx mission create --type code --name "Fix login" --goal "Tests pass" --repo-path . --test-command "pytest"
  autoctx mission run --id mission-abc123 --max-iterations 3
  autoctx mission list --status active
  autoctx mission status --id mission-abc123
  autoctx mission artifacts --id mission-abc123
"""


# ---------------------------------------------------------------------------
# Plan dataclasses (mirror TS planMissionCreate / planMissionRun)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MissionCreatePlan:
    mission_type: str  # "generic" or "code"
    name: str
    goal: str
    budget: MissionBudget | None
    repo_path: str | None = None
    test_command: str | None = None
    lint_command: str | None = None
    build_command: str | None = None


@dataclass(frozen=True)
class MissionRunPlan:
    mission_id: str
    max_iterations: int
    step_description: str | None


def _parse_optional_positive_int(raw: int | None, label: str) -> int | None:
    if raw is None:
        return None
    if raw <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return raw


def plan_mission_create(
    *,
    mission_type: str | None,
    name: str | None,
    goal: str | None,
    max_steps: int | None,
    repo_path: str | None,
    test_command: str | None,
    lint_command: str | None,
    build_command: str | None,
) -> MissionCreatePlan:
    if not name or not goal:
        raise ValueError(
            "Usage: autoctx mission create --name <name> --goal <goal> "
            "[--type code --repo-path <path> --test-command <cmd> "
            "[--lint-command <cmd>] [--build-command <cmd>]] [--max-steps N]"
        )
    budget_steps = _parse_optional_positive_int(max_steps, "--max-steps")
    budget = MissionBudget(max_steps=budget_steps) if budget_steps else None
    resolved_type = (
        "code" if (mission_type == "code" or repo_path or test_command or lint_command or build_command) else "generic"
    )
    if resolved_type == "code":
        if not repo_path or not test_command:
            raise ValueError("Code missions require --repo-path and --test-command.")
        return MissionCreatePlan(
            mission_type="code",
            name=name,
            goal=goal,
            budget=budget,
            repo_path=str(Path(repo_path).resolve()),
            test_command=test_command,
            lint_command=lint_command,
            build_command=build_command,
        )
    return MissionCreatePlan(mission_type="generic", name=name, goal=goal, budget=budget)


def plan_mission_run(
    *,
    mission_id: str | None,
    max_iterations: int | None,
    step_description: str | None,
) -> MissionRunPlan:
    if not mission_id:
        raise ValueError("Usage: autoctx mission run --id <mission-id> [--max-iterations N] [--step-description <text>]")
    resolved_iterations = _parse_optional_positive_int(max_iterations, "--max-iterations") or 1
    return MissionRunPlan(
        mission_id=mission_id,
        max_iterations=resolved_iterations,
        step_description=step_description,
    )


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def _model_dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _write_error_stderr(message: str) -> None:
    """Mirrors `cli_probes.py` (PR #1009 review fix): write directly
    to stderr so `--json` consumers can safely parse stdout on
    failure paths."""
    sys.stderr.write(message)
    if not message.endswith("\n"):
        sys.stderr.write("\n")
    sys.stderr.flush()


def _mission_manager(settings: AppSettings) -> MissionManager:
    return MissionManager(str(settings.db_path))


def _checkpoint_runs_root(settings: AppSettings) -> str:
    return str(settings.runs_root)


def _checkpoint_payload(manager: MissionManager, mission_id: str, runs_root: str) -> dict[str, Any]:
    payload = build_mission_status_payload(manager, mission_id)
    payload["checkpointPath"] = write_mission_checkpoint(manager, mission_id, runs_root)
    return payload


def _render_mission_status_text(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"Mission {payload['id']}  [{payload['status']}]")
    lines.append(f"  name:   {payload['name']}")
    lines.append(f"  goal:   {payload['goal']}")
    if payload.get("budget"):
        budget = payload["budget"]
        cap = budget.get("max_steps")
        if cap is not None:
            lines.append(f"  budget: max_steps={cap}")
    lines.append(
        f"  steps={payload['stepsCount']} subgoals={payload['subgoalCount']} verifications={payload['verificationCount']}"
    )
    usage = payload.get("budgetUsage") or {}
    lines.append(f"  usage:  steps_used={usage.get('steps_used', 0)} exhausted={usage.get('exhausted', False)}")
    latest = payload.get("latestVerification")
    if latest is not None:
        outcome = "pass" if latest.get("passed") else "fail"
        lines.append(f"  verify: [{outcome}] {latest.get('reason', '')}")
    if "checkpointPath" in payload:
        lines.append(f"  checkpoint: {payload['checkpointPath']}")
    return "\n".join(lines)


def _render_mission_list_text(missions: list[dict[str, Any]]) -> str:
    if not missions:
        return "No missions."
    lines = ["MISSIONS"]
    for mission in missions:
        lines.append(f"  {mission['id']}  [{mission['status']}]  {mission['name']}")
    return "\n".join(lines)


def _render_mission_artifacts_text(payload: dict[str, Any]) -> str:
    lines = [f"Mission {payload['missionId']}  [{payload['status']}]"]
    checkpoints = payload.get("checkpoints", [])
    if not checkpoints:
        lines.append("  no checkpoints")
        return "\n".join(lines)
    for entry in checkpoints:
        lines.append(f"  {entry['name']}  {entry['sizeBytes']}B  {entry['updatedAt']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Typer command registry
# ---------------------------------------------------------------------------


def register_mission_command(app: typer.Typer, *, console: Console) -> None:
    """Mount the ``mission`` sub-Typer on ``app`` with the slice-4
    subcommands (create / run / status / list / artifacts). The
    slice-5 lifecycle subcommands land on the same sub-app.

    ``invoke_without_command=True`` so the contract walker yields
    ``("mission",)`` as a registered path (matches the slice-3
    `serve` and slice-3 `queue` groups). The empty callback just
    prints help when the bare ``autoctx mission`` invocation is
    used; subcommands behave as registered.
    """
    mission_app = typer.Typer(invoke_without_command=True, help="Manage verifier-driven missions.")

    @mission_app.callback(invoke_without_command=True)
    def _mission_root(ctx: typer.Context) -> None:
        if ctx.invoked_subcommand is None:
            console.print(MISSION_HELP_TEXT)

    @mission_app.command("create")
    def _create(
        name: str = typer.Option("", "--name", help="Mission name."),
        goal: str = typer.Option("", "--goal", help="Mission goal."),
        mission_type: str = typer.Option("", "--type", help="Mission type: generic (default) or code."),
        max_steps: int | None = typer.Option(None, "--max-steps", help="Optional max-step budget."),
        repo_path: str = typer.Option("", "--repo-path", help="Repository path (required for code missions)."),
        test_command: str = typer.Option(
            "",
            "--test-command",
            help="Shell command that verifies success (required for code missions).",
        ),
        lint_command: str = typer.Option("", "--lint-command", help="Optional secondary lint command."),
        build_command: str = typer.Option("", "--build-command", help="Optional secondary build command."),
        json_output: bool = typer.Option(False, "--json", help="Emit a structured JSON payload."),
    ) -> None:
        """Create a new mission. Writes a checkpoint immediately so
        the slice-4 `artifacts` subcommand always has a baseline to
        list."""
        try:
            plan = plan_mission_create(
                mission_type=mission_type or None,
                name=name or None,
                goal=goal or None,
                max_steps=max_steps,
                repo_path=repo_path or None,
                test_command=test_command or None,
                lint_command=lint_command or None,
                build_command=build_command or None,
            )
        except ValueError as err:
            _write_error_stderr(f"autoctx mission create: {err}")
            raise typer.Exit(code=1) from err

        settings = load_settings()
        with _mission_manager(settings) as mgr:
            if plan.mission_type == "code":
                assert plan.repo_path is not None and plan.test_command is not None
                spec = CodeMissionSpec(
                    name=plan.name,
                    goal=plan.goal,
                    repo_path=plan.repo_path,
                    test_command=plan.test_command,
                    lint_command=plan.lint_command,
                    build_command=plan.build_command,
                    budget=plan.budget,
                )
                mission_id = create_code_mission(mgr, spec)
            else:
                mission_id = mgr.create(name=plan.name, goal=plan.goal, budget=plan.budget)
            payload = _checkpoint_payload(mgr, mission_id, _checkpoint_runs_root(settings))

        if json_output:
            print(json.dumps(payload, indent=2))
        else:
            console.print(_render_mission_status_text(payload))

    @mission_app.command("run")
    def _run(
        mission_id: str = typer.Option("", "--id", help="Mission id to advance."),
        max_iterations: int | None = typer.Option(
            None,
            "--max-iterations",
            help="Cap on iterations for this run (default 1).",
        ),
        step_description: str = typer.Option(
            "",
            "--step-description",
            help="Override the auto-generated step description.",
        ),
        json_output: bool = typer.Option(False, "--json", help="Emit a structured JSON payload."),
    ) -> None:
        """Advance the mission via the slice-3 legacy mission loop
        and write a checkpoint. Adaptive LLM-driven planning lands
        in a follow-up slice."""
        try:
            plan = plan_mission_run(
                mission_id=mission_id or None,
                max_iterations=max_iterations,
                step_description=step_description or None,
            )
        except ValueError as err:
            _write_error_stderr(f"autoctx mission run: {err}")
            raise typer.Exit(code=1) from err

        settings = load_settings()
        with _mission_manager(settings) as mgr:
            try:
                result = run_mission_loop(
                    mgr,
                    plan.mission_id,
                    _checkpoint_runs_root(settings),
                    max_iterations=plan.max_iterations,
                    step_description=plan.step_description,
                )
            except ValueError as err:
                _write_error_stderr(f"autoctx mission run: {err}")
                raise typer.Exit(code=1) from err

        if json_output:
            print(json.dumps(result, indent=2))
        else:
            console.print(
                f"Mission {result['id']}  [{result['finalStatus']}]\n"
                f"  steps_executed: {result['stepsExecuted']}\n"
                f"  verifier_passed: {result['verifierPassed']}\n"
                f"  checkpoint: {result['checkpointPath']}"
            )

    @mission_app.command("status")
    def _status(
        mission_id: str = typer.Option("", "--id", help="Mission id."),
        json_output: bool = typer.Option(False, "--json", help="Emit a structured JSON payload."),
    ) -> None:
        """Show the current state of a mission."""
        if not mission_id:
            _write_error_stderr("autoctx mission status: --id <mission-id> is required")
            raise typer.Exit(code=1)
        settings = load_settings()
        with _mission_manager(settings) as mgr:
            try:
                payload = build_mission_status_payload(mgr, mission_id)
            except ValueError as err:
                _write_error_stderr(f"autoctx mission status: {err}")
                raise typer.Exit(code=1) from err
        if json_output:
            print(json.dumps(payload, indent=2))
        else:
            console.print(_render_mission_status_text(payload))

    @mission_app.command("list")
    def _list(
        status: str = typer.Option("", "--status", help="Filter by mission status."),
        json_output: bool = typer.Option(False, "--json", help="Emit a structured JSON payload."),
    ) -> None:
        """List missions, optionally filtered by status."""
        settings = load_settings()
        with _mission_manager(settings) as mgr:
            missions = mgr.list_missions(status or None)  # type: ignore[arg-type]
        payload = [_model_dump(m) for m in missions]
        if json_output:
            print(json.dumps(payload, indent=2))
        else:
            console.print(_render_mission_list_text(payload))

    @mission_app.command("artifacts")
    def _artifacts(
        mission_id: str = typer.Option("", "--id", help="Mission id."),
        json_output: bool = typer.Option(False, "--json", help="Emit a structured JSON payload."),
    ) -> None:
        """List the checkpoints persisted for a mission."""
        if not mission_id:
            _write_error_stderr("autoctx mission artifacts: --id <mission-id> is required")
            raise typer.Exit(code=1)
        settings = load_settings()
        with _mission_manager(settings) as mgr:
            try:
                payload = build_mission_artifacts_payload(mgr, mission_id, _checkpoint_runs_root(settings))
            except ValueError as err:
                _write_error_stderr(f"autoctx mission artifacts: {err}")
                raise typer.Exit(code=1) from err
        if json_output:
            print(json.dumps(payload, indent=2))
        else:
            console.print(_render_mission_artifacts_text(payload))

    app.add_typer(mission_app, name="mission")
