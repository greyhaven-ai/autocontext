from __future__ import annotations

import resource
from collections.abc import Mapping
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, TimeoutError
from importlib import import_module
from typing import Any

from autocontext.scenarios.base import ExecutionLimits, ReplayEnvelope, Result, ScenarioInterface


def _execute_in_subprocess(
    scenario_module: str,
    scenario_class: str,
    strategy: dict[str, Any],
    seed: int,
    max_memory_mb: int,
) -> Result:
    memory_bytes = int(max_memory_mb * 1024 * 1024)
    try:
        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
    except Exception:
        pass
    module = import_module(scenario_module)
    scenario_type = getattr(module, scenario_class)
    scenario: ScenarioInterface = scenario_type()
    return scenario.execute_match(strategy=strategy, seed=seed)


class LocalExecutor:
    def execute(
        self,
        scenario: ScenarioInterface,
        strategy: Mapping[str, Any],
        seed: int,
        limits: ExecutionLimits,
    ) -> tuple[Result, ReplayEnvelope]:
        if "__code__" in strategy:
            from autocontext.execution.executors.monty import MontyExecutor
            monty_exec = MontyExecutor()
            return monty_exec.execute_code_strategy(
                scenario=scenario,
                code=str(strategy["__code__"]),
                seed=seed,
                limits=limits,
            )
        try:
            with ProcessPoolExecutor(max_workers=1) as pool:
                future = pool.submit(
                    _execute_in_subprocess,
                    scenario.__class__.__module__,
                    scenario.__class__.__name__,
                    dict(strategy),
                    seed,
                    limits.max_memory_mb,
                )
                try:
                    result = future.result(timeout=limits.timeout_seconds)
                except TimeoutError as exc:
                    future.cancel()
                    raise TimeoutError(f"strategy execution exceeded {limits.timeout_seconds}s") from exc
        except PermissionError:
            # Sandboxed runners may disallow process semaphores; keep timeout semantics with threads.
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(scenario.execute_match, dict(strategy), seed)
                try:
                    result = future.result(timeout=limits.timeout_seconds)
                except TimeoutError as exc:
                    future.cancel()
                    raise TimeoutError(f"strategy execution exceeded {limits.timeout_seconds}s") from exc
        replay = ReplayEnvelope(
            scenario=scenario.name,
            seed=seed,
            narrative=scenario.replay_to_narrative(result.replay),
            timeline=result.replay,
        )
        return result, replay
