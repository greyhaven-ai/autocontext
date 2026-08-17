"""Worker adapters for campaign scheduler assignments."""

from __future__ import annotations

from collections.abc import Callable

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
    def __init__(self, execute: Callable[[CampaignAssignment], CampaignJobResult]) -> None:
        self._execute = execute

    def execute(self, assignment: CampaignAssignment) -> CampaignJobResult:
        return self._execute(assignment)


class RemoteCampaignWorker:
    def __init__(
        self,
        adapter: RemoteExecutionAdapter,
        request_factory: Callable[[CampaignAssignment], RemoteExecutionRequest],
    ) -> None:
        self._adapter = adapter
        self._request_factory = request_factory

    def execute(self, assignment: CampaignAssignment) -> CampaignJobResult:
        return campaign_result_from_remote(self._adapter.execute_request(self._request_factory(assignment)))


def campaign_result_from_remote(result: RemoteExecutionResult) -> CampaignJobResult:
    if result.status == "success":
        outcome: JobOutcome = "candidate_success"
    elif result.status in {"task_error", "artifact_error"}:
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
