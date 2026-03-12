"""Tests for autocontext.harness.core.controller — LoopController."""

from __future__ import annotations

import threading

from autocontext.harness.core.controller import LoopController


def test_controller_starts_unpaused() -> None:
    ctrl = LoopController()
    assert not ctrl.is_paused()


def test_pause_resume_cycle() -> None:
    ctrl = LoopController()
    ctrl.pause()
    assert ctrl.is_paused()
    ctrl.resume()
    assert not ctrl.is_paused()


def test_is_paused_reflects_state() -> None:
    ctrl = LoopController()
    assert not ctrl.is_paused()
    ctrl.pause()
    assert ctrl.is_paused()


def test_gate_override_set_and_take() -> None:
    ctrl = LoopController()
    ctrl.set_gate_override("advance")
    assert ctrl.take_gate_override() == "advance"


def test_gate_override_take_clears() -> None:
    ctrl = LoopController()
    ctrl.set_gate_override("retry")
    ctrl.take_gate_override()
    assert ctrl.take_gate_override() is None


def test_hint_inject_and_take() -> None:
    ctrl = LoopController()
    ctrl.inject_hint("try more aggression")
    assert ctrl.take_hint() == "try more aggression"


def test_hint_take_clears() -> None:
    ctrl = LoopController()
    ctrl.inject_hint("hint")
    ctrl.take_hint()
    assert ctrl.take_hint() is None


def test_chat_submit_and_respond() -> None:
    ctrl = LoopController()

    def _loop_thread() -> None:
        msg = ctrl.poll_chat()
        while msg is None:
            msg = ctrl.poll_chat()
        role, message = msg
        ctrl.respond_chat(role, f"echo: {message}")

    t = threading.Thread(target=_loop_thread)
    t.start()
    response = ctrl.submit_chat("user", "hello")
    t.join(timeout=5)
    assert response == "echo: hello"


def test_poll_chat_empty_returns_none() -> None:
    ctrl = LoopController()
    assert ctrl.poll_chat() is None
