from __future__ import annotations

from typing import Any

from autocontext.config import AppSettings
from autocontext.extensions import HookBus, HookEvents, load_extensions


def initialize_hook_bus(settings: AppSettings) -> tuple[HookBus, list[str]]:
    hook_bus = HookBus(fail_fast=settings.extension_fail_fast)
    loaded_extensions = load_extensions(settings.extensions, hook_bus) if settings.extensions else []
    return hook_bus, loaded_extensions


def ensure_hook_bus(runner: Any) -> HookBus:
    hook_bus = getattr(runner, "hook_bus", None)
    if isinstance(hook_bus, HookBus):
        return hook_bus
    hook_bus, loaded_extensions = initialize_hook_bus(runner.settings)
    runner.hook_bus = hook_bus
    runner.loaded_extensions = loaded_extensions
    return hook_bus


def loaded_extensions(runner: Any) -> list[str]:
    ensure_hook_bus(runner)
    value = getattr(runner, "loaded_extensions", [])
    return list(value) if isinstance(value, list) else []


def emit_run_start(
    runner: Any,
    *,
    run_id: str,
    scenario: str,
    target_generations: int,
) -> None:
    event = ensure_hook_bus(runner).emit(
        HookEvents.RUN_START,
        {
            "run_id": run_id,
            "scenario": scenario,
            "target_generations": target_generations,
            "loaded_extensions": loaded_extensions(runner),
        },
    )
    event.raise_if_blocked()


def emit_run_end(runner: Any, payload: dict[str, Any]) -> None:
    event = ensure_hook_bus(runner).emit(HookEvents.RUN_END, payload)
    event.raise_if_blocked()


def emit_generation_end(runner: Any, payload: dict[str, Any]) -> None:
    event = ensure_hook_bus(runner).emit(HookEvents.GENERATION_END, payload)
    event.raise_if_blocked()


def emit_generation_failed(runner: Any, *, run_id: str, scenario: str, generation: int, error: str) -> None:
    emit_generation_end(
        runner,
        {"run_id": run_id, "scenario": scenario, "generation": generation, "status": "failed", "error": error},
    )


def emit_run_failed(
    runner: Any,
    *,
    run_id: str,
    scenario: str,
    completed_generations: int,
    best_score: float,
    elo: float,
    error: str,
) -> None:
    emit_run_end(
        runner,
        {
            "run_id": run_id,
            "scenario": scenario,
            "status": "failed",
            "completed_generations": completed_generations,
            "best_score": best_score,
            "elo": elo,
            "error": error,
        },
    )


def emit_run_completed(
    runner: Any,
    *,
    run_id: str,
    scenario: str,
    completed_generations: int,
    best_score: float,
    elo: float,
    session_report_path: str | None,
    dead_ends_found: int,
) -> None:
    emit_run_end(
        runner,
        {
            "run_id": run_id,
            "scenario": scenario,
            "status": "completed",
            "completed_generations": completed_generations,
            "best_score": best_score,
            "elo": elo,
            "session_report_path": session_report_path,
            "dead_ends_found": dead_ends_found,
        },
    )
