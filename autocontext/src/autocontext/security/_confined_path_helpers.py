"""Pure path checks shared by confined filesystem implementations."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from typing import Any, cast

_WINDOWS_RESERVED_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "CLOCK$",
        "CONIN$",
        "CONOUT$",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
        *(f"COM{index}" for index in ("¹", "²", "³")),
        *(f"LPT{index}" for index in ("¹", "²", "³")),
    }
)


def confined_segment_is_valid(segment: str, *, windows: bool) -> bool:
    """Return whether one untrusted component is safe to use as a basename."""
    if (
        not segment
        or segment in {".", ".."}
        or Path(segment).name != segment
        or "\x00" in segment
        or "/" in segment
        or "\\" in segment
    ):
        return False
    if not windows:
        return True
    normalized = segment.rstrip(" .")
    reserved_stem = normalized.partition(".")[0].upper()
    return not (
        normalized != segment
        or any(character in '<>:"|?*' for character in segment)
        or any(ord(character) < 32 for character in segment)
        or reserved_stem in _WINDOWS_RESERVED_NAMES
    )


def stat_is_reparse_point(path_stat: os.stat_result) -> bool:
    if stat.S_ISLNK(path_stat.st_mode):
        return True
    attributes = getattr(path_stat, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


def stat_identity(path_stat: os.stat_result) -> tuple[int, int]:
    return path_stat.st_dev, path_stat.st_ino


def stable_directory_identity(path: Path, expected_stat: os.stat_result) -> bool:
    """Return whether a just-pinned path still names the expected safe directory."""
    try:
        current_stat = os.lstat(path)
    except OSError:
        return False
    return (
        stat.S_ISDIR(current_stat.st_mode)
        and not stat_is_reparse_point(current_stat)
        and stat_identity(current_stat) == stat_identity(expected_stat)
    )


def windows_handle_identity(handle: int) -> tuple[int, int]:
    """Return the volume and file IDs used by this Python's Windows stat."""
    try:
        import ctypes
        from ctypes import wintypes

        windows_ctypes = cast(Any, ctypes)
        kernel32 = windows_ctypes.WinDLL("kernel32", use_last_error=True)
        if sys.version_info >= (3, 12):

            class FileId128(ctypes.Structure):
                _fields_ = [("identifier", ctypes.c_ubyte * 16)]

            class FileIdInfo(ctypes.Structure):
                _fields_ = [
                    ("volume_serial_number", ctypes.c_ulonglong),
                    ("file_id", FileId128),
                ]

            get_information_ex = kernel32.GetFileInformationByHandleEx
            get_information_ex.argtypes = [
                wintypes.HANDLE,
                wintypes.INT,
                wintypes.LPVOID,
                wintypes.DWORD,
            ]
            get_information_ex.restype = wintypes.BOOL
            information_ex = FileIdInfo()
            if get_information_ex(
                handle,
                18,  # FileIdInfo
                ctypes.byref(information_ex),
                ctypes.sizeof(information_ex),
            ):
                file_id = int.from_bytes(bytes(information_ex.file_id.identifier), "little")
                return int(information_ex.volume_serial_number), file_id

        class ByHandleFileInformation(ctypes.Structure):
            _fields_ = [
                ("file_attributes", wintypes.DWORD),
                ("creation_time", wintypes.FILETIME),
                ("last_access_time", wintypes.FILETIME),
                ("last_write_time", wintypes.FILETIME),
                ("volume_serial_number", wintypes.DWORD),
                ("file_size_high", wintypes.DWORD),
                ("file_size_low", wintypes.DWORD),
                ("number_of_links", wintypes.DWORD),
                ("file_index_high", wintypes.DWORD),
                ("file_index_low", wintypes.DWORD),
            ]

        get_information = kernel32.GetFileInformationByHandle
        get_information.argtypes = [wintypes.HANDLE, wintypes.LPVOID]
        get_information.restype = wintypes.BOOL
        information = ByHandleFileInformation()
        if not get_information(handle, ctypes.byref(information)):
            raise windows_ctypes.WinError(windows_ctypes.get_last_error())
        file_id = (int(information.file_index_high) << 32) | int(information.file_index_low)
        return int(information.volume_serial_number), file_id
    except OSError:
        return -1, -1


def path_is_within(candidate: Path, root: Path) -> bool:
    normalized_candidate = os.path.normcase(os.path.abspath(os.fspath(candidate)))
    normalized_root = os.path.normcase(os.path.abspath(os.fspath(root)))
    try:
        return os.path.commonpath((normalized_candidate, normalized_root)) == normalized_root
    except ValueError:
        return False


def paths_equal(left: Path, right: Path) -> bool:
    return os.path.normcase(os.path.abspath(os.fspath(left))) == os.path.normcase(os.path.abspath(os.fspath(right)))
