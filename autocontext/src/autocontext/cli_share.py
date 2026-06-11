"""`autoctx share` command group — local bundle preparation (tier-0/tier-1).

`share prepare` runs the deterministic safeguards over a run's shareable files
entirely on the local machine, writes a masked prepare-report.json, and (unless
``--dry-run``) writes a redacted bundle. It refuses to produce a bundle when any
reject-severity finding remains. No upload, signed URL, or network call happens
here — this is the client-side pass from the trace-exchange spec section 4.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from autocontext.sharing.prepare import PrepareResult, prepare_share

if TYPE_CHECKING:
    from rich.console import Console

_PREPARE_REPORT_NAME = "prepare-report.json"
_REFUSED_EXIT_CODE = 2


def register_share_command(app: typer.Typer, *, console: Console) -> None:
    share_app = typer.Typer(help="Prepare run artifacts for the trace exchange (local only)")

    @share_app.command("prepare")
    def prepare(
        run_id: Annotated[str, typer.Argument(help="Run id under the runs root")],
        scenario: Annotated[
            str | None,
            typer.Option("--scenario", help="Scenario name for knowledge files (knowledge/<scenario>/)"),
        ] = None,
        runs_root: Annotated[Path, typer.Option("--runs-root", help="Runs root directory")] = Path("runs"),
        knowledge_root: Annotated[Path, typer.Option("--knowledge-root", help="Knowledge root directory")] = Path("knowledge"),
        output: Annotated[
            Path | None,
            typer.Option(
                "--output",
                help="Directory for the prepared bundle + report (default: cwd; bundle written only when not --dry-run)",
            ),
        ] = None,
        license_spdx: Annotated[str, typer.Option("--license", help="SPDX license id asserted for sharing")] = "CC-BY-4.0",
        dry_run: Annotated[bool, typer.Option("--dry-run", help="Scan and report only; never write a bundle")] = False,
        json_output: Annotated[bool, typer.Option("--json", help="Print the prepare report as JSON")] = False,
    ) -> None:
        """Scan a run's shareable files locally and prepare a redacted bundle."""
        # A non-dry-run with no --output still writes the bundle, to cwd; dry-run
        # never writes a bundle regardless.
        bundle_output = output if output is not None else (None if dry_run else Path.cwd())
        result = prepare_share(
            runs_root=runs_root,
            knowledge_root=knowledge_root,
            run_id=run_id,
            scenario_name=scenario,
            output_dir=bundle_output,
            dry_run=dry_run,
            license_spdx=license_spdx,
        )

        report = result.to_report()
        report_dir = output if output is not None else Path.cwd()
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / _PREPARE_REPORT_NAME
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

        if json_output:
            console.print_json(json.dumps(report))
        else:
            _render(console, result, report_path)

        if result.refused:
            raise typer.Exit(code=_REFUSED_EXIT_CODE)

    app.add_typer(share_app, name="share")


def _render(console: Console, result: PrepareResult, report_path: Path) -> None:
    if not result.files:
        console.print(f"[yellow]no shareable files found for run [bold]{result.run_id}[/bold][/yellow]")
        return

    console.print(
        f"[bold]share prepare[/bold] · run [cyan]{result.run_id}[/cyan]"
        + (f" · scenario [cyan]{result.scenario}[/cyan]" if result.scenario else "")
    )

    for report in result.files:
        verdict = report.verdict
        colour = "red" if verdict == "rejected" else "yellow" if verdict == "needs_user_redaction" else "green"
        suffix = f" — intake: {report.intake_rejected}" if report.intake_rejected else ""
        console.print(
            f"  [{colour}]{verdict}[/{colour}]  {report.path} "
            f"([dim]{report.kind}[/dim], {report.finding_count} findings, "
            f"{report.redaction_count} redactions){suffix}"
        )

    overall = result.overall_verdict
    overall_colour = "red" if overall == "rejected" else "yellow" if overall == "needs_user_redaction" else "green"
    console.print(f"\noverall: [{overall_colour}]{overall}[/{overall_colour}]")
    console.print(f"report:  {report_path}")

    if result.refused:
        console.print("[red]bundle refused[/red]: reject-severity findings must be removed before sharing.")
    elif result.dry_run:
        console.print("[dim]dry run — no bundle written.[/dim]")
    elif result.bundle_dir is not None:
        console.print(f"bundle:  {result.bundle_dir}")
