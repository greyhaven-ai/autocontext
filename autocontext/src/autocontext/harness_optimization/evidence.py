"""Read/write/validate API for the harness-optimization CandidateEvidence artifact (AC-876).

This is a thin, hand-written surface over the generated
`autocontext.harness_optimization.contract.models.CandidateEvidence` model. The
model itself is regenerated from the canonical JSON Schema and must not be edited
by hand; this module only provides the persistence and validation entry points
both language packages agree on.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from autocontext.harness_optimization.contract.models import CandidateEvidence

__all__ = [
    "CandidateEvidence",
    "read_candidate_evidence",
    "validate_candidate_evidence",
    "write_candidate_evidence",
]


def validate_candidate_evidence(data: dict[str, Any]) -> CandidateEvidence:
    """Construct a CandidateEvidence from a plain dict.

    Raises pydantic ``ValidationError`` if the data violates the schema
    (missing required field, unknown extra field, bad enum value, ...).
    """
    return CandidateEvidence(**data)


def read_candidate_evidence(path: Path) -> CandidateEvidence:
    """Load and validate a CandidateEvidence artifact from a JSON file."""
    data = json.loads(Path(path).read_text())
    return validate_candidate_evidence(data)


def write_candidate_evidence(evidence: CandidateEvidence, path: Path) -> None:
    """Persist a CandidateEvidence artifact as JSON with a stable key order.

    Keys are sorted so the on-disk form is deterministic across writes, and
    optional fields are emitted (as ``null``) so the record shape is explicit.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = evidence.model_dump(mode="json", exclude_none=False)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
