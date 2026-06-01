"""AC-728 `autoctx probes` CLI surface (Python parity, slice 4).

Mirrors ``ts/src/control-plane/contract-probes/cli/check.ts`` (TS
PR #991). In-process handler:

- ``run_probes_check(args)`` parses args, loads the suite (file path
  or stdin via ``-``), runs ``run_contract_probe_suite``, and returns
  ``{stdout, stderr, exit_code}``. Tests consume this directly so
  there is no need to spawn a subprocess.
- ``register_probes_command(app, *, console)`` mounts a ``probes``
  sub-Typer with ``check`` as the first subcommand. The outer typer
  command translates the in-process result to stdout / stderr /
  ``typer.Exit``.

Schema-invalid suites surface every Pydantic issue line by line so
operators can fix typos at parse time rather than discover the
missing expectation in a green-but-wrong run.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass

import typer
from pydantic import ValidationError
from rich.console import Console

from autocontext.control_plane.contract_probes import (
    ContractProbeSuiteResult,
    ContractProbeSuiteSchema,
    load_contract_probe_suite,
    run_contract_probe_suite,
)

__all__ = [
    "CHECK_HELP_TEXT",
    "ProbesCheckResult",
    "register_probes_command",
    "run_probes_check",
]


CHECK_HELP_TEXT = """autoctx probes check -- run a contract-probe suite against observed harness state.

Usage:
  autoctx probes check --suite <path> [--json]
  autoctx probes extract --trace <trace> | autoctx probes check --suite -
  autoctx probes check --help

Options:
  --suite <path>   Path to a JSON probe suite (validated against
                   ContractProbeSuiteSchema). Use `-` to read the suite
                   from stdin (cross-platform; the documented pipe form
                   for `autoctx probes extract | autoctx probes check`).
                   Required.
  --json           Emit a structured JSON report instead of human-readable
                   text. The JSON shape mirrors ContractProbeSuiteResult:
                     {
                       "passed": boolean,
                       "results": [
                         {
                           "kind": <probe kind>,
                           "label": <optional caller-supplied attribution>,
                           "passed": boolean,
                           "failures": [ { "kind", "message", ... } ]
                         },
                         ...
                       ]
                     }
  -h, --help       Show this help text.

Exit codes:
  0   every probe in the suite passed.
  1   at least one probe failed, or the suite failed to load / parse.

The JSON suite-file format (with a minimal example and the seven
probe kinds) is documented under the "Contract Probes" section of
the autocontext README. Every input field that the suite declares an
expectation about must carry a corresponding observation; missing
observations fail with kind `missing-observation` rather than
silently passing.
"""


@dataclass(frozen=True)
class ProbesCheckResult:
    stdout: str
    stderr: str
    exit_code: int


def _render_text_report(result: ContractProbeSuiteResult) -> str:
    lines: list[str] = []
    lines.append("probes check: PASS" if result.passed else "probes check: FAIL")
    for probe in result.results:
        label = f" [{probe.label}]" if probe.label else ""
        status = "pass" if probe.passed else "fail"
        lines.append(f"  {probe.kind}{label}: {status}")
        if not probe.passed:
            for failure in probe.failures:
                lines.append(f"    - {failure.kind}: {failure.message}")
    return "\n".join(lines)


def _render_validation_error(err: ValidationError) -> str:
    issues: list[str] = []
    for issue in err.errors():
        loc = ".".join(str(p) for p in issue["loc"]) if issue["loc"] else "<root>"
        issues.append(f"  - {loc}: {issue['msg']}")
    return "\n".join(issues)


def _result_to_json(result: ContractProbeSuiteResult) -> str:
    payload = result.model_dump(mode="json")
    return json.dumps(payload, indent=2)


def run_probes_check(
    args: list[str],
    *,
    stdin_text: str | None = None,
) -> ProbesCheckResult:
    """Pure in-process entry point for `autoctx probes check`.

    Mirrors the TS handler shape: parse args, load + validate the
    suite, run it, render the report. Returns
    ``{stdout, stderr, exit_code}`` so the typer wrapper (or a test)
    can route it to the appropriate sink without coupling to global
    process state.

    ``stdin_text`` is an optional override used by tests; if
    ``None`` and ``--suite -`` is requested, the function reads from
    ``sys.stdin``.
    """
    suite_path: str | None = None
    json_output = False
    help_flag = False

    it = iter(args)
    for arg in it:
        if arg in ("-h", "--help"):
            help_flag = True
        elif arg == "--json":
            json_output = True
        elif arg == "--suite":
            try:
                suite_path = next(it)
            except StopIteration:
                return ProbesCheckResult(
                    stdout="",
                    stderr=f"autoctx probes check: --suite requires a value\n\n{CHECK_HELP_TEXT}",
                    exit_code=1,
                )
        elif arg.startswith("--suite="):
            suite_path = arg.split("=", 1)[1]
        else:
            return ProbesCheckResult(
                stdout="",
                stderr=f"autoctx probes check: unknown argument {arg!r}\n\n{CHECK_HELP_TEXT}",
                exit_code=1,
            )

    if help_flag:
        return ProbesCheckResult(stdout=CHECK_HELP_TEXT, stderr="", exit_code=0)

    if not suite_path:
        return ProbesCheckResult(
            stdout="",
            stderr=f"autoctx probes check: --suite <path> is required\n\n{CHECK_HELP_TEXT}",
            exit_code=1,
        )

    try:
        if suite_path == "-":
            # PR #992 review (P3 equivalent) on the TS side: support
            # stdin so the documented `extract | check` pipe works
            # cross-platform.
            raw = stdin_text if stdin_text is not None else sys.stdin.read()
            parsed_json = json.loads(raw)
            suite = ContractProbeSuiteSchema.model_validate(parsed_json)
        else:
            suite = load_contract_probe_suite(suite_path)
    except FileNotFoundError as err:
        return ProbesCheckResult(
            stdout="",
            stderr=f"autoctx probes check: failed to load suite from {suite_path}: {err}",
            exit_code=1,
        )
    except json.JSONDecodeError as err:
        return ProbesCheckResult(
            stdout="",
            stderr=f"autoctx probes check: failed to load suite from {suite_path}: {err.msg}",
            exit_code=1,
        )
    except ValidationError as err:
        rendered = _render_validation_error(err)
        return ProbesCheckResult(
            stdout="",
            stderr=f"autoctx probes check: suite validation failed\n{rendered}",
            exit_code=1,
        )

    result = run_contract_probe_suite(suite)
    if json_output:
        return ProbesCheckResult(
            stdout=_result_to_json(result),
            stderr="",
            exit_code=0 if result.passed else 1,
        )
    return ProbesCheckResult(
        stdout=_render_text_report(result),
        stderr="",
        exit_code=0 if result.passed else 1,
    )


def register_probes_command(app: typer.Typer, *, console: Console) -> None:
    """Mount the `probes` sub-Typer on ``app`` with `check` as the
    first subcommand."""
    probes_app = typer.Typer(help="AC-728 contract probes.")

    @probes_app.command("check")
    def _check(
        suite: str = typer.Option(
            "",
            "--suite",
            help="Path to a JSON probe suite; use `-` to read from stdin.",
        ),
        json_output: bool = typer.Option(False, "--json", help="Emit a structured JSON report instead of text."),
    ) -> None:
        """Run a contract-probe suite and report per-probe pass/fail."""
        # Reconstruct argv-style args so the in-process handler stays
        # the single source of truth for parsing + dispatch.
        args: list[str] = []
        if suite:
            args.extend(["--suite", suite])
        if json_output:
            args.append("--json")
        result = run_probes_check(args)
        if result.stdout:
            # Plain stdout so `--json` output is parseable; the rich
            # console adds ANSI codes that break JSON consumers.
            print(result.stdout)
        if result.stderr:
            # PR #1008 review (P2): the parent `console` writes to
            # stdout by default, so routing errors through it would
            # contaminate `--json` output on load / parse / validation
            # failures. Write directly to stderr instead.
            sys.stderr.write(result.stderr)
            if not result.stderr.endswith("\n"):
                sys.stderr.write("\n")
            sys.stderr.flush()
        raise typer.Exit(code=result.exit_code)

    app.add_typer(probes_app, name="probes")
