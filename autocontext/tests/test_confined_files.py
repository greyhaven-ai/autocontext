"""Cross-platform contracts for confined storage operations."""

from __future__ import annotations

import os
import stat
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from autocontext.security import confined_files
from autocontext.security.confined_files import (
    ConfinedFileTooLarge,
    ConfinedPathError,
    atomic_write_confined_bytes,
    atomic_write_confined_text,
    list_confined_regular_files,
    read_confined_bytes,
    read_confined_text,
    unlink_confined_file,
)


@pytest.fixture()
def portable_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise portable algorithms with an emulated stable guard on POSIX."""
    monkeypatch.setattr(confined_files, "_descriptor_anchoring_supported", lambda: False)
    monkeypatch.setattr(confined_files, "_stable_portable_directory", _emulated_stable_directory)


def _make_test_guard(
    root: Path,
    parts: Sequence[str],
    *,
    create: bool,
) -> confined_files._PortableDirectoryGuard:
    resolved_root, directory = confined_files._portable_directory(root, parts, create=create)
    chain = [resolved_root]
    current = resolved_root
    for part in parts:
        current = (current / part).resolve(strict=True)
        chain.append(current)
    return confined_files._PortableDirectoryGuard(
        root=root,
        parts=tuple(parts),
        resolved_root=resolved_root,
        directory=directory,
        identities=tuple(
            (path, confined_files._stat_identity(path.stat(follow_symlinks=False)))
            for path in chain
        ),
    )


@contextmanager
def _emulated_stable_directory(
    root: Path,
    parts: Sequence[str],
    *,
    create: bool,
) -> Iterator[confined_files._PortableDirectoryGuard]:
    yield _make_test_guard(root, parts, create=create)


def test_portable_confined_storage_round_trip(portable_path: None, tmp_path: Path) -> None:
    root = tmp_path / "knowledge"
    parts = ("grid_ctf", "harness")

    atomic_write_confined_text(root, parts, "validator.py", "first", max_bytes=32)
    atomic_write_confined_text(root, parts, "validator.py", "second", max_bytes=32)
    atomic_write_confined_bytes(root, parts, "fixture.bin", b"\x00\x01", max_bytes=32)

    assert read_confined_text(root, parts, "validator.py", max_bytes=32) == "second"
    assert read_confined_bytes(root, parts, "fixture.bin", max_bytes=32) == b"\x00\x01"
    assert list_confined_regular_files(root, parts, suffix=".py", max_entries=8) == ["validator.py"]

    unlink_confined_file(root, parts, "validator.py")
    assert read_confined_text(root, parts, "validator.py", max_bytes=32) is None
    assert not list((root / "grid_ctf" / "harness").glob("*.tmp"))


def test_portable_mutations_fail_closed_without_stable_directory_handles(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    if confined_files._windows_directory_guards_available():
        pytest.skip("native Windows directory guards are available")
    monkeypatch.setattr(confined_files, "_descriptor_anchoring_supported", lambda: False)

    with pytest.raises(ConfinedPathError, match="require stable Windows directory handles"):
        atomic_write_confined_text(tmp_path / "knowledge", ("grid_ctf",), "playbook.md", "safe", max_bytes=32)


def test_all_portable_operations_fail_closed_without_stable_directory_handles(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    if confined_files._windows_directory_guards_available():
        pytest.skip("native Windows directory guards are available")
    root = tmp_path / "knowledge"
    directory = root / "grid_ctf"
    directory.mkdir(parents=True)
    (directory / "playbook.md").write_text("safe", encoding="utf-8")
    monkeypatch.setattr(confined_files, "_descriptor_anchoring_supported", lambda: False)

    with pytest.raises(ConfinedPathError, match="require stable Windows directory handles"):
        read_confined_text(root, ("grid_ctf",), "playbook.md", max_bytes=32)
    with pytest.raises(ConfinedPathError, match="require stable Windows directory handles"):
        list_confined_regular_files(root, ("grid_ctf",), suffix=".md", max_entries=8)
    with pytest.raises(ConfinedPathError, match="require stable Windows directory handles"):
        unlink_confined_file(root, ("grid_ctf",), "playbook.md")


class _SwappingGuard:
    def __init__(
        self,
        guard: confined_files._PortableDirectoryGuard,
        *,
        replacement: Path,
        moved: Path,
    ) -> None:
        self.root = guard.root
        self.parts = guard.parts
        self.resolved_root = guard.resolved_root
        self.directory = guard.directory
        self.identities = guard.identities
        self._guard = guard
        self._replacement = replacement
        self._moved = moved
        self._swapped = False

    def verify(self) -> None:
        if not self._swapped:
            self.directory.rename(self._moved)
            self._replacement.rename(self.directory)
            self._swapped = True
        self._guard.verify()


def test_portable_atomic_replace_rejects_directory_swap_and_preserves_attacker_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "knowledge"
    directory = root / "grid_ctf"
    replacement = tmp_path / "attacker"
    moved = root / "original"
    directory.mkdir(parents=True)
    replacement.mkdir()
    (directory / "playbook.md").write_text("inside", encoding="utf-8")
    (replacement / "playbook.md").write_text("outside", encoding="utf-8")
    attacker_temp = replacement / ".playbook.md.fixed.tmp"
    attacker_temp.write_text("outside-temp", encoding="utf-8")

    @contextmanager
    def swapping_directory(
        confined_root: Path,
        parts: Sequence[str],
        *,
        create: bool,
    ) -> Iterator[_SwappingGuard]:
        yield _SwappingGuard(
            _make_test_guard(confined_root, parts, create=create),
            replacement=replacement,
            moved=moved,
        )

    monkeypatch.setattr(confined_files, "_descriptor_anchoring_supported", lambda: False)
    monkeypatch.setattr(confined_files, "_stable_portable_directory", swapping_directory)
    monkeypatch.setattr(confined_files.secrets, "token_hex", lambda _length: "fixed")

    with pytest.raises(ConfinedPathError, match="changed during operation"):
        atomic_write_confined_text(root, ("grid_ctf",), "playbook.md", "replacement", max_bytes=32)

    assert (directory / "playbook.md").read_text(encoding="utf-8") == "outside"
    assert (directory / attacker_temp.name).read_text(encoding="utf-8") == "outside-temp"
    assert (moved / "playbook.md").read_text(encoding="utf-8") == "inside"


def test_portable_unlink_rejects_directory_swap(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = tmp_path / "knowledge"
    directory = root / "grid_ctf"
    replacement = tmp_path / "attacker"
    moved = root / "original"
    directory.mkdir(parents=True)
    replacement.mkdir()
    (directory / "hint_state.json").write_text("inside", encoding="utf-8")
    (replacement / "hint_state.json").write_text("outside", encoding="utf-8")

    @contextmanager
    def swapping_directory(
        confined_root: Path,
        parts: Sequence[str],
        *,
        create: bool,
    ) -> Iterator[_SwappingGuard]:
        yield _SwappingGuard(
            _make_test_guard(confined_root, parts, create=create),
            replacement=replacement,
            moved=moved,
        )

    monkeypatch.setattr(confined_files, "_descriptor_anchoring_supported", lambda: False)
    monkeypatch.setattr(confined_files, "_stable_portable_directory", swapping_directory)

    with pytest.raises(ConfinedPathError, match="changed during operation"):
        unlink_confined_file(root, ("grid_ctf",), "hint_state.json")

    assert (directory / "hint_state.json").read_text(encoding="utf-8") == "outside"
    assert (moved / "hint_state.json").read_text(encoding="utf-8") == "inside"


@pytest.mark.parametrize("operation", ["read", "list"])
def test_portable_reads_reject_directory_swap(
    operation: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "knowledge"
    directory = root / "grid_ctf"
    replacement = tmp_path / "attacker"
    moved = root / "original"
    directory.mkdir(parents=True)
    replacement.mkdir()
    (directory / "inside.json").write_text("inside", encoding="utf-8")
    (replacement / "outside.json").write_text("outside-secret", encoding="utf-8")

    @contextmanager
    def swapping_directory(
        confined_root: Path,
        parts: Sequence[str],
        *,
        create: bool,
    ) -> Iterator[_SwappingGuard]:
        yield _SwappingGuard(
            _make_test_guard(confined_root, parts, create=create),
            replacement=replacement,
            moved=moved,
        )

    monkeypatch.setattr(confined_files, "_descriptor_anchoring_supported", lambda: False)
    monkeypatch.setattr(confined_files, "_stable_portable_directory", swapping_directory)

    with pytest.raises(ConfinedPathError, match="changed during operation"):
        if operation == "read":
            read_confined_text(root, ("grid_ctf",), "inside.json", max_bytes=32)
        else:
            list_confined_regular_files(root, ("grid_ctf",), suffix=".json", max_entries=8)

    assert (directory / "outside.json").read_text(encoding="utf-8") == "outside-secret"


def test_stable_windows_guard_holds_entire_directory_chain(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "knowledge"
    directory = root / "grid_ctf" / "harness"
    directory.mkdir(parents=True)
    opened: list[Path] = []
    closed: list[int] = []

    def open_guard(path: Path, _expected_stat: os.stat_result) -> int:
        opened.append(path)
        return len(opened)

    monkeypatch.setattr(confined_files, "_windows_directory_guards_available", lambda: True)
    monkeypatch.setattr(confined_files, "_windows_open_stable_directory", open_guard)
    monkeypatch.setattr(confined_files, "_windows_close_handle", closed.append)

    with confined_files._stable_portable_directory(root, ("grid_ctf", "harness"), create=False) as guard:
        guard.verify()
        assert closed == []

    assert opened == [root.resolve(), (root / "grid_ctf").resolve(), directory.resolve()]
    assert closed == [3, 2, 1]


def test_stable_windows_guard_closes_parent_on_component_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "knowledge"
    (root / "grid_ctf").mkdir(parents=True)
    opened: list[Path] = []
    closed: list[int] = []

    def open_guard(path: Path, _expected_stat: os.stat_result) -> int:
        opened.append(path)
        if len(opened) == 2:
            raise ConfinedPathError("simulated guard failure")
        return len(opened)

    monkeypatch.setattr(confined_files, "_windows_directory_guards_available", lambda: True)
    monkeypatch.setattr(confined_files, "_windows_open_stable_directory", open_guard)
    monkeypatch.setattr(confined_files, "_windows_close_handle", closed.append)

    with pytest.raises(ConfinedPathError, match="simulated guard failure"):
        with confined_files._stable_portable_directory(root, ("grid_ctf",), create=False):
            pass

    assert closed == [1]


def test_stable_windows_guard_rejects_root_swap_while_opening(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "knowledge"
    moved = tmp_path / "original-root"
    replacement = tmp_path / "replacement-root"
    root.mkdir()
    replacement.mkdir()
    closed: list[int] = []

    def swap_root(_path: Path, _expected_stat: os.stat_result) -> int:
        root.rename(moved)
        replacement.rename(root)
        return 1

    monkeypatch.setattr(confined_files, "_windows_directory_guards_available", lambda: True)
    monkeypatch.setattr(confined_files, "_windows_open_stable_directory", swap_root)
    monkeypatch.setattr(confined_files, "_windows_close_handle", closed.append)

    with pytest.raises(ConfinedPathError, match="root changed while it was pinned"):
        with confined_files._stable_portable_directory(root, (), create=False):
            pass
    assert closed == [1]


def test_stable_windows_guard_rejects_component_swap_while_opening(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "knowledge"
    component = root / "grid_ctf"
    moved = root / "original-component"
    replacement = tmp_path / "replacement-component"
    component.mkdir(parents=True)
    replacement.mkdir()
    opened = 0
    closed: list[int] = []

    def swap_component(_path: Path, _expected_stat: os.stat_result) -> int:
        nonlocal opened
        opened += 1
        if opened == 2:
            component.rename(moved)
            replacement.rename(component)
        return opened

    monkeypatch.setattr(confined_files, "_windows_directory_guards_available", lambda: True)
    monkeypatch.setattr(confined_files, "_windows_open_stable_directory", swap_component)
    monkeypatch.setattr(confined_files, "_windows_close_handle", closed.append)

    with pytest.raises(ConfinedPathError, match="component changed while it was pinned"):
        with confined_files._stable_portable_directory(root, ("grid_ctf",), create=False):
            pass
    assert closed == [2, 1]


@pytest.mark.parametrize("swap_target", ["root", "component"])
def test_stable_windows_guard_rejects_junction_aba_during_resolution(
    swap_target: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import ctypes

    root = tmp_path / "knowledge"
    component = root / "grid_ctf"
    outside = tmp_path / "outside"
    moved = tmp_path / f"moved-{swap_target}"
    component.mkdir(parents=True)
    outside.mkdir()
    identities = {
        1: confined_files._stat_identity(os.lstat(root)),
        2: confined_files._stat_identity(os.lstat(component)),
    }
    final_paths = {1: root, 2: component}
    next_handle = 0
    closed: list[int] = []

    def create_file(*_args: object) -> int:
        nonlocal next_handle
        next_handle += 1
        return next_handle

    kernel32 = SimpleNamespace(CreateFileW=create_file)
    original_resolve = Path.resolve
    swapped = False

    def resolve_with_aba(path: Path, strict: bool = False) -> Path:
        nonlocal swapped
        target = root if swap_target == "root" else component
        if path == target and not swapped:
            target.rename(moved)
            target.symlink_to(outside, target_is_directory=True)
            try:
                resolved = original_resolve(path, strict=strict)
            finally:
                target.unlink()
                moved.rename(target)
            swapped = True
            return resolved
        return original_resolve(path, strict=strict)

    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: kernel32, raising=False)
    monkeypatch.setattr(Path, "resolve", resolve_with_aba)
    monkeypatch.setattr(confined_files, "_windows_directory_guards_available", lambda: True)
    monkeypatch.setattr(confined_files, "_windows_handle_attributes", lambda _handle: 0x0010)
    monkeypatch.setattr(
        confined_files._path_helpers,
        "windows_handle_identity",
        identities.__getitem__,
    )
    monkeypatch.setattr(confined_files, "_windows_final_path_from_handle", final_paths.__getitem__)
    monkeypatch.setattr(confined_files, "_windows_close_handle", closed.append)

    with pytest.raises(ConfinedPathError, match="resolved to an unexpected path"):
        with confined_files._stable_portable_directory(root, ("grid_ctf",), create=False):
            pass

    assert swapped
    assert closed == ([1] if swap_target == "root" else [2, 1])


def test_windows_directory_guard_rejects_handle_identity_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import ctypes

    root = tmp_path / "knowledge"
    root.mkdir()

    def create_file(*_args: object) -> int:
        return 17

    kernel32 = SimpleNamespace(CreateFileW=create_file)
    closed: list[int] = []
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: kernel32, raising=False)
    monkeypatch.setattr(confined_files, "_windows_handle_attributes", lambda _handle: 0x0010)
    monkeypatch.setattr(confined_files._path_helpers, "windows_handle_identity", lambda _handle: (-1, -1))
    monkeypatch.setattr(confined_files, "_windows_close_handle", closed.append)

    with pytest.raises(ConfinedPathError, match="unexpected identity"):
        confined_files._windows_open_stable_directory(root, os.lstat(root))

    assert closed == [17]


@pytest.mark.parametrize("modern_identity", [False, True])
def test_windows_handle_identity_matches_python_stat_format(
    modern_identity: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ctypes
    from ctypes import wintypes

    volume = 0x1234ABCD
    low_file_id = 0x8877665544332211

    if modern_identity:

        class FileId128(ctypes.Structure):
            _fields_ = [("identifier", ctypes.c_ubyte * 16)]

        class FileIdInfo(ctypes.Structure):
            _fields_ = [
                ("volume_serial_number", ctypes.c_ulonglong),
                ("file_id", FileId128),
            ]

        high_file_id = 0xFFEEDDCCBBAA0099

        def get_information_ex(
            _handle: int,
            _information_class: int,
            information_pointer: object,
            _size: int,
        ) -> int:
            information = ctypes.cast(
                information_pointer,
                ctypes.POINTER(FileIdInfo),
            ).contents
            information.volume_serial_number = volume
            raw_file_id = low_file_id.to_bytes(8, "little") + high_file_id.to_bytes(8, "little")
            information.file_id.identifier[:] = raw_file_id
            return 1

        kernel32 = SimpleNamespace(GetFileInformationByHandleEx=get_information_ex)
        expected_file_id = (high_file_id << 64) | low_file_id
        version = (3, 12)
    else:

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

        def get_information(
            _handle: int,
            information_pointer: object,
        ) -> int:
            information = ctypes.cast(
                information_pointer,
                ctypes.POINTER(ByHandleFileInformation),
            ).contents
            information.volume_serial_number = volume
            information.file_index_high = low_file_id >> 32
            information.file_index_low = low_file_id & 0xFFFFFFFF
            return 1

        kernel32 = SimpleNamespace(GetFileInformationByHandle=get_information)
        expected_file_id = low_file_id
        version = (3, 11)

    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: kernel32, raising=False)
    monkeypatch.setattr(confined_files._path_helpers.sys, "version_info", version)

    assert confined_files._path_helpers.windows_handle_identity(17) == (
        volume,
        expected_file_id,
    )


@pytest.mark.skipif(os.name != "nt", reason="requires Windows delete-sharing semantics")
def test_native_windows_guard_blocks_directory_rename_until_release(tmp_path: Path) -> None:
    root = tmp_path / "knowledge"
    directory = root / "grid_ctf"
    renamed = root / "renamed"
    directory.mkdir(parents=True)

    with confined_files._stable_portable_directory(root, ("grid_ctf",), create=False):
        with pytest.raises(OSError):
            directory.rename(renamed)

    directory.rename(renamed)
    assert renamed.is_dir()


def test_portable_confined_storage_enforces_byte_limits(portable_path: None, tmp_path: Path) -> None:
    root = tmp_path / "knowledge"
    with pytest.raises(ConfinedFileTooLarge, match="byte limit"):
        atomic_write_confined_bytes(root, ("jobs",), "job.json", b"12345", max_bytes=4)

    target = root / "jobs" / "job.json"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"12345")
    with pytest.raises(ConfinedFileTooLarge, match="byte limit"):
        read_confined_bytes(root, ("jobs",), "job.json", max_bytes=4)


@pytest.mark.parametrize("portable", [False, True])
def test_confined_listing_stops_after_limit_plus_one_entries(
    portable: bool,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    consumed = 0

    def entry_stat(*, follow_symlinks: bool) -> SimpleNamespace:
        assert not follow_symlinks
        return SimpleNamespace(
            st_mode=stat.S_IFREG | 0o600,
            st_file_attributes=0,
        )

    class CountingEntries:
        def __enter__(self) -> CountingEntries:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def __iter__(self) -> CountingEntries:
            return self

        def __next__(self) -> SimpleNamespace:
            nonlocal consumed
            if consumed == 10:
                raise StopIteration
            consumed += 1
            return SimpleNamespace(
                name=f"entry-{consumed}.json",
                stat=entry_stat,
            )

    monkeypatch.setattr(confined_files.os, "scandir", lambda _target: CountingEntries())
    if portable:
        guard = SimpleNamespace(directory=tmp_path, verify=lambda: None)

        @contextmanager
        def stable_directory(
            _root: Path,
            _parts: Sequence[str],
            *,
            create: bool,
        ) -> Iterator[SimpleNamespace]:
            assert not create
            yield guard

        monkeypatch.setattr(
            confined_files,
            "_descriptor_anchoring_supported",
            lambda: False,
        )
        monkeypatch.setattr(confined_files, "_stable_portable_directory", stable_directory)
    else:

        @contextmanager
        def open_directory(
            _root: Path,
            _parts: Sequence[str],
            *,
            create: bool,
        ) -> Iterator[int]:
            assert not create
            yield 42

        monkeypatch.setattr(
            confined_files,
            "_descriptor_anchoring_supported",
            lambda: True,
        )
        monkeypatch.setattr(confined_files, "_open_directory", open_directory)

    with pytest.raises(ConfinedFileTooLarge, match="entry limit"):
        list_confined_regular_files(
            tmp_path,
            ("grid_ctf",),
            suffix=".json",
            max_entries=3,
        )

    assert consumed == 4


def test_portable_confined_storage_rejects_symlinked_directory(
    portable_path: None,
    tmp_path: Path,
) -> None:
    root = tmp_path / "knowledge"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    try:
        (root / "grid_ctf").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("this Windows host does not permit creating test symlinks")

    with pytest.raises(ConfinedPathError, match="component is unsafe"):
        atomic_write_confined_text(root, ("grid_ctf",), "playbook.md", "unsafe", max_bytes=32)
    with pytest.raises(ConfinedPathError, match="component is unsafe"):
        read_confined_text(root, ("grid_ctf",), "playbook.md", max_bytes=32)
    assert list(outside.iterdir()) == []


def test_portable_confined_storage_rejects_symlinked_root(
    portable_path: None,
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    root = tmp_path / "knowledge"
    outside.mkdir()
    try:
        root.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("this Windows host does not permit creating test symlinks")

    with pytest.raises(ConfinedPathError, match="root is unsafe"):
        atomic_write_confined_text(root, ("grid_ctf",), "playbook.md", "unsafe", max_bytes=32)
    with pytest.raises(ConfinedPathError, match="root is unsafe"):
        read_confined_text(root, ("grid_ctf",), "playbook.md", max_bytes=32)
    assert list(outside.iterdir()) == []


@pytest.mark.skipif(os.name != "posix", reason="descriptor path is POSIX-specific")
def test_descriptor_confined_storage_rejects_symlinked_root(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    root = tmp_path / "knowledge"
    outside.mkdir()
    (outside / "playbook.md").write_text("outside", encoding="utf-8")
    root.symlink_to(outside, target_is_directory=True)

    operations = (
        lambda: read_confined_text(root, (), "playbook.md", max_bytes=32),
        lambda: atomic_write_confined_text(
            root,
            (),
            "playbook.md",
            "unsafe",
            max_bytes=32,
        ),
        lambda: list_confined_regular_files(
            root,
            (),
            suffix=".md",
            max_entries=8,
        ),
        lambda: unlink_confined_file(root, (), "playbook.md"),
    )
    for operation in operations:
        with pytest.raises(ConfinedPathError, match="root is unsafe"):
            operation()

    assert (outside / "playbook.md").read_text(encoding="utf-8") == "outside"


def test_portable_confined_storage_rejects_symlinked_file(
    portable_path: None,
    tmp_path: Path,
) -> None:
    root = tmp_path / "knowledge"
    directory = root / "grid_ctf"
    outside = tmp_path / "outside.md"
    directory.mkdir(parents=True)
    outside.write_text("secret", encoding="utf-8")
    try:
        (directory / "playbook.md").symlink_to(outside)
    except OSError:
        pytest.skip("this Windows host does not permit creating test symlinks")

    with pytest.raises(ConfinedPathError, match="not a regular file"):
        read_confined_text(root, ("grid_ctf",), "playbook.md", max_bytes=32)
    with pytest.raises(ConfinedPathError, match="not a regular file"):
        atomic_write_confined_text(root, ("grid_ctf",), "playbook.md", "unsafe", max_bytes=32)
    assert list_confined_regular_files(root, ("grid_ctf",), suffix=".md", max_entries=8) == []
    assert outside.read_text(encoding="utf-8") == "secret"


def test_reparse_attribute_is_rejected_even_for_regular_mode() -> None:
    regular_stat = SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_file_attributes=0x400)
    assert confined_files._stat_is_reparse_point(regular_stat)
