"""Execute ready roles as soon as their dependencies complete."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait

from autocontext.harness.core.types import RoleExecution
from autocontext.harness.orchestration.dag import RoleDAG

RoleHandler = Callable[[str, str, dict[str, RoleExecution]], RoleExecution]


class PipelineEngine:
    def __init__(self, dag: RoleDAG, handler: RoleHandler, max_workers: int = 4) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be positive")
        self._dag = dag
        self._handler = handler
        self._max_workers = max_workers

    def execute(
        self,
        prompts: dict[str, str],
        on_role_event: Callable[[str, str], None] | None = None,
    ) -> dict[str, RoleExecution]:
        self._dag.validate()
        roles = self._dag.roles
        pending = set(roles)
        completed: dict[str, RoleExecution] = {}
        running: dict[Future[RoleExecution], str] = {}

        def run(name: str, snapshot: dict[str, RoleExecution]) -> RoleExecution:
            return self._handler(name, prompts.get(name, ""), snapshot)

        with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
            try:
                while pending or running:
                    ready = sorted(name for name in pending if all(dep in completed for dep in roles[name].depends_on))
                    for name in ready[:self._max_workers - len(running)]:
                        if on_role_event:
                            on_role_event(name, "started")
                        running[pool.submit(run, name, dict(completed))] = name
                        pending.remove(name)
                    done, _ = wait(running, return_when=FIRST_COMPLETED)
                    # Resolve all finished futures before dispatching dependents;
                    # any failure prevents new work from being submitted.
                    results = [(running[future], future.result()) for future in done]
                    for name, result in sorted(results):
                        completed[name] = result
                        if on_role_event:
                            on_role_event(name, "completed")
                    for future in done:
                        del running[future]
            except BaseException:
                for future in running:
                    future.cancel()
                raise
        return completed
