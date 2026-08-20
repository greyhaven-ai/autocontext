"""Worker adapters for campaign scheduler assignments."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from autocontext.execution.campaign_scheduler_models import (
    CampaignAssignment,
    CampaignJobResult,
    JobOutcome,
    SchedulerBudget,
)
from autocontext.execution.remote_execution import (
    RemoteExecutionAdapter,
    RemoteExecutionRequest,
    RemoteExecutionResult,
)


class CallableCampaignWorker:
    def __init__(
        self,
        execute: Callable[[CampaignAssignment], CampaignJobResult],
        cancel: Callable[[CampaignAssignment], bool] | None = None,
    ) -> None:
        self._execute = execute
        self._cancel = cancel

    def execute(self, assignment: CampaignAssignment) -> CampaignJobResult:
        return self._execute(assignment)

    def cancel(self, assignment: CampaignAssignment) -> bool:
        return self._cancel(assignment) if self._cancel is not None else False


class RemoteCampaignWorker:
    def __init__(
        self,
        adapter: RemoteExecutionAdapter,
        request_factory: Callable[[CampaignAssignment], RemoteExecutionRequest],
    ) -> None:
        self._adapter = adapter
        self._request_factory = request_factory

    def execute(self, assignment: CampaignAssignment) -> CampaignJobResult:
        request = self._request_factory(assignment)
        if assignment.lease.lifecycle == "warm_snapshot":
            request = replace(request, lifecycle="warm_snapshot")
        return campaign_result_from_remote(self._adapter.execute_request(request))

    def execute_many(self, assignments: tuple[CampaignAssignment, ...]) -> tuple[CampaignJobResult, ...]:
        if not assignments:
            return ()
        lifecycle = assignments[0].lease.lifecycle
        if lifecycle != "reuse_matched_trials" or len(assignments) == 1:
            return tuple(self.execute(assignment) for assignment in assignments)
        execute_requests = getattr(self._adapter, "execute_requests", None)
        if not callable(execute_requests):
            raise RuntimeError("remote adapter does not implement matched-trial session reuse")
        requests = tuple(
            replace(
                self._request_factory(assignment),
                lifecycle="reuse_matched_trials",
                max_reuse_tasks=len(assignments),
            )
            for assignment in assignments
        )
        results = tuple(execute_requests(requests))
        if len(results) != len(assignments):
            raise RuntimeError("remote adapter returned the wrong number of matched-trial results")
        return tuple(campaign_result_from_remote(result) for result in results)

    def cancel(self, assignment: CampaignAssignment) -> bool:
        cancel_request = getattr(self._adapter, "cancel_request", None)
        if not callable(cancel_request):
            return False
        return bool(cancel_request(self._request_factory(assignment)))


def campaign_result_from_remote(result: RemoteExecutionResult) -> CampaignJobResult:
    if result.status == "success":
        outcome: JobOutcome = "candidate_success"
    elif result.status == "task_error":
        outcome = "candidate_failure"
    else:
        outcome = "infrastructure_failure"
    return CampaignJobResult(
        outcome=outcome,
        consumed=SchedulerBudget(
            wall_seconds=result.usage.wall_seconds,
            compute_units=result.usage.accelerator_seconds or result.usage.cpu_seconds or 0.0,
            jobs=1,
        ),
        detail=result.error,
        cleanup_succeeded=result.cleanup.succeeded,
        metadata={"remote_status": result.status, "provider": result.provider, "session_id": result.session_id},
    )


__all__ = ["CallableCampaignWorker", "RemoteCampaignWorker", "campaign_result_from_remote"]
