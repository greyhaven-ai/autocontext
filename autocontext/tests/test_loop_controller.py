from __future__ import annotations

import threading
import time

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
