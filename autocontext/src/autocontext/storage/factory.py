from __future__ import annotations

from pathlib import Path

from autocontext.config.settings import AppSettings
from autocontext.extensions import HookBus
from autocontext.storage.artifacts import ArtifactStore


def artifact_store_from_settings(
    settings: AppSettings,
    *,
    runs_root: Path | None = None,
    knowledge_root: Path | None = None,
    skills_root: Path | None = None,
    claude_skills_path: Path | None = None,
    enable_buffered_writes: bool = False,
    hook_bus: HookBus | None = None,
) -> ArtifactStore:
    """Build an ArtifactStore from app settings, including blob-store wiring."""
    blob_store = None
    if settings.blob_store_enabled:
        from autocontext.blobstore.factory import create_blob_store

        blob_store = create_blob_store(
            backend=settings.blob_store_backend,
            root=settings.blob_store_root,
            repo_id=settings.blob_store_repo,
        )

    return ArtifactStore(
        runs_root=runs_root or settings.runs_root,
        knowledge_root=knowledge_root or settings.knowledge_root,
        skills_root=skills_root or settings.skills_root,
        claude_skills_path=claude_skills_path or settings.claude_skills_path,
        max_playbook_versions=settings.playbook_max_versions,
        enable_buffered_writes=enable_buffered_writes,
        blob_store=blob_store,
        blob_store_min_size_bytes=settings.blob_store_min_size_bytes,
        hook_bus=hook_bus,
    )
