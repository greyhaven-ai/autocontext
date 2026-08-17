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
    files = snapshot_files(source, max_file_bytes)
    for child in destination.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
    for relative, data in files.items():
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def safe_workspace_id(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in value)
    return cleaned[:48] or "workspace"
