"""Descriptor-anchored file operations for security-sensitive workspace data."""

from __future__ import annotations

import os
import secrets
import stat
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path


class ConfinedPathError(OSError):
    """A path component or target is unsafe for confined file access."""


class ConfinedFileTooLarge(ConfinedPathError):
    """A confined file crossed its configured byte limit."""


def read_confined_text(
    root: Path,
    directory_parts: Sequence[str],
    filename: str,
    *,
    max_bytes: int,
) -> str | None:
    """Read a bounded regular file without following directory or file symlinks."""
    raw = read_confined_bytes(root, directory_parts, filename, max_bytes=max_bytes)
    if raw is None:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ConfinedPathError("confined file is not valid UTF-8") from exc


def read_confined_bytes(
    root: Path,
    directory_parts: Sequence[str],
    filename: str,
    *,
    max_bytes: int,
) -> bytes | None:
    """Read bounded bytes from a regular file through a no-follow directory chain."""
    _validate_filename(filename)
    if max_bytes < 0:
        raise ValueError("confined file byte limit must be non-negative")
    try:
        with _open_directory(root, directory_parts, create=False) as directory_fd:
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            try:
                file_fd = os.open(filename, flags, dir_fd=directory_fd)
            except FileNotFoundError:
                return None
            except OSError as exc:
                raise ConfinedPathError("confined file target is unsafe") from exc
            try:
                file_stat = os.fstat(file_fd)
                if not stat.S_ISREG(file_stat.st_mode):
                    raise ConfinedPathError("confined target is not a regular file")
                if file_stat.st_size > max_bytes:
                    raise ConfinedFileTooLarge("confined file exceeds its byte limit")
                with os.fdopen(file_fd, "rb", closefd=False) as handle:
                    raw = handle.read(max_bytes + 1)
            finally:
                os.close(file_fd)
    except FileNotFoundError:
        return None
    if len(raw) > max_bytes:
        raise ConfinedFileTooLarge("confined file exceeds its byte limit")
    return raw


def atomic_write_confined_text(
    root: Path,
    directory_parts: Sequence[str],
    filename: str,
    content: str,
    *,
    max_bytes: int,
) -> None:
    """Atomically replace a bounded file relative to a no-follow directory FD."""
    encoded = content.encode("utf-8")
    atomic_write_confined_bytes(
        root,
        directory_parts,
        filename,
        encoded,
        max_bytes=max_bytes,
    )


def atomic_write_confined_bytes(
    root: Path,
    directory_parts: Sequence[str],
    filename: str,
    content: bytes,
    *,
    max_bytes: int,
) -> None:
    """Atomically replace a bounded binary file through a no-follow directory chain."""
    _validate_filename(filename)
    if max_bytes < 0:
        raise ValueError("confined file byte limit must be non-negative")
    if len(content) > max_bytes:
        raise ConfinedFileTooLarge("confined file exceeds its byte limit")

    with _open_directory(root, directory_parts, create=True) as directory_fd:
        _reject_unsafe_existing_target(directory_fd, filename)
        temp_name = f".{filename}.{secrets.token_hex(8)}.tmp"
        temp_exists = False
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            file_fd = os.open(temp_name, flags, 0o600, dir_fd=directory_fd)
            temp_exists = True
            with os.fdopen(file_fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            _reject_unsafe_existing_target(directory_fd, filename)
            os.replace(temp_name, filename, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
            temp_exists = False
            os.fsync(directory_fd)
        finally:
            if temp_exists:
                try:
                    os.unlink(temp_name, dir_fd=directory_fd)
                except FileNotFoundError:
                    pass


def unlink_confined_file(root: Path, directory_parts: Sequence[str], filename: str) -> None:
    """Unlink one non-directory entry without following it."""
    _validate_filename(filename)
    with _open_directory(root, directory_parts, create=False) as directory_fd:
        try:
            target_stat = os.stat(filename, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        if stat.S_ISDIR(target_stat.st_mode):
            raise ConfinedPathError("confined target must not be a directory")
        os.unlink(filename, dir_fd=directory_fd)
        os.fsync(directory_fd)


def list_confined_regular_files(
    root: Path,
    directory_parts: Sequence[str],
    *,
    suffix: str,
    max_entries: int,
) -> list[str]:
    """List bounded regular, non-symlink files in a confined directory."""
    if not suffix or "/" in suffix or max_entries < 1:
        raise ValueError("invalid confined listing limit")
    with _open_directory(root, directory_parts, create=False) as directory_fd:
        names = os.listdir(directory_fd)
        if len(names) > max_entries:
            raise ConfinedFileTooLarge("confined directory exceeds its entry limit")
        result: list[str] = []
        for name in sorted(names):
            if not name.endswith(suffix):
                continue
            try:
                _validate_filename(name)
                entry_stat = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            except (ConfinedPathError, FileNotFoundError):
                continue
            if stat.S_ISREG(entry_stat.st_mode):
                result.append(name)
        return result


@contextmanager
def _open_directory(root: Path, parts: Sequence[str], *, create: bool) -> Iterator[int]:
    for part in parts:
        _validate_segment(part)
    if create:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        resolved_root = root.resolve(strict=True)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise ConfinedPathError("confined root is unavailable") from exc

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        current_fd = os.open(resolved_root, flags)
    except OSError as exc:
        raise ConfinedPathError("confined root is unsafe") from exc
    try:
        for part in parts:
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=current_fd)
                except FileExistsError:
                    pass
            try:
                next_fd = os.open(part, flags, dir_fd=current_fd)
            except FileNotFoundError:
                raise
            except OSError as exc:
                raise ConfinedPathError("confined directory component is unsafe") from exc
            os.close(current_fd)
            current_fd = next_fd
        yield current_fd
    finally:
        os.close(current_fd)


def _reject_unsafe_existing_target(directory_fd: int, filename: str) -> None:
    try:
        target_stat = os.stat(filename, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if not stat.S_ISREG(target_stat.st_mode):
        raise ConfinedPathError("confined target is not a regular file")


def _validate_segment(segment: str) -> None:
    if not segment or segment in {".", ".."} or Path(segment).name != segment:
        raise ConfinedPathError("invalid confined directory segment")
    if os.altsep and os.altsep in segment:
        raise ConfinedPathError("invalid confined directory segment")


def _validate_filename(filename: str) -> None:
    _validate_segment(filename)
