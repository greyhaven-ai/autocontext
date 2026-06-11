"""Local `share prepare` orchestration (tier-0 intake + tier-1 scan).

Collects allowlisted run/knowledge files, runs the deterministic safeguards
locally, and produces a prepare-report. Fail-closed: if any file yields a
reject-severity finding the bundle is refused (no ``--force``). Under
``--dry-run`` no bundle is ever written. Nothing is uploaded; this is the
client-side tier-0 pass described in spec section 4.
"""

from __future__ import annotations

import datetime
import json
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from autocontext.sharing.collector import collect_session_artifacts
from autocontext.sharing.manifest import (
    build_manifest,
    infer_kind,
    intake_rejection,
    normalize_bundle_path,
    sha256_text,
)
from autocontext.sharing.safeguards import (
    RULESET_VERSION,
    ReviewState,
    ScanReport,
    scan_content,
)

_VERDICT_RANK: dict[ReviewState, int] = {
    "needs_human_review": 1,
    "needs_user_redaction": 2,
    "rejected": 3,
}


@dataclass(slots=True)
class PrepareFileReport:
    path: str
    kind: str
    verdict: ReviewState
    intake_rejected: str | None
    scanner_results: dict[str, str]
    finding_count: int
    redaction_count: int
    findings: list[dict[str, Any]]
    scan: ScanReport | None = None


@dataclass(slots=True)
class PrepareResult:
    run_id: str
    scenario: str
    overall_verdict: ReviewState
    refused: bool
    dry_run: bool
    files: list[PrepareFileReport] = field(default_factory=list)
    bundle_dir: Path | None = None

    def to_report(self) -> dict[str, Any]:
        """Masked, upload-safe prepare-report.json structure (no raw values)."""
        return {
            "schema": "trace-exchange.prepare-report.v1",
            "ruleset_version": RULESET_VERSION,
            "run_id": self.run_id,
            "scenario": self.scenario,
            "overall_verdict": self.overall_verdict,
            "refused": self.refused,
            "dry_run": self.dry_run,
            "files": [
                {
                    "path": report.path,
                    "kind": report.kind,
                    "verdict": report.verdict,
                    "intake_rejected": report.intake_rejected,
                    "scanner_results": report.scanner_results,
                    "finding_count": report.finding_count,
                    "redaction_count": report.redaction_count,
                    "findings": report.findings,
                }
                for report in self.files
            ],
        }


def _cli_version() -> str:
    try:
        return version("autocontext")
    except PackageNotFoundError:
        return "0+unknown"


def _strictest(verdicts: list[ReviewState]) -> ReviewState:
    if not verdicts:
        return "needs_human_review"
    return max(verdicts, key=lambda verdict: _VERDICT_RANK.get(verdict, 1))


def prepare_share(
    *,
    runs_root: Path,
    knowledge_root: Path,
    run_id: str,
    scenario_name: str | None = None,
    output_dir: Path | None = None,
    dry_run: bool = False,
    license_spdx: str = "CC-BY-4.0",
    run_kind: str = "custom",
) -> PrepareResult:
    """Run the local prepare pass and optionally write a redacted bundle."""
    artifacts = collect_session_artifacts(runs_root, knowledge_root, run_id, scenario_name)

    file_reports: list[PrepareFileReport] = []
    manifest_files: list[dict[str, Any]] = []
    redacted_payloads: list[tuple[str, str]] = []
    total_redactions = 0

    for artifact in artifacts:
        try:
            bundle_path = normalize_bundle_path(artifact.bundle_path)
        except ValueError as error:
            file_reports.append(
                PrepareFileReport(
                    path=artifact.bundle_path,
                    kind="report",
                    verdict="rejected",
                    intake_rejected=str(error),
                    scanner_results={},
                    finding_count=0,
                    redaction_count=0,
                    findings=[],
                )
            )
            continue

        kind = infer_kind(artifact.name, artifact.category)

        try:
            content = artifact.path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            file_reports.append(
                PrepareFileReport(
                    path=bundle_path,
                    kind=kind,
                    verdict="rejected",
                    intake_rejected=f"unreadable as UTF-8 text: {error}",
                    scanner_results={},
                    finding_count=0,
                    redaction_count=0,
                    findings=[],
                )
            )
            continue

        rejection = intake_rejection(artifact.name, content)
        if rejection is not None:
            file_reports.append(
                PrepareFileReport(
                    path=bundle_path,
                    kind=kind,
                    verdict="rejected",
                    intake_rejected=rejection,
                    scanner_results={},
                    finding_count=0,
                    redaction_count=0,
                    findings=[],
                )
            )
            continue

        scan = scan_content(content, kind=kind)
        redaction_count = sum(entry.count for entry in scan.redaction_manifest)
        total_redactions += redaction_count
        redacted_payloads.append((bundle_path, scan.redacted_text))
        manifest_files.append(
            {
                "path": bundle_path,
                "kind": kind,
                "bytes": len(scan.redacted_text.encode("utf-8")),
                "sha256": sha256_text(scan.redacted_text),
                "lines": scan.redacted_text.count("\n") + 1,
            }
        )
        file_reports.append(
            PrepareFileReport(
                path=bundle_path,
                kind=kind,
                verdict=scan.verdict,
                intake_rejected=None,
                scanner_results={key: value for key, value in scan.scanner_results.items()},
                finding_count=len(scan.findings),
                redaction_count=redaction_count,
                findings=[
                    {
                        "rule_id": finding.rule_id,
                        "scanner": finding.scanner,
                        "label": finding.label,
                        "severity": finding.severity,
                        "excerpt": finding.excerpt,
                    }
                    for finding in scan.findings
                ],
                scan=scan,
            )
        )

    overall = _strictest([report.verdict for report in file_reports])
    refused = overall == "rejected"

    result = PrepareResult(
        run_id=run_id,
        scenario=scenario_name or "",
        overall_verdict=overall,
        refused=refused,
        dry_run=dry_run,
        files=file_reports,
    )

    if not dry_run and not refused and output_dir is not None and redacted_payloads:
        # local_scan reflects whether the local scan actually found anything —
        # NOT the routing verdict. `needs_human_review` covers both clean
        # bundles and ones with review-level findings (e.g. an IPv4), so deriving
        # it from the verdict would mislabel flagged bundles as "passed".
        local_scan = (
            "flagged"
            if total_redactions > 0
            or any(
                report.finding_count > 0 or any(state == "flagged" for state in report.scanner_results.values())
                for report in file_reports
            )
            else "passed"
        )
        result.bundle_dir = _write_bundle(
            output_dir=output_dir,
            run_id=run_id,
            scenario_name=scenario_name or "",
            run_kind=run_kind,
            license_spdx=license_spdx,
            manifest_files=manifest_files,
            redacted_payloads=redacted_payloads,
            total_redactions=total_redactions,
            local_scan=local_scan,
        )

    return result


def _write_bundle(
    *,
    output_dir: Path,
    run_id: str,
    scenario_name: str,
    run_kind: str,
    license_spdx: str,
    manifest_files: list[dict[str, Any]],
    redacted_payloads: list[tuple[str, str]],
    total_redactions: int,
    local_scan: str,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)

    for bundle_path, redacted_text in redacted_payloads:
        dest = output_dir / bundle_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(redacted_text, encoding="utf-8")

    manifest = build_manifest(
        run_id=run_id,
        run_kind=run_kind,
        scenario=scenario_name,
        family=None,
        autocontext_version=_cli_version(),
        created_at=datetime.datetime.now(datetime.UTC).isoformat(),
        license_spdx=license_spdx,
        rights_attestation=False,
        files=manifest_files,
        cli_version=_cli_version(),
        local_scan=local_scan,
        local_redactions=total_redactions,
    )
    (output_dir / "bundle.manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return output_dir
