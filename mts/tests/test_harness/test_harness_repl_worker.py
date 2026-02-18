"""Tests for mts.harness.repl.worker — ReplWorker, CodeTimeout."""

from __future__ import annotations

import pytest

from mts.harness.repl.types import ReplCommand
from mts.harness.repl.worker import CodeTimeout, ReplWorker


def test_worker_executes_simple_expression() -> None:
    worker = ReplWorker()
    result = worker.run_code(ReplCommand(code="2 + 2"))
    assert "4" in result.stdout


def test_worker_restricts_open() -> None:
    worker = ReplWorker()
    result = worker.run_code(ReplCommand(code="open('file')"))
    assert result.error is not None


def test_worker_restricts_import_os() -> None:
    worker = ReplWorker()
    result = worker.run_code(ReplCommand(code="import os"))
    assert result.error is not None


def test_worker_allows_json_module() -> None:
    worker = ReplWorker()
    result = worker.run_code(ReplCommand(code="json.dumps({'a': 1})"))
    assert result.error is None
    assert '"a"' in result.stdout


def test_worker_allows_math_module() -> None:
    worker = ReplWorker()
    result = worker.run_code(ReplCommand(code="math.sqrt(16)"))
    assert result.error is None
    assert "4.0" in result.stdout


def test_worker_captures_stdout() -> None:
    worker = ReplWorker()
    result = worker.run_code(ReplCommand(code="print('hello world')"))
    assert "hello world" in result.stdout


def test_worker_truncates_long_stdout() -> None:
    worker = ReplWorker(max_stdout_chars=50)
    result = worker.run_code(ReplCommand(code="print('x' * 200)"))
    assert len(result.stdout) < 200
    assert "truncated" in result.stdout


def test_worker_answer_dict_accessible() -> None:
    worker = ReplWorker()
    result = worker.run_code(ReplCommand(code='answer["content"] = "hello"\nanswer["ready"] = True'))
    assert result.answer["content"] == "hello"
    assert result.answer["ready"] is True


def test_worker_timeout_raises_code_timeout() -> None:
    worker = ReplWorker(timeout_seconds=0.5)
    with pytest.raises(CodeTimeout):
        worker.run_code(ReplCommand(code="time.sleep(5)"))


def test_worker_text_helpers_available() -> None:
    worker = ReplWorker()
    # peek
    result = worker.run_code(ReplCommand(code="peek('hello world', 0, 5)"))
    assert result.error is None
    assert "hello" in result.stdout
    # grep
    result = worker.run_code(ReplCommand(code="grep('line1\\nline2\\nline3', 'line2')"))
    assert result.error is None
    assert "line2" in result.stdout
    # chunk_by_size
    result = worker.run_code(ReplCommand(code="len(chunk_by_size('a' * 100, 30))"))
    assert result.error is None
    # chunk_by_headers
    result = worker.run_code(ReplCommand(code="chunk_by_headers('# Title\\ncontent')"))
    assert result.error is None
