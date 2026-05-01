from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Protocol

import typer

from autocontext.config.settings import AppSettings
from autocontext.providers.base import LLMProvider, ProviderError
from autocontext.storage.sqlite_store import SQLiteStore

if TYPE_CHECKING:
    from rich.console import Console


class WorkerRunner(Protocol):
    def run(self) -> int: ...

    def run_batch(self, limit: int | None = None) -> int: ...


class WorkerRunnerFactory(Protocol):
    def __call__(
        self,
        settings: AppSettings,
        *,
        store: SQLiteStore,
        provider: LLMProvider,
        model: str = "",
        poll_interval: float = 60.0,
        max_consecutive_empty: int = 0,
        concurrency: int = 1,
    ) -> WorkerRunner: ...


def _cli_attr(dependency_module: str, name: str) -> Any:
    return getattr(importlib.import_module(dependency_module), name)


def _close_if_supported(resource: object) -> None:
    close = getattr(resource, "close", None)
    if callable(close):
        close()


def _validate_worker_options(
    *,
    poll_interval: float,
    concurrency: int,
    max_empty_polls: int,
) -> None:
    if poll_interval < 0:
        raise ValueError("--poll-interval must be non-negative")
    if concurrency < 1:
        raise ValueError("--concurrency must be a positive integer")
    if max_empty_polls < 0:
        raise ValueError("--max-empty-polls must be zero or a positive integer")


def _write_worker_error(
    message: str,
    *,
    json_output: bool,
    console: Console,
    write_json_stderr: Callable[[str], None],
) -> None:
    if json_output:
        write_json_stderr(message)
    else:
        console.print(f"[red]{message}[/red]")


def _select_worker_model(settings: AppSettings, provider: LLMProvider, model: str) -> str:
    requested = model.strip()
    if requested:
        return requested
    configured = getattr(settings, "judge_model", "")
    if isinstance(configured, str) and configured.strip():
        return configured.strip()
    return provider.default_model()


def run_worker_command(
    *,
    poll_interval: float,
    concurrency: int,
    max_empty_polls: int,
    model: str,
    once: bool,
    json_output: bool,
    console: Console,
    load_settings_fn: Callable[[], AppSettings],
    sqlite_from_settings: Callable[[AppSettings], SQLiteStore],
    get_provider_fn: Callable[[AppSettings], LLMProvider],
    create_task_runner_fn: WorkerRunnerFactory,
    write_json_stdout: Callable[[object], None],
    write_json_stderr: Callable[[str], None],
) -> None:
    try:
        _validate_worker_options(
            poll_interval=poll_interval,
            concurrency=concurrency,
            max_empty_polls=max_empty_polls,
        )
    except ValueError as exc:
        _write_worker_error(
            str(exc),
            json_output=json_output,
            console=console,
            write_json_stderr=write_json_stderr,
        )
        raise typer.Exit(code=1) from exc

    settings = load_settings_fn()
    store = sqlite_from_settings(settings)
    provider: LLMProvider | None = None

    try:
        provider = get_provider_fn(settings)
        runner = create_task_runner_fn(
            settings,
            store=store,
            provider=provider,
            model=_select_worker_model(settings, provider, model),
            poll_interval=poll_interval,
            max_consecutive_empty=max_empty_polls,
            concurrency=concurrency,
        )
        if once:
            tasks_processed = runner.run_batch(concurrency)
            mode = "once"
        else:
            tasks_processed = runner.run()
            mode = "daemon"
    except ProviderError as exc:
        _write_worker_error(
            str(exc),
            json_output=json_output,
            console=console,
            write_json_stderr=write_json_stderr,
        )
        raise typer.Exit(code=1) from exc
    finally:
        if provider is not None:
            _close_if_supported(provider)
        _close_if_supported(store)

    payload = {
        "status": "stopped",
        "mode": mode,
        "tasks_processed": tasks_processed,
        "poll_interval": poll_interval,
        "concurrency": concurrency,
    }
    if json_output:
        write_json_stdout(payload)
    else:
        console.print(
            f"Worker stopped ({mode}). Processed {tasks_processed} task(s) "
            f"with concurrency {concurrency}."
        )


def register_worker_command(
    app: typer.Typer,
    *,
    console: Console,
    dependency_module: str = "autocontext.cli",
) -> None:
    @app.command()
    def worker(
        poll_interval: float = typer.Option(
            60.0,
            "--poll-interval",
            min=0.0,
            help="Seconds to sleep between empty queue polls",
        ),
        concurrency: int = typer.Option(
            1,
            "--concurrency",
            min=1,
            help="Maximum queued tasks to process per batch",
        ),
        max_empty_polls: int = typer.Option(
            0,
            "--max-empty-polls",
            min=0,
            help="Stop after this many empty polls; 0 runs until signaled",
        ),
        model: str = typer.Option("", "--model", help="Judge model override for queued tasks"),
        once: bool = typer.Option(False, "--once", help="Process one batch and exit"),
        json_output: bool = typer.Option(False, "--json", help="Output structured JSON on exit"),
    ) -> None:
        """Run the background task queue worker."""
        from autocontext.execution.task_runner import create_task_runner_from_settings
        from autocontext.providers.registry import get_provider

        run_worker_command(
            poll_interval=poll_interval,
            concurrency=concurrency,
            max_empty_polls=max_empty_polls,
            model=model,
            once=once,
            json_output=json_output,
            console=console,
            load_settings_fn=_cli_attr(dependency_module, "load_settings"),
            sqlite_from_settings=_cli_attr(dependency_module, "_sqlite_from_settings"),
            get_provider_fn=get_provider,
            create_task_runner_fn=create_task_runner_from_settings,
            write_json_stdout=_cli_attr(dependency_module, "_write_json_stdout"),
            write_json_stderr=_cli_attr(dependency_module, "_write_json_stderr"),
        )
