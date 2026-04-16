from __future__ import annotations

import importlib
import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Protocol

import typer

from autocontext.config.settings import AppSettings
from autocontext.storage.sqlite_store import SQLiteStore

if TYPE_CHECKING:
    from rich.console import Console


class QueueEnqueuer(Protocol):
    def __call__(
        self,
        *,
        store: SQLiteStore,
        spec_name: str,
        task_prompt: str | None = None,
        rubric: str | None = None,
        max_rounds: int = 5,
        quality_threshold: float = 0.9,
        min_rounds: int = 1,
        priority: int = 0,
    ) -> str: ...


def _cli_attr(dependency_module: str, name: str) -> Any:
    return getattr(importlib.import_module(dependency_module), name)


def derive_queue_spec_name(task_prompt: str) -> str:
    words = re.sub(r"[^a-z0-9\s]", " ", task_prompt.lower()).split()
    return ("_".join(words)[:80] or "queue_task").strip("_") or "queue_task"


def resolve_queue_spec_name(spec: str, task_prompt: str) -> str:
    cleaned_spec = spec.strip()
    if cleaned_spec:
        return cleaned_spec

    cleaned_prompt = task_prompt.strip()
    if cleaned_prompt:
        return derive_queue_spec_name(cleaned_prompt)

    raise ValueError("Either --spec or --task-prompt is required.")


def run_queue_command(
    *,
    action: str,
    spec: str,
    task_prompt: str,
    rubric: str,
    max_rounds: int,
    threshold: float,
    min_rounds: int,
    priority: int,
    provider: str,
    json_output: bool,
    console: Console,
    load_settings_fn: Callable[[], AppSettings],
    sqlite_from_settings: Callable[[AppSettings], SQLiteStore],
    enqueue_task_fn: QueueEnqueuer,
    write_json_stdout: Callable[[object], None],
    write_json_stderr: Callable[[str], None],
) -> None:
    normalized_action = (action or "add").strip().lower()
    if normalized_action != "add":
        message = f"Unsupported queue action '{action}'. Only 'add' is supported."
        if json_output:
            write_json_stderr(message)
        else:
            console.print(f"[red]{message}[/red]")
        raise typer.Exit(code=1)

    try:
        resolved_spec_name = resolve_queue_spec_name(spec, task_prompt)
    except ValueError as exc:
        if json_output:
            write_json_stderr(str(exc))
        else:
            console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    _provider_override = provider.strip()

    settings = load_settings_fn()
    store = sqlite_from_settings(settings)

    has_direct_task_payload = bool(task_prompt.strip() or rubric.strip())
    if not has_direct_task_payload and max_rounds == 5 and threshold == 0.9 and min_rounds == 1:
        task_id = enqueue_task_fn(
            store=store,
            spec_name=resolved_spec_name,
            priority=priority,
        )
    elif min_rounds == 1:
        task_id = enqueue_task_fn(
            store=store,
            spec_name=resolved_spec_name,
            task_prompt=task_prompt.strip() or None,
            rubric=rubric.strip() or None,
            max_rounds=max_rounds,
            quality_threshold=threshold,
            priority=priority,
        )
    else:
        task_id = enqueue_task_fn(
            store=store,
            spec_name=resolved_spec_name,
            task_prompt=task_prompt.strip() or None,
            rubric=rubric.strip() or None,
            max_rounds=max_rounds,
            quality_threshold=threshold,
            min_rounds=min_rounds,
            priority=priority,
        )

    payload = {"task_id": task_id, "spec_name": resolved_spec_name, "status": "queued"}
    if json_output:
        write_json_stdout(payload)
    else:
        console.print(f"Queued task {task_id} for spec '{resolved_spec_name}' (priority {priority})")


def register_queue_command(
    app: typer.Typer,
    *,
    console: Console,
    dependency_module: str = "autocontext.cli",
) -> None:
    @app.command()
    def queue(
        action: str = typer.Argument("add"),
        spec: str = typer.Option("", "--spec", "-s", help="Task spec name"),
        task_prompt: str = typer.Option("", "--task-prompt", "--prompt", "-p", help="The queued task prompt"),
        rubric: str = typer.Option("", "--rubric", "-r", help="Evaluation rubric"),
        max_rounds: int = typer.Option(5, "--rounds", "-n", min=1, help="Maximum improvement rounds"),
        threshold: float = typer.Option(0.9, "--threshold", "-t", help="Quality threshold to stop"),
        min_rounds: int = typer.Option(1, "--min-rounds", min=1, help="Minimum rounds before threshold stops"),
        priority: int = typer.Option(0, "--priority", help="Task priority"),
        provider: str = typer.Option("", "--provider", help="Provider override accepted for queue-script compatibility"),
        json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
    ) -> None:
        """Add a task to the background runner queue."""
        from autocontext.execution.task_runner import enqueue_task

        run_queue_command(
            action=action,
            spec=spec,
            task_prompt=task_prompt,
            rubric=rubric,
            max_rounds=max_rounds,
            threshold=threshold,
            min_rounds=min_rounds,
            priority=priority,
            provider=provider,
            json_output=json_output,
            console=console,
            load_settings_fn=_cli_attr(dependency_module, "load_settings"),
            sqlite_from_settings=_cli_attr(dependency_module, "_sqlite_from_settings"),
            enqueue_task_fn=enqueue_task,
            write_json_stdout=_cli_attr(dependency_module, "_write_json_stdout"),
            write_json_stderr=_cli_attr(dependency_module, "_write_json_stderr"),
        )
