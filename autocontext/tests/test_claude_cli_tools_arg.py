"""Tests for AC-736: empty ``AUTOCONTEXT_CLAUDE_TOOLS=""`` rendering.

The bug: when the operator sets ``AUTOCONTEXT_CLAUDE_TOOLS=""`` to mean
"run claude with NO tools", the runtime emits ``["--tools", ""]`` which
renders in ``ps`` listings as ``--tools  --permission-mode`` (a double
space where the empty arg lives). It works but looks broken.

The fix: use the ``--tools=<value>`` form so empty values render
unambiguously as ``--tools=`` rather than as a missing argument.
"""

from __future__ import annotations

from autocontext.runtimes.claude_cli import ClaudeCLIConfig, ClaudeCLIRuntime


def _build_argv(*, tools: str | None) -> list[str]:
    """Build a runtime and return the argv list it would invoke."""
    rt = ClaudeCLIRuntime(ClaudeCLIConfig(tools=tools))
    rt._claude_path = "/fake/bin/claude"  # noqa: SLF001
    return rt._build_args()  # noqa: SLF001


class TestToolsRendering:
    def test_none_omits_tools_flag_entirely(self):
        # tools=None means "use claude's default tool set" — no flag emitted.
        argv = _build_argv(tools=None)
        assert "--tools" not in argv
        assert not any(a.startswith("--tools=") for a in argv)

    def test_empty_string_uses_equals_form(self):
        # AC-736: empty tools should NOT emit two separate args
        # (--tools and "") because that renders as a confusing double space
        # in ps listings.
        argv = _build_argv(tools="")
        # The empty-arg pattern must NOT appear:
        # Bare ``--tools`` (with separate value) must not appear.
        assert "--tools" not in argv, f"--tools emitted as separate arg; argv={argv}"
        # The equals-form MUST be present:
        assert "--tools=" in argv, f"expected '--tools=' in argv; got {argv}"

    def test_non_empty_tools_uses_equals_form(self):
        # For consistency the equals form is used uniformly.
        argv = _build_argv(tools="Read,Bash")
        # Must not emit --tools as bare flag with separate value:
        # Bare ``--tools`` (with separate value) must not appear.
        assert "--tools" not in argv, f"--tools emitted as separate arg; argv={argv}"
        assert "--tools=Read,Bash" in argv


class TestNoRegressionOfOtherFlags:
    def test_other_flags_unchanged(self):
        # We changed only the --tools rendering; other flags must still
        # appear in their familiar shape.
        argv = _build_argv(tools=None)
        # --model is always emitted with separate value (this we keep).
        assert "--model" in argv
        # --permission-mode follows
        assert "--permission-mode" in argv
