"""Bounded worker execution helpers for the campaign scheduler."""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from typing import Protocol

from autocontext.execution.campaign_scheduler_models import (
    CampaignAssignment,
    CampaignJobResult,
    CampaignLease,
    CampaignWorker,
    SchedulerBudget,
    _JobState,
    _WorkerState,
)

Heartbeat = Callable[[tuple[CampaignAssignment, ...], threading.Event], None]
ClaimWave = Callable[[], tuple[tuple[CampaignAssignment, ...], tuple[CampaignWorker, ...]]]
ExecuteWave = Callable[[tuple[CampaignAssignment, ...], tuple[CampaignWorker, ...]], None]


class _RestartLeaseScheduler(Protocol):
    _restart_orphan_lease_ids: set[str]
    _restart_durable_replay_lease_ids: set[str]
    _workers: dict[str, _WorkerState]
    _executors: dict[str, CampaignWorker]
    _jobs: dict[str, _JobState]
    _leases: dict[str, str]
    clock: Callable[[], float]

    def heartbeat(self, worker_id: str, lease_ids: Sequence[str] = ()) -> None: ...


def restart_orphan_lease_sets(
    jobs: dict[str, _JobState],
    durable_replay_lease_ids: set[str],
) -> tuple[set[str], set[str]]:
    """Recover orphan identities and the leases promised durable replay."""

    orphan_ids: set[str] = set()
    durable_ids: set[str] = set()
    for state in jobs.values():
        lease = state.lease
        if state.status != "leased" or lease is None:
            continue
        orphan_ids.add(lease.lease_id)
        if lease.lease_id in durable_replay_lease_ids:
            durable_ids.add(lease.lease_id)
    return orphan_ids, durable_ids


def run_scheduler_service(
    stop_event: threading.Event,
    *,
    poll_interval: float,
    cancel_grace_seconds: float,
    reconcile: Callable[[], object],
    claim_wave: ClaimWave,
    execute_wave: ExecuteWave,
    cancel_active: Callable[[float, float], None],
) -> int:
    """Poll and dispatch without tying service shutdown to worker completion."""

    dispatched = 0
    active_waves: set[threading.Thread] = set()
    wave_errors: queue.SimpleQueue[BaseException] = queue.SimpleQueue()

    def run_wave(assignments: tuple[CampaignAssignment, ...], executors: tuple[CampaignWorker, ...]) -> None:
        try:
            execute_wave(assignments, executors)
        except BaseException as exc:
            wave_errors.put(exc)

    try:
        while not stop_event.is_set():
            active_waves = _reap_waves(active_waves)
            if not wave_errors.empty():
                raise wave_errors.get()
            reconcile()
            assignments, executors = claim_wave()
            if assignments:
                dispatched += len(assignments)
                wave = threading.Thread(
                    target=run_wave,
                    args=(assignments, executors),
                    name="campaign-scheduler-wave",
                    daemon=True,
                )
                active_waves.add(wave)
                wave.start()
            stop_event.wait(poll_interval)
    finally:
        cancel_active(cancel_grace_seconds, poll_interval)
    return dispatched


def run_scheduler_until_idle(
    *,
    max_waves: int,
    poll_interval: float,
    timeout_seconds: float,
    cancel_grace_seconds: float,
    stop_event: threading.Event,
    reconcile: Callable[[], object],
    claim_wave: ClaimWave,
    execute_wave: ExecuteWave,
    work_counts: Callable[[], tuple[int, int]],
    cancel_active: Callable[[float, float], None],
) -> int:
    """Drain durable work, including replayed leases, with a hard deadline."""

    dispatched = 0
    waves = 0
    active_waves: set[threading.Thread] = set()
    wave_errors: queue.SimpleQueue[BaseException] = queue.SimpleQueue()
    deadline = time.monotonic() + timeout_seconds

    def run_wave(assignments: tuple[CampaignAssignment, ...], executors: tuple[CampaignWorker, ...]) -> None:
        try:
            execute_wave(assignments, executors)
        except BaseException as exc:
            wave_errors.put(exc)

    try:
        while True:
            if stop_event.is_set():
                raise InterruptedError("campaign scheduler drain was canceled")
            if time.monotonic() >= deadline:
                raise TimeoutError("campaign scheduler did not become idle before its deadline")
            active_waves = _reap_waves(active_waves)
            if not wave_errors.empty():
                raise wave_errors.get()
            reconcile()
            queued, running = work_counts()
            if queued == 0 and running == 0 and not active_waves:
                return dispatched
            assignments: tuple[CampaignAssignment, ...]
            executors: tuple[CampaignWorker, ...]
            if (queued or running) and waves >= max_waves:
                if running == 0 and not active_waves:
                    raise RuntimeError("campaign scheduler exceeded max_waves before becoming idle")
                assignments, executors = (), ()
            elif queued or running:
                assignments, executors = claim_wave()
            else:
                assignments, executors = (), ()
            if assignments:
                dispatched += len(assignments)
                waves += 1
                wave = threading.Thread(
                    target=run_wave,
                    args=(assignments, executors),
                    name="campaign-scheduler-drain-wave",
                    daemon=True,
                )
                active_waves.add(wave)
                wave.start()
            elif queued and running == 0 and not active_waves:
                remaining_queued, remaining_running = work_counts()
                if remaining_queued == 0 and remaining_running == 0:
                    continue
                raise RuntimeError("queued campaign jobs have no runnable worker or budget capacity")
            stop_event.wait(min(poll_interval, max(0.0, deadline - time.monotonic())))
    except BaseException:
        cancel_active(cancel_grace_seconds, poll_interval)
        raise


def _reap_waves(active_waves: set[threading.Thread]) -> set[threading.Thread]:
    remaining: set[threading.Thread] = set()
    for wave in active_waves:
        if wave.is_alive():
            remaining.add(wave)
        else:
            wave.join()
    return remaining


def restart_lease_has_durable_replay(scheduler: _RestartLeaseScheduler, lease: CampaignLease) -> bool:
    """Whether a reconstructed lease can safely re-enter its original worker."""

    worker = scheduler._workers.get(lease.worker_id)
    return (
        lease.lease_id in scheduler._restart_orphan_lease_ids
        and lease.lease_id in scheduler._restart_durable_replay_lease_ids
        and scheduler._executors.get(lease.worker_id) is not None
        and worker is not None
        and worker.descriptor.locality == "remote"
        and worker.descriptor.environment_fingerprint == lease.environment_fingerprint
        and "durable_result_replay" in worker.descriptor.sandbox_features
    )


def claim_expired_restart_leases(
    scheduler: _RestartLeaseScheduler,
    assignments: list[CampaignAssignment],
    executors: list[CampaignWorker],
) -> None:
    """Resume expired durable leases without minting a new paid-task identity."""

    for lease_id in sorted(tuple(scheduler._restart_orphan_lease_ids)):
        job_id = scheduler._leases.get(lease_id)
        state = scheduler._jobs.get(job_id) if job_id is not None else None
        if state is None or state.status != "leased" or state.lease is None:
            scheduler._restart_orphan_lease_ids.discard(lease_id)
            continue
        lease = state.lease
        executor = scheduler._executors.get(lease.worker_id)
        if (
            lease.expires_at > scheduler.clock()
            or executor is None
            or not restart_lease_has_durable_replay(scheduler, lease)
        ):
            continue
        scheduler.heartbeat(lease.worker_id, (lease_id,))
        assert state.lease is not None
        assignments.append(CampaignAssignment(state.request, state.lease))
        executors.append(executor)
        scheduler._restart_orphan_lease_ids.discard(lease_id)


def execute_assignment_groups(
    assignments: tuple[CampaignAssignment, ...],
    executors: tuple[CampaignWorker, ...],
    heartbeat: Heartbeat,
) -> Iterator[tuple[CampaignAssignment, CampaignJobResult]]:
    """Execute a wave and yield completed groups without input-order blocking."""

    if not assignments:
        return
    grouped_work: dict[tuple[str, str], tuple[CampaignWorker, list[CampaignAssignment]]] = {}
    for executor, assignment in zip(executors, assignments, strict=True):
        supports_batch = callable(getattr(executor, "execute_many", None))
        reusable = supports_batch and assignment.lease.lifecycle == "reuse_matched_trials"
        group_key = (
            assignment.lease.worker_id,
            assignment.lease.reuse_key if reusable else assignment.lease.lease_id,
        )
        if group_key not in grouped_work:
            grouped_work[group_key] = (executor, [])
        grouped_work[group_key][1].append(assignment)

    completed: queue.Queue[tuple[tuple[CampaignAssignment, ...], tuple[CampaignJobResult, ...] | None, BaseException | None]] = (
        queue.Queue()
    )

    def run_group(worker: CampaignWorker, grouped: tuple[CampaignAssignment, ...]) -> None:
        try:
            completed.put((grouped, _execute_group(worker, grouped, heartbeat), None))
        except BaseException as exc:
            completed.put((grouped, None, exc))

    threads = [
        threading.Thread(target=run_group, args=(executor, tuple(grouped)), daemon=True)
        for executor, grouped in grouped_work.values()
    ]
    for thread in threads:
        thread.start()
    for _ in threads:
        grouped, results, error = completed.get()
        if error is not None:
            raise error
        assert results is not None
        yield from zip(grouped, results, strict=True)
    for thread in threads:
        thread.join()


def _execute_group(
    worker: CampaignWorker,
    grouped: tuple[CampaignAssignment, ...],
    heartbeat: Heartbeat,
) -> tuple[CampaignJobResult, ...]:
    heartbeat_stop = threading.Event()
    heartbeat_thread = threading.Thread(target=heartbeat, args=(grouped, heartbeat_stop), daemon=True)
    heartbeat_thread.start()
    started = time.perf_counter()
    try:
        execute_many = getattr(worker, "execute_many", None)
        if len(grouped) > 1 and callable(execute_many):
            batch_results: list[CampaignJobResult] = []
            try:
                for result in execute_many(grouped):
                    if not isinstance(result, CampaignJobResult):
                        raise TypeError("campaign batch worker returned an invalid result")
                    batch_results.append(result)
                if len(batch_results) != len(grouped):
                    raise RuntimeError("campaign batch worker returned the wrong number of results")
                return tuple(batch_results)
            except Exception as exc:
                return _dispatcher_failures(exc, grouped, time.perf_counter() - started, tuple(batch_results))

        item_results: list[CampaignJobResult] = []
        for assignment in grouped:
            item_started = time.perf_counter()
            try:
                result = worker.execute(assignment)
                if not isinstance(result, CampaignJobResult):
                    raise TypeError("campaign worker returned an invalid result")
                item_results.append(result)
            except Exception as exc:
                item_results.append(
                    _dispatcher_failure(
                        exc,
                        assignment,
                        time.perf_counter() - item_started,
                    )
                )
        return tuple(item_results)
    finally:
        heartbeat_stop.set()
        heartbeat_thread.join(timeout=1.0)


def _dispatcher_failures(
    exc: Exception,
    assignments: tuple[CampaignAssignment, ...],
    elapsed: float,
    known_results: tuple[CampaignJobResult, ...] = (),
) -> tuple[CampaignJobResult, ...]:
    # A batch exception is ambiguous: a provider may have executed every item
    # before the dispatcher lost the response. Never amortize known usage or a
    # reservation across the batch; doing so reopens every durable budget.
    return tuple(
        _dispatcher_failure(
            exc,
            assignment,
            elapsed,
            known_results[index] if index < len(known_results) else None,
        )
        for index, assignment in enumerate(assignments)
    )


def _dispatcher_failure(
    exc: Exception,
    assignment: CampaignAssignment,
    elapsed: float,
    known_result: CampaignJobResult | None = None,
) -> CampaignJobResult:
    reservation = assignment.job.reservation
    known = known_result.consumed if known_result is not None else SchedulerBudget()
    return CampaignJobResult(
        outcome="infrastructure_failure",
        consumed=SchedulerBudget(
            tokens=max(reservation.tokens, known.tokens),
            wall_seconds=max(reservation.wall_seconds, elapsed, known.wall_seconds),
            compute_units=max(reservation.compute_units, known.compute_units),
            jobs=max(1, reservation.jobs, known.jobs),
            shared_evidence_tokens=max(reservation.shared_evidence_tokens, known.shared_evidence_tokens),
        ),
        detail=f"{type(exc).__name__}: {exc}",
        cleanup_succeeded=False,
        metadata={"usage_estimated": True, "ambiguous_dispatch": True},
        retryable=False,
    )


__all__ = ["execute_assignment_groups", "run_scheduler_service", "run_scheduler_until_idle"]
