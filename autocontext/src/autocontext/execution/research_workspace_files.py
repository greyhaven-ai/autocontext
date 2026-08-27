"""Transactional filesystem operations for research workspaces."""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Mapping
from pathlib import Path


def snapshot_files(
    root: Path,
    max_file_bytes: int,
    max_workspace_bytes: int | None = None,
    max_workspace_inodes: int | None = None,
) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    aggregate_bytes = 0
    aggregate_inodes = 0
    for path in sorted(root.rglob("*")):
        aggregate_inodes += 1
        _check_aggregate_quota(aggregate_bytes, aggregate_inodes, max_workspace_bytes, max_workspace_inodes)
        if path.is_symlink():
            raise ValueError(f"workspace snapshots do not follow symbolic links: {path.relative_to(root)}")
        if path.is_file():
            data = path.read_bytes()
            if len(data) > max_file_bytes:
                raise ValueError(f"workspace file exceeds byte limit: {path.relative_to(root)}")
            aggregate_bytes += len(data)
            _check_aggregate_quota(aggregate_bytes, aggregate_inodes, max_workspace_bytes, max_workspace_inodes)
            files[path.relative_to(root).as_posix()] = data
    return files


def restore_files(
    root: Path,
    files: Mapping[str, bytes],
    max_file_bytes: int,
    max_workspace_bytes: int | None = None,
    max_workspace_inodes: int | None = None,
) -> None:
    _validate_file_mapping(files, max_file_bytes, max_workspace_bytes, max_workspace_inodes)
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
        replace_workspace(staging, root, max_file_bytes, max_workspace_bytes, max_workspace_inodes)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def copy_workspace(
    source: Path,
    destination: Path,
    max_file_bytes: int,
    max_workspace_bytes: int | None = None,
    max_workspace_inodes: int | None = None,
) -> None:
    for relative, data in snapshot_files(
        source,
        max_file_bytes,
        max_workspace_bytes,
        max_workspace_inodes,
    ).items():
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def replace_workspace(
    source: Path,
    destination: Path,
    max_file_bytes: int,
    max_workspace_bytes: int | None = None,
    max_workspace_inodes: int | None = None,
) -> None:
    """Replace ``destination`` and roll back its prior tree on failure.

    The candidate tree is fully validated and materialized next to the live
    workspace before the old tree is moved.  Both renames therefore stay on
    one filesystem.  A failed activation restores the backup before the
    exception is allowed to escape.
    """

    files = snapshot_files(source, max_file_bytes, max_workspace_bytes, max_workspace_inodes)
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


def _validate_file_mapping(
    files: Mapping[str, bytes],
    max_file_bytes: int,
    max_workspace_bytes: int | None,
    max_workspace_inodes: int | None,
) -> None:
    aggregate_bytes = 0
    paths: set[str] = set()
    for relative, data in files.items():
        if not isinstance(relative, str) or not relative or not isinstance(data, bytes):
            raise ValueError("workspace file mappings require non-empty string paths and bytes values")
        relative_path = Path(relative)
        if relative_path == Path(".") or relative_path.is_absolute() or ".." in relative_path.parts:
            raise ValueError(f"snapshot path escapes workspace root: {relative}")
        if len(data) > max_file_bytes:
            raise ValueError(f"snapshot file exceeds byte limit: {relative}")
        aggregate_bytes += len(data)
        normalized = relative_path.as_posix()
        paths.add(normalized)
        parent = relative_path.parent
        while parent != Path("."):
            paths.add(parent.as_posix())
            parent = parent.parent
    _check_aggregate_quota(aggregate_bytes, len(paths), max_workspace_bytes, max_workspace_inodes)


def _check_aggregate_quota(
    aggregate_bytes: int,
    aggregate_inodes: int,
    max_workspace_bytes: int | None,
    max_workspace_inodes: int | None,
) -> None:
    if max_workspace_bytes is not None and aggregate_bytes > max_workspace_bytes:
        raise ValueError("workspace exceeds aggregate byte quota")
    if max_workspace_inodes is not None and aggregate_inodes > max_workspace_inodes:
        raise ValueError("workspace exceeds aggregate inode quota")


def safe_workspace_id(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in value)
    return cleaned[:48] or "workspace"
