"""Tests for the external-command output verifier (AC-733)."""

from __future__ import annotations

import os
import sys
import textwrap

from autocontext.execution.output_verifier import (
    FILE_PLACEHOLDER,
    OutputVerifier,
    VerifyResult,
    make_verifier,
)

# -- Disabled / no-op verifier --


class TestDisabled:
    def test_none_command_disables_verifier(self):
        v = OutputVerifier(command=None)
        assert v.enabled is False

    def test_empty_string_command_disables_verifier(self):
        v = OutputVerifier(command="")
        assert v.enabled is False

    def test_disabled_run_returns_ok_skipped(self):
        v = OutputVerifier(command=None)
        res = v.run("anything")
        assert res.ok is True
        assert res.skipped is True
        assert "disabled" in (res.error or "").lower()

    def test_make_verifier_returns_none_for_falsy(self):
        assert make_verifier(None) is None
        assert make_verifier("") is None
        assert make_verifier([]) is None


# -- Stdin mode --


class TestStdinMode:
    def test_passing_command_returns_ok(self):
        v = OutputVerifier(command=[sys.executable, "-c", "import sys; sys.stdin.read()"])
        res = v.run("hello")
        assert res.ok is True
        assert res.exit_code == 0
        assert res.skipped is False

    def test_failing_command_returns_not_ok_with_stderr(self):
        script = "import sys; sys.stdin.read(); print('bad', file=sys.stderr); sys.exit(2)"
        v = OutputVerifier(command=[sys.executable, "-c", script])
        res = v.run("anything")
        assert res.ok is False
        assert res.exit_code == 2
        assert "bad" in res.stderr

    def test_stdin_content_is_passed_to_command(self):
        # Reflect stdin to stdout, then exit 0.
        script = "import sys; sys.stdout.write(sys.stdin.read())"
        v = OutputVerifier(command=[sys.executable, "-c", script])
        res = v.run("PAYLOAD-MARKER")
        assert res.ok is True
        assert "PAYLOAD-MARKER" in res.stdout

    def test_string_command_is_split(self):
        # A shell-style string command should be split via shlex.
        v = OutputVerifier(command=f"{sys.executable} -c 'import sys; sys.stdin.read()'")
        res = v.run("ok")
        assert res.ok is True


# -- File mode --


class TestFileMode:
    def test_file_placeholder_substitutes_temp_path(self):
        # Verify the temp file's contents match the output.
        script = textwrap.dedent(
            """
            import sys, pathlib
            data = pathlib.Path(sys.argv[1]).read_text()
            sys.stdout.write(data)
            """
        ).strip()
        v = OutputVerifier(
            command=[sys.executable, "-c", script, FILE_PLACEHOLDER],
            file_suffix=".txt",
        )
        res = v.run("FILE-CONTENT-MARKER")
        assert res.ok is True
        assert "FILE-CONTENT-MARKER" in res.stdout

    def test_file_suffix_is_applied(self):
        script = textwrap.dedent(
            """
            import sys
            sys.stdout.write(sys.argv[1])
            """
        ).strip()
        v = OutputVerifier(
            command=[sys.executable, "-c", script, FILE_PLACEHOLDER],
            file_suffix=".lean",
        )
        res = v.run("x")
        assert res.ok is True
        assert res.stdout.endswith(".lean")

    def test_file_mode_failure_propagates_exit_code(self):
        script = textwrap.dedent(
            """
            import sys
            print('compile error: line 1', file=sys.stderr)
            sys.exit(7)
            """
        ).strip()
        v = OutputVerifier(
            command=[sys.executable, "-c", script, FILE_PLACEHOLDER],
        )
        res = v.run("anything")
        assert res.ok is False
        assert res.exit_code == 7
        assert "compile error" in res.stderr


# -- Error handling --


class TestErrorHandling:
    def test_missing_executable(self):
        v = OutputVerifier(command=["this-binary-does-not-exist-zzz"])
        res = v.run("x")
        assert res.ok is False
        assert res.error is not None
        assert "not found" in res.error.lower()

    def test_timeout(self):
        # Sleep longer than the timeout; verifier should report timed_out.
        v = OutputVerifier(
            command=[sys.executable, "-c", "import time; time.sleep(5)"],
            timeout_s=0.2,
        )
        res = v.run("x")
        assert res.ok is False
        assert res.timed_out is True
        assert "timed out" in res.message.lower()


# -- Message formatting (used in revision feedback) --


class TestMessage:
    def test_passing_message(self):
        res = VerifyResult(ok=True, exit_code=0, stdout="all good", stderr="")
        assert res.message == "verifier passed"

    def test_failing_message_includes_stderr(self):
        res = VerifyResult(
            ok=False,
            exit_code=3,
            stdout="",
            stderr="error at line 5: bad name",
        )
        m = res.message
        assert "exit 3" in m
        assert "bad name" in m

    def test_failing_message_falls_back_to_stdout(self):
        res = VerifyResult(
            ok=False,
            exit_code=2,
            stdout="oops",
            stderr="",
        )
        m = res.message
        assert "oops" in m

    def test_failing_message_with_no_output(self):
        res = VerifyResult(ok=False, exit_code=4, stdout="", stderr="")
        # Still informative even when both streams are empty.
        assert "exit code 4" in res.message

    def test_skipped_message(self):
        res = VerifyResult(
            ok=True,
            exit_code=0,
            stdout="",
            stderr="",
            skipped=True,
            error="verifier disabled (no command configured)",
        )
        assert "disabled" in res.message.lower()

    def test_timeout_message(self):
        res = VerifyResult(
            ok=False,
            exit_code=-1,
            stdout="",
            stderr="",
            timed_out=True,
            error="t/o",
        )
        assert "timed out" in res.message.lower()


# -- Working directory & env --


class TestEnvAndCwd:
    def test_cwd_is_respected(self, tmp_path):
        # Write the cwd to stdout; verify it's tmp_path.
        script = "import os; print(os.getcwd())"
        v = OutputVerifier(
            command=[sys.executable, "-c", script],
            cwd=tmp_path,
        )
        res = v.run("x")
        assert res.ok is True
        assert os.path.realpath(tmp_path) in os.path.realpath(res.stdout.strip())
