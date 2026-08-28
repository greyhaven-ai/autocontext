"""Confined, no-follow file operations for security-sensitive workspace data."""

from __future__ import annotations

import os
import secrets
import stat
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from autocontext.security import _confined_path_helpers as _path_helpers

confined_segment_is_valid = _path_helpers.confined_segment_is_valid
_path_is_within = _path_helpers.path_is_within
_paths_equal = _path_helpers.paths_equal
_stat_identity = _path_helpers.stat_identity
_stat_is_reparse_point = _path_helpers.stat_is_reparse_point


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
    if not _descriptor_anchoring_supported():
        return _read_confined_bytes_portable(root, directory_parts, filename, max_bytes=max_bytes)
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
    if not _descriptor_anchoring_supported():
        _atomic_write_confined_bytes_portable(
            root,
            directory_parts,
            filename,
            content,
        )
        return

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
    if not _descriptor_anchoring_supported():
        _unlink_confined_file_portable(root, directory_parts, filename)
        return
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
    if not suffix or "/" in suffix or "\\" in suffix or max_entries < 1:
        raise ValueError("invalid confined listing limit")
    if not _descriptor_anchoring_supported():
        return _list_confined_regular_files_portable(
            root,
            directory_parts,
            suffix=suffix,
            max_entries=max_entries,
        )
    with _open_directory(root, directory_parts, create=False) as directory_fd:
        result: list[str] = []
        with os.scandir(directory_fd) as entries:
            for entry_count, entry in enumerate(entries, start=1):
                if entry_count > max_entries:
                    raise ConfinedFileTooLarge(
                        "confined directory exceeds its entry limit"
                    )
                name = entry.name
                if not name.endswith(suffix):
                    continue
                try:
                    _validate_filename(name)
                    entry_stat = entry.stat(follow_symlinks=False)
                except (ConfinedPathError, FileNotFoundError):
                    continue
                if stat.S_ISREG(entry_stat.st_mode):
                    result.append(name)
        return sorted(result)


@contextmanager
def _open_directory(root: Path, parts: Sequence[str], *, create: bool) -> Iterator[int]:
    for part in parts:
        _validate_segment(part)
    if create:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        root_stat = os.lstat(root)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise ConfinedPathError("confined root is unavailable") from exc
    if not stat.S_ISDIR(root_stat.st_mode) or _stat_is_reparse_point(root_stat):
        raise ConfinedPathError("confined root is unsafe")

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        current_fd = os.open(root, flags)
    except OSError as exc:
        raise ConfinedPathError("confined root is unsafe") from exc
    try:
        opened_root_stat = os.fstat(current_fd)
        if (
            not stat.S_ISDIR(opened_root_stat.st_mode)
            or _stat_identity(opened_root_stat) != _stat_identity(root_stat)
        ):
            raise ConfinedPathError("confined root changed during open")
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
            try:
                os.close(current_fd)
            except BaseException:
                try:
                    os.close(next_fd)
                finally:
                    raise
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


def _descriptor_anchoring_supported() -> bool:
    required_dir_fd = (os.open, os.mkdir, os.stat, os.unlink, os.rename)
    return (
        bool(getattr(os, "O_NOFOLLOW", 0))
        and bool(getattr(os, "O_DIRECTORY", 0))
        and all(operation in os.supports_dir_fd for operation in required_dir_fd)
        and os.stat in os.supports_follow_symlinks
        and os.scandir in os.supports_fd
    )


@dataclass(frozen=True, slots=True)
class _PortableDirectoryGuard:
    root: Path
    parts: tuple[str, ...]
    resolved_root: Path
    directory: Path
    identities: tuple[tuple[Path, tuple[int, int]], ...]

    def verify(self) -> None:
        """Verify every pinned directory entry still names the same directory."""
        current_root, current_directory = _portable_directory(self.root, self.parts, create=False)
        if not _paths_equal(current_root, self.resolved_root) or not _paths_equal(current_directory, self.directory):
            raise ConfinedPathError("confined directory changed during mutation")
        for path, expected_identity in self.identities:
            try:
                path_stat = os.lstat(path)
            except OSError as exc:
                raise ConfinedPathError("confined directory changed during operation") from exc
            if (
                not stat.S_ISDIR(path_stat.st_mode)
                or _stat_is_reparse_point(path_stat)
                or _stat_identity(path_stat) != expected_identity
            ):
                raise ConfinedPathError("confined directory changed during operation")


@contextmanager
def _stable_portable_directory(
    root: Path,
    parts: Sequence[str],
    *,
    create: bool,
) -> Iterator[_PortableDirectoryGuard]:
    """Pin a Windows directory chain against rename/delete for one operation."""
    if not _windows_directory_guards_available():
        raise ConfinedPathError("portable confined operations require stable Windows directory handles")
    for part in parts:
        _validate_segment(part)
    if create:
        try:
            root.mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError as exc:
            raise ConfinedPathError("confined root is unavailable") from exc
    try:
        unresolved_root_stat = os.lstat(root)
        if not stat.S_ISDIR(unresolved_root_stat.st_mode) or _stat_is_reparse_point(
            unresolved_root_stat
        ):
            raise ConfinedPathError("confined root is unsafe")
    except FileNotFoundError:
        raise
    except ConfinedPathError:
        raise
    except (OSError, RuntimeError) as exc:
        raise ConfinedPathError("confined root is unavailable") from exc
    handles: list[int] = []
    chain: list[Path] = []
    try:
        handles.append(_windows_open_stable_directory(root, unresolved_root_stat))
        if not _path_helpers.stable_directory_identity(root, unresolved_root_stat):
            raise ConfinedPathError("confined root changed while it was pinned")
        resolved_root = root.resolve(strict=True)
        chain.append(resolved_root)
        current = resolved_root
        for part in parts:
            candidate = current / part
            if create:
                try:
                    candidate.mkdir(mode=0o700)
                except FileExistsError:
                    pass
                except OSError as exc:
                    raise ConfinedPathError("confined directory component is unavailable") from exc
            try:
                component_stat = os.lstat(candidate)
            except FileNotFoundError:
                raise
            except OSError as exc:
                raise ConfinedPathError("confined directory component is unavailable") from exc
            if not stat.S_ISDIR(component_stat.st_mode) or _stat_is_reparse_point(component_stat):
                raise ConfinedPathError("confined directory component is unsafe")
            handles.append(_windows_open_stable_directory(candidate, component_stat))
            if not _path_helpers.stable_directory_identity(candidate, component_stat):
                raise ConfinedPathError("confined directory component changed while it was pinned")
            try:
                resolved_candidate = candidate.resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                raise ConfinedPathError("confined directory component is unsafe") from exc
            if not _path_is_within(resolved_candidate, resolved_root):
                raise ConfinedPathError("confined directory component escaped its root")
            chain.append(resolved_candidate)
            current = resolved_candidate

        identities = tuple((path, _stat_identity(os.lstat(path))) for path in chain)
        guard = _PortableDirectoryGuard(
            root=root,
            parts=tuple(parts),
            resolved_root=resolved_root,
            directory=current,
            identities=identities,
        )
        guard.verify()
        yield guard
    finally:
        for handle in reversed(handles):
            _windows_close_handle(handle)


def _windows_directory_guards_available() -> bool:
    return os.name == "nt"


def _windows_open_stable_directory(path: Path, expected_stat: os.stat_result) -> int:
    """Open a verified directory handle while deliberately denying delete sharing."""
    try:
        import ctypes
        from ctypes import wintypes

        windows_ctypes = cast(Any, ctypes)
        kernel32 = windows_ctypes.WinDLL("kernel32", use_last_error=True)
        create_file = kernel32.CreateFileW
        create_file.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        create_file.restype = wintypes.HANDLE
        handle = create_file(
            _windows_extended_path(path),
            0x0001 | 0x0080,  # FILE_LIST_DIRECTORY | FILE_READ_ATTRIBUTES
            0x0001 | 0x0002,  # FILE_SHARE_READ | FILE_SHARE_WRITE; intentionally no FILE_SHARE_DELETE
            None,
            3,  # OPEN_EXISTING
            0x02000000 | 0x00200000,  # BACKUP_SEMANTICS | OPEN_REPARSE_POINT
            None,
        )
        if handle == windows_ctypes.c_void_p(-1).value:
            raise windows_ctypes.WinError(windows_ctypes.get_last_error())
    except OSError as exc:
        raise ConfinedPathError("could not pin confined directory against replacement") from exc

    try:
        attributes = _windows_handle_attributes(handle)
        if not attributes & 0x0010 or attributes & 0x0400:  # DIRECTORY / REPARSE_POINT
            raise ConfinedPathError("confined directory handle is unsafe")
        if _path_helpers.windows_handle_identity(handle) != _stat_identity(expected_stat):
            raise ConfinedPathError("confined directory handle has an unexpected identity")
        resolved_path = path.resolve(strict=True)
        final_path = _windows_final_path_from_handle(handle)
        if not _paths_equal(final_path, resolved_path):
            raise ConfinedPathError("confined directory handle resolved to an unexpected path")
        return int(handle)
    except BaseException:
        _windows_close_handle(int(handle))
        raise


def _windows_handle_attributes(handle: int) -> int:
    try:
        import ctypes
        from ctypes import wintypes

        class FileAttributeTagInfo(ctypes.Structure):
            _fields_ = [("file_attributes", wintypes.DWORD), ("reparse_tag", wintypes.DWORD)]

        windows_ctypes = cast(Any, ctypes)
        get_information = windows_ctypes.WinDLL(
            "kernel32",
            use_last_error=True,
        ).GetFileInformationByHandleEx
        get_information.argtypes = [wintypes.HANDLE, wintypes.INT, wintypes.LPVOID, wintypes.DWORD]
        get_information.restype = wintypes.BOOL
        information = FileAttributeTagInfo()
        if not get_information(handle, 9, ctypes.byref(information), ctypes.sizeof(information)):
            raise windows_ctypes.WinError(windows_ctypes.get_last_error())
        return int(information.file_attributes)
    except OSError as exc:
        raise ConfinedPathError("could not inspect confined directory handle") from exc


def _windows_close_handle(handle: int) -> None:
    import ctypes
    from ctypes import wintypes

    windows_ctypes = cast(Any, ctypes)
    close_handle = windows_ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    close_handle(handle)


def _windows_extended_path(path: Path) -> str:
    raw_path = os.path.abspath(os.fspath(path))
    if raw_path.startswith("\\\\?\\"):
        return raw_path
    if raw_path.startswith("\\\\"):
        return f"\\\\?\\UNC\\{raw_path[2:]}"
    return f"\\\\?\\{raw_path}"


def _read_confined_bytes_portable(
    root: Path,
    directory_parts: Sequence[str],
    filename: str,
    *,
    max_bytes: int,
) -> bytes | None:
    try:
        with _stable_portable_directory(root, directory_parts, create=False) as guard:
            target = guard.directory / filename
            try:
                _reject_unsafe_portable_target(target)
                expected_identity = _stat_identity(os.lstat(target))
            except FileNotFoundError:
                return None
            guard.verify()

            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            try:
                file_fd = os.open(target, flags)
            except FileNotFoundError:
                return None
            except OSError as exc:
                raise ConfinedPathError("confined file target is unsafe") from exc
            try:
                file_stat = os.fstat(file_fd)
                if not stat.S_ISREG(file_stat.st_mode) or _stat_is_reparse_point(file_stat):
                    raise ConfinedPathError("confined target is not a regular file")
                if _stat_identity(file_stat) != expected_identity:
                    raise ConfinedPathError("confined target changed before it was opened")
                _assert_open_file_confined(file_fd, guard.resolved_root, target)
                guard.verify()
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


def _atomic_write_confined_bytes_portable(
    root: Path,
    directory_parts: Sequence[str],
    filename: str,
    content: bytes,
) -> None:
    with _stable_portable_directory(root, directory_parts, create=True) as guard:
        resolved_root = guard.resolved_root
        directory = guard.directory
        target = directory / filename
        try:
            _reject_unsafe_portable_target(target)
        except FileNotFoundError:
            pass

        temp_path = directory / f".{filename}.{secrets.token_hex(8)}.tmp"
        temp_exists = False
        temp_identity: tuple[int, int] | None = None
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
            flags |= getattr(os, "O_NOINHERIT", 0) | getattr(os, "O_NOFOLLOW", 0)
            try:
                file_fd = os.open(temp_path, flags, 0o600)
            except OSError as exc:
                raise ConfinedPathError("confined temporary file is unsafe") from exc
            temp_exists = True
            try:
                _assert_open_file_confined(file_fd, resolved_root, temp_path)
                temp_stat = os.fstat(file_fd)
                if not stat.S_ISREG(temp_stat.st_mode) or _stat_is_reparse_point(temp_stat):
                    raise ConfinedPathError("confined temporary file is unsafe")
                temp_identity = _stat_identity(temp_stat)
                with os.fdopen(file_fd, "wb", closefd=False) as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
            finally:
                os.close(file_fd)

            guard.verify()
            try:
                _reject_unsafe_portable_target(target)
            except FileNotFoundError:
                pass
            replacement_stat = os.lstat(temp_path)
            if (
                not stat.S_ISREG(replacement_stat.st_mode)
                or _stat_is_reparse_point(replacement_stat)
                or _stat_identity(replacement_stat) != temp_identity
            ):
                raise ConfinedPathError("confined temporary file changed before replacement")
            guard.verify()
            os.replace(temp_path, target)
            temp_exists = False
            _fsync_portable_directory(directory)
        finally:
            if temp_exists:
                try:
                    guard.verify()
                    cleanup_stat = os.lstat(temp_path)
                    if (
                        stat.S_ISREG(cleanup_stat.st_mode)
                        and not _stat_is_reparse_point(cleanup_stat)
                        and _stat_identity(cleanup_stat) == temp_identity
                    ):
                        temp_path.unlink()
                except OSError:
                    pass


def _unlink_confined_file_portable(root: Path, directory_parts: Sequence[str], filename: str) -> None:
    with _stable_portable_directory(root, directory_parts, create=False) as guard:
        target = guard.directory / filename
        try:
            target_stat = os.lstat(target)
        except FileNotFoundError:
            return
        if not stat.S_ISREG(target_stat.st_mode) or _stat_is_reparse_point(target_stat):
            raise ConfinedPathError("confined target must be a regular non-reparse file")
        target_identity = _stat_identity(target_stat)
        guard.verify()
        verified_stat = os.lstat(target)
        if (
            not stat.S_ISREG(verified_stat.st_mode)
            or _stat_is_reparse_point(verified_stat)
            or _stat_identity(verified_stat) != target_identity
        ):
            raise ConfinedPathError("confined target changed before unlink")
        guard.verify()
        target.unlink()
        _fsync_portable_directory(guard.directory)


def _list_confined_regular_files_portable(
    root: Path,
    directory_parts: Sequence[str],
    *,
    suffix: str,
    max_entries: int,
) -> list[str]:
    with _stable_portable_directory(root, directory_parts, create=False) as guard:
        result: list[str] = []
        with os.scandir(guard.directory) as entries:
            for entry_count, entry in enumerate(entries, start=1):
                if entry_count > max_entries:
                    raise ConfinedFileTooLarge(
                        "confined directory exceeds its entry limit"
                    )
                name = entry.name
                if not name.endswith(suffix):
                    continue
                try:
                    _validate_filename(name)
                    entry_stat = entry.stat(follow_symlinks=False)
                except (ConfinedPathError, FileNotFoundError):
                    continue
                if stat.S_ISREG(entry_stat.st_mode) and not _stat_is_reparse_point(
                    entry_stat
                ):
                    result.append(name)
        guard.verify()
        return sorted(result)


def _portable_directory(root: Path, parts: Sequence[str], *, create: bool) -> tuple[Path, Path]:
    for part in parts:
        _validate_segment(part)
    if create:
        try:
            root.mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError as exc:
            raise ConfinedPathError("confined root is unavailable") from exc
    try:
        unresolved_root_stat = os.lstat(root)
        if not stat.S_ISDIR(unresolved_root_stat.st_mode) or _stat_is_reparse_point(
            unresolved_root_stat
        ):
            raise ConfinedPathError("confined root is unsafe")
        resolved_root = root.resolve(strict=True)
        root_stat = resolved_root.stat()
    except FileNotFoundError:
        raise
    except ConfinedPathError:
        raise
    except (OSError, RuntimeError) as exc:
        raise ConfinedPathError("confined root is unavailable") from exc
    if not stat.S_ISDIR(root_stat.st_mode):
        raise ConfinedPathError("confined root is not a directory")

    current = resolved_root
    for part in parts:
        candidate = current / part
        if create:
            try:
                candidate.mkdir(mode=0o700)
            except FileExistsError:
                pass
            except OSError as exc:
                raise ConfinedPathError("confined directory component is unavailable") from exc
        try:
            component_stat = os.lstat(candidate)
        except FileNotFoundError:
            raise
        except OSError as exc:
            raise ConfinedPathError("confined directory component is unavailable") from exc
        if not stat.S_ISDIR(component_stat.st_mode) or _stat_is_reparse_point(component_stat):
            raise ConfinedPathError("confined directory component is unsafe")
        try:
            resolved_candidate = candidate.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ConfinedPathError("confined directory component is unsafe") from exc
        if not _path_is_within(resolved_candidate, resolved_root):
            raise ConfinedPathError("confined directory component escaped its root")
        try:
            verified_stat = os.lstat(candidate)
        except OSError as exc:
            raise ConfinedPathError("confined directory component changed during validation") from exc
        if (
            not stat.S_ISDIR(verified_stat.st_mode)
            or _stat_is_reparse_point(verified_stat)
            or (verified_stat.st_dev, verified_stat.st_ino) != (component_stat.st_dev, component_stat.st_ino)
        ):
            raise ConfinedPathError("confined directory component changed during validation")
        current = resolved_candidate
    return resolved_root, current


def _reject_unsafe_portable_target(target: Path) -> None:
    try:
        target_stat = os.lstat(target)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise ConfinedPathError("confined file target is unsafe") from exc
    if not stat.S_ISREG(target_stat.st_mode) or _stat_is_reparse_point(target_stat):
        raise ConfinedPathError("confined target is not a regular file")


def _assert_open_file_confined(file_fd: int, root: Path, expected_path: Path) -> None:
    if os.name != "nt":
        return
    final_path = _windows_final_path(file_fd)
    if not _path_is_within(final_path, root) or not _paths_equal(final_path, expected_path):
        raise ConfinedPathError("opened confined file escaped its expected path")


def _windows_final_path(file_fd: int) -> Path:
    try:
        import msvcrt

        windows_msvcrt = cast(Any, msvcrt)
        return _windows_final_path_from_handle(windows_msvcrt.get_osfhandle(file_fd))
    except OSError as exc:
        raise ConfinedPathError("could not verify the opened confined file") from exc


def _windows_final_path_from_handle(handle: int) -> Path:
    try:
        import ctypes
        from ctypes import wintypes

        windows_ctypes = cast(Any, ctypes)
        get_final_path = windows_ctypes.WinDLL("kernel32", use_last_error=True).GetFinalPathNameByHandleW
        get_final_path.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
        get_final_path.restype = wintypes.DWORD
        capacity = 32_768
        buffer = ctypes.create_unicode_buffer(capacity)
        length = get_final_path(handle, buffer, capacity, 0)
        if length == 0:
            raise windows_ctypes.WinError(windows_ctypes.get_last_error())
        if length >= capacity:
            capacity = length + 1
            buffer = ctypes.create_unicode_buffer(capacity)
            length = get_final_path(handle, buffer, capacity, 0)
            if length == 0 or length >= capacity:
                raise windows_ctypes.WinError(windows_ctypes.get_last_error())
        raw_path = buffer.value
    except OSError as exc:
        raise ConfinedPathError("could not verify the opened confined file") from exc
    if raw_path.startswith("\\\\?\\UNC\\"):
        raw_path = f"\\\\{raw_path[8:]}"
    elif raw_path.startswith("\\\\?\\"):
        raw_path = raw_path[4:]
    return Path(raw_path)


def _fsync_portable_directory(directory: Path) -> None:
    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0)
    directory_fd = os.open(directory, flags)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _validate_segment(segment: str) -> None:
    if not confined_segment_is_valid(segment, windows=os.name == "nt"):
        raise ConfinedPathError("invalid confined directory segment")


def _validate_filename(filename: str) -> None:
    _validate_segment(filename)
