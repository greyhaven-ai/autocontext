"""PipelineEngine — execute roles in DAG order with parallel batches."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from autocontext.harness.core.types import RoleExecution
from autocontext.harness.orchestration.dag import RoleDAG

RoleHandler = Callable[[str, str, dict[str, RoleExecution]], RoleExecution]


class PipelineEngine:
    def __init__(self, dag: RoleDAG, handler: RoleHandler, max_workers: int = 4) -> None:
        self._dag = dag
        self._handler = handler
        self._max_workers = max_workers

    def execute(
        self,
        prompts: dict[str, str],
        on_role_event: Callable[[str, str], None] | None = None,
    ) -> dict[str, RoleExecution]:
        completed: dict[str, RoleExecution] = {}
        batches = self._dag.execution_batches()

        for batch in batches:
            if len(batch) == 1:
                name = batch[0]
                if on_role_event:
                    on_role_event(name, "started")
                completed[name] = self._handler(name, prompts.get(name, ""), completed)
                if on_role_event:
                    on_role_event(name, "completed")
            else:
                workers = min(len(batch), self._max_workers)
                snapshot = dict(completed)

                def _run(role_name: str, snap: dict[str, RoleExecution] = snapshot) -> tuple[str, RoleExecution]:
                    if on_role_event:
                        on_role_event(role_name, "started")
                    result = self._handler(role_name, prompts.get(role_name, ""), snap)
                    if on_role_event:
                        on_role_event(role_name, "completed")
                    return role_name, result

                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futures = [pool.submit(_run, name) for name in batch]
                    for future in futures:
                        name, execution = future.result()
                        completed[name] = execution

        return completed
