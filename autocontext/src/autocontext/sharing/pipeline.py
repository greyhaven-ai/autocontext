"""Full sharing pipeline: collect → redact → scan → bundle → attest (AC-519)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from autocontext.sharing.attestation import AttestationRecord, create_attestation
from autocontext.sharing.bundle import ExportBundle, create_bundle
from autocontext.sharing.collector import collect_session_artifacts
from autocontext.sharing.review import find_suspicious_patterns

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ShareResult:
    """Outcome of the share pipeline."""

    bundle: ExportBundle
    attestation: AttestationRecord | None
    scan_clean: bool
    suspicious_count: int


def share_session(
    runs_root: Path,
    knowledge_root: Path,
    run_id: str,
    output_dir: Path,
    operator: str = "anonymous",
    scenario_name: str | None = None,
    scan_for_secrets: bool = True,
    interactive: bool = False,
) -> ShareResult:
    """Execute the full sharing pipeline.

    Steps:
    1. Collect source artifacts
    2. Create redacted export bundle
    3. Run TruffleHog backstop scan
    4. Find remaining suspicious patterns
    5. Auto-approve (non-interactive) or prompt for attestation
    """
    # 1. Collect
    artifacts = collect_session_artifacts(
        runs_root=runs_root,
        knowledge_root=knowledge_root,
        run_id=run_id,
        scenario_name=scenario_name,
    )
    if not artifacts:
        logger.warning("No artifacts found for run %s", run_id)

    source_files = [a.path for a in artifacts]

    # 2. Create redacted bundle
    bundle = create_bundle(
        source_files=source_files,
        output_dir=output_dir,
        run_id=run_id,
        scenario_name=scenario_name or "",
    )

    # 3. TruffleHog backstop
    scan_clean = True
    if scan_for_secrets:
        try:
            from autocontext.security.scanner import SecretScanner

            scanner = SecretScanner()
            scan_result = scanner.scan(str(output_dir))
            scan_clean = scan_result.is_clean

            # Persist scan report
            report_path = output_dir / "secret_scan_report.json"
            report_path.write_text(json.dumps(scan_result.to_dict(), indent=2), encoding="utf-8")

            if not scan_clean:
                logger.warning("TruffleHog found %d secrets in export bundle", scan_result.finding_count)
        except Exception:
            logger.debug("Secret scanning unavailable", exc_info=True)

    # 4. Find suspicious patterns
    suspicious_count = 0
    for path in output_dir.rglob("*"):
        if path.is_file() and path.suffix in {".json", ".md", ".txt", ".ndjson"}:
            if path.name in {"manifest.json", "redaction_report.json", "secret_scan_report.json", "attestation.json"}:
                continue
            try:
                content = path.read_text(encoding="utf-8")
                findings = find_suspicious_patterns(content)
                suspicious_count += len(findings)
            except (OSError, UnicodeDecodeError):
                continue

    # 5. Attestation
    if interactive:
        # Interactive mode would prompt the operator — for now, defer to CLI wrapper
        attestation = None
    else:
        # Non-interactive: auto-approve if clean, auto-reject if secrets found
        if scan_clean:
            attestation = create_attestation(
                operator=operator,
                bundle_id=f"bundle_{run_id}",
                decision="auto_approved",
                reason="Non-interactive mode, scan clean",
            )
        else:
            attestation = create_attestation(
                operator=operator,
                bundle_id=f"bundle_{run_id}",
                decision="rejected",
                reason="TruffleHog findings detected",
            )

    # Persist attestation
    if attestation:
        (output_dir / "attestation.json").write_text(
            json.dumps(attestation.to_dict(), indent=2),
            encoding="utf-8",
        )
        bundle.attestation = attestation

    return ShareResult(
        bundle=bundle,
        attestation=attestation,
        scan_clean=scan_clean,
        suspicious_count=suspicious_count,
    )
