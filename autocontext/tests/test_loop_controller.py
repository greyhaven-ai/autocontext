from __future__ import annotations

import threading
import time

import pytest

from autocontext.loop.controller import LoopController


def test_starts_unpaused() -> None:
    ctrl = LoopController()
    assert not ctrl.is_paused()


def test_pause_and_resume() -> None:
    ctrl = LoopController()
    ctrl.pause()
    assert ctrl.is_paused()
    ctrl.resume()
    assert not ctrl.is_paused()


def test_wait_if_paused_blocks_then_resumes() -> None:
    ctrl = LoopController()
    ctrl.pause()
    resumed = threading.Event()

    def worker() -> None:
        ctrl.wait_if_paused()
        resumed.set()

    t = threading.Thread(target=worker, daemon=True)
    t.start()

    # Worker should be blocked
    time.sleep(0.05)
    assert not resumed.is_set()

    ctrl.resume()
    t.join(timeout=1.0)
    assert resumed.is_set()


def test_wait_if_paused_returns_immediately_when_running() -> None:
    ctrl = LoopController()
    # Should not block
    ctrl.wait_if_paused()


def test_gate_override_set_and_take() -> None:
    ctrl = LoopController()
    assert ctrl.take_gate_override() is None

    ctrl.set_gate_override("advance")
    assert ctrl.take_gate_override() == "advance"
    # Consumed — should be None now
    assert ctrl.take_gate_override() is None


def test_hint_inject_and_take() -> None:
    ctrl = LoopController()
    assert ctrl.take_hint() is None

    ctrl.inject_hint("try defensive strategy")
    assert ctrl.take_hint() == "try defensive strategy"
    # Consumed
    assert ctrl.take_hint() is None


def test_chat_submit_and_respond() -> None:
    ctrl = LoopController()

    response_holder: list[str] = []

    def requester() -> None:
        resp = ctrl.submit_chat("analyst", "why low scores?")
        response_holder.append(resp)

    t = threading.Thread(target=requester, daemon=True)
    t.start()

    # Give requester time to put chat on queue
    time.sleep(0.05)
    chat = ctrl.poll_chat()
    assert chat is not None
    role, msg = chat
    assert role == "analyst"
    assert msg == "why low scores?"

    ctrl.respond_chat("analyst", "scores are low because...")
    t.join(timeout=1.0)
    assert response_holder == ["scores are low because..."]


def test_aborting_chat_session_wakes_pending_and_inflight_submitters() -> None:
    ctrl = LoopController()
    failures: list[str] = []

    def requester(message: str) -> None:
        try:
            ctrl.submit_chat("analyst", message)
        except RuntimeError as exc:
            failures.append(str(exc))

    pending = threading.Thread(target=requester, args=("pending",), daemon=True)
    inflight = threading.Thread(target=requester, args=("inflight",), daemon=True)
    pending.start()
    inflight.start()
    try:
        deadline = time.monotonic() + 1.0
        while ctrl.pending_chat_count() != 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ctrl.poll_chat() is not None

        ctrl.abort_pending_chats("interactive run ended")
        pending.join(timeout=1.0)
        inflight.join(timeout=1.0)

        assert not pending.is_alive()
        assert not inflight.is_alive()
        assert len(failures) == 2
        assert set(failures) == {"interactive run ended"}
        with pytest.raises(RuntimeError, match="interactive run ended"):
            ctrl.submit_chat("analyst", "late")

        ctrl.begin_chat_session()
        assert ctrl.pending_chat_count() == 0
    finally:
        ctrl.abort_pending_chats("test cleanup")
        pending.join(timeout=1.0)
        inflight.join(timeout=1.0)


def test_poll_chat_empty() -> None:
    ctrl = LoopController()
    assert ctrl.poll_chat() is None


def test_gate_override_last_wins() -> None:
    ctrl = LoopController()
    ctrl.set_gate_override("retry")
    ctrl.set_gate_override("rollback")
    assert ctrl.take_gate_override() == "rollback"


def test_hint_last_wins() -> None:
    ctrl = LoopController()
    ctrl.inject_hint("first hint")
    ctrl.inject_hint("second hint")
    assert ctrl.take_hint() == "second hint"


@pytest.mark.parametrize(
    ("setter", "taker"),
    [
        ("inject_hint", "take_hint"),
        ("set_gate_override", "take_gate_override"),
    ],
)
def test_direct_take_is_atomic_across_concurrent_callers(
    setter: str,
    taker: str,
) -> None:
    ctrl = LoopController()
    getattr(ctrl, setter)("once")
    barrier = threading.Barrier(3)
    values: list[str | None] = []
    failures: list[BaseException] = []

    def take() -> None:
        try:
            barrier.wait(timeout=1.0)
            values.append(getattr(ctrl, taker)())
        except BaseException as exc:
            failures.append(exc)

    workers = [threading.Thread(target=take, daemon=True) for _ in range(2)]
    started_workers: list[threading.Thread] = []
    try:
        for worker in workers:
            worker.start()
            started_workers.append(worker)
        barrier.wait(timeout=1.0)
        for worker in started_workers:
            worker.join(timeout=1.0)
    finally:
        barrier.abort()
        for worker in started_workers:
            worker.join(timeout=1.0)

    assert not any(worker.is_alive() for worker in started_workers)
    assert failures == []
    assert values.count("once") == 1
    assert values.count(None) == 1


def test_hint_reservation_excludes_direct_take_and_preserves_newer_value() -> None:
    ctrl = LoopController()
    ctrl.inject_hint("reserved")
    reservation, value = ctrl.reserve_hint()

    assert value == "reserved"
    with pytest.raises(RuntimeError, match="reserved for delivery"):
        ctrl.take_hint()

    ctrl.inject_hint("newer")
    ctrl.commit_hint(reservation)
    assert ctrl.take_hint() == "newer"


def test_gate_reservation_rollback_restores_direct_consumption() -> None:
    ctrl = LoopController()
    ctrl.set_gate_override("advance")
    reservation, value = ctrl.reserve_gate_override()

    assert value == "advance"
    with pytest.raises(RuntimeError, match="reserved for delivery"):
        ctrl.take_gate_override()

    ctrl.rollback_gate_override(reservation)
    assert ctrl.take_gate_override() == "advance"


def test_chat_response_reservation_excludes_direct_response_and_checks_role() -> None:
    ctrl = LoopController()
    response_holder: list[str] = []

    thread = threading.Thread(
        target=lambda: response_holder.append(ctrl.submit_chat("analyst", "question")),
        daemon=True,
    )
    thread.start()
    try:
        deadline = time.monotonic() + 1.0
        while ctrl.pending_chat_count() != 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ctrl.poll_chat() == ("analyst", "question")

        with pytest.raises(ValueError, match="role does not match"):
            ctrl.reserve_chat_response("architect", "wrong")
        reservation = ctrl.reserve_chat_response("analyst", "reserved")
        with pytest.raises(RuntimeError, match="reserved for delivery"):
            ctrl.respond_chat("analyst", "racing")

        ctrl.rollback_chat_response(reservation)
        ctrl.respond_chat("analyst", "delivered")
        thread.join(timeout=1.0)
        assert response_holder == ["delivered"]
    finally:
        ctrl.abort_pending_chats("test cleanup")
        thread.join(timeout=1.0)


def test_begin_chat_session_aborts_stale_pre_run_request() -> None:
    ctrl = LoopController()
    finished = threading.Event()
    failure: list[str] = []

    def requester() -> None:
        try:
            ctrl.submit_chat("user", "before run")
        except RuntimeError as exc:
            failure.append(str(exc))
        finally:
            finished.set()

    thread = threading.Thread(target=requester, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 1.0
        while ctrl.pending_chat_count() != 1 and time.monotonic() < deadline:
            time.sleep(0.01)

        ctrl.begin_chat_session()
        thread.join(timeout=1.0)

        assert finished.is_set()
        assert failure == ["previous interactive chat session ended"]
        assert ctrl.pending_chat_count() == 0
    finally:
        ctrl.abort_pending_chats("test cleanup")
        thread.join(timeout=1.0)


def test_begin_run_session_resets_all_prior_run_state() -> None:
    ctrl = LoopController()
    ctrl.pause()
    ctrl.request_stop("old-stop", "old run")
    ctrl.inject_hint("old hint")
    ctrl.set_gate_override("rollback")

    ctrl.begin_run_session()

    assert not ctrl.is_paused()
    assert not ctrl.stop_requested()
    assert ctrl.stop_details() == (None, None)
    assert ctrl.take_hint() is None
    assert ctrl.take_gate_override() is None


def test_aborted_run_clears_values_and_invalidates_reservations() -> None:
    ctrl = LoopController()
    ctrl.inject_hint("old hint")
    hint_reservation, _hint = ctrl.reserve_hint()
    ctrl.set_gate_override("retry")
    gate_reservation, _gate = ctrl.reserve_gate_override()

    ctrl.abort_pending_chats("old run ended")
    ctrl.inject_hint("new hint")
    ctrl.set_gate_override("advance")

    ctrl.rollback_hint(hint_reservation)
    ctrl.rollback_gate_override(gate_reservation)
    assert ctrl.take_hint() == "new hint"
    assert ctrl.take_gate_override() == "advance"
    with pytest.raises(RuntimeError, match="no longer active"):
        ctrl.commit_hint(hint_reservation)
    with pytest.raises(RuntimeError, match="no longer active"):
        ctrl.commit_gate_override(gate_reservation)


def test_fresh_controller_has_no_stop_request() -> None:
    ctrl = LoopController()
    assert ctrl.stop_requested() is False
    assert ctrl.stop_details() == (None, None)


def test_request_stop_sets_flag_and_details() -> None:
    ctrl = LoopController()
    ctrl.request_stop("c1", "abort")
    assert ctrl.stop_requested() is True
    assert ctrl.stop_details() == ("c1", "abort")


def test_request_stop_wakes_paused_waiter() -> None:
    ctrl = LoopController()
    ctrl.pause()
    unblocked = threading.Event()

    def worker() -> None:
        ctrl.wait_if_paused()
        unblocked.set()

    t = threading.Thread(target=worker, daemon=True)
    t.start()

    # Worker should still be blocked (small window, not racy on a wall clock).
    assert not unblocked.wait(0.1)

    ctrl.request_stop("c1", "x")
    t.join(timeout=2.0)
    assert not t.is_alive()
    assert unblocked.is_set()


def test_clear_stop_resets_stop_state() -> None:
    ctrl = LoopController()
    ctrl.request_stop("c1", "abort")
    assert ctrl.stop_requested()
    assert ctrl.stop_details() == ("c1", "abort")

    ctrl.clear_stop()
    assert not ctrl.stop_requested()
    assert ctrl.stop_details() == (None, None)


def test_clear_stop_lets_a_reused_controller_stop_again() -> None:
    # A controller reused across runs must be able to stop a later run after a
    # prior stop was cleared (guards against a leaked stop flag).
    ctrl = LoopController()
    ctrl.request_stop("first", "one")
    ctrl.clear_stop()
    assert not ctrl.stop_requested()

    ctrl.request_stop("second", "two")
    assert ctrl.stop_requested()
    assert ctrl.stop_details() == ("second", "two")
