"""AC-728 `autoctx probes` CLI surface (Python parity, slices 4 + 5).

Mirrors ``ts/src/control-plane/contract-probes/cli/check.ts`` (TS
PR #991) for ``check`` and the slice-1 portion of
``ts/src/control-plane/contract-probes/cli/extract.ts`` (TS PR #992)
for ``extract``. In-process handlers:

- ``run_probes_check(args)`` parses args, loads the suite (file path
  or stdin via ``-``), runs ``run_contract_probe_suite``, and returns
  ``{stdout, stderr, exit_code}``.
- ``run_probes_extract(args)`` parses args, loads + validates a
  harness trace via ``HarnessTraceSchema``, joins observations with
  expectations into a ``ContractProbeSuite``, and returns
  ``{stdout, stderr, exit_code}``. The suite is emitted to stdout
  by default; ``--output <path>`` writes it to a file (parent
  directories created).
- ``register_probes_command(app, *, console)`` mounts a ``probes``
  sub-Typer with ``check`` and ``extract`` subcommands. The outer
  typer commands translate the in-process result to stdout / stderr
  / ``typer.Exit``.

Tests consume the in-process handlers directly so there is no need
to spawn a subprocess. Schema-invalid suites or traces surface every
Pydantic issue line by line so operators can fix typos at parse
time rather than discover the missing expectation in a green-but-
wrong run.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import typer
from pydantic import ValidationError
from rich.console import Console

from autocontext.control_plane.contract_probes import (
    ContractProbeSuiteResult,
    ContractProbeSuiteSchema,
    load_contract_probe_suite,
    run_contract_probe_suite,
)
from autocontext.control_plane.contract_probes.extract import (
    HarnessTraceSchema,
    extract_contract_probe_suite,
    serialize_suite,
)

__all__ = [
    "CHECK_HELP_TEXT",
    "EXTRACT_HELP_TEXT",
    "ProbesCheckResult",
    "ProbesExtractResult",
    "register_probes_command",
    "run_probes_check",
    "run_probes_extract",
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


EXTRACT_HELP_TEXT = """autoctx probes extract -- synthesize a contract-probe suite from a harness trace.

Usage:
  autoctx probes extract --trace <path> [--output <path>]
  autoctx probes extract --help

A harness trace bundles observations (what happened in a recorded run)
and expectations (what the operator declared should have happened). The
extractor joins them into a runnable probe suite that `autoctx probes
check` can execute. See the "Contract Probes" section of the autocontext
README for the harness-trace JSON format and a minimal example.

Options:
  --trace <path>   Path to a harness-trace JSON file. Required.
  --output <path>  Write the resulting suite to this path. Parent
                   directories are created. If omitted, the suite is
                   emitted to stdout so it can be piped to
                   `autoctx probes check --suite -`.
  -h, --help       Show this help text.

Exit codes:
  0   the trace parsed and a suite was emitted.
  1   the trace failed to load / parse, or a write to --output failed.

Slice 5 covers the four base probe kinds (terminal, directory,
service, artifact); cleanup / media / distributed extraction lands
in slice 6. Per-section observations and expectations must both be
supplied for any kind the suite asserts on; orphan expectations
fail validation at parse time rather than silently producing a
vacuously-passing suite.
"""


@dataclass(frozen=True)
class ProbesCheckResult:
    stdout: str
    stderr: str
    exit_code: int


@dataclass(frozen=True)
class ProbesExtractResult:
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


def run_probes_extract(args: list[str]) -> ProbesExtractResult:
    """Pure in-process entry point for `autoctx probes extract`.

    Parses argv, loads + validates a harness trace via
    ``HarnessTraceSchema``, joins observations with expectations into
    a ``ContractProbeSuite`` dict, and returns
    ``{stdout, stderr, exit_code}``. Mirrors the TS handler shape so
    tests can consume it directly without spawning a subprocess.
    """
    trace_path: str | None = None
    output_path: str | None = None
    help_flag = False

    it = iter(args)
    for arg in it:
        if arg in ("-h", "--help"):
            help_flag = True
        elif arg == "--trace":
            try:
                trace_path = next(it)
            except StopIteration:
                return ProbesExtractResult(
                    stdout="",
                    stderr=f"autoctx probes extract: --trace requires a value\n\n{EXTRACT_HELP_TEXT}",
                    exit_code=1,
                )
        elif arg.startswith("--trace="):
            trace_path = arg.split("=", 1)[1]
        elif arg == "--output":
            try:
                output_path = next(it)
            except StopIteration:
                return ProbesExtractResult(
                    stdout="",
                    stderr=f"autoctx probes extract: --output requires a value\n\n{EXTRACT_HELP_TEXT}",
                    exit_code=1,
                )
        elif arg.startswith("--output="):
            output_path = arg.split("=", 1)[1]
        else:
            return ProbesExtractResult(
                stdout="",
                stderr=f"autoctx probes extract: unknown argument {arg!r}\n\n{EXTRACT_HELP_TEXT}",
                exit_code=1,
            )

    if help_flag:
        return ProbesExtractResult(stdout=EXTRACT_HELP_TEXT, stderr="", exit_code=0)

    if not trace_path:
        return ProbesExtractResult(
            stdout="",
            stderr=f"autoctx probes extract: --trace <path> is required\n\n{EXTRACT_HELP_TEXT}",
            exit_code=1,
        )

    try:
        raw = Path(trace_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as err:
        # PR #1010 review (P2): the previous `except FileNotFoundError`
        # only handled the missing-file case. Passing a directory
        # raised `IsADirectoryError`, permission errors raised
        # `PermissionError`, and non-UTF8 files raised
        # `UnicodeDecodeError` — all of which escaped as Rich
        # tracebacks instead of returning a `ProbesExtractResult` with
        # a friendly stderr message. Catching `OSError` covers the
        # full filesystem-error family (it is the base of
        # `FileNotFoundError`, `IsADirectoryError`,
        # `PermissionError`, etc.) and `UnicodeDecodeError` covers
        # the encoding case.
        return ProbesExtractResult(
            stdout="",
            stderr=f"autoctx probes extract: failed to read trace from {trace_path}: {err}",
            exit_code=1,
        )

    try:
        parsed_json = json.loads(raw)
    except json.JSONDecodeError as err:
        return ProbesExtractResult(
            stdout="",
            stderr=f"autoctx probes extract: invalid JSON in {trace_path}: {err.msg}",
            exit_code=1,
        )

    try:
        trace = HarnessTraceSchema.model_validate(parsed_json)
    except ValidationError as err:
        rendered = _render_validation_error(err)
        return ProbesExtractResult(
            stdout="",
            stderr=f"autoctx probes extract: trace validation failed\n{rendered}",
            exit_code=1,
        )

    suite_dict = extract_contract_probe_suite(trace)
    serialized = serialize_suite(suite_dict)

    if output_path:
        try:
            out = Path(output_path)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(serialized + "\n", encoding="utf-8")
        except OSError as err:
            return ProbesExtractResult(
                stdout="",
                stderr=f"autoctx probes extract: failed to write suite to {output_path}: {err}",
                exit_code=1,
            )
        return ProbesExtractResult(stdout=f"wrote suite to {output_path}", stderr="", exit_code=0)

    return ProbesExtractResult(stdout=serialized, stderr="", exit_code=0)


def register_probes_command(app: typer.Typer, *, console: Console) -> None:
    """Mount the `probes` sub-Typer on ``app`` with `check` and
    `extract` as the first two subcommands."""
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

    @probes_app.command("extract")
    def _extract(
        trace: str = typer.Option(
            "",
            "--trace",
            help="Path to a harness-trace JSON file.",
        ),
        output: str = typer.Option(
            "",
            "--output",
            help="Write the resulting suite to this path (parent dirs created); omit to emit to stdout.",
        ),
    ) -> None:
        """Synthesize a contract-probe suite from a harness trace."""
        args: list[str] = []
        if trace:
            args.extend(["--trace", trace])
        if output:
            args.extend(["--output", output])
        result = run_probes_extract(args)
        if result.stdout:
            # Plain stdout so the JSON suite is parseable; the rich
            # console adds ANSI codes that break JSON consumers.
            print(result.stdout)
        if result.stderr:
            # PR #1008 review (P2): same stderr-routing fix as `check`
            # so the documented `extract | check` pipe pattern stays
            # parseable even on parse / validation failures.
            sys.stderr.write(result.stderr)
            if not result.stderr.endswith("\n"):
                sys.stderr.write("\n")
            sys.stderr.flush()
        raise typer.Exit(code=result.exit_code)

    app.add_typer(probes_app, name="probes")
