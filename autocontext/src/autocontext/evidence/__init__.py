"""Browsable prior-run evidence workspace (AC-504)."""

from __future__ import annotations

from autocontext.evidence.manifest import render_artifact_detail, render_evidence_manifest
from autocontext.evidence.materializer import materialize_workspace
from autocontext.evidence.tracker import compute_utilization, load_access_log, record_access, save_access_log
from autocontext.evidence.workspace import EvidenceArtifact, EvidenceWorkspace

__all__ = [
    "EvidenceArtifact",
    "EvidenceWorkspace",
    "materialize_workspace",
    "render_evidence_manifest",
    "render_artifact_detail",
    "record_access",
    "save_access_log",
    "load_access_log",
    "compute_utilization",
]
