"""External-command verifier for improvement-loop outputs.

AC-733: the LLM judge cannot run a real verifier (compiler, type-checker, etc.).
This module shells out to a user-supplied command after each judge round and
forces the round's effective score to 0 on non-zero exit, with the verifier's
stderr/stdout fed back into the revision feedback so the next round can fix
the actual error rather than the judge's prose impression of the output.

Usage shape::

    verifier = OutputVerifier(command=["lake", "env", "lean", "{file}"])
    res = verifier.run(output_text)
    if not res.ok:
        # res.message contains stderr/stdout, suitable for revision feedback

The ``{file}`` placeholder in the command template is replaced with a path to
a temp file containing ``output_text``. If the command contains no
``{file}`` placeholder, the output is piped to stdin instead.
"""

from __future__ import annotations

import logging
import os
import shlex
import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 300.0
FILE_PLACEHOLDER = "{file}"


@dataclass(slots=True)
class VerifyResult:
    """Outcome of running an external verifier on an output."""

    ok: bool
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False
    skipped: bool = False
    error: str | None = None

    @property
    def message(self) -> str:
        """Human-readable summary suitable for inclusion in revision feedback."""
        if self.skipped:
            return self.error or "verifier skipped"
        if self.timed_out:
            return f"verifier timed out after {DEFAULT_TIMEOUT_S}s"
        if self.error is not None:
            return f"verifier could not run: {self.error}"
        if self.ok:
            return "verifier passed"
        out = (self.stderr or "").strip() or (self.stdout or "").strip()
        if not out:
            out = f"exit code {self.exit_code}"
        return f"verifier failed (exit {self.exit_code}):\n{out}"


class OutputVerifier:
    """Runs an external command on improvement-loop output and reports pass/fail.

    Two execution modes:
    - **File mode** (when ``{file}`` appears in the command template): the output
      is written to a temp file with optional ``file_suffix`` (e.g. ``.lean``)
      and the placeholder is substituted with the file path.
    - **Stdin mode** (no placeholder): the output is piped to the command's stdin.

    Non-zero exit, timeout, or executable-not-found all produce ``ok=False``;
    no exception escapes ``run()``. The return value's ``message`` property is
    designed to be suitable for direct inclusion in the next round's revision
    prompt so the agent can see what the real verifier complained about.
    """

    def __init__(
        self,
        command: str | Sequence[str] | None,
        *,
        file_suffix: str = ".txt",
        timeout_s: float = DEFAULT_TIMEOUT_S,
        cwd: str | os.PathLike[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        if command is None:
            self._argv: list[str] = []
            self._enabled = False
        elif isinstance(command, str):
            self._argv = shlex.split(command)
            self._enabled = bool(self._argv)
        else:
            self._argv = list(command)
            self._enabled = bool(self._argv)
        self._file_suffix = file_suffix
        self._timeout_s = timeout_s
        self._cwd = str(cwd) if cwd is not None else None
        self._env = env

    @property
    def enabled(self) -> bool:
        """Whether this verifier will actually invoke a command."""
        return self._enabled

    def run(self, output_text: str) -> VerifyResult:
        """Verify ``output_text``. Always returns a result, never raises."""
        if not self._enabled:
            return VerifyResult(
                ok=True,
                exit_code=0,
                stdout="",
                stderr="",
                skipped=True,
                error="verifier disabled (no command configured)",
            )

        executable = self._argv[0]
        if shutil.which(executable) is None and not Path(executable).exists():
            return VerifyResult(
                ok=False,
                exit_code=-1,
                stdout="",
                stderr="",
                error=f"verifier executable not found: {executable!r}",
            )

        uses_file = any(FILE_PLACEHOLDER in arg for arg in self._argv)
        if uses_file:
            return self._run_with_file(output_text)
        return self._run_with_stdin(output_text)

    def _run_with_file(self, output_text: str) -> VerifyResult:
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=self._file_suffix,
            delete=False,
            encoding="utf-8",
        ) as fh:
            fh.write(output_text)
            file_path = fh.name
        try:
            argv = [arg.replace(FILE_PLACEHOLDER, file_path) for arg in self._argv]
            return self._invoke(argv, stdin_text=None)
        finally:
            try:
                os.unlink(file_path)
            except OSError as exc:
                logger.debug("could not unlink temp file %s: %s", file_path, exc)

    def _run_with_stdin(self, output_text: str) -> VerifyResult:
        return self._invoke(list(self._argv), stdin_text=output_text)

    def _invoke(self, argv: list[str], *, stdin_text: str | None) -> VerifyResult:
        try:
            completed = subprocess.run(
                argv,
                input=stdin_text,
                capture_output=True,
                text=True,
                timeout=self._timeout_s,
                cwd=self._cwd,
                env=self._env,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return VerifyResult(
                ok=False,
                exit_code=-1,
                stdout="",
                stderr="",
                timed_out=True,
                error=f"verifier timed out after {self._timeout_s}s",
            )
        except (FileNotFoundError, PermissionError) as exc:
            return VerifyResult(
                ok=False,
                exit_code=-1,
                stdout="",
                stderr="",
                error=f"verifier could not be executed: {exc}",
            )
        except OSError as exc:
            return VerifyResult(
                ok=False,
                exit_code=-1,
                stdout="",
                stderr="",
                error=f"verifier failed to start: {exc}",
            )

        return VerifyResult(
            ok=completed.returncode == 0,
            exit_code=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )


def make_verifier(
    command: str | Sequence[str] | None,
    *,
    file_suffix: str = ".txt",
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> OutputVerifier | None:
    """Convenience constructor returning ``None`` for falsy commands.

    Lets callers write::

        verifier = make_verifier(settings.verify_command)
        if verifier and verifier.enabled:
            ...

    instead of branching twice on ``None`` and ``""``.
    """
    if not command:
        return None
    verifier = OutputVerifier(
        command=command,
        file_suffix=file_suffix,
        timeout_s=timeout_s,
    )
    return verifier if verifier.enabled else None


def make_checkpointer(
    command: str | Sequence[str] | None,
    *,
    file_suffix: str = ".txt",
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> OutputVerifier | None:
    """Convenience constructor for the per-round checkpoint command (AC-727).

    Structurally identical to :func:`make_verifier` -- both build an
    `OutputVerifier` runner around a user-supplied command -- but the
    *semantic* role is different: a checkpoint is a non-vetoing side
    effect that preserves partial progress (e.g.
    ``git commit -am 'round N checkpoint'`` or
    ``cp {file} /tmp/round-N.lean``). The improvement loop runs it after
    each round and logs failures rather than zeroing the round's score.

    Returns ``None`` for falsy commands so callers can write::

        checkpointer = make_checkpointer(settings.checkpoint_command)
        if checkpointer and checkpointer.enabled:
            ...
    """
    if not command:
        return None
    checkpointer = OutputVerifier(
        command=command,
        file_suffix=file_suffix,
        timeout_s=timeout_s,
    )
    return checkpointer if checkpointer.enabled else None
