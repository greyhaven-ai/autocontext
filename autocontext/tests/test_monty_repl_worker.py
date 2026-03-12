"""Unit tests for MontyReplWorker with mocked Monty interpreter."""
from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock, patch

from autocontext.harness.repl.types import ReplCommand, ReplWorkerProtocol

# ---------------------------------------------------------------------------
# Mock helpers (same pattern as Phase 1/2 tests)
# ---------------------------------------------------------------------------

def _make_complete(output: Any) -> MagicMock:
    """Build a completion object: no function_name attr, has .output."""
    c = MagicMock(spec=[])
    c.output = output
    return c


def _make_snapshot(fn_name: str, args: tuple[Any, ...]) -> MagicMock:
    """Build a snapshot object: has function_name, args, resume()."""
    s = MagicMock()
    s.function_name = fn_name
    s.args = args
    return s


def _build_simple_monty_mock(
    external_calls: list[tuple[str, tuple[Any, ...]]],
    final_output: Any,
) -> MagicMock:
    """Build a mock Monty that walks through external calls then completes."""
    complete = _make_complete(final_output)

    snapshots: list[MagicMock] = []
    for fn_name, args in external_calls:
        snap = _make_snapshot(fn_name, args)
        snapshots.append(snap)

    for i, snap in enumerate(snapshots):
        if i + 1 < len(snapshots):
            snap.resume.return_value = snapshots[i + 1]
        else:
            snap.resume.return_value = complete

    monty = MagicMock()
    monty.start.return_value = snapshots[0] if snapshots else complete
    return monty


def _build_print_monty(text: str, answer: dict[str, Any] | None = None, state: dict[str, Any] | None = None) -> MagicMock:
    """Build a mock Monty that calls _print(text) then completes."""
    ans = answer or {"content": "", "ready": False}
    st = state or {}
    return _build_simple_monty_mock(
        external_calls=[("_print", (text,))],
        final_output={"answer": ans, "state": st},
    )


def _worker(**kwargs: Any) -> Any:
    from autocontext.harness.repl.monty_worker import MontyReplWorker
    return MontyReplWorker(**kwargs)


# ---------------------------------------------------------------------------
# Construction tests
# ---------------------------------------------------------------------------


class TestMontyReplWorkerConstruction:
    def test_default_namespace_has_answer(self) -> None:
        w = _worker()
        assert "answer" in w.namespace
        assert w.namespace["answer"] == {"content": "", "ready": False}

    def test_default_namespace_has_state(self) -> None:
        w = _worker()
        assert "state" in w.namespace
        assert w.namespace["state"] == {}

    def test_custom_namespace_merged(self) -> None:
        w = _worker(namespace={"my_data": [1, 2, 3]})
        assert w.namespace["my_data"] == [1, 2, 3]
        assert "answer" in w.namespace  # defaults preserved

    def test_namespace_is_mutable(self) -> None:
        """RlmSession writes get_history into worker.namespace after construction."""
        w = _worker()
        w.namespace["get_history"] = lambda: []
        assert callable(w.namespace["get_history"])

    def test_satisfies_protocol(self) -> None:
        w = _worker()
        assert isinstance(w, ReplWorkerProtocol)


# ---------------------------------------------------------------------------
# Execution tests
# ---------------------------------------------------------------------------


class TestMontyReplWorkerExecution:
    def test_simple_expression_captured_via_print(self) -> None:
        """Trailing expression should be auto-converted to _print(repr(...))."""
        mock = _build_print_monty("42")

        w = _worker()
        with patch("autocontext.harness.repl.monty_worker._create_repl_monty", return_value=mock):
            result = w.run_code(ReplCommand("42"))

        assert "42" in result.stdout
        assert result.error is None

    def test_print_call_captured(self) -> None:
        """print() calls rewritten to _print() and captured."""
        mock = _build_print_monty("hello world")

        w = _worker()
        with patch("autocontext.harness.repl.monty_worker._create_repl_monty", return_value=mock):
            result = w.run_code(ReplCommand("print('hello world')"))

        assert "hello world" in result.stdout
        assert result.error is None

    def test_syntax_error_returns_error(self) -> None:
        w = _worker()
        result = w.run_code(ReplCommand("def "))
        assert result.error is not None
        assert "SyntaxError" in result.error

    def test_runtime_error_captured(self) -> None:
        """Runtime errors from Monty should be captured as errors, not raised."""
        monty = MagicMock()
        monty.start.side_effect = RuntimeError("NameError: x is not defined")

        w = _worker()
        with patch("autocontext.harness.repl.monty_worker._create_repl_monty", return_value=monty):
            result = w.run_code(ReplCommand("x + 1"))

        assert result.error is not None
        assert "NameError" in result.error


# ---------------------------------------------------------------------------
# Answer persistence tests
# ---------------------------------------------------------------------------


class TestMontyReplWorkerAnswer:
    def test_answer_updated_from_output(self) -> None:
        mock = _build_simple_monty_mock(
            external_calls=[],
            final_output={"answer": {"content": "done", "ready": True}, "state": {}},
        )

        w = _worker()
        with patch("autocontext.harness.repl.monty_worker._create_repl_monty", return_value=mock):
            result = w.run_code(ReplCommand('answer["content"] = "done"\nanswer["ready"] = True'))

        assert result.answer["content"] == "done"
        assert result.answer["ready"] is True
        assert w.namespace["answer"]["content"] == "done"

    def test_answer_persists_across_turns(self) -> None:
        mock1 = _build_simple_monty_mock(
            external_calls=[],
            final_output={"answer": {"content": "step1", "ready": False}, "state": {}},
        )
        mock2 = _build_simple_monty_mock(
            external_calls=[],
            final_output={"answer": {"content": "step2", "ready": True}, "state": {}},
        )

        w = _worker()
        with patch("autocontext.harness.repl.monty_worker._create_repl_monty", return_value=mock1):
            w.run_code(ReplCommand('answer["content"] = "step1"'))
        assert w.namespace["answer"]["content"] == "step1"

        with patch("autocontext.harness.repl.monty_worker._create_repl_monty", return_value=mock2):
            result = w.run_code(ReplCommand('answer["content"] = "step2"\nanswer["ready"] = True'))
        assert result.answer["content"] == "step2"


# ---------------------------------------------------------------------------
# State persistence tests
# ---------------------------------------------------------------------------


class TestMontyReplWorkerState:
    def test_state_persists_across_turns(self) -> None:
        mock1 = _build_simple_monty_mock(
            external_calls=[],
            final_output={"answer": {"content": "", "ready": False}, "state": {"x": 42}},
        )
        mock2 = _build_simple_monty_mock(
            external_calls=[("_print", ("42",))],
            final_output={"answer": {"content": "", "ready": False}, "state": {"x": 42, "y": 100}},
        )

        w = _worker()
        with patch("autocontext.harness.repl.monty_worker._create_repl_monty", return_value=mock1):
            w.run_code(ReplCommand('state["x"] = 42'))
        assert w.namespace["state"]["x"] == 42

        with patch("autocontext.harness.repl.monty_worker._create_repl_monty", return_value=mock2):
            result = w.run_code(ReplCommand('print(state["x"])'))
        assert "42" in result.stdout


# ---------------------------------------------------------------------------
# Stdlib dispatch tests
# ---------------------------------------------------------------------------


class TestMontyReplWorkerStdlib:
    def test_stdlib_json_dumps(self) -> None:
        """stdlib("json", "dumps", ...) should dispatch to json.dumps."""
        import json

        mock = _build_simple_monty_mock(
            external_calls=[
                ("stdlib", ("json", "dumps", {"a": 1})),
                ("_print", (json.dumps({"a": 1}),)),
            ],
            final_output={"answer": {"content": "", "ready": False}, "state": {}},
        )

        w = _worker()
        with patch("autocontext.harness.repl.monty_worker._create_repl_monty", return_value=mock):
            result = w.run_code(ReplCommand('result = stdlib("json", "dumps", {"a": 1})\nprint(result)'))

        assert result.error is None
        # Verify the stdlib dispatch was called - mock.start was invoked
        mock.start.assert_called_once()

    def test_stdlib_math_sqrt(self) -> None:
        import math

        mock = _build_simple_monty_mock(
            external_calls=[
                ("stdlib", ("math", "sqrt", 16.0)),
                ("_print", (str(math.sqrt(16.0)),)),
            ],
            final_output={"answer": {"content": "", "ready": False}, "state": {}},
        )

        w = _worker()
        with patch("autocontext.harness.repl.monty_worker._create_repl_monty", return_value=mock):
            result = w.run_code(ReplCommand('result = stdlib("math", "sqrt", 16.0)\nprint(result)'))
        assert result.error is None

    def test_stdlib_unknown_module_raises(self) -> None:
        mock = _build_simple_monty_mock(
            external_calls=[("stdlib", ("shutil", "rmtree", "/tmp"))],
            final_output={"answer": {"content": "", "ready": False}, "state": {}},
        )

        w = _worker()
        with patch("autocontext.harness.repl.monty_worker._create_repl_monty", return_value=mock):
            result = w.run_code(ReplCommand('stdlib("shutil", "rmtree", "/tmp")'))
        # Dispatch should raise ValueError which gets captured
        assert result.error is not None

    def test_stdlib_unknown_function_raises(self) -> None:
        mock = _build_simple_monty_mock(
            external_calls=[("stdlib", ("json", "evil_func", "{}"))],
            final_output={"answer": {"content": "", "ready": False}, "state": {}},
        )

        w = _worker()
        with patch("autocontext.harness.repl.monty_worker._create_repl_monty", return_value=mock):
            result = w.run_code(ReplCommand('stdlib("json", "evil_func", "{}")'))
        assert result.error is not None


# ---------------------------------------------------------------------------
# Text helper tests
# ---------------------------------------------------------------------------


class TestMontyReplWorkerTextHelpers:
    def test_peek_external_function(self) -> None:
        long_text = "a" * 5000
        mock = _build_simple_monty_mock(
            external_calls=[
                ("peek", (long_text, 0, 100)),
                ("_print", ("a" * 100,)),
            ],
            final_output={"answer": {"content": "", "ready": False}, "state": {}},
        )

        w = _worker(namespace={"my_text": long_text})
        with patch("autocontext.harness.repl.monty_worker._create_repl_monty", return_value=mock):
            result = w.run_code(ReplCommand('chunk = peek(my_text, 0, 100)\nprint(chunk)'))
        assert result.error is None

    def test_grep_external_function(self) -> None:
        text = "line1\nfoo bar\nline3"
        mock = _build_simple_monty_mock(
            external_calls=[
                ("grep", (text, "foo")),
                ("_print", (str(["foo bar"]),)),
            ],
            final_output={"answer": {"content": "", "ready": False}, "state": {}},
        )

        w = _worker(namespace={"text": text})
        with patch("autocontext.harness.repl.monty_worker._create_repl_monty", return_value=mock):
            result = w.run_code(ReplCommand('hits = grep(text, "foo")\nprint(hits)'))
        assert result.error is None


# ---------------------------------------------------------------------------
# Callable dispatch tests
# ---------------------------------------------------------------------------


class TestMontyReplWorkerCallables:
    def test_llm_batch_dispatched_to_injected_callable(self) -> None:
        fake_llm = MagicMock(return_value=["response1"])

        mock = _build_simple_monty_mock(
            external_calls=[
                ("llm_batch", (["prompt1"],)),
                ("_print", (str(["response1"]),)),
            ],
            final_output={"answer": {"content": "", "ready": False}, "state": {}},
        )

        w = _worker(namespace={"llm_batch": fake_llm})
        with patch("autocontext.harness.repl.monty_worker._create_repl_monty", return_value=mock):
            result = w.run_code(ReplCommand('result = llm_batch(["prompt1"])\nprint(result)'))
        assert result.error is None

    def test_get_history_dispatched_to_injected_callable(self) -> None:
        fake_history = MagicMock(return_value=[{"turn": 1}])

        mock = _build_simple_monty_mock(
            external_calls=[
                ("get_history", ()),
                ("_print", (str([{"turn": 1}]),)),
            ],
            final_output={"answer": {"content": "", "ready": False}, "state": {}},
        )

        w = _worker(namespace={"get_history": fake_history})
        with patch("autocontext.harness.repl.monty_worker._create_repl_monty", return_value=mock):
            result = w.run_code(ReplCommand('h = get_history()\nprint(h)'))
        assert result.error is None


# ---------------------------------------------------------------------------
# Truncation tests
# ---------------------------------------------------------------------------


class TestMontyReplWorkerTruncation:
    def test_stdout_truncated_at_max(self) -> None:
        big_text = "x" * 20000
        mock = _build_print_monty(big_text)

        w = _worker(max_stdout_chars=100)
        with patch("autocontext.harness.repl.monty_worker._create_repl_monty", return_value=mock):
            result = w.run_code(ReplCommand("print('x' * 20000)"))
        assert len(result.stdout) < 20000
        assert "truncated" in result.stdout


# ---------------------------------------------------------------------------
# Timeout tests
# ---------------------------------------------------------------------------


class TestMontyReplWorkerTimeout:
    def test_timeout_returns_error(self) -> None:
        """A Monty dispatch loop that exceeds timeout should return error."""
        # Create a snapshot whose resume introduces a delay
        snap = _make_snapshot("_print", ("tick",))

        def slow_resume(**kwargs: Any) -> Any:
            time.sleep(0.5)
            return snap  # Keep looping forever via self-reference

        snap.resume.side_effect = slow_resume

        monty = MagicMock()
        monty.start.return_value = snap

        w = _worker(timeout_seconds=0.2)
        with patch("autocontext.harness.repl.monty_worker._create_repl_monty", return_value=monty):
            result = w.run_code(ReplCommand("x = 1"))
        assert result.error is not None
        assert "timeout" in result.error.lower() or "Timeout" in result.error or "exceeded" in result.error.lower()


# ---------------------------------------------------------------------------
# Trailing expression conversion tests
# ---------------------------------------------------------------------------


class TestTrailingExpressionConversion:
    def test_trailing_expr_converted_to_print(self) -> None:
        from autocontext.harness.repl.monty_worker import _rewrite_trailing_expr

        code = "x = 1\nx + 1"
        result = _rewrite_trailing_expr(code)
        assert "_print(repr(" in result

    def test_no_trailing_expr_unchanged(self) -> None:
        from autocontext.harness.repl.monty_worker import _rewrite_trailing_expr

        code = "x = 1\ny = 2"
        result = _rewrite_trailing_expr(code)
        assert "_print" not in result

    def test_print_call_not_double_wrapped(self) -> None:
        from autocontext.harness.repl.monty_worker import _rewrite_trailing_expr

        code = "print('hello')"
        result = _rewrite_trailing_expr(code)
        # Should not wrap a print() call in _print(repr(...))
        assert "_print(repr(" not in result


# ---------------------------------------------------------------------------
# Import guard tests
# ---------------------------------------------------------------------------


class TestMontyReplImportGuard:
    def test_import_error_when_monty_missing(self) -> None:
        """If pydantic_monty is missing, run_code should return error ReplResult."""
        w = _worker()
        with patch("autocontext.harness.repl.monty_worker._create_repl_monty", side_effect=ImportError("no pydantic_monty")):
            result = w.run_code(ReplCommand("x = 1"))
        assert result.error is not None
        assert "pydantic" in result.error.lower() or "import" in result.error.lower()
