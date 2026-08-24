"""Artifact classification helpers for durable kernel campaigns."""

from __future__ import annotations

from pathlib import Path

from autocontext.kernel_evolution.campaign_journal_models import KernelArtifactKind
from autocontext.kernel_evolution.generation import KernelGenerationFailure
from autocontext.util.json_io import read_json


def artifact_kind(relative: str) -> KernelArtifactKind:
    if relative == "manifest.json":
        return "manifest"
    prefixes: tuple[tuple[str, KernelArtifactKind], ...] = (
        ("prompts/", "prompt"),
        ("generation/receipts/", "generation_receipt"),
        ("generation/evaluations/", "evaluation_claim"),
        ("artifacts/", "source"),
        ("reports/", "report"),
        ("attempts/", "attempt"),
        ("champion", "champion"),
        ("audit/", "audit"),
    )
    for prefix, kind in prefixes:
        if relative.startswith(prefix):
            return kind
    if relative.endswith("/claim.json") and relative.startswith("generation/proposals/"):
        return "generation_claim"
    if relative.endswith("/failure.json") or "/cancellations/" in relative:
        return "generation_failure"
    if relative.endswith("/attempt-link.json"):
        return "attempt_link"
    exact: dict[str, KernelArtifactKind] = {
        "lineage.jsonl": "lineage",
        "summary.json": "summary",
        "profile_evidence.json": "profile_evidence",
    }
    return exact.get(relative, "other")


def champion_artifact_digest(run_dir: Path) -> str | None:
    path = run_dir / "champion.json"
    if not path.is_file():
        return None
    payload = read_json(path)
    value = payload.get("artifact_digest") if isinstance(payload, dict) else None
    return value if isinstance(value, str) else None


def add_durable_failure(
    failures: dict[str, KernelGenerationFailure],
    failure: KernelGenerationFailure,
    proposal_index: int,
) -> None:
    if failure.proposal_index != proposal_index:
        raise ValueError("generation failure belongs to a different proposal")
    existing = failures.get(failure.failure_id)
    if existing is not None and existing != failure:
        raise ValueError("generation failure id has conflicting payloads")
    failures[failure.failure_id] = failure
