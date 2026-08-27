"""MCP tool implementations — artifact_tools (extracted from tools.py, AC-482)."""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
from pathlib import Path
from typing import TYPE_CHECKING, Any

from autocontext.execution.harness_loader import HarnessLoader
from autocontext.mcp._base import MtsToolContext
from autocontext.scenarios import SCENARIO_REGISTRY

logger = logging.getLogger(__name__)

_ARTIFACT_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_ARTIFACTS_DIR_NAME = "_openclaw_artifacts"

if TYPE_CHECKING:
    pass


def _validate_artifact_id(artifact_id: str) -> str:
    """Return a storage-safe artifact ID or raise ``ValueError``.

    Artifact IDs become filenames, so keep the accepted language deliberately
    narrower than a generic path segment. In particular, dots, separators,
    absolute paths, and traversal components are never valid IDs.
    """
    if not isinstance(artifact_id, str) or _ARTIFACT_ID_PATTERN.fullmatch(artifact_id) is None:
        raise ValueError(
            "artifact id must be 1-128 ASCII letters, digits, underscores, or hyphens and must start with a letter or digit"
        )
    return artifact_id


def _resolve_canonical(path: Path, *, label: str) -> Path:
    """Resolve symlinks while converting loops and filesystem errors to validation failures."""
    try:
        return path.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"unable to resolve {label}") from exc


def _canonical_artifacts_dir(ctx: MtsToolContext) -> Path:
    """Resolve the OpenClaw store and require it to remain under knowledge root."""
    knowledge_root = _resolve_canonical(ctx.settings.knowledge_root, label="knowledge root")
    artifacts_dir = _resolve_canonical(knowledge_root / _ARTIFACTS_DIR_NAME, label="OpenClaw artifact store")
    try:
        artifacts_dir.relative_to(knowledge_root)
    except ValueError as exc:
        raise ValueError("OpenClaw artifact store escapes the configured knowledge root") from exc
    if artifacts_dir == knowledge_root:
        raise ValueError("OpenClaw artifact store must be a child of the configured knowledge root")
    return artifacts_dir


def _canonical_artifact_path(ctx: MtsToolContext, artifact_id: str) -> tuple[Path, Path]:
    """Return ``(store, artifact_path)`` after ID and canonical confinement checks."""
    safe_id = _validate_artifact_id(artifact_id)
    artifacts_dir = _canonical_artifacts_dir(ctx)
    # Keep the final component lexical. Reads use O_NOFOLLOW and writes use an
    # anchored atomic rename, so resolving it here would turn an in-store
    # symlink into a cross-artifact overwrite primitive.
    artifact_path = artifacts_dir / f"{safe_id}.json"
    try:
        artifact_path.relative_to(artifacts_dir)
    except ValueError as exc:
        raise ValueError("OpenClaw artifact path escapes the artifact store") from exc
    if artifact_path == artifacts_dir:
        raise ValueError("OpenClaw artifact path must name a file")
    if artifact_path.is_symlink():
        raise ValueError("OpenClaw artifact path must not be a symbolic link")
    return artifacts_dir, artifact_path


def _open_artifacts_directory(artifacts_dir: Path) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    return os.open(artifacts_dir, flags)


def _write_artifact_json(artifacts_dir: Path, artifact_path: Path, content: str) -> None:
    """Atomically replace an artifact without following a destination symlink."""
    directory_fd = _open_artifacts_directory(artifacts_dir)
    temp_name = f".{artifact_path.name}.{secrets.token_hex(8)}.tmp"
    temp_exists = False
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        file_fd = os.open(temp_name, flags, 0o600, dir_fd=directory_fd)
        temp_exists = True
        with os.fdopen(file_fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(
            temp_name,
            artifact_path.name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        temp_exists = False
    finally:
        if temp_exists:
            try:
                os.unlink(temp_name, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
        os.close(directory_fd)


def _read_artifact_json(artifacts_dir: Path, artifact_path: Path) -> dict[str, Any]:
    """Read one artifact relative to an anchored directory without following symlinks."""
    directory_fd = _open_artifacts_directory(artifacts_dir)
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        file_fd = os.open(artifact_path.name, flags, dir_fd=directory_fd)
        with os.fdopen(file_fd, encoding="utf-8") as handle:
            value = json.load(handle)
    finally:
        os.close(directory_fd)
    if not isinstance(value, dict):
        raise ValueError("artifact JSON must be an object")
    return value


def _iter_safe_artifact_files(ctx: MtsToolContext) -> list[tuple[Path, Path]]:
    """Return confined artifact files, ignoring unsafe names and symlink escapes."""
    try:
        artifacts_dir = _canonical_artifacts_dir(ctx)
    except ValueError:
        return []
    if not artifacts_dir.is_dir():
        return []

    files: list[tuple[Path, Path]] = []
    try:
        candidates = sorted(artifacts_dir.iterdir())
    except OSError:
        return []
    for candidate in candidates:
        if candidate.suffix != ".json":
            continue
        try:
            safe_id = _validate_artifact_id(candidate.stem)
            confined_dir, confined_path = _canonical_artifact_path(ctx, safe_id)
        except ValueError:
            continue
        files.append((confined_dir, confined_path))
    return files


def evaluate_strategy(
    scenario_name: str,
    strategy: dict[str, Any],
    num_matches: int = 3,
    seed_base: int = 42,
) -> dict[str, Any]:
    """Evaluate a candidate strategy against a scenario by running matches.

    Returns aggregate scores for the strategy across multiple seeds.
    """
    if scenario_name not in SCENARIO_REGISTRY:
        supported = ", ".join(sorted(SCENARIO_REGISTRY.keys()))
        return {"error": f"Unknown scenario '{scenario_name}'. Available: {supported}"}

    scenario = SCENARIO_REGISTRY[scenario_name]()
    if not hasattr(scenario, "execute_match"):
        return {
            "error": (
                f"'{scenario_name}' is an agent task scenario. "
                "Use evaluate_output() for judge-based evaluation."
            )
        }

    scores: list[float] = []
    for i in range(num_matches):
        result = scenario.execute_match(strategy, seed_base + i)
        scores.append(result.score)

    return {
        "scenario": scenario_name,
        "matches": num_matches,
        "scores": scores,
        "mean_score": sum(scores) / len(scores) if scores else 0.0,
        "best_score": max(scores) if scores else 0.0,
    }


def validate_strategy_against_harness(
    scenario_name: str,
    strategy: dict[str, Any],
    ctx: MtsToolContext | None = None,
) -> dict[str, Any]:
    """Validate a strategy against scenario constraints and any harness validators.

    Checks both built-in scenario validation and any published harness artifacts.
    """
    if scenario_name not in SCENARIO_REGISTRY:
        supported = ", ".join(sorted(SCENARIO_REGISTRY.keys()))
        return {"error": f"Unknown scenario '{scenario_name}'. Available: {supported}"}

    scenario = SCENARIO_REGISTRY[scenario_name]()
    if not hasattr(scenario, "validate_actions"):
        return {
            "valid": True,
            "reason": "Agent task scenarios use judge evaluation, not action validation",
        }

    state = scenario.initial_state(seed=42)
    valid, reason = scenario.validate_actions(state, "challenger", strategy)
    harness_loaded: list[str] = []
    harness_errors: list[str] = []
    harness_passed = True

    if valid and ctx is not None and ctx.settings.unsafe_openclaw_executable_artifacts_enabled:
        harness_loaded = _sync_published_harness_artifacts(ctx, scenario_name)
        harness_loader = HarnessLoader(
            ctx.artifacts.harness_dir(scenario_name),
            timeout_seconds=ctx.settings.harness_timeout_seconds,
        )
        harness_loaded = harness_loader.load()
        harness_result = harness_loader.validate_strategy(dict(strategy), scenario)
        harness_passed = harness_result.passed
        harness_errors = harness_result.errors

    return {
        "valid": valid and harness_passed,
        "reason": reason,
        "scenario": scenario_name,
        "harness_loaded": harness_loaded,
        "harness_passed": harness_passed,
        "harness_errors": harness_errors,
    }


def _sync_published_harness_artifacts(ctx: MtsToolContext, scenario_name: str) -> list[str]:
    """Mirror published harness artifacts into the runtime harness directory."""
    synced: list[str] = []
    for artifacts_dir, artifact_path in _iter_safe_artifact_files(ctx):
        try:
            artifact_data = _read_artifact_json(artifacts_dir, artifact_path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if artifact_data.get("artifact_type") != "harness" or artifact_data.get("scenario") != scenario_name:
            continue
        source_code = artifact_data.get("source_code")
        artifact_id = artifact_data.get("id", artifact_path.stem)
        if not isinstance(source_code, str) or not source_code.strip():
            continue
        try:
            safe_id = _validate_artifact_id(artifact_id)
        except ValueError:
            continue
        if safe_id != artifact_path.stem:
            continue
        module_name = f"openclaw_{safe_id.replace('-', '_')}"
        ctx.artifacts.write_harness(scenario_name, module_name, source_code)
        synced.append(module_name)
    return synced


def _validate_and_persist_artifact(
    ctx: MtsToolContext,
    artifact_data: dict[str, Any],
    artifact_type: str,
) -> tuple[str, str]:
    """Validate artifact data and persist to disk. Returns (artifact_id, json_content)."""
    from autocontext.artifacts import DistilledModelArtifact, HarnessArtifact, PolicyArtifact

    validated: HarnessArtifact | PolicyArtifact | DistilledModelArtifact
    if artifact_type == "harness":
        validated = HarnessArtifact.model_validate(artifact_data)
    elif artifact_type == "policy":
        validated = PolicyArtifact.model_validate(artifact_data)
    else:
        validated = DistilledModelArtifact.model_validate(artifact_data)

    artifacts_dir, artifact_path = _canonical_artifact_path(ctx, validated.id)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    # Re-resolve after creation to catch a symlink introduced while the store
    # directory was being established.
    artifacts_dir, artifact_path = _canonical_artifact_path(ctx, validated.id)
    _write_artifact_json(artifacts_dir, artifact_path, validated.model_dump_json(indent=2))
    if isinstance(validated, HarnessArtifact):
        module_name = f"openclaw_{validated.id.replace('-', '_')}"
        ctx.artifacts.write_harness(validated.scenario, module_name, validated.source_code)

    return validated.id, str(artifact_path)


def publish_artifact(
    ctx: MtsToolContext,
    artifact_data: dict[str, Any],
) -> dict[str, Any]:
    """Publish an artifact (harness, policy, or distilled model) to the local store.

    The artifact_data must be a valid serialized artifact dict with an artifact_type field.
    """
    artifact_type = artifact_data.get("artifact_type")
    if artifact_type not in ("harness", "policy", "distilled_model"):
        return {
            "error": (
                f"Invalid or missing artifact_type: {artifact_type!r}. "
                "Must be harness, policy, or distilled_model."
            )
        }

    if (
        artifact_type in ("harness", "policy")
        and not ctx.settings.unsafe_openclaw_executable_artifacts_enabled
    ):
        return {
            "error": (
                "Executable OpenClaw artifacts are disabled because no isolated execution backend is configured. "
                "Set AUTOCONTEXT_UNSAFE_OPENCLAW_EXECUTABLE_ARTIFACTS_ENABLED=true only for trusted local "
                "compatibility use."
            )
        }

    try:
        artifact_id, artifact_path = _validate_and_persist_artifact(ctx, artifact_data, str(artifact_type))
    except Exception as exc:
        logger.debug("mcp.tools: caught Exception", exc_info=True)
        return {"error": f"Invalid artifact data: {exc}"}

    return {
        "status": "published",
        "artifact_id": artifact_id,
        "artifact_type": str(artifact_type),
        "path": artifact_path,
    }


def fetch_artifact(
    ctx: MtsToolContext,
    artifact_id: str,
) -> dict[str, Any]:
    """Fetch a published artifact by its ID."""

    try:
        artifacts_dir, artifact_path = _canonical_artifact_path(ctx, artifact_id)
    except ValueError as exc:
        return {"error": f"Invalid artifact id: {exc}"}
    try:
        return _read_artifact_json(artifacts_dir, artifact_path)
    except FileNotFoundError:
        return {"error": f"Artifact '{artifact_id}' not found"}
    except (OSError, ValueError, json.JSONDecodeError):
        return {"error": f"Artifact '{artifact_id}' is not a safe JSON artifact"}


def list_artifacts(
    ctx: MtsToolContext,
    scenario: str | None = None,
    artifact_type: str | None = None,
) -> list[dict[str, Any]]:
    """List published artifacts, optionally filtered by scenario or type."""

    results: list[dict[str, Any]] = []
    for artifacts_dir, path in _iter_safe_artifact_files(ctx):
        try:
            data = _read_artifact_json(artifacts_dir, path)
            safe_id = _validate_artifact_id(data.get("id", path.stem))
        except (OSError, ValueError, json.JSONDecodeError):
            logger.debug("mcp.tools: caught Exception", exc_info=True)
            continue
        if safe_id != path.stem:
            continue
        if scenario and data.get("scenario") != scenario:
            continue
        if artifact_type and data.get("artifact_type") != artifact_type:
            continue
        results.append({
            "id": safe_id,
            "name": data.get("name", ""),
            "artifact_type": data.get("artifact_type", ""),
            "scenario": data.get("scenario", ""),
            "version": data.get("version", 0),
        })
    return results
