"""AC-697 mission manager (slice 2).

Mirrors ``ts/src/mission/manager.ts`` (AC-410). High-level mission
lifecycle facade over the slice-1 ``MissionStore``: create, advance,
verify, pause / resume / cancel, and budget-usage lookups. Verifiers
are registered per-mission and called via ``verify(mission_id)``
which records the result + transitions the mission status (passing
verifier -> completed; failing verifier leaves status untouched).
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from typing import Any

from autocontext.mission._async_bridge import AsyncContextError, has_running_loop
from autocontext.mission.events import MissionEventEmitter
from autocontext.mission.lifecycle import resolve_mission_status_transition
from autocontext.mission.store import MissionStore
from autocontext.mission.types import (
    BudgetUsage,
    Mission,
    MissionBudget,
    MissionStatus,
    MissionStep,
    MissionSubgoal,
    MissionVerificationRecord,
    StepStatus,
    SubgoalStatus,
    VerifierResult,
)
from autocontext.mission.verification import (
    build_missing_verifier_outcome,
    resolve_mission_verification_error_outcome,
    resolve_mission_verification_outcome,
)

__all__ = ["MissionManager", "MissionVerifierCallable"]


MissionVerifierCallable = Callable[[str], VerifierResult]
"""Signature for per-mission verifier callbacks; matches TS
``MissionVerifier``."""


class MissionManager:
    """Lifecycle facade. Owns a ``MissionStore`` connection plus
    optional event emitter; verifier callbacks live in an in-memory
    map keyed by mission id."""

    def __init__(self, db_path: str, *, events: MissionEventEmitter | None = None) -> None:
        self._store = MissionStore(db_path)
        self._verifiers: dict[str, MissionVerifierCallable] = {}
        self._events = events

    # -------------------------------------------------------------------
    # Mission CRUD
    # -------------------------------------------------------------------

    def create(
        self,
        *,
        name: str,
        goal: str,
        budget: MissionBudget | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        mission_id = self._store.create_mission(name=name, goal=goal, budget=budget, metadata=metadata)
        if self._events is not None:
            self._events.emit_created(mission_id, name, goal)
        return mission_id

    def get(self, mission_id: str) -> Mission | None:
        return self._store.get_mission(mission_id)

    def list_missions(self, status: MissionStatus | None = None) -> list[Mission]:
        return self._store.list_missions(status)

    # -------------------------------------------------------------------
    # Steps
    # -------------------------------------------------------------------

    def advance(self, mission_id: str, description: str) -> str:
        step_id = self._store.add_step(mission_id, description=description)
        if self._events is not None:
            self._events.emit_step(mission_id, description, len(self._store.get_steps(mission_id)))
        return step_id

    def steps(self, mission_id: str) -> list[MissionStep]:
        return self._store.get_steps(mission_id)

    def update_step(
        self,
        step_id: str,
        status: StepStatus,
        result: str | None = None,
    ) -> None:
        self._store.update_step_status(step_id, status, result)

    # -------------------------------------------------------------------
    # Subgoals
    # -------------------------------------------------------------------

    def subgoals(self, mission_id: str) -> list[MissionSubgoal]:
        return self._store.get_subgoals(mission_id)

    def add_subgoal(self, mission_id: str, *, description: str, priority: int = 1) -> str:
        return self._store.add_subgoal(mission_id, description=description, priority=priority)

    def update_subgoal_status(self, subgoal_id: str, status: SubgoalStatus) -> None:
        self._store.update_subgoal_status(subgoal_id, status)

    # -------------------------------------------------------------------
    # Verifiers
    # -------------------------------------------------------------------

    def set_verifier(self, mission_id: str, verifier: MissionVerifierCallable) -> None:
        self._verifiers[mission_id] = verifier

    def has_verifier(self, mission_id: str) -> bool:
        return mission_id in self._verifiers

    def verify(self, mission_id: str) -> VerifierResult:
        """Run the registered verifier (or surface "no verifier" when
        none is registered), persist the outcome, transition the
        mission status if the outcome demands it, and return the
        result.

        PR #1015 review:

        - P2 (async verifiers): the TS `MissionVerifier` contract is
          promise-based; an `async def` Python verifier returns a
          coroutine. We detect coroutines via ``inspect.iscoroutine``
          and run them to completion via ``asyncio.run`` so an async
          verifier resolves into a real ``VerifierResult`` instead of
          flowing through the error path as `'coroutine' object has
          no attribute 'passed'`. Sync verifiers are unchanged. The
          caller must not be inside a running event loop (matches
          the standard sync-to-async bridge limitation).

        - P2 (transition ordering): a verifier outcome that demands a
          status transition (passing -> completed) must validate the
          transition BEFORE persisting the verification record or
          emitting events. Otherwise a `paused` mission with a
          passing verifier would persist a passing record + emit
          `mission_verified`, then raise on the invalid
          `paused -> completed` transition and leave the mission in
          an inconsistent state. We resolve + apply the transition,
          then record the verification, then emit events.
        """
        verifier = self._verifiers.get(mission_id)
        if verifier is None:
            outcome = build_missing_verifier_outcome()
        else:
            try:
                raw = verifier(mission_id)
                if inspect.iscoroutine(raw):
                    # PR #1016 review (P2) parity for executor.py: refuse
                    # the async-in-running-loop case BEFORE recording any
                    # state. Without the pre-check, `asyncio.run` would
                    # raise `RuntimeError` from inside an active loop, the
                    # catch-all would persist a failing verification, and
                    # the mission would stay open even though the verifier
                    # would have passed.
                    if has_running_loop():
                        raw.close()  # avoid an unawaited-coroutine warning
                        raise AsyncContextError(
                            "MissionManager.verify received an async verifier while a "
                            "running event loop is active. Run from sync code, or "
                            "await the verifier yourself and pass the resolved "
                            "VerifierResult into a future async-aware verify API."
                        )
                    raw = asyncio.run(raw)
                outcome = resolve_mission_verification_outcome(raw)
            except AsyncContextError:
                raise
            except Exception as err:
                outcome = resolve_mission_verification_error_outcome(str(err), type(err).__name__)

        # Pre-validate the status transition so an invalid one does not
        # leave a stranded verification record + status-change event.
        transition = None
        previous_status: MissionStatus | None = None
        if outcome.next_status is not None:
            mission = self._store.get_mission(mission_id)
            previous_status = mission.status if mission is not None else None
            transition = resolve_mission_status_transition(previous_status, outcome.next_status)

        # Validation passed: persist verification + transition together,
        # then emit best-effort notifications.
        self._store.record_verification(mission_id, outcome.result)
        if transition is not None:
            self._store.update_mission_status(mission_id, transition.next_status)
        if self._events is not None:
            self._events.emit_verified(mission_id, outcome.result.passed, outcome.result.reason)
            if transition is not None and previous_status is not None and transition.should_emit_status_change:
                self._events.emit_status_change(mission_id, previous_status, transition.next_status)
        return outcome.result

    def verifications(self, mission_id: str) -> list[MissionVerificationRecord]:
        return self._store.get_verifications(mission_id)

    # -------------------------------------------------------------------
    # Status transitions
    # -------------------------------------------------------------------

    def pause(self, mission_id: str) -> None:
        self._transition_mission_status(mission_id, "paused")

    def resume(self, mission_id: str) -> None:
        self._transition_mission_status(mission_id, "active")

    def cancel(self, mission_id: str) -> None:
        self._transition_mission_status(mission_id, "canceled")

    def set_status(self, mission_id: str, status: MissionStatus) -> None:
        self._transition_mission_status(mission_id, status)

    # -------------------------------------------------------------------
    # Budget usage
    # -------------------------------------------------------------------

    def budget_usage(self, mission_id: str) -> BudgetUsage:
        return self._store.get_budget_usage(mission_id)

    # -------------------------------------------------------------------
    # Checkpoint
    # -------------------------------------------------------------------

    def save_checkpoint(self, mission_id: str, checkpoint_dir: str) -> str:
        """Persist a full mission snapshot to ``checkpoint_dir``.
        Returns the resulting file path. Mirrors TS
        ``manager.saveCheckpoint``."""
        # Local import keeps the slice-1/2 manager free of the
        # slice-3 checkpoint dependency until callers actually use it.
        from autocontext.mission.checkpoint import save_checkpoint

        return save_checkpoint(self._store, mission_id, checkpoint_dir)

    # -------------------------------------------------------------------
    # Bookkeeping
    # -------------------------------------------------------------------

    def get_db_path(self) -> str:
        return self._store.get_db_path()

    def close(self) -> None:
        self._store.close()

    def __enter__(self) -> MissionManager:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    # -------------------------------------------------------------------
    # internal
    # -------------------------------------------------------------------

    def _transition_mission_status(self, mission_id: str, status: MissionStatus) -> None:
        mission = self._store.get_mission(mission_id)
        previous_status = mission.status if mission is not None else None
        transition = resolve_mission_status_transition(previous_status, status)
        self._store.update_mission_status(mission_id, transition.next_status)
        if previous_status is not None and transition.should_emit_status_change and self._events is not None:
            self._events.emit_status_change(mission_id, previous_status, transition.next_status)
