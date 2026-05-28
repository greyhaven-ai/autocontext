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
        browser_url: str | None = None,
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
    browser_url: str,
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
    if normalized_action not in {"add", "status"}:
        message = f"Unsupported queue action '{action}'. Supported actions: 'add', 'status'."
        if json_output:
            write_json_stderr(message)
        else:
            console.print(f"[red]{message}[/red]")
        raise typer.Exit(code=1)

    # AC-697 slice 2: `autoctx queue status` reports the queue-pending
    # count (the semantic that used to live under top-level `status` in
    # TypeScript). Python's top-level `status` already meant run-status,
    # so this slice fills the parity gap by adding the queue subcommand.
    # No spec / task_prompt is required for the status action.
    if normalized_action == "status":
        settings = load_settings_fn()
        store = sqlite_from_settings(settings)
        try:
            pending = int(store.pending_task_count())
        finally:
            close = getattr(store, "close", None)
            if callable(close):
                close()
        payload: dict[str, Any] = {"pending_count": pending}
        if json_output:
            write_json_stdout(payload)
        else:
            console.print(f"Pending queued tasks: {pending}")
        return

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

    task_kwargs: dict[str, Any] = {
        "store": store,
        "spec_name": resolved_spec_name,
        "priority": priority,
    }
    normalized_task_prompt = task_prompt.strip() or None
    normalized_rubric = rubric.strip() or None
    normalized_browser_url = browser_url.strip() or None
    if normalized_task_prompt is not None:
        task_kwargs["task_prompt"] = normalized_task_prompt
    if normalized_rubric is not None:
        task_kwargs["rubric"] = normalized_rubric
    if normalized_browser_url is not None:
        task_kwargs["browser_url"] = normalized_browser_url
    if max_rounds != 5:
        task_kwargs["max_rounds"] = max_rounds
    if threshold != 0.9:
        task_kwargs["quality_threshold"] = threshold
    if min_rounds != 1:
        task_kwargs["min_rounds"] = min_rounds

    task_id = enqueue_task_fn(**task_kwargs)

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
    """Mount the `queue` typer group on `app`.

    AC-697 slice 3 promoted `queue` from a single typer command with
    an `action` positional to a sub-Typer group with `add` and
    `status` subcommands. Backward compatibility is preserved via the
    group's `invoke_without_command` callback: `autoctx queue -s
    <spec>` (no subcommand) still routes to the add behavior, so
    existing scripts continue to work.

    Closes the slice-2 Python `queue.status` contract gap: the
    walker in :func:`autocontext.cli_contract.iter_python_command_paths`
    now sees `["queue", "status"]` as a registered subcommand.
    """

    def _dispatch(
        *,
        action: str,
        spec: str,
        task_prompt: str,
        rubric: str,
        browser_url: str,
        max_rounds: int,
        threshold: float,
        min_rounds: int,
        priority: int,
        provider: str,
        json_output: bool,
    ) -> None:
        from autocontext.execution.task_runner import enqueue_task

        run_queue_command(
            action=action,
            spec=spec,
            task_prompt=task_prompt,
            rubric=rubric,
            browser_url=browser_url,
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

    # Per-option (default-value, "non-default" rule) so the
    # callback-vs-subcommand merge can tell "user passed this
    # explicitly" apart from "Typer filled in the default". PR #998
    # review (P2): the previous design discarded callback options
    # whenever Typer saw a subcommand, breaking legacy forms like
    # `autoctx queue --json status` and `autoctx queue -s abc add
    # --json` where flags appear before the subcommand. The new
    # design stashes callback values in `ctx.obj` and merges in each
    # subcommand: subcommand-explicit > callback-explicit > default.
    _DEFAULTS: dict[str, Any] = {
        "spec": "",
        "task_prompt": "",
        "rubric": "",
        "browser_url": "",
        "max_rounds": 5,
        "threshold": 0.9,
        "min_rounds": 1,
        "priority": 0,
        "provider": "",
        "json_output": False,
    }

    def _merge_with_callback(
        ctx: typer.Context,
        **subcommand_values: Any,
    ) -> dict[str, Any]:
        """Return the effective option set for a queue subcommand.

        For each option, prefer the subcommand's explicit value (i.e.
        any value other than the default); otherwise fall back to the
        callback's explicit value (also distinguished by non-default);
        otherwise return the default. This lets users put flags before
        the subcommand (legacy form) or after (canonical form) and
        get the same behavior.
        """
        callback_values: dict[str, Any] = (ctx.obj or {}) if isinstance(ctx.obj, dict) else {}
        merged: dict[str, Any] = {}
        for key, default in _DEFAULTS.items():
            sub_val = subcommand_values.get(key, default)
            cb_val = callback_values.get(key, default)
            if sub_val != default:
                merged[key] = sub_val
            elif cb_val != default:
                merged[key] = cb_val
            else:
                merged[key] = default
        return merged

    queue_app = typer.Typer(invoke_without_command=True, help="Manage the background task queue.")

    @queue_app.callback(invoke_without_command=True)
    def queue_root(
        ctx: typer.Context,
        spec: str = typer.Option("", "--spec", "-s", help="Task spec name (legacy `queue -s <spec>` form)"),
        task_prompt: str = typer.Option("", "--task-prompt", "--prompt", "-p", help="The queued task prompt"),
        rubric: str = typer.Option("", "--rubric", "-r", help="Evaluation rubric"),
        browser_url: str = typer.Option("", "--browser-url", help="Optional browser URL to capture before execution"),
        max_rounds: int = typer.Option(5, "--rounds", "-n", min=1, help="Maximum improvement rounds"),
        threshold: float = typer.Option(0.9, "--threshold", "-t", help="Quality threshold to stop"),
        min_rounds: int = typer.Option(1, "--min-rounds", min=1, help="Minimum rounds before threshold stops"),
        priority: int = typer.Option(0, "--priority", help="Task priority"),
        provider: str = typer.Option("", "--provider", help="Provider override accepted for queue-script compatibility"),
        json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
    ) -> None:
        """`autoctx queue -s <spec>` (no subcommand) routes to add.

        Options passed to the callback (before the subcommand) are
        stashed on ``ctx.obj`` so subcommands can merge them with
        their own explicit values; this preserves legacy forms like
        ``autoctx queue --json status`` and ``autoctx queue -s abc
        add --json`` that the original action-positional pattern
        accepted.
        """
        ctx.obj = {
            "spec": spec,
            "task_prompt": task_prompt,
            "rubric": rubric,
            "browser_url": browser_url,
            "max_rounds": max_rounds,
            "threshold": threshold,
            "min_rounds": min_rounds,
            "priority": priority,
            "provider": provider,
            "json_output": json_output,
        }
        if ctx.invoked_subcommand is not None:
            return
        _dispatch(action="add", **ctx.obj)

    @queue_app.command("add")
    def queue_add(
        ctx: typer.Context,
        spec: str = typer.Option("", "--spec", "-s", help="Task spec name"),
        task_prompt: str = typer.Option("", "--task-prompt", "--prompt", "-p", help="The queued task prompt"),
        rubric: str = typer.Option("", "--rubric", "-r", help="Evaluation rubric"),
        browser_url: str = typer.Option("", "--browser-url", help="Optional browser URL to capture before execution"),
        max_rounds: int = typer.Option(5, "--rounds", "-n", min=1, help="Maximum improvement rounds"),
        threshold: float = typer.Option(0.9, "--threshold", "-t", help="Quality threshold to stop"),
        min_rounds: int = typer.Option(1, "--min-rounds", min=1, help="Minimum rounds before threshold stops"),
        priority: int = typer.Option(0, "--priority", help="Task priority"),
        provider: str = typer.Option("", "--provider", help="Provider override accepted for queue-script compatibility"),
        json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
    ) -> None:
        """Add a task to the background runner queue."""
        merged = _merge_with_callback(
            ctx,
            spec=spec,
            task_prompt=task_prompt,
            rubric=rubric,
            browser_url=browser_url,
            max_rounds=max_rounds,
            threshold=threshold,
            min_rounds=min_rounds,
            priority=priority,
            provider=provider,
            json_output=json_output,
        )
        _dispatch(action="add", **merged)

    @queue_app.command("status")
    def queue_status(
        ctx: typer.Context,
        json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
    ) -> None:
        """Show the count of pending tasks in the background queue."""
        # Merge so `autoctx queue --json status` (callback-side
        # --json) still emits JSON; the subcommand's --json wins if
        # both are passed.
        merged = _merge_with_callback(ctx, json_output=json_output)
        _dispatch(action="status", **merged)

    app.add_typer(queue_app, name="queue")
