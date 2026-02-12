from __future__ import annotations

import pytest

from mts.rlm.repl_worker import CodeTimeout, ReplWorker
from mts.rlm.types import ReplCommand


class TestReplWorkerStdout:
    def test_print_captured(self) -> None:
        worker = ReplWorker()
        result = worker.run_code(ReplCommand('print("hello world")'))
        assert result.stdout.strip() == "hello world"
        assert result.error is None

    def test_trailing_expression_captured(self) -> None:
        worker = ReplWorker()
        result = worker.run_code(ReplCommand("2 + 3"))
        assert "5" in result.stdout

    def test_print_and_expression(self) -> None:
        worker = ReplWorker()
        result = worker.run_code(ReplCommand('print("first")\n42'))
        assert "first" in result.stdout
        assert "42" in result.stdout


class TestReplWorkerNamespace:
    def test_namespace_persists_across_calls(self) -> None:
        worker = ReplWorker()
        worker.run_code(ReplCommand("x = 123"))
        result = worker.run_code(ReplCommand("print(x)"))
        assert "123" in result.stdout

    def test_custom_namespace_injected(self) -> None:
        worker = ReplWorker(namespace={"data": [1, 2, 3]})
        result = worker.run_code(ReplCommand("print(len(data))"))
        assert "3" in result.stdout

    def test_safe_modules_available(self) -> None:
        worker = ReplWorker()
        worker.run_code(ReplCommand("import json; print(json.dumps({'a': 1}))"))
        # json is in the namespace directly, but import also works via builtins
        # Either way, the namespace has json available
        worker2 = ReplWorker()
        result2 = worker2.run_code(ReplCommand('print(json.dumps({"a": 1}))'))
        assert '{"a": 1}' in result2.stdout


class TestReplWorkerTruncation:
    def test_stdout_truncated(self) -> None:
        worker = ReplWorker(max_stdout_chars=50)
        result = worker.run_code(ReplCommand('print("x" * 200)'))
        assert len(result.stdout) < 200
        assert "truncated" in result.stdout


class TestReplWorkerErrors:
    def test_syntax_error(self) -> None:
        worker = ReplWorker()
        result = worker.run_code(ReplCommand("def"))
        assert result.error is not None
        assert "SyntaxError" in result.error

    def test_runtime_error(self) -> None:
        worker = ReplWorker()
        result = worker.run_code(ReplCommand("1 / 0"))
        assert result.error is not None
        assert "ZeroDivisionError" in result.error

    def test_name_error_after_error(self) -> None:
        worker = ReplWorker()
        worker.run_code(ReplCommand("1 / 0"))
        result = worker.run_code(ReplCommand("print('recovered')"))
        assert "recovered" in result.stdout
        assert result.error is None


class TestReplWorkerAnswerProtocol:
    def test_answer_default(self) -> None:
        worker = ReplWorker()
        result = worker.run_code(ReplCommand("print('hello')"))
        assert result.answer == {"content": "", "ready": False}

    def test_answer_content_set(self) -> None:
        worker = ReplWorker()
        result = worker.run_code(ReplCommand('answer["content"] = "my analysis"'))
        assert result.answer["content"] == "my analysis"
        assert result.answer["ready"] is False

    def test_answer_ready(self) -> None:
        worker = ReplWorker()
        worker.run_code(ReplCommand('answer["content"] = "done"'))
        result = worker.run_code(ReplCommand('answer["ready"] = True'))
        assert result.answer["ready"] is True
        assert result.answer["content"] == "done"


class TestReplWorkerRestrictions:
    def test_open_blocked(self) -> None:
        worker = ReplWorker()
        result = worker.run_code(ReplCommand('open("/etc/passwd")'))
        assert result.error is not None

    def test_os_blocked(self) -> None:
        worker = ReplWorker()
        result = worker.run_code(ReplCommand("os.listdir('.')"))
        assert result.error is not None

    def test_import_os_blocked(self) -> None:
        worker = ReplWorker()
        result = worker.run_code(ReplCommand("import os"))
        assert result.error is not None

    def test_subprocess_blocked(self) -> None:
        worker = ReplWorker()
        result = worker.run_code(ReplCommand("import subprocess"))
        assert result.error is not None


class TestReplWorkerTimeout:
    def test_timeout_raises(self) -> None:
        worker = ReplWorker(timeout_seconds=0.5)
        # Use a sleep-based loop that the thread-based timeout can detect
        # (tight `while True: pass` can't be interrupted from a daemon thread).
        with pytest.raises(CodeTimeout):
            worker.run_code(ReplCommand("while True: time.sleep(0.01)"))
