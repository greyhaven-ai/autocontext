"""Export bundle — the shareable artifact (AC-519).

An ExportBundle is a directory containing:
- Redacted copies of source artifacts
- manifest.json with provenance metadata
- redaction_report.json summarizing what was changed
- attestation.json (added after operator review)
"""

from __future__ import annotations

import datetime
import json
from dataclasses import dataclass
from pathlib import Path

from autocontext.sharing.attestation import AttestationRecord
from autocontext.sharing.redactor import RedactionReport, redact_content_with_report


@dataclass(slots=True)
class ExportBundle:
    """A completed export bundle ready for review/publication."""

    output_dir: Path
    run_id: str
    scenario_name: str
    source_files: list[str]
    redaction_report: RedactionReport
    attestation: AttestationRecord | None = None


def create_bundle(
    source_files: list[Path],
    output_dir: Path,
    run_id: str,
    scenario_name: str = "",
) -> ExportBundle:
    """Create a redacted export bundle from source files."""
    output_dir.mkdir(parents=True, exist_ok=True)

    all_redactions = RedactionReport()
    exported_names: list[str] = []

    for src in source_files:
        if not src.is_file():
            continue
        try:
            content = src.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        redacted, report = redact_content_with_report(content)
        all_redactions.redactions.extend(report.redactions)

        dest = output_dir / src.name
        dest.write_text(redacted, encoding="utf-8")
        exported_names.append(src.name)

    all_redactions.total_count = len(all_redactions.redactions)

    # Write manifest
    manifest = {
        "run_id": run_id,
        "scenario_name": scenario_name,
        "exported_files": exported_names,
        "created_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "redaction_count": all_redactions.total_count,
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    # Write redaction report
    (output_dir / "redaction_report.json").write_text(
        json.dumps(all_redactions.to_dict(), indent=2),
        encoding="utf-8",
    )

    return ExportBundle(
        output_dir=output_dir,
        run_id=run_id,
        scenario_name=scenario_name,
        source_files=exported_names,
        redaction_report=all_redactions,
    )
