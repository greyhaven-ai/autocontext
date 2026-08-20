"""Transactional filesystem operations for research workspaces."""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Mapping
from pathlib import Path


def snapshot_files(root: Path, max_file_bytes: int) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"workspace snapshots do not follow symbolic links: {path.relative_to(root)}")
        if path.is_file():
            data = path.read_bytes()
            if len(data) > max_file_bytes:
                raise ValueError(f"workspace file exceeds byte limit: {path.relative_to(root)}")
            files[path.relative_to(root).as_posix()] = data
    return files


def restore_files(root: Path, files: Mapping[str, bytes], max_file_bytes: int) -> None:
    staging = Path(tempfile.mkdtemp(prefix=".autocontext-restore-", dir=root.parent)).resolve()
    try:
        for relative, data in files.items():
            if len(data) > max_file_bytes:
                raise ValueError(f"snapshot file exceeds byte limit: {relative}")
            target = (staging / relative).resolve()
            try:
                target.relative_to(staging)
            except ValueError as exc:
                raise ValueError(f"snapshot path escapes workspace root: {relative}") from exc
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        replace_workspace(staging, root, max_file_bytes)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def copy_workspace(source: Path, destination: Path, max_file_bytes: int) -> None:
    for relative, data in snapshot_files(source, max_file_bytes).items():
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def replace_workspace(source: Path, destination: Path, max_file_bytes: int) -> None:
    """Replace ``destination`` and roll back its prior tree on failure.

    The candidate tree is fully validated and materialized next to the live
    workspace before the old tree is moved.  Both renames therefore stay on
    one filesystem.  A failed activation restores the backup before the
    exception is allowed to escape.
    """

    files = snapshot_files(source, max_file_bytes)
    prepared = Path(tempfile.mkdtemp(prefix=".autocontext-commit-", dir=destination.parent)).resolve()
    backup_parent: Path | None = None
    backup: Path | None = None
    activated = False
    rollback_failed = False
    try:
        for relative, data in files.items():
            target = prepared / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        backup_parent = Path(tempfile.mkdtemp(prefix=".autocontext-backup-", dir=destination.parent)).resolve()
        backup = backup_parent / "workspace"
        destination.replace(backup)
        try:
            prepared.replace(destination)
            activated = True
        except BaseException as activation_error:  # noqa: BLE001 - rollback must also cover interrupts
            try:
                backup.replace(destination)
            except BaseException as rollback_error:  # noqa: BLE001 - retain backup for manual recovery
                rollback_failed = True
                raise OSError(
                    f"workspace activation failed and rollback is retained at {backup}"
                ) from rollback_error
            raise activation_error
    finally:
        if not activated and backup is not None and backup.exists() and not destination.exists():
            try:
                backup.replace(destination)
            except OSError:
                rollback_failed = True
        shutil.rmtree(prepared, ignore_errors=True)
        if backup_parent is not None and not rollback_failed:
            shutil.rmtree(backup_parent, ignore_errors=True)


def safe_workspace_id(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in value)
    return cleaned[:48] or "workspace"
