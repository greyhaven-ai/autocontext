"""Tests for TaskInput — the operator-supplied task value object (AC-737).

The CLI accepts either ``--description "<text>"`` or ``--task-file <path>``
to specify the task. Both surfaces resolve to a single ``TaskInput`` value
object so downstream code can ignore the input channel.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from autocontext.cli_task_input import TaskInput, TaskInputError

# -- Factories --


class TestFromText:
    def test_from_text_carries_payload(self):
        ti = TaskInput.from_text("hello world")
        assert ti.text == "hello world"

    def test_from_text_strips_trailing_whitespace(self):
        # Operator copy-pasted text often has trailing newlines; strip them.
        ti = TaskInput.from_text("hello\n\n  ")
        assert ti.text == "hello"

    def test_from_text_rejects_empty_string(self):
        with pytest.raises(TaskInputError):
            TaskInput.from_text("")

    def test_from_text_rejects_whitespace_only(self):
        with pytest.raises(TaskInputError):
            TaskInput.from_text("   \n\t")


class TestFromFile:
    def test_from_file_reads_contents(self, tmp_path: Path):
        f = tmp_path / "task.txt"
        f.write_text("file contents marker", encoding="utf-8")
        ti = TaskInput.from_file(f)
        assert "file contents marker" in ti.text

    def test_from_file_rejects_missing_path(self, tmp_path: Path):
        missing = tmp_path / "nope.txt"
        with pytest.raises(TaskInputError) as excinfo:
            TaskInput.from_file(missing)
        assert "not found" in str(excinfo.value).lower()

    def test_from_file_rejects_directory(self, tmp_path: Path):
        with pytest.raises(TaskInputError):
            TaskInput.from_file(tmp_path)

    def test_from_file_rejects_empty_file(self, tmp_path: Path):
        f = tmp_path / "empty.txt"
        f.write_text("", encoding="utf-8")
        with pytest.raises(TaskInputError):
            TaskInput.from_file(f)

    def test_from_file_accepts_string_path(self, tmp_path: Path):
        # Convenient when wired from typer.Option(str)-typed values.
        f = tmp_path / "task.txt"
        f.write_text("hello", encoding="utf-8")
        ti = TaskInput.from_file(str(f))
        assert ti.text == "hello"


# -- Combined factory used by the CLI --


class TestFromArgs:
    def test_text_only(self):
        ti = TaskInput.from_args(text="from-string", file=None)
        assert ti.text == "from-string"

    def test_file_only(self, tmp_path: Path):
        f = tmp_path / "t.txt"
        f.write_text("from-file", encoding="utf-8")
        ti = TaskInput.from_args(text=None, file=f)
        assert ti.text == "from-file"

    def test_neither_is_an_error(self):
        with pytest.raises(TaskInputError) as excinfo:
            TaskInput.from_args(text=None, file=None)
        # The error message guides the operator toward the right flag.
        assert "--description" in str(excinfo.value) or "--task-file" in str(excinfo.value)

    def test_both_is_an_error(self, tmp_path: Path):
        # Both is ambiguous — refuse rather than silently picking one.
        f = tmp_path / "t.txt"
        f.write_text("file", encoding="utf-8")
        with pytest.raises(TaskInputError) as excinfo:
            TaskInput.from_args(text="text", file=f)
        assert "both" in str(excinfo.value).lower() or "exclusive" in str(excinfo.value).lower()

    def test_empty_string_treated_as_missing(self, tmp_path: Path):
        # The CLI default for --description is "" (typer-friendly). Treat
        # empty strings the same as None so users don't get confusing
        # "neither" errors when only --task-file is given.
        f = tmp_path / "t.txt"
        f.write_text("from-file", encoding="utf-8")
        ti = TaskInput.from_args(text="", file=f)
        assert ti.text == "from-file"


# -- Immutability (value-object discipline) --


class TestImmutability:
    def test_text_field_is_read_only(self, tmp_path: Path):
        ti = TaskInput.from_text("x")
        with pytest.raises((AttributeError, TypeError)):
            ti.text = "y"  # type: ignore[misc]
