"""Deterministic, relocatable source snapshot for the synthetic study contract."""

from __future__ import annotations

import json
import os
import platform
import secrets
import stat
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from contract import baseline_source, canonical_digest, digest, validated_workload_id

from autocontext.kernel_evolution import KernelCandidate

EXAMPLE_DIR = Path(__file__).resolve().parent
REPOSITORY_ROOT = EXAMPLE_DIR.parents[2]
PACKAGE_ROOT = REPOSITORY_ROOT / "autocontext"
PACKAGE_SOURCE_ROOT = PACKAGE_ROOT / "src" / "autocontext"


@dataclass(frozen=True, slots=True)
class ContractSource:
    logical_path: str
    role: str
    content: bytes


@dataclass(frozen=True, slots=True)
class ContractSnapshot:
    manifest: dict[str, Any]
    sources: tuple[ContractSource, ...]

    @property
    def contract_digest(self) -> str:
        return canonical_digest(self.manifest)


@dataclass(frozen=True, slots=True)
class ContractRuntime:
    root: Path
    adapter: Path
    python_path: Path
    manifest_path: Path


def _ensure_safe_directory(path: Path) -> int:
    """Create a directory no-follow and retain its descriptor."""
    supports_dir_fd = all(
        function in os.supports_dir_fd for function in (os.open, os.mkdir, os.link, os.unlink)
    )
    if not supports_dir_fd or not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        raise RuntimeError("secure study artifact publication requires directory-relative no-follow operations")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(path.anchor, flags)
    try:
        for component in path.parts[1:]:
            try:
                os.mkdir(component, dir_fd=descriptor)
            except FileExistsError:
                pass
            try:
                child = os.open(component, flags, dir_fd=descriptor)
            except OSError as exc:
                raise RuntimeError(f"study artifact parent must be a real directory: {path}") from exc
            os.close(descriptor)
            descriptor = child
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def create_private_directory(path: Path) -> int:
    """Create one new private directory and return its retained descriptor."""
    path = path.absolute()
    if path == Path(path.anchor) or not path.name or Path(path.name).name != path.name:
        raise ValueError("private study directory must be a safe child path")
    parent_fd = _ensure_safe_directory(path.parent)
    created = False
    try:
        os.mkdir(path.name, 0o700, dir_fd=parent_fd)
        created = True
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        descriptor = os.open(path.name, flags, dir_fd=parent_fd)
        try:
            os.fchmod(descriptor, 0o700)
            observed = os.fstat(descriptor)
            if not stat.S_ISDIR(observed.st_mode) or stat.S_IMODE(observed.st_mode) != 0o700:
                raise RuntimeError("study root must be a private real directory")
            os.fsync(descriptor)
            os.fsync(parent_fd)
        except BaseException:
            os.close(descriptor)
            raise
        return descriptor
    except BaseException:
        if created:
            try:
                os.rmdir(path.name, dir_fd=parent_fd)
            except (OSError, TypeError):
                pass
        raise
    finally:
        os.close(parent_fd)


def _safe_relative_parent(directory_fd: int, relative: Path, *, create: bool = True) -> int:
    if relative.is_absolute() or not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError("study artifact path must be a safe relative path")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.dup(directory_fd)
    try:
        for component in relative.parts[:-1]:
            if create:
                try:
                    os.mkdir(component, dir_fd=descriptor)
                except FileExistsError:
                    pass
            try:
                child = os.open(component, flags, dir_fd=descriptor)
            except OSError as exc:
                raise RuntimeError("study artifact parent must be a real directory") from exc
            os.close(descriptor)
            descriptor = child
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _stable_descriptor_bytes(descriptor: int, display_path: Path) -> bytes:
    before = os.fstat(descriptor)
    if not stat.S_ISREG(before.st_mode):
        raise RuntimeError(f"contract source must be a regular file: {display_path}")
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks = []
    while chunk := os.read(descriptor, 1024 * 1024):
        chunks.append(chunk)
    content = b"".join(chunks)
    after = os.fstat(descriptor)
    before_identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    after_identity = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if before_identity != after_identity or len(content) != after.st_size:
        raise RuntimeError(f"contract source changed while it was snapshotted: {display_path}")
    return content


def _stable_source_bytes_at(directory_fd: int, name: str, display_path: Path) -> bytes:
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=directory_fd)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise RuntimeError(f"contract source must be a regular non-symlink file: {display_path}") from exc
    try:
        return _stable_descriptor_bytes(descriptor, display_path)
    finally:
        os.close(descriptor)


def _unlink_if_identity_matches(directory_fd: int, name: str, identity: tuple[int, int]) -> None:
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0),
            dir_fd=directory_fd,
        )
    except OSError:
        return
    try:
        observed = os.fstat(descriptor)
        if (observed.st_dev, observed.st_ino) == identity:
            os.unlink(name, dir_fd=directory_fd)
    finally:
        os.close(descriptor)


def _write_exact_bytes_at(directory_fd: int, path: Path, content: bytes) -> tuple[int, int] | None:
    try:
        existing = _stable_source_bytes_at(directory_fd, path.name, path)
    except FileNotFoundError:
        pass
    else:
        if existing != content:
            raise RuntimeError(f"immutable study contract changed: {path}")
        return None
    for _ in range(100):
        temporary_name = f".{path.name}.{secrets.token_hex(16)}.tmp"
        try:
            descriptor = os.open(
                temporary_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory_fd,
            )
        except FileExistsError:
            continue
        break
    else:
        raise RuntimeError(f"could not reserve a temporary study artifact beside {path}")
    temporary_stat = os.fstat(descriptor)
    temporary_identity = (temporary_stat.st_dev, temporary_stat.st_ino)
    linked_identity: tuple[int, int] | None = None
    publication_succeeded = False
    try:
        remaining = memoryview(content)
        while remaining:
            written = os.write(descriptor, remaining)
            if written == 0:
                raise OSError("short write while staging study artifact")
            remaining = remaining[written:]
        os.fsync(descriptor)
        try:
            os.link(
                temporary_name,
                path.name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
                follow_symlinks=False,
            )
        except FileExistsError:
            if _stable_source_bytes_at(directory_fd, path.name, path) != content:
                raise RuntimeError(f"immutable study contract changed: {path}") from None
            return None
        target_descriptor = os.open(
            path.name,
            os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0),
            dir_fd=directory_fd,
        )
        try:
            target_stat = os.fstat(target_descriptor)
            linked_identity = (target_stat.st_dev, target_stat.st_ino)
            linked_content = _stable_descriptor_bytes(target_descriptor, path)
        finally:
            os.close(target_descriptor)
        if linked_identity != temporary_identity or linked_content != content:
            raise RuntimeError(f"study artifact changed during atomic publication: {path}")
        os.fsync(directory_fd)
        publication_succeeded = True
        return temporary_identity
    finally:
        if linked_identity is not None and not publication_succeeded:
            _unlink_if_identity_matches(directory_fd, path.name, linked_identity)
        _unlink_if_identity_matches(directory_fd, temporary_name, temporary_identity)
        os.close(descriptor)


def write_exact_bytes(path: Path, content: bytes) -> None:
    path = path.absolute()
    if path.is_symlink():
        raise RuntimeError(f"study artifact target must not be a symlink: {path}")
    directory_fd = _ensure_safe_directory(path.parent)
    try:
        _write_exact_bytes_at(directory_fd, path, content)
    finally:
        os.close(directory_fd)


def write_exact_relative(directory_fd: int, relative_path: str | Path, content: bytes) -> None:
    """Publish immutable bytes beneath an already-retained directory identity."""
    relative = Path(relative_path)
    parent_fd = _safe_relative_parent(directory_fd, relative)
    try:
        _write_exact_bytes_at(parent_fd, relative, content)
    finally:
        os.close(parent_fd)


def read_exact_relative(directory_fd: int, relative_path: str | Path) -> bytes:
    """Read a stable regular file beneath an already-retained directory identity."""
    relative = Path(relative_path)
    parent_fd = _safe_relative_parent(directory_fd, relative, create=False)
    try:
        return _stable_source_bytes_at(parent_fd, relative.name, relative)
    finally:
        os.close(parent_fd)


@contextmanager
def retained_safe_directory(directory: Path) -> Iterator[int]:
    """Retain one no-follow directory identity across reads and publication."""
    directory_fd = _ensure_safe_directory(directory.absolute())
    try:
        yield directory_fd
    finally:
        os.close(directory_fd)


@contextmanager
def retained_working_directory(directory: Path) -> Iterator[Path]:
    """Run pathname-based legacy diagnostics from one retained directory identity."""
    if not hasattr(os, "fchdir"):
        raise RuntimeError("secure study diagnostics require descriptor-relative working directories")
    directory = directory.absolute()
    directory_fd = _ensure_safe_directory(directory)
    prior_fd = os.open(".", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    identity = os.fstat(directory_fd)
    active_error: BaseException | None = None
    try:
        os.fchdir(directory_fd)
        try:
            yield Path(".")
        except BaseException as exc:
            active_error = exc
            raise
        finally:
            os.fchdir(prior_fd)
    finally:
        try:
            current = directory.lstat()
            if (
                not stat.S_ISDIR(current.st_mode)
                or (current.st_dev, current.st_ino) != (identity.st_dev, identity.st_ino)
            ):
                raise RuntimeError("retained study diagnostic directory changed")
        except BaseException as identity_exc:
            if active_error is not None:
                active_error.add_note(f"study diagnostic identity check failed: {identity_exc}")
            else:
                raise
        finally:
            os.close(prior_fd)
            os.close(directory_fd)


@contextmanager
def retained_relative_working_directory(directory_fd: int, relative_path: str | Path) -> Iterator[Path]:
    """Run diagnostics beneath an already-retained study directory."""
    if not hasattr(os, "fchdir"):
        raise RuntimeError("secure study diagnostics require descriptor-relative working directories")
    relative = Path(relative_path)
    parent_fd = _safe_relative_parent(directory_fd, relative)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        try:
            os.mkdir(relative.name, dir_fd=parent_fd)
        except FileExistsError:
            pass
        try:
            working_fd = os.open(relative.name, flags, dir_fd=parent_fd)
        except OSError as exc:
            raise RuntimeError("study diagnostic directory must be a real directory") from exc
        prior_fd = os.open(".", flags)
        identity = os.fstat(working_fd)
        active_error: BaseException | None = None
        try:
            os.fchdir(working_fd)
            try:
                yield Path(".")
            except BaseException as exc:
                active_error = exc
                raise
            finally:
                os.fchdir(prior_fd)
        finally:
            try:
                current = os.stat(relative.name, dir_fd=parent_fd, follow_symlinks=False)
                if (
                    not stat.S_ISDIR(current.st_mode)
                    or (current.st_dev, current.st_ino) != (identity.st_dev, identity.st_ino)
                ):
                    raise RuntimeError("retained study diagnostic directory changed")
            except BaseException as identity_exc:
                if active_error is not None:
                    active_error.add_note(f"study diagnostic identity check failed: {identity_exc}")
                else:
                    raise
            finally:
                os.close(prior_fd)
                os.close(working_fd)
    finally:
        os.close(parent_fd)


def publish_exact_bundle(
    directory: Path,
    files: tuple[tuple[str, bytes], ...],
    *,
    directory_fd: int | None = None,
    expected_portable_files: tuple[dict[str, Any], ...] | None = None,
    report_content: bytes | None = None,
) -> None:
    """Publish a marker-last bundle through one retained directory descriptor."""
    names = [name for name, _ in files]
    if len(names) != len(set(names)) or any(Path(name).name != name or name in {".", ".."} for name in names):
        raise ValueError("published study artifact names must be unique safe path components")
    directory = directory.absolute()
    owned_fd = directory_fd is None
    active_fd = _ensure_safe_directory(directory) if directory_fd is None else directory_fd
    directory_stat = os.fstat(active_fd)
    published_handles: list[tuple[str, bytes, int, tuple[int, int]]] = []
    try:
        if expected_portable_files is not None:
            if report_content is None:
                raise ValueError("portable inventory replay requires the exact report bytes")
            if portable_file_inventory(
                directory,
                report_content=report_content,
                directory_fd=active_fd,
            ) != expected_portable_files:
                raise RuntimeError("portable study artifact inventory changed before publication")
        for name in names:
            try:
                _stable_source_bytes_at(active_fd, name, directory / name)
            except FileNotFoundError:
                continue
            raise RuntimeError(f"success artifact already exists: {directory / name}")
        for name, content in files:
            identity = _write_exact_bytes_at(active_fd, directory / name, content)
            if identity is None:
                raise RuntimeError(f"success artifact appeared during publication: {directory / name}")
            published_fd = os.open(
                name,
                os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0),
                dir_fd=active_fd,
            )
            published_stat = os.fstat(published_fd)
            if (published_stat.st_dev, published_stat.st_ino) != identity:
                os.close(published_fd)
                raise RuntimeError(f"success artifact identity changed during publication: {directory / name}")
            published_handles.append((name, content, published_fd, identity))
        current = directory.lstat()
        if (
            not stat.S_ISDIR(current.st_mode)
            or (current.st_dev, current.st_ino) != (directory_stat.st_dev, directory_stat.st_ino)
        ):
            raise RuntimeError("study root changed during success publication")
        if expected_portable_files is not None:
            assert report_content is not None
            if portable_file_inventory(
                directory,
                report_content=report_content,
                directory_fd=active_fd,
            ) != expected_portable_files:
                raise RuntimeError("portable study artifact inventory changed during publication")
        for name, content, published_fd, identity in published_handles:
            if _stable_descriptor_bytes(published_fd, directory / name) != content:
                raise RuntimeError(f"published study artifact changed: {directory / name}")
            current_fd = os.open(
                name,
                os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0),
                dir_fd=active_fd,
            )
            try:
                current_stat = os.fstat(current_fd)
                current_content = _stable_descriptor_bytes(current_fd, directory / name)
                if (current_stat.st_dev, current_stat.st_ino) != identity or current_content != content:
                    raise RuntimeError(f"published study artifact was replaced: {directory / name}")
            finally:
                os.close(current_fd)
        os.fsync(active_fd)
    except BaseException:
        for name, _ in reversed(files):
            try:
                os.unlink(name, dir_fd=active_fd)
            except (FileNotFoundError, IsADirectoryError):
                pass
        os.fsync(active_fd)
        raise
    finally:
        for _, _, published_fd, _ in published_handles:
            os.close(published_fd)
        if owned_fd:
            os.close(active_fd)


def _stable_source_bytes(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"contract source must be a regular non-symlink file: {path}")
    before = path.stat()
    if not stat.S_ISREG(before.st_mode):
        raise RuntimeError(f"contract source must be a regular file: {path}")
    content = path.read_bytes()
    after = path.stat()
    before_identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    after_identity = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if before_identity != after_identity or len(content) != after.st_size:
        raise RuntimeError(f"contract source changed while it was snapshotted: {path}")
    return content


def _source_paths() -> tuple[tuple[str, str, Path], ...]:
    example_sources = tuple(
        (f"example/{name}", role, EXAMPLE_DIR / name)
        for name, role in (
            ("adapter.py", "synthetic-adapter"),
            ("contract.py", "study-contract"),
            ("contract_runtime.py", "contract-snapshot-runtime"),
            ("evidence_runtime.py", "evidence-runtime"),
            ("run.py", "study-orchestrator"),
        )
    )
    package_sources = tuple(
        (
            f"package/src/autocontext/{path.relative_to(PACKAGE_SOURCE_ROOT).as_posix()}",
            "first-party-runtime",
            path,
        )
        for path in sorted(PACKAGE_SOURCE_ROOT.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts and path.suffix not in {".pyc", ".pyo"}
    )
    environment_sources = (
        ("environment/pyproject.toml", "package-contract", PACKAGE_ROOT / "pyproject.toml"),
        ("environment/uv.lock", "dependency-lock", PACKAGE_ROOT / "uv.lock"),
    )
    return tuple(sorted((*example_sources, *package_sources, *environment_sources), key=lambda item: item[0]))


def contract_snapshot() -> ContractSnapshot:
    sources = tuple(
        ContractSource(logical_path=logical_path, role=role, content=_stable_source_bytes(path))
        for logical_path, role, path in _source_paths()
    )
    logical_paths = [source.logical_path for source in sources]
    if logical_paths != sorted(logical_paths) or len(logical_paths) != len(set(logical_paths)):
        raise RuntimeError("contract runtime paths must be sorted and unique")
    executable = Path(sys.executable).resolve(strict=True)
    manifest = {
        "schema_version": "autocontext.synthetic-kernel-contract-runtime/v1",
        "files": [
            {
                "path": source.logical_path,
                "role": source.role,
                "digest": digest(source.content),
                "size_bytes": len(source.content),
            }
            for source in sources
        ],
        "runtime": {
            "python_implementation": sys.implementation.name,
            "python_version": platform.python_version(),
            "python_cache_tag": sys.implementation.cache_tag,
            "platform_system": platform.system(),
            "platform_machine": platform.machine(),
            "executable_digest": digest(_stable_source_bytes(executable)),
        },
    }
    return ContractSnapshot(manifest=manifest, sources=sources)


def _write_study_content(
    study_root: Path,
    target: Path,
    content: bytes,
    directory_fd: int | None,
) -> None:
    if directory_fd is None:
        write_exact_bytes(target, content)
    else:
        write_exact_relative(directory_fd, target.absolute().relative_to(study_root.absolute()), content)


def materialize_contract_runtime(
    study_root: Path,
    snapshot: ContractSnapshot,
    *,
    directory_fd: int | None = None,
) -> ContractRuntime:
    root = study_root.absolute() / "contract-runtime"
    if root.is_symlink():
        raise RuntimeError("contract runtime root must not be a symlink")
    for source in snapshot.sources:
        relative = Path(source.logical_path)
        target = root / relative
        if relative.is_absolute() or ".." in relative.parts or not target.is_relative_to(root):
            raise RuntimeError("contract runtime source path escaped its bundle")
        _write_study_content(study_root, target, source.content, directory_fd)
    manifest_path = root / "manifest.json"
    _write_study_content(
        study_root,
        manifest_path,
        (json.dumps(snapshot.manifest, indent=2, sort_keys=True) + "\n").encode(),
        directory_fd,
    )
    return ContractRuntime(
        root=root,
        adapter=root / "example" / "adapter.py",
        python_path=root / "package" / "src",
        manifest_path=manifest_path,
    )


def verify_contract_snapshot(
    snapshot: ContractSnapshot,
    runtime: ContractRuntime,
    *,
    directory_fd: int | None = None,
) -> None:
    if contract_snapshot().manifest != snapshot.manifest:
        raise RuntimeError("live contract runtime changed after the study snapshot")
    expected = {source.logical_path: digest(source.content) for source in snapshot.sources}
    expected["manifest.json"] = digest((json.dumps(snapshot.manifest, indent=2, sort_keys=True) + "\n").encode())
    if directory_fd is not None:
        runtime_relative = runtime.root.relative_to(runtime.root.parent)
        observed = {
            path: digest(read_exact_relative(directory_fd, runtime_relative / path))
            for path in expected
        }
    else:
        observed = {}
        for path in sorted(runtime.root.rglob("*")):
            if path.is_symlink():
                raise RuntimeError("materialized contract runtime contains a symlink")
            if path.is_file():
                observed[path.relative_to(runtime.root).as_posix()] = digest(_stable_source_bytes(path))
    if observed != expected:
        raise RuntimeError("materialized contract runtime disagrees with its exact source manifest")


def materialize_reference_sources(
    study_root: Path,
    workloads: list[dict[str, Any]],
    *,
    directory_fd: int | None = None,
) -> tuple[dict[str, Any], ...]:
    candidate = KernelCandidate(source=baseline_source(workloads), source_suffix=".py", entrypoint="kernel_fn")
    source_path = study_root / "sources" / f"{candidate.source_digest.removeprefix('sha256:')}.py"
    _write_study_content(study_root, source_path, candidate.source_bytes, directory_fd)
    return tuple(
        {
            "workload_id": validated_workload_id(workload["workload_id"]),
            "source_digest": candidate.source_digest,
            "artifact_digest": candidate.artifact_digest,
            "source_suffix": candidate.source_suffix,
            "entrypoint": candidate.entrypoint,
            "path": source_path.relative_to(study_root).as_posix(),
            "file_digest": digest(candidate.source_bytes),
        }
        for workload in workloads
    )


def _portable_tree_inventory(
    directory_fd: int,
    *,
    relative_root: str,
    study_root: Path,
) -> list[dict[str, Any]]:
    inventory: list[dict[str, Any]] = []
    try:
        with os.scandir(directory_fd) as entries:
            names = sorted(entry.name for entry in entries)
    except OSError as exc:
        raise RuntimeError("portable study artifact directory could not be enumerated safely") from exc
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0)
    for name in names:
        if not name or Path(name).name != name or name in {".", ".."}:
            raise RuntimeError("portable study artifact name is unsafe")
        relative = f"{relative_root}/{name}"
        display_path = study_root / relative
        try:
            child_fd = os.open(name, flags, dir_fd=directory_fd)
        except OSError as exc:
            raise RuntimeError("portable study artifacts must not contain symlinks") from exc
        try:
            child_stat = os.fstat(child_fd)
            if stat.S_ISDIR(child_stat.st_mode):
                inventory.extend(
                    _portable_tree_inventory(
                        child_fd,
                        relative_root=relative,
                        study_root=study_root,
                    )
                )
            elif stat.S_ISREG(child_stat.st_mode):
                content = _stable_descriptor_bytes(child_fd, display_path)
                inventory.append(
                    {
                        "path": relative,
                        "role": relative.split("/", maxsplit=1)[0],
                        "digest": digest(content),
                        "size_bytes": len(content),
                    }
                )
            else:
                raise RuntimeError("portable study artifacts must be regular files or directories")
        finally:
            os.close(child_fd)
    return inventory


def portable_file_inventory(
    study_root: Path,
    *,
    report_content: bytes,
    directory_fd: int,
) -> tuple[dict[str, Any], ...]:
    """Seal every portable regular file except the self-referential success marker."""
    manifest_content = _stable_source_bytes_at(directory_fd, "manifest.json", study_root / "manifest.json")
    inventory: list[dict[str, Any]] = [
        {
            "path": "manifest.json",
            "role": "manifest.json",
            "digest": digest(manifest_content),
            "size_bytes": len(manifest_content),
        },
        {
            "path": "study_report.json",
            "role": "study_report.json",
            "digest": digest(report_content),
            "size_bytes": len(report_content),
        }
    ]
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    for name in ("contract-runtime", "contracts", "evidence", "sources"):
        try:
            portable_root_fd = os.open(name, directory_flags, dir_fd=directory_fd)
        except OSError as exc:
            raise RuntimeError(f"portable study artifact root is missing or unsafe: {name}") from exc
        try:
            inventory.extend(
                _portable_tree_inventory(
                    portable_root_fd,
                    relative_root=name,
                    study_root=study_root,
                )
            )
        finally:
            os.close(portable_root_fd)
    inventory.sort(key=lambda item: item["path"])
    paths = [item["path"] for item in inventory]
    if len(paths) != len(set(paths)):
        raise RuntimeError("portable study artifact inventory contains duplicate paths")
    return tuple(inventory)


def runtime_manifest_failure_fields(
    study_root: Path,
    runtime: ContractRuntime,
    *,
    directory_fd: int | None = None,
) -> dict[str, Any]:
    """Describe the runtime manifest without making failure evidence depend on it."""
    path = runtime.manifest_path.relative_to(study_root).as_posix()
    try:
        content = (
            read_exact_relative(directory_fd, path)
            if directory_fd is not None
            else _stable_source_bytes(runtime.manifest_path)
        )
        file_digest = digest(content)
        read_error = None
    except Exception as exc:
        file_digest = None
        read_error = f"{type(exc).__name__}: {exc}"
    return {
        "contract_runtime_manifest_path": path,
        "contract_runtime_manifest_file_digest": file_digest,
        "contract_runtime_manifest_read_error": read_error,
    }


__all__ = [
    "ContractRuntime",
    "ContractSnapshot",
    "contract_snapshot",
    "create_private_directory",
    "materialize_contract_runtime",
    "materialize_reference_sources",
    "portable_file_inventory",
    "publish_exact_bundle",
    "read_exact_relative",
    "retained_relative_working_directory",
    "retained_safe_directory",
    "retained_working_directory",
    "runtime_manifest_failure_fields",
    "verify_contract_snapshot",
    "write_exact_bytes",
    "write_exact_relative",
]
