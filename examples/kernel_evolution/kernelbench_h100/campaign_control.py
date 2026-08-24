#!/usr/bin/env python3
"""Inspect or stop a durable kernel campaign without GPU/provider access."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from profile_contract import PROFILE_NAMES, profile_output_root

from autocontext.kernel_evolution import (
    read_kernel_campaign_status,
    request_kernel_campaign_stop,
)

_SAFE_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def _outer_campaign_status(lineage_root: Path, run_id: str) -> dict[str, Any] | None:
    path = lineage_root / run_id / "campaign_status.json"
    if path.is_symlink():
        raise RuntimeError(f"outer campaign status must not be a symlink: {path}")
    if not path.exists():
        return None
    if not path.is_file():
        raise RuntimeError(f"outer campaign status must be a regular non-symlink file: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"outer campaign status is not valid UTF-8 JSON: {path}") from exc
    if not isinstance(payload, dict) or payload.get("run_id") != run_id:
        raise RuntimeError(f"outer campaign status has an invalid run identity: {path}")
    status = payload.get("status")
    if status not in {"running", "complete", "cancelled", "interrupted", "failed"}:
        raise RuntimeError(f"outer campaign status has an invalid lifecycle state: {path}")
    return payload


def _read_control_status(lineage_root: Path, run_id: str) -> dict[str, Any]:
    """Prefer the evidence-export lifecycle while retaining kernel progress."""

    outer = _outer_campaign_status(lineage_root, run_id)
    try:
        kernel = read_kernel_campaign_status(lineage_root, run_id).model_dump(mode="json")
    except FileNotFoundError:
        if outer is None:
            raise
        kernel = None
    if outer is None:
        assert kernel is not None
        if kernel.get("status") == "complete":
            status_path = lineage_root / run_id / "campaign_status.json"
            return {
                "schema_version": "autocontext.kernel-h100-control-status/v1",
                "run_id": run_id,
                "status": "failed",
                "profile_evidence_ready": False,
                "campaign_status_path": str(status_path.resolve()),
                "error_type": "OuterCampaignStatusMissing",
                "error": f"kernel completed without a durable outer campaign status: {status_path}",
                "kernel": kernel,
            }
        return kernel

    evidence_path = lineage_root / run_id / "profile_evidence.json"
    evidence_ready = outer["status"] == "complete" and evidence_path.is_file() and not evidence_path.is_symlink()
    status = outer["status"]
    error_type = outer.get("error_type")
    error = outer.get("error")
    if status == "complete" and not evidence_ready:
        status = "failed"
        error_type = "ProfileEvidenceExportMissing"
        error = f"completed campaign is missing its durable profile evidence: {evidence_path}"
    payload: dict[str, Any] = {
        "schema_version": "autocontext.kernel-h100-control-status/v1",
        "run_id": run_id,
        "status": status,
        "profile_evidence_ready": evidence_ready,
        "campaign_status_path": str((lineage_root / run_id / "campaign_status.json").resolve()),
        "kernel": kernel,
    }
    for name, value in (
        ("reason", outer.get("reason")),
        ("error_type", error_type),
        ("error", error),
    ):
        if isinstance(value, str):
            payload[name] = value
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("status", "stop"))
    parser.add_argument("run_id")
    parser.add_argument("--output", type=Path, default=Path("runs/kernel-evolution-h100"))
    parser.add_argument("--precision-profile", choices=PROFILE_NAMES, required=True)
    parser.add_argument("--requested-by", default="operator")
    args = parser.parse_args()
    if _SAFE_RUN_ID.fullmatch(args.run_id) is None or ".." in args.run_id:
        parser.error("run_id must be a safe path segment")

    lineage_root = profile_output_root(args.output, args.precision_profile).resolve()
    if args.action == "stop":
        request_kernel_campaign_stop(lineage_root, args.run_id, requested_by=args.requested_by)
    print(json.dumps(_read_control_status(lineage_root, args.run_id), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
