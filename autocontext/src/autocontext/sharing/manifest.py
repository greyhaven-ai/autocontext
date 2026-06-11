"""trace-exchange.v1 bundle manifest + tier-0 intake validation.

Mirrors spec sections 3 (manifest schema + limits) and 5.1 (intake checks) of
docs/internal/trace-exchange-implementation-spec.md in autocontext-website.
Pure helpers — no I/O beyond reading file sizes/bytes the caller passes in.
"""

from __future__ import annotations

import hashlib
from pathlib import PurePosixPath
from typing import Any

from autocontext.sharing.safeguards import RULESET_VERSION, BundleFileKind

SCHEMA_VERSION = "trace-exchange.v1"

MAX_BUNDLE_BYTES = 25 * 1024 * 1024
MAX_FILES = 200
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_LINE_LENGTH = 10_000

ALLOWED_EXTENSIONS: frozenset[str] = frozenset(
    {".json", ".jsonl", ".ndjson", ".md", ".txt", ".csv", ".yaml", ".yml", ".py", ".ts", ".js", ".sql"}
)

_SOURCE_EXTENSIONS: frozenset[str] = frozenset({".py", ".ts", ".js", ".sql"})


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_bundle_path(raw_path: str) -> str:
    """Normalize a relative bundle path; reject traversal/absolute/unsafe forms."""
    if not raw_path or raw_path.startswith("/") or raw_path.startswith("~"):
        raise ValueError(f"path must be relative: {raw_path!r}")
    if "\\" in raw_path or "\x00" in raw_path:
        raise ValueError(f"path contains unsafe characters: {raw_path!r}")

    pure = PurePosixPath(raw_path)
    parts = [part for part in pure.parts if part not in (".",)]
    if any(part == ".." for part in parts):
        raise ValueError(f"path escapes bundle root: {raw_path!r}")
    if not parts:
        raise ValueError(f"empty normalized path: {raw_path!r}")

    return "/".join(parts)


def infer_kind(name: str, category: str | None = None) -> BundleFileKind:
    """Infer a spec bundle kind from filename, then collector category."""
    lower = name.lower()
    suffix = PurePosixPath(lower).suffix

    if suffix in _SOURCE_EXTENSIONS:
        return "tool"
    if lower in ("playbook.md",) or "playbook" in lower:
        return "playbook"
    if "hints" in lower or "dead_ends" in lower:
        return "hints"
    if lower.endswith("lessons.json") or lower == "lessons.json":
        return "lessons"
    if suffix in (".jsonl", ".ndjson"):
        return "trace"
    if lower.endswith(".csv") or "dataset" in lower:
        return "dataset"
    if "report" in lower or suffix == ".md":
        return "report"

    category_map: dict[str, BundleFileKind] = {
        "trace": "trace",
        "session": "trace",
        "output": "report",
        "report": "report",
        "playbook": "playbook",
    }
    if category and category in category_map:
        return category_map[category]
    return "report"


def intake_rejection(name: str, content: str) -> str | None:
    """Tier-0 check on a single text file; return a reason string or None."""
    suffix = PurePosixPath(name.lower()).suffix
    if suffix not in ALLOWED_EXTENSIONS:
        return f"extension {suffix or '(none)'} not in allowlist"
    if "\x00" in content:
        return "NUL byte (binary content not allowed)"
    size = len(content.encode("utf-8"))
    if size > MAX_FILE_BYTES:
        return f"file exceeds {MAX_FILE_BYTES} bytes"
    for line in content.split("\n"):
        if len(line) > MAX_LINE_LENGTH:
            return f"line exceeds {MAX_LINE_LENGTH} chars"
    return None


def build_manifest(
    *,
    run_id: str,
    run_kind: str,
    scenario: str,
    family: str | None,
    autocontext_version: str,
    created_at: str,
    license_spdx: str,
    rights_attestation: bool,
    files: list[dict[str, Any]],
    cli_version: str,
    local_scan: str,
    local_redactions: int,
) -> dict[str, Any]:
    """Assemble a trace-exchange.v1 manifest dict (uploader filled server-side)."""
    return {
        "schema_version": SCHEMA_VERSION,
        "ruleset_version": RULESET_VERSION,
        "source": {
            "run_id": run_id,
            "run_kind": run_kind,
            "scenario": scenario,
            "family": family,
            "autocontext_version": autocontext_version,
            "created_at": created_at,
        },
        "license": {
            "spdx": license_spdx,
            "rights_attestation": rights_attestation,
        },
        "files": files,
        "prepare": {
            "cli_version": cli_version,
            "local_scan": local_scan,
            "local_redactions": local_redactions,
        },
    }
