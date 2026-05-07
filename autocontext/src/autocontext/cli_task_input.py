"""TaskInput — operator-supplied task value object (AC-737).

CLI commands that take a "task" can accept it as either an inline string
(``--description``) or as a file path (``--task-file``). Both surfaces
resolve to a single :class:`TaskInput` so downstream code never has to
branch on the input channel.

Domain rules:

- Exactly one source must be supplied (XOR: text or file).
- Empty / whitespace-only inputs are rejected — silent fall-through to
  defaults is what AC-737 explicitly fixes.
- Files must exist, be readable, and have non-empty content.

Empty strings are treated the same as ``None`` because Typer's default
``Option("")`` makes it natural to write ``text=description, file=task_file``
and let the value object decide what was actually supplied.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class TaskInputError(ValueError):
    """Raised when operator-supplied task input cannot be resolved.

    Inherits from ``ValueError`` so callers that catch broad validation
    errors still cover this case; messages are operator-facing and name
    the relevant CLI flag(s) so the fix is obvious.
    """


@dataclass(frozen=True, slots=True)
class TaskInput:
    """The single resolved task text supplied by the operator.

    Construct via :meth:`from_text`, :meth:`from_file`, or
    :meth:`from_args`; never via the raw constructor (which exists only
    so downstream code can hold immutable instances).
    """

    text: str

    @classmethod
    def from_text(cls, text: str) -> TaskInput:
        """Build from an inline string. Trailing whitespace is stripped."""
        if text is None or not text.strip():
            raise TaskInputError('task description must not be empty (use --description "<text>")')
        return cls(text=text.strip())

    @classmethod
    def from_file(cls, path: str | Path) -> TaskInput:
        """Build by reading the contents of ``path``."""
        p = Path(path)
        if not p.exists():
            raise TaskInputError(f"--task-file path not found: {p}")
        if not p.is_file():
            raise TaskInputError(f"--task-file is not a regular file: {p}")
        try:
            content = p.read_text(encoding="utf-8")
        except OSError as exc:
            raise TaskInputError(f"--task-file could not be read: {p} ({exc})") from exc
        if not content.strip():
            raise TaskInputError(f"--task-file is empty: {p}")
        return cls(text=content.strip())

    @classmethod
    def from_args(
        cls,
        *,
        text: str | None,
        file: str | Path | None,
    ) -> TaskInput:
        """Resolve text-or-file pair from CLI args.

        Treats empty strings the same as ``None`` so Typer's default
        ``Option("")`` stays ergonomic. Refuses both-supplied (ambiguous)
        and neither-supplied (under-specified) — silent fall-through is
        what AC-737 forbids.
        """
        text_supplied = bool(text and text.strip())
        file_supplied = file is not None and (isinstance(file, Path) or (isinstance(file, str) and file.strip()))
        if not text_supplied and not file_supplied:
            raise TaskInputError('no task supplied: pass --description "<text>" or --task-file <path>')
        if text_supplied and file_supplied:
            raise TaskInputError("--description and --task-file are mutually exclusive; supply only one")
        if text_supplied:
            assert text is not None  # for type checker
            return cls.from_text(text)
        assert file is not None  # for type checker
        return cls.from_file(file)
