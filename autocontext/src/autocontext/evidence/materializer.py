"""Evidence workspace materializer (AC-504).

Scans prior-run directories and knowledge artifacts, copies them into a
flat workspace directory, and returns a manifest.
"""

from __future__ import annotations

import dataclasses
import datetime
import hashlib
import json
import logging
import shutil
from pathlib import Path

from autocontext.evidence.workspace import EvidenceArtifact, EvidenceWorkspace

logger = logging.getLogger(__name__)

ARTIFACT_PRIORITY = ["gate_decision", "trace", "report", "role_output", "tool", "log"]
_DEFAULT_BUDGET = 10 * 1024 * 1024  # 10 MB
_MANIFEST_FILENAME = "manifest.json"
_ACCESS_LOG_FILENAME = "evidence_access_log.json"

_KIND_PATTERNS: dict[str, list[str]] = {
    "gate_decision": ["gate_decision*.json", "gate*.json"],
    "trace": ["events.ndjson", "trace*.json", "event_stream*.ndjson"],
    "report": ["playbook.md", "dead_ends.md", "session_report*.md", "weakness_report*.md", "progress_report*.md"],
    "role_output": ["analyst_output*.md", "coach_output*.md", "architect_output*.md", "competitor_output*.md"],
    "tool": ["*.py"],
    "log": ["*.log", "execution_log*.txt"],
}


def materialize_workspace(
    knowledge_root: Path,
    runs_root: Path,
    source_run_ids: list[str],
    workspace_dir: Path,
    budget_bytes: int = _DEFAULT_BUDGET,
    scenario_name: str | None = None,
    scan_for_secrets: bool = False,
) -> EvidenceWorkspace:
    """Materialize evidence from prior runs into a flat workspace directory."""
    workspace_dir.mkdir(parents=True, exist_ok=True)

    all_artifacts: list[EvidenceArtifact] = []

    # Scan run directories
    for run_id in source_run_ids:
        run_dir = runs_root / run_id
        if run_dir.is_dir():
            all_artifacts.extend(_scan_run_artifacts(run_dir, run_id))

    # Scan knowledge directory
    if scenario_name:
        knowledge_dir = knowledge_root / scenario_name
        if knowledge_dir.is_dir():
            all_artifacts.extend(_scan_knowledge_artifacts(knowledge_dir, scenario_name))

    source_signature = _compute_source_signature(
        artifacts=all_artifacts,
        source_run_ids=source_run_ids,
        budget_bytes=budget_bytes,
        scenario_name=scenario_name,
        scan_for_secrets=scan_for_secrets,
    )
    cached = _load_cached_workspace(workspace_dir, source_signature=source_signature)
    if cached is not None:
        if scan_for_secrets:
            return _refresh_cached_workspace_after_secret_scan(workspace_dir, cached)
        return cached

    _cleanup_previous_workspace(workspace_dir)

    # Sort by priority then recency (mtime descending)
    priority_map = {kind: i for i, kind in enumerate(ARTIFACT_PRIORITY)}
    all_artifacts.sort(key=lambda a: (priority_map.get(a.kind, 99), -a.size_bytes))

    # Copy into workspace respecting budget
    selected: list[EvidenceArtifact] = []
    total_size = 0
    for artifact in all_artifacts:
        if total_size + artifact.size_bytes > budget_bytes:
            continue
        # Copy file into workspace
        src_path = Path(artifact.path)
        if not src_path.exists():
            continue
        dest_name = f"{artifact.artifact_id}_{src_path.name}"
        dest_path = workspace_dir / dest_name
        try:
            shutil.copy2(str(src_path), str(dest_path))
        except OSError:
            continue
        # Update artifact path to workspace-relative
        workspace_artifact = EvidenceArtifact(
            artifact_id=artifact.artifact_id,
            source_run_id=artifact.source_run_id,
            kind=artifact.kind,
            path=dest_name,
            summary=artifact.summary,
            size_bytes=artifact.size_bytes,
            generation=artifact.generation,
            source_path=artifact.source_path or str(src_path),
            source_mtime_ns=artifact.source_mtime_ns,
        )
        selected.append(workspace_artifact)
        total_size += artifact.size_bytes

    # AC-519 prep: TruffleHog backstop scan — filter flagged artifacts
    if scan_for_secrets:
        selected, total_size = _apply_secret_scan(workspace_dir, selected, total_size)

    workspace = EvidenceWorkspace(
        workspace_dir=str(workspace_dir),
        source_runs=source_run_ids,
        artifacts=selected,
        total_size_bytes=total_size,
        materialized_at=datetime.datetime.now(datetime.UTC).isoformat(),
        source_signature=source_signature,
        cache_hit=False,
    )

    # Write manifest
    manifest_path = workspace_dir / _MANIFEST_FILENAME
    manifest_path.write_text(json.dumps(workspace.to_dict(), indent=2), encoding="utf-8")

    return workspace


def _compute_source_signature(
    *,
    artifacts: list[EvidenceArtifact],
    source_run_ids: list[str],
    budget_bytes: int,
    scenario_name: str | None,
    scan_for_secrets: bool,
) -> str:
    digest = hashlib.sha256()
    digest.update(str(sorted(source_run_ids)).encode())
    digest.update(str(budget_bytes).encode())
    digest.update(str(bool(scan_for_secrets)).encode())
    digest.update(str(scenario_name or "").encode())
    for artifact in sorted(artifacts, key=lambda item: (item.source_run_id, item.kind, item.path)):
        digest.update(artifact.source_run_id.encode())
        digest.update(artifact.kind.encode())
        digest.update((artifact.source_path or artifact.path).encode())
        digest.update(str(artifact.size_bytes).encode())
        digest.update(str(artifact.source_mtime_ns or 0).encode())
        digest.update(str(artifact.generation if artifact.generation is not None else "").encode())
    return digest.hexdigest()


def _load_cached_workspace(workspace_dir: Path, *, source_signature: str) -> EvidenceWorkspace | None:
    manifest_path = workspace_dir / _MANIFEST_FILENAME
    if not manifest_path.is_file():
        return None
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    if str(data.get("source_signature", "")) != source_signature:
        return None
    artifacts = data.get("artifacts", [])
    if not isinstance(artifacts, list):
        return None
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            return None
        rel_path = artifact.get("path")
        if not isinstance(rel_path, str):
            return None
        artifact_path = _resolve_workspace_path(workspace_dir, rel_path)
        if artifact_path is None or not artifact_path.exists():
            return None
    try:
        workspace = EvidenceWorkspace.from_dict(data)
    except (KeyError, TypeError, ValueError):
        return None
    return dataclasses.replace(workspace, cache_hit=True)


def _refresh_cached_workspace_after_secret_scan(
    workspace_dir: Path,
    workspace: EvidenceWorkspace,
) -> EvidenceWorkspace:
    artifacts, total_size = _apply_secret_scan(workspace_dir, list(workspace.artifacts), workspace.total_size_bytes)
    workspace.artifacts = artifacts
    workspace.total_size_bytes = total_size
    manifest_path = workspace_dir / _MANIFEST_FILENAME
    manifest_path.write_text(json.dumps(workspace.to_dict(), indent=2), encoding="utf-8")
    return workspace


def _apply_secret_scan(
    workspace_dir: Path,
    artifacts: list[EvidenceArtifact],
    total_size: int,
) -> tuple[list[EvidenceArtifact], int]:
    """Run TruffleHog on the workspace and remove flagged artifacts."""
    from autocontext.security.scanner import SecretScanner

    scanner = SecretScanner()
    result = scanner.scan(str(workspace_dir))

    # Persist scan report
    report_path = workspace_dir / "secret_scan_report.json"
    report_path.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")

    if result.is_clean:
        return artifacts, total_size

    if result.scan_error is not None:
        logger.warning("secret scan failed for %s: %s — excluding all artifacts", workspace_dir, result.scan_error)
        for artifact in artifacts:
            artifact_path = workspace_dir / artifact.path
            try:
                artifact_path.unlink(missing_ok=True)
            except OSError:
                pass
        return [], 0

    # Remove flagged artifacts from the manifest and delete files
    flagged_basenames = {Path(f).name for f in result.flagged_files}
    clean: list[EvidenceArtifact] = []
    clean_size = 0
    for artifact in artifacts:
        if artifact.path in flagged_basenames or (workspace_dir / artifact.path).name in flagged_basenames:
            logger.warning("secret scan flagged artifact %s (%s) — excluding", artifact.artifact_id, artifact.path)
            flagged_path = workspace_dir / artifact.path
            if flagged_path.exists():
                flagged_path.unlink()
        else:
            clean.append(artifact)
            clean_size += artifact.size_bytes

    return clean, clean_size


def _cleanup_previous_workspace(workspace_dir: Path) -> None:
    """Remove files tracked by the prior manifest before rewriting the workspace."""
    manifest_path = workspace_dir / _MANIFEST_FILENAME
    if manifest_path.is_file():
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            artifacts = data.get("artifacts", [])
            if isinstance(artifacts, list):
                for artifact in artifacts:
                    if not isinstance(artifact, dict):
                        continue
                    rel_path = artifact.get("path")
                    if not isinstance(rel_path, str):
                        continue
                    artifact_path = _resolve_workspace_path(workspace_dir, rel_path)
                    if artifact_path is None:
                        continue
                    try:
                        artifact_path.unlink(missing_ok=True)
                    except OSError:
                        pass
        except (json.JSONDecodeError, OSError):
            pass

    for metadata_name in (_MANIFEST_FILENAME, _ACCESS_LOG_FILENAME):
        try:
            (workspace_dir / metadata_name).unlink(missing_ok=True)
        except OSError:
            pass


def _resolve_workspace_path(workspace_dir: Path, rel_path: str) -> Path | None:
    """Resolve a manifest path inside the workspace and reject directory escapes."""
    candidate = (workspace_dir / rel_path).resolve()
    try:
        candidate.relative_to(workspace_dir.resolve())
    except ValueError:
        return None
    return candidate


def _scan_run_artifacts(run_dir: Path, run_id: str) -> list[EvidenceArtifact]:
    """Discover artifacts in a run directory."""
    artifacts: list[EvidenceArtifact] = []
    try:
        for path in sorted(run_dir.rglob("*")):
            if not path.is_file():
                continue
            kind = _classify_file(path, run_dir)
            if kind is None:
                continue
            generation = _extract_generation(path)
            artifacts.append(
                EvidenceArtifact(
                    artifact_id=_make_id(run_id, path),
                    source_run_id=run_id,
                    kind=kind,
                    path=str(path),
                    summary=f"{kind}: {path.name} from {run_id}",
                    size_bytes=path.stat().st_size,
                    generation=generation,
                    source_path=str(path),
                    source_mtime_ns=path.stat().st_mtime_ns,
                )
            )
    except OSError:
        pass
    return artifacts


def _scan_knowledge_artifacts(knowledge_dir: Path, scenario_name: str) -> list[EvidenceArtifact]:
    """Discover artifacts in the knowledge directory."""
    artifacts: list[EvidenceArtifact] = []
    source_id = f"knowledge:{scenario_name}"

    # Known knowledge files
    known_files = {
        "playbook.md": "report",
        "dead_ends.md": "report",
    }
    for fname, kind in known_files.items():
        fpath = knowledge_dir / fname
        if fpath.is_file():
            artifacts.append(
                EvidenceArtifact(
                    artifact_id=_make_id(source_id, fpath),
                    source_run_id=source_id,
                    kind=kind,
                    path=str(fpath),
                    summary=f"{kind}: {fname} for {scenario_name}",
                    size_bytes=fpath.stat().st_size,
                    generation=None,
                    source_path=str(fpath),
                    source_mtime_ns=fpath.stat().st_mtime_ns,
                )
            )

    # Tools directory
    tools_dir = knowledge_dir / "tools"
    if tools_dir.is_dir():
        for tpath in sorted(tools_dir.glob("*.py")):
            if tpath.is_file():
                artifacts.append(
                    EvidenceArtifact(
                        artifact_id=_make_id(source_id, tpath),
                        source_run_id=source_id,
                        kind="tool",
                        path=str(tpath),
                        summary=f"tool: {tpath.name} for {scenario_name}",
                        size_bytes=tpath.stat().st_size,
                        generation=None,
                        source_path=str(tpath),
                        source_mtime_ns=tpath.stat().st_mtime_ns,
                    )
                )

    # Analysis directory
    analysis_dir = knowledge_dir / "analysis"
    if analysis_dir.is_dir():
        for apath in sorted(analysis_dir.glob("gen_*.md")):
            if apath.is_file():
                gen = _extract_generation(apath)
                artifacts.append(
                    EvidenceArtifact(
                        artifact_id=_make_id(source_id, apath),
                        source_run_id=source_id,
                        kind="report",
                        path=str(apath),
                        summary=f"analysis: {apath.name} for {scenario_name}",
                        size_bytes=apath.stat().st_size,
                        generation=gen,
                        source_path=str(apath),
                        source_mtime_ns=apath.stat().st_mtime_ns,
                    )
                )

    return artifacts


def _classify_file(path: Path, root: Path) -> str | None:
    """Classify a file into an evidence kind based on name/location."""
    name = path.name.lower()
    rel = str(path.relative_to(root)).lower()

    if "gate_decision" in name or "gate" in name and name.endswith(".json"):
        return "gate_decision"
    if name.endswith(".ndjson") or "event" in name or "trace" in name:
        return "trace"
    if any(kw in name for kw in ("playbook", "dead_end", "report", "weakness", "session")):
        return "report"
    if any(kw in name for kw in ("analyst", "coach", "architect", "competitor")) and "_output" in name:
        return "role_output"
    if "tools/" in rel and name.endswith(".py"):
        return "tool"
    if name.endswith(".log") or "execution_log" in name:
        return "log"
    return None


def _extract_generation(path: Path) -> int | None:
    """Extract generation number from filename like gen_3.md or gen_3/."""
    import re

    match = re.search(r"gen[_-]?(\d+)", path.name)
    if match:
        return int(match.group(1))
    for parent in path.parents:
        match = re.search(r"gen[_-]?(\d+)", parent.name)
        if match:
            return int(match.group(1))
    return None


def _make_id(source: str, path: Path) -> str:
    """Generate a stable artifact ID from source and path."""
    raw = f"{source}:{path}"
    return hashlib.sha256(raw.encode()).hexdigest()[:12]
