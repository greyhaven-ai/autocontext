from __future__ import annotations

import copy
import resource
import sys
from collections.abc import Mapping
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, TimeoutError
from importlib import import_module
from pathlib import Path
from typing import Any

from autocontext.scenarios.base import (
    ExecutionLimits,
    Observation,
    ReplayEnvelope,
    Result,
    ScenarioInterface,
)
from autocontext.scenarios.custom.loader import load_custom_module_from_path


def _load_scenario_module(scenario_module: str, scenario_source_path: str | None) -> Any:
    try:
        return import_module(scenario_module)
    except ModuleNotFoundError:
        if scenario_source_path is None:
            raise
        return load_custom_module_from_path(scenario_module, Path(scenario_source_path))


def _execute_in_subprocess(
    scenario_module: str,
    scenario_class: str,
    scenario_source_path: str | None,
    strategy: dict[str, Any],
    seed: int,
    max_memory_mb: int,
    initial_state: dict[str, Any] | None,
    scenario_instance_state: dict[str, Any] | None,
) -> Result:
    memory_bytes = int(max_memory_mb * 1024 * 1024)
    try:
        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
    except Exception:
        pass
    module = _load_scenario_module(scenario_module, scenario_source_path)
    scenario_type = getattr(module, scenario_class)
    if scenario_instance_state is None:
        scenario: ScenarioInterface = scenario_type()
    else:
        scenario = object.__new__(scenario_type)
        for name, value in scenario_instance_state.items():
            object.__setattr__(scenario, name, value)
    return _execute_scenario(scenario, strategy, seed, initial_state)


def _execute_scenario(
    scenario: ScenarioInterface,
    strategy: Mapping[str, Any],
    seed: int,
    initial_state: Mapping[str, Any] | None,
) -> Result:
    if initial_state is None:
        return scenario.execute_match(strategy=strategy, seed=seed)
    return scenario.execute_match_from_state(
        strategy=strategy,
        seed=seed,
        initial_state=copy.deepcopy(dict(initial_state)),
    )


def _scenario_instance_state(scenario: ScenarioInterface) -> dict[str, Any]:
    """Capture prepared-scenario configuration for subprocess reconstruction."""

    state: dict[str, Any] = {}
    instance_dict = getattr(scenario, "__dict__", None)
    if isinstance(instance_dict, dict):
        state.update(copy.deepcopy(instance_dict))
    for scenario_type in reversed(type(scenario).__mro__):
        declared_slots = scenario_type.__dict__.get("__slots__", ())
        slots = (declared_slots,) if isinstance(declared_slots, str) else declared_slots
        for name in slots:
            if name in {"__dict__", "__weakref__"} or name in state:
                continue
            try:
                state[name] = copy.deepcopy(getattr(scenario, name))
            except AttributeError:
                continue
    return state


class LocalExecutor:
    def execute(
        self,
        scenario: ScenarioInterface,
        strategy: Mapping[str, Any],
        seed: int,
        limits: ExecutionLimits,
    ) -> tuple[Result, ReplayEnvelope]:
        return self._execute(
            scenario,
            strategy,
            seed,
            limits,
            initial_state=None,
            initial_observation=None,
        )

    def execute_prepared_fixture(
        self,
        scenario: ScenarioInterface,
        strategy: Mapping[str, Any],
        seed: int,
        limits: ExecutionLimits,
        *,
        initial_state: Mapping[str, Any],
        initial_observation: Observation,
        fixture_digest: str,
    ) -> tuple[Result, ReplayEnvelope]:
        del fixture_digest
        return self._execute(
            scenario,
            strategy,
            seed,
            limits,
            initial_state=initial_state,
            initial_observation=initial_observation,
        )

    def _execute(
        self,
        scenario: ScenarioInterface,
        strategy: Mapping[str, Any],
        seed: int,
        limits: ExecutionLimits,
        *,
        initial_state: Mapping[str, Any] | None,
        initial_observation: Observation | None,
    ) -> tuple[Result, ReplayEnvelope]:
        if "__code__" in strategy:
            from autocontext.execution.executors.monty import MontyExecutor

            monty_exec = MontyExecutor()
            return monty_exec.execute_code_strategy(
                scenario=scenario,
                code=str(strategy["__code__"]),
                seed=seed,
                limits=limits,
                initial_state=initial_state,
                initial_observation=initial_observation,
            )
        scenario_module = scenario.__class__.__module__
        source_module = sys.modules.get(scenario_module)
        scenario_source_path = getattr(source_module, "__file__", None)

        try:
            with ProcessPoolExecutor(max_workers=1) as pool:
                future = pool.submit(
                    _execute_in_subprocess,
                    scenario_module,
                    scenario.__class__.__name__,
                    scenario_source_path,
                    dict(strategy),
                    seed,
                    limits.max_memory_mb,
                    copy.deepcopy(dict(initial_state)) if initial_state is not None else None,
                    _scenario_instance_state(scenario) if initial_state is not None else None,
                )
                try:
                    result = future.result(timeout=limits.timeout_seconds)
                except TimeoutError as exc:
                    future.cancel()
                    raise TimeoutError(f"strategy execution exceeded {limits.timeout_seconds}s") from exc
        except PermissionError:
            # Sandboxed runners may disallow process semaphores; keep timeout semantics with threads.
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(
                    _execute_scenario,
                    scenario,
                    dict(strategy),
                    seed,
                    initial_state,
                )
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
