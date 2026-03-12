"""Live tests for MontyReplWorker with real pydantic-monty interpreter.

Skipped when pydantic-monty is not installed (CI/offline environments).
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

try:
    import pydantic_monty  # noqa: F401

    HAS_MONTY = True
except ImportError:
    HAS_MONTY = False

pytestmark = pytest.mark.skipif(not HAS_MONTY, reason="pydantic-monty not installed")


def _worker(**kwargs: Any) -> Any:
    from autocontext.harness.repl.monty_worker import MontyReplWorker

    return MontyReplWorker(**kwargs)


def _cmd(code: str) -> Any:
    from autocontext.harness.repl.types import ReplCommand

    return ReplCommand(code)


class TestMontyReplLiveBasic:
    def test_simple_print(self) -> None:
        w = _worker()
        result = w.run_code(_cmd('print("hello")'))
        assert "hello" in result.stdout
        assert result.error is None

    def test_trailing_expression_displayed(self) -> None:
        w = _worker()
        result = w.run_code(_cmd("1 + 2"))
        assert "3" in result.stdout
        assert result.error is None

    def test_answer_dict_roundtrip(self) -> None:
        w = _worker()
        result = w.run_code(_cmd('answer["content"] = "test output"\nanswer["ready"] = True'))
        assert result.answer["content"] == "test output"
        assert result.answer["ready"] is True
        assert w.namespace["answer"]["content"] == "test output"

    def test_state_persists_across_turns(self) -> None:
        w = _worker()
        # Turn 1: store value
        r1 = w.run_code(_cmd('state["x"] = 42'))
        assert r1.error is None
        assert w.namespace["state"]["x"] == 42

        # Turn 2: read value
        r2 = w.run_code(_cmd('print(state["x"])'))
        assert "42" in r2.stdout
        assert r2.error is None

    def test_stdlib_json(self) -> None:
        w = _worker()
        result = w.run_code(_cmd('text = stdlib("json", "dumps", {"a": 1})\nprint(text)'))
        assert result.error is None
        assert '"a"' in result.stdout

    def test_stdlib_math(self) -> None:
        w = _worker()
        result = w.run_code(_cmd('val = stdlib("math", "sqrt", 16.0)\nprint(val)'))
        assert result.error is None
        assert "4.0" in result.stdout

    def test_data_variables_accessible(self) -> None:
        w = _worker(namespace={"scores": [0.1, 0.5, 0.9]})
        result = w.run_code(_cmd("print(len(scores))"))
        assert "3" in result.stdout
        assert result.error is None

    def test_text_helper_peek(self) -> None:
        long_text = "a" * 5000
        w = _worker(namespace={"big": long_text})
        result = w.run_code(_cmd("chunk = peek(big, 0, 50)\nprint(len(chunk))"))
        assert "50" in result.stdout
        assert result.error is None

    def test_syntax_error_caught(self) -> None:
        w = _worker()
        result = w.run_code(_cmd("def "))
        assert result.error is not None
        assert "SyntaxError" in result.error

    def test_callable_injection_llm_batch(self) -> None:
        fake_llm = MagicMock(return_value=["response_one"])
        w = _worker(namespace={"llm_batch": fake_llm})
        result = w.run_code(_cmd('result = llm_batch(["hello"])\nprint(result)'))
        assert result.error is None
        fake_llm.assert_called_once_with(["hello"])
