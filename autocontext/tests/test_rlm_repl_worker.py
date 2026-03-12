from __future__ import annotations

import pytest

from autocontext.rlm.repl_worker import (
    CodeTimeout,
    ReplWorker,
    _chunk_by_headers,
    _chunk_by_size,
    _grep,
    _peek,
)
from autocontext.rlm.types import ReplCommand


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


class TestPeek:
    def test_returns_substring(self) -> None:
        assert _peek("abcdefghij", start=2, length=5) == "cdefg"

    def test_default_offset(self) -> None:
        text = "x" * 3000
        result = _peek(text)
        assert len(result) == 2000
        assert result == "x" * 2000

    def test_beyond_bounds(self) -> None:
        assert _peek("short", start=3, length=100) == "rt"


class TestGrep:
    def test_finds_matching_lines(self) -> None:
        text = "alpha\nbeta\ngamma\nbeta2"
        assert _grep(text, "beta") == ["beta", "beta2"]

    def test_case_insensitive(self) -> None:
        text = "Hello\nhello\nHELLO"
        assert len(_grep(text, "hello")) == 3

    def test_with_context_lines(self) -> None:
        text = "line1\nline2\nTARGET\nline4\nline5"
        hits = _grep(text, "TARGET", context=1)
        assert len(hits) == 1
        assert "line2" in hits[0]
        assert "TARGET" in hits[0]
        assert "line4" in hits[0]

    def test_no_matches(self) -> None:
        assert _grep("abc\ndef", "zzz") == []


class TestChunkBySize:
    def test_basic(self) -> None:
        text = "a" * 10
        chunks = _chunk_by_size(text, size=4)
        assert chunks == ["aaaa", "aaaa", "aa"]

    def test_with_overlap(self) -> None:
        text = "abcdefghij"
        chunks = _chunk_by_size(text, size=5, overlap=2)
        # step=3: [0:5]="abcde", [3:8]="defgh", [6:11]="ghij"
        assert chunks == ["abcde", "defgh", "ghij"]

    def test_empty(self) -> None:
        assert _chunk_by_size("") == []


class TestChunkByHeaders:
    def test_markdown(self) -> None:
        text = "# Title\nContent here.\n## Sub\nMore content."
        parts = _chunk_by_headers(text)
        assert len(parts) == 2
        assert parts[0]["header"] == "# Title"
        assert "Content here." in parts[0]["content"]
        assert parts[1]["header"] == "## Sub"
        assert "More content." in parts[1]["content"]

    def test_no_headers(self) -> None:
        text = "Just plain text\nwith no headers."
        parts = _chunk_by_headers(text)
        assert len(parts) == 1
        assert parts[0]["header"] == ""
        assert "Just plain text" in parts[0]["content"]


class TestHelpersInNamespace:
    def test_helpers_available_in_namespace(self) -> None:
        worker = ReplWorker()
        result = worker.run_code(ReplCommand('print(peek("hello world", 0, 5))'))
        assert "hello" in result.stdout
        assert result.error is None

        result2 = worker.run_code(ReplCommand('print(grep("a\\nb\\nc", "b"))'))
        assert "b" in result2.stdout

        result3 = worker.run_code(ReplCommand('print(len(chunk_by_size("x" * 10, 4)))'))
        assert "3" in result3.stdout

        result4 = worker.run_code(ReplCommand('print(len(chunk_by_headers("# H1\\ntext\\n## H2\\nmore")))'))
        assert "2" in result4.stdout
