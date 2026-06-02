"""AC-697 mission manager tests (slice 2).

Mirrors the unit-test surface for ``ts/src/mission/manager.ts``.
Covers CRUD facade, verifier registration + run, status transitions,
event emission, budget usage, and the error-tolerant verifier path
(a thrown exception lands as a failing result tagged with the
exception class name).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from autocontext.mission import (
    MissionBudget,
    MissionEventEmitter,
    MissionManager,
    VerifierResult,
)


def _manager(tmp_path: Path) -> MissionManager:
    return MissionManager(str(tmp_path / "mission.sqlite3"))


# ---------------------------------------------------------------------------
# CRUD facade
# ---------------------------------------------------------------------------


def test_create_returns_id_and_persists_mission(tmp_path: Path) -> None:
    with _manager(tmp_path) as mgr:
        mid = mgr.create(name="x", goal="g")
        mission = mgr.get(mid)
        assert mission is not None and mission.status == "active"


def test_advance_records_a_completed_step(tmp_path: Path) -> None:
    with _manager(tmp_path) as mgr:
        mid = mgr.create(name="x", goal="g")
        sid = mgr.advance(mid, "ran cli")
        steps = mgr.steps(mid)
        assert len(steps) == 1 and steps[0].id == sid
        assert steps[0].status == "completed"


def test_subgoals_add_and_update_status(tmp_path: Path) -> None:
    with _manager(tmp_path) as mgr:
        mid = mgr.create(name="x", goal="g")
        sgid = mgr.add_subgoal(mid, description="first")
        mgr.update_subgoal_status(sgid, "completed")
        assert mgr.subgoals(mid)[0].status == "completed"


def test_budget_usage_reflects_step_count_against_budget(tmp_path: Path) -> None:
    with _manager(tmp_path) as mgr:
        mid = mgr.create(name="x", goal="g", budget=MissionBudget(max_steps=2))
        mgr.advance(mid, "s1")
        usage = mgr.budget_usage(mid)
        assert usage.steps_used == 1 and usage.exhausted is False
        mgr.advance(mid, "s2")
        assert mgr.budget_usage(mid).exhausted is True


# ---------------------------------------------------------------------------
# Verifier
# ---------------------------------------------------------------------------


def test_verify_without_registered_verifier_records_failing_result(
    tmp_path: Path,
) -> None:
    with _manager(tmp_path) as mgr:
        mid = mgr.create(name="x", goal="g")
        result = mgr.verify(mid)
        assert result.passed is False
        assert result.reason == "No verifier registered"
        assert mgr.get(mid).status == "active"  # status unchanged
        assert mgr.verifications(mid)[0].passed is False


def test_passing_verifier_completes_mission_and_records_result(
    tmp_path: Path,
) -> None:
    with _manager(tmp_path) as mgr:
        mid = mgr.create(name="x", goal="g")
        mgr.set_verifier(mid, lambda _mid: VerifierResult(passed=True, reason="green"))
        result = mgr.verify(mid)
        assert result.passed is True
        assert mgr.get(mid).status == "completed"


def test_failing_verifier_leaves_status_unchanged_but_records_result(
    tmp_path: Path,
) -> None:
    with _manager(tmp_path) as mgr:
        mid = mgr.create(name="x", goal="g")
        mgr.set_verifier(mid, lambda _mid: VerifierResult(passed=False, reason="tests failed"))
        result = mgr.verify(mid)
        assert result.passed is False
        assert mgr.get(mid).status == "active"


def test_verifier_that_throws_yields_failing_result_with_error_metadata(
    tmp_path: Path,
) -> None:
    with _manager(tmp_path) as mgr:
        mid = mgr.create(name="x", goal="g")

        def boom(_mid: str) -> VerifierResult:
            raise RuntimeError("kaboom")

        mgr.set_verifier(mid, boom)
        result = mgr.verify(mid)
        assert result.passed is False
        assert result.metadata["errorName"] == "RuntimeError"
        assert "kaboom" in result.reason


# ---------------------------------------------------------------------------
# Status transitions
# ---------------------------------------------------------------------------


def test_pause_resume_cancel_round_trip(tmp_path: Path) -> None:
    with _manager(tmp_path) as mgr:
        mid = mgr.create(name="x", goal="g")
        mgr.pause(mid)
        assert mgr.get(mid).status == "paused"
        mgr.resume(mid)
        assert mgr.get(mid).status == "active"
        mgr.cancel(mid)
        assert mgr.get(mid).status == "canceled"


def test_invalid_transition_rejects(tmp_path: Path) -> None:
    """The transition table forbids paused -> completed; the manager
    surfaces the error from the lifecycle helper rather than silently
    persisting the change."""
    with _manager(tmp_path) as mgr:
        mid = mgr.create(name="x", goal="g")
        mgr.pause(mid)
        with pytest.raises(ValueError, match="paused -> completed"):
            mgr.set_status(mid, "completed")
        assert mgr.get(mid).status == "paused"


# ---------------------------------------------------------------------------
# Event emission
# ---------------------------------------------------------------------------


def test_events_emit_created_step_status_and_verified(tmp_path: Path) -> None:
    events = MissionEventEmitter()
    captured: list[tuple[str, object]] = []
    for kind in (
        "mission_created",
        "mission_step",
        "mission_status_changed",
        "mission_verified",
    ):
        events.on(kind, lambda payload, k=kind: captured.append((k, payload)))

    with MissionManager(str(tmp_path / "m.sqlite3"), events=events) as mgr:
        mid = mgr.create(name="x", goal="g")
        mgr.advance(mid, "s1")
        mgr.pause(mid)
        mgr.set_verifier(mid, lambda _mid: VerifierResult(passed=False, reason="r"))
        mgr.verify(mid)

    kinds = [k for k, _ in captured]
    assert kinds == [
        "mission_created",
        "mission_step",
        "mission_status_changed",
        "mission_verified",
    ]


# ---------------------------------------------------------------------------
# PR #1015 review (P2): async verifier support
# ---------------------------------------------------------------------------


def test_async_verifier_is_awaited_and_completes_mission(tmp_path: Path) -> None:
    """The TS `MissionVerifier` contract is promise-based; an
    `async def` Python verifier returns a coroutine. The manager now
    detects coroutines and runs them via `asyncio.run` so an async
    verifier resolves into a real `VerifierResult` instead of
    flowing through the error path as a coroutine-attribute error.
    """
    with _manager(tmp_path) as mgr:
        mid = mgr.create(name="x", goal="g")

        async def passing(_mid: str) -> VerifierResult:
            return VerifierResult(passed=True, reason="green")

        mgr.set_verifier(mid, passing)
        result = mgr.verify(mid)
        assert result.passed is True
        assert mgr.get(mid).status == "completed"


def test_async_verifier_that_raises_yields_failing_result(tmp_path: Path) -> None:
    with _manager(tmp_path) as mgr:
        mid = mgr.create(name="x", goal="g")

        async def boom(_mid: str) -> VerifierResult:
            raise RuntimeError("kaboom")

        mgr.set_verifier(mid, boom)
        result = mgr.verify(mid)
        assert result.passed is False
        assert result.metadata["errorName"] == "RuntimeError"
        assert "kaboom" in result.reason


def test_async_verifier_inside_running_loop_raises_without_persisting(
    tmp_path: Path,
) -> None:
    """PR #1016 review (P2) parity with the executor fix: the sync
    `verify()` API cannot bridge an async verifier from inside a
    running event loop because `asyncio.run` rejects a nested call.
    The fix raises `AsyncContextError` BEFORE recording any
    verification so the mission stays state-consistent.
    """
    import asyncio

    from autocontext.mission._async_bridge import AsyncContextError

    with _manager(tmp_path) as mgr:
        mid = mgr.create(name="x", goal="g")

        async def passing(_mid: str) -> VerifierResult:
            return VerifierResult(passed=True, reason="green")

        mgr.set_verifier(mid, passing)

        async def driver() -> None:
            with pytest.raises(AsyncContextError):
                mgr.verify(mid)

        asyncio.run(driver())
        assert mgr.verifications(mid) == []
        assert mgr.get(mid).status == "active"


# ---------------------------------------------------------------------------
# PR #1015 review (P2): transition ordering
# ---------------------------------------------------------------------------


def test_invalid_transition_under_verify_does_not_persist_verification(
    tmp_path: Path,
) -> None:
    """A paused mission with a passing verifier raises
    `paused -> completed`. Before this fix the verification record
    was already persisted and the `mission_verified` event already
    emitted, leaving an inconsistent state. After the fix the
    transition is pre-validated so the rejection short-circuits
    before either side effect.
    """
    with _manager(tmp_path) as mgr:
        mid = mgr.create(name="x", goal="g")
        mgr.pause(mid)
        mgr.set_verifier(mid, lambda _mid: VerifierResult(passed=True, reason="green"))
        with pytest.raises(ValueError, match="paused -> completed"):
            mgr.verify(mid)
        # No verification record persisted because the transition
        # rejection short-circuited first.
        assert mgr.verifications(mid) == []
        # Mission still paused.
        assert mgr.get(mid).status == "paused"


def test_verified_listener_raising_does_not_block_status_completion(
    tmp_path: Path,
) -> None:
    """Status transitions are durable state; events are
    best-effort. If a `mission_verified` listener raises, the
    status update is already persisted so the mission stays
    completed."""
    events = MissionEventEmitter()
    events.on(
        "mission_verified",
        lambda _payload: (_ for _ in ()).throw(RuntimeError("listener boom")),
    )
    with MissionManager(str(tmp_path / "m.sqlite3"), events=events) as mgr:
        mid = mgr.create(name="x", goal="g")
        mgr.set_verifier(mid, lambda _mid: VerifierResult(passed=True, reason="green"))
        with pytest.raises(RuntimeError, match="listener boom"):
            mgr.verify(mid)
        # The status transition is already persisted; the listener
        # exception did not block it.
        assert mgr.get(mid).status == "completed"


def test_status_change_event_carries_from_and_to(tmp_path: Path) -> None:
    events = MissionEventEmitter()
    payloads: list[object] = []
    events.on("mission_status_changed", payloads.append)
    with MissionManager(str(tmp_path / "m.sqlite3"), events=events) as mgr:
        mid = mgr.create(name="x", goal="g")
        mgr.pause(mid)

    assert len(payloads) == 1
    payload = payloads[0]
    assert payload.from_status == "active"  # type: ignore[union-attr]
    assert payload.to_status == "paused"  # type: ignore[union-attr]
