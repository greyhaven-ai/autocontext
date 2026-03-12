"""Tests for autocontext.harness.repl.types — ReplCommand, ReplResult, ExecutionRecord, RlmContext."""

from __future__ import annotations

from autocontext.harness.repl.types import ExecutionRecord, ReplCommand, ReplResult, RlmContext


def test_repl_command_has_code_field() -> None:
    cmd = ReplCommand(code="print('hello')")
    assert cmd.code == "print('hello')"


def test_repl_result_fields() -> None:
    result = ReplResult(stdout="4", error=None, answer={"content": "", "ready": False})
    assert result.stdout == "4"
    assert result.error is None
    assert result.answer == {"content": "", "ready": False}


def test_execution_record_fields() -> None:
    rec = ExecutionRecord(turn=1, code="x = 1", stdout="", error=None, answer_ready=False)
    assert rec.turn == 1
    assert rec.code == "x = 1"
    assert rec.stdout == ""
    assert rec.error is None
    assert not rec.answer_ready


def test_rlm_context_fields() -> None:
    ctx = RlmContext(variables={"data": [1, 2, 3]}, summary="test data")
    assert ctx.variables == {"data": [1, 2, 3]}
    assert ctx.summary == "test data"


def test_rlm_context_defaults() -> None:
    ctx = RlmContext()
    assert ctx.variables == {}
    assert ctx.summary == ""
