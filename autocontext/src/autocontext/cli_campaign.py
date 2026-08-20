"""CLI entrypoint for durable local/remote campaign plans."""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from autocontext.audit import CampaignAuditStore, make_operator_disposition
from autocontext.config import load_settings
from autocontext.execution.campaign_runtime import load_campaign_plan, run_campaign_plan

if TYPE_CHECKING:
    from rich.console import Console


class _AuditDisposition(StrEnum):
    ACCEPTED = "accepted"
    DISMISSED = "dismissed"
    MITIGATED = "mitigated"
    DEFERRED = "deferred"


def register_campaign_command(app: typer.Typer, *, console: Console) -> None:
    campaign_app = typer.Typer(help="Run or resume a durable, resource-bounded campaign plan.")
    audit_app = typer.Typer(help="Inspect and resolve durable independent campaign audits.")

    @campaign_app.command("run")
    def run_command(
        plan: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
        state_root: Annotated[
            Path | None,
            typer.Option("--state-root", help="Override durable scheduler state root."),
        ] = None,
        json_output: Annotated[bool, typer.Option("--json", help="Emit the report as JSON.")] = False,
    ) -> None:
        try:
            settings = load_settings()
            outcome = run_campaign_plan(load_campaign_plan(plan), settings, state_root=state_root)
        except Exception as exc:
            if json_output:
                typer.echo(json.dumps({"error": str(exc)}), err=True)
            else:
                console.print(f"[red]Campaign failed: {exc}[/red]")
            raise typer.Exit(code=1) from exc
        if json_output:
            typer.echo(json.dumps(outcome.report.to_dict(), sort_keys=True))
            return
        console.print(outcome.report.to_markdown())
        console.print(f"Report: {outcome.report_path}")
        console.print(f"Scheduler report: {outcome.scheduler_report_path}")

    @audit_app.command("list")
    def list_audits(
        campaign_id: Annotated[str, typer.Argument(help="Durable campaign identity.")],
        store_root: Annotated[
            Path | None,
            typer.Option("--store-root", help="Override the campaign-audit store root."),
        ] = None,
        json_output: Annotated[bool, typer.Option("--json", help="Emit records as JSON.")] = False,
    ) -> None:
        settings = load_settings()
        store = CampaignAuditStore(store_root or settings.runs_root / "campaign-audits")
        records = store.records(campaign_id)
        if json_output:
            typer.echo(json.dumps([record.to_dict() for record in records], sort_keys=True))
            return
        if not records:
            console.print("No durable campaign audits found.")
            return
        for record in records:
            audit = record.audit
            disposition = record.dispositions[-1].disposition if record.dispositions else "unresolved"
            console.print(
                f"{audit.audit_id}  {audit.checkpoint}  {audit.status}  "
                f"{audit.policy_outcome}  disposition={disposition}  evidence={audit.evidence_fingerprint}"
            )

    @audit_app.command("resolve")
    def resolve_audit(
        campaign_id: Annotated[str, typer.Argument(help="Durable campaign identity.")],
        evidence_fingerprint: Annotated[str, typer.Argument(help="Evidence fingerprint printed by audit list.")],
        operator: Annotated[str, typer.Option("--operator", help="Operator identity recorded in the disposition.")],
        disposition: Annotated[
            _AuditDisposition,
            typer.Option("--disposition", help="One of: accepted, dismissed, mitigated, deferred."),
        ],
        rationale: Annotated[str, typer.Option("--rationale", help="Required operator rationale.")],
        audit_id: Annotated[
            str | None,
            typer.Option("--audit-id", help="Resolve a specific audit when multiple configurations exist."),
        ] = None,
        store_root: Annotated[
            Path | None,
            typer.Option("--store-root", help="Override the campaign-audit store root."),
        ] = None,
    ) -> None:
        if not operator.strip() or not rationale.strip():
            raise typer.BadParameter("operator and rationale must be non-empty")
        settings = load_settings()
        store = CampaignAuditStore(store_root or settings.runs_root / "campaign-audits")
        record = store.read_by_fingerprint(campaign_id, evidence_fingerprint)
        if audit_id is not None:
            record = next(
                (
                    candidate
                    for candidate in store.records(campaign_id)
                    if candidate.audit.evidence_fingerprint == evidence_fingerprint and candidate.audit.audit_id == audit_id
                ),
                None,
            )
        if record is None:
            raise typer.BadParameter("matching campaign audit was not found")
        operator_disposition = make_operator_disposition(
            record.audit,
            operator=operator.strip(),
            disposition=disposition.value,
            rationale=rationale.strip(),
        )
        updated = store.add_disposition(campaign_id, evidence_fingerprint, operator_disposition)
        typer.echo(
            json.dumps(
                {
                    "audit_id": updated.audit.audit_id,
                    "campaign_id": campaign_id,
                    "disposition": operator_disposition.to_dict(),
                    "evidence_fingerprint": evidence_fingerprint,
                },
                sort_keys=True,
            )
        )

    campaign_app.add_typer(audit_app, name="audit")
    app.add_typer(campaign_app, name="campaign")


__all__ = ["register_campaign_command"]
