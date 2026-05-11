"""Runtime workspace/session environment contract and adapters."""

from __future__ import annotations

import os
import posixpath
import shlex
import shutil
import stat as stat_mode
import subprocess
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from autocontext.runtimes.workspace_grants import (
    DEFAULT_RUNTIME_COMMAND_OUTPUT_LIMIT_BYTES,
    RuntimeGrantEvent,
    RuntimeGrantEventSinkLike,
    RuntimeGrantInheritanceMode,
    RuntimeGrantKind,
    RuntimeGrantProvenance,
    RuntimeGrantScopePolicy,
    base_grant_redaction,
    combine_timeout_ms,
    emit_runtime_grant_event,
    inherits_to_child_tasks,
    normalize_output_limit,
    pick_process_env,
    preview_text,
    secret_values,
    summarize_args,
)


@dataclass(frozen=True, slots=True)
class RuntimeExecOptions:
    cwd: str | None = None
    env: Mapping[str, str] = field(default_factory=dict)
    timeout_ms: int | None = None


@dataclass(frozen=True, slots=True)
class RuntimeExecResult:
    stdout: str
    stderr: str
    exit_code: int


@dataclass(frozen=True, slots=True)
class RuntimeFileStat:
    is_file: bool
    is_directory: bool
    is_symbolic_link: bool
    size: int
    mtime: float


@dataclass(frozen=True, slots=True)
class RuntimeCommandContext:
    cwd: str
    env: Mapping[str, str]
    host_cwd: str | None = None
    timeout_ms: int | None = None


RuntimeCommandResult = RuntimeExecResult | Mapping[str, object]
RuntimeCommandHandler = Callable[[Sequence[str], RuntimeCommandContext], RuntimeCommandResult]


@dataclass(frozen=True, slots=True)
class RuntimeCommandGrant:
    name: str
    execute: RuntimeCommandHandler
    env: Mapping[str, str] = field(default_factory=dict)
    kind: RuntimeGrantKind = "command"
    description: str = ""
    provenance: Mapping[str, str] | RuntimeGrantProvenance | None = None
    scope: RuntimeGrantScopePolicy | Mapping[str, Any] | None = None
    output_limit_bytes: int = DEFAULT_RUNTIME_COMMAND_OUTPUT_LIMIT_BYTES


class RuntimeWorkspaceEnv(Protocol):
    """Core runtime isolation boundary for filesystem and command operations."""

    @property
    def cwd(self) -> str:
        """Virtual current working directory inside this workspace."""
        ...

    def exec(self, command: str, options: RuntimeExecOptions | None = None) -> RuntimeExecResult:
        """Execute a command from this workspace."""
        ...

    def scope(
        self,
        *,
        cwd: str | None = None,
        commands: Sequence[RuntimeCommandGrant] | None = None,
        grant_event_sink: RuntimeGrantEventSinkLike | None = None,
        grant_inheritance: RuntimeGrantInheritanceMode = "scope",
    ) -> RuntimeWorkspaceEnv:
        """Return a child view with optional cwd and command grants."""
        ...

    def read_file(self, file_path: str) -> str:
        """Read UTF-8 text from a virtual path."""
        ...

    def read_file_bytes(self, file_path: str) -> bytes:
        """Read bytes from a virtual path."""
        ...

    def write_file(self, file_path: str, content: str | bytes) -> None:
        """Write text or bytes to a virtual path."""
        ...

    def stat(self, file_path: str) -> RuntimeFileStat:
        """Return metadata for a virtual path."""
        ...

    def readdir(self, dir_path: str) -> list[str]:
        """List direct entries under a virtual directory."""
        ...

    def exists(self, file_path: str) -> bool:
        """Return whether a virtual path exists."""
        ...

    def mkdir(self, dir_path: str, *, recursive: bool = False) -> None:
        """Create a virtual directory."""
        ...

    def rm(self, file_path: str, *, recursive: bool = False, force: bool = False) -> None:
        """Remove a virtual path."""
        ...

    def resolve_path(self, file_path: str) -> str:
        """Resolve a path against ``cwd`` into a normalized virtual absolute path."""
        ...

    def cleanup(self) -> None:
        """Release resources owned by this workspace."""
        ...


def create_in_memory_workspace_env(
    *,
    cwd: str = "/",
    files: Mapping[str, str | bytes] | None = None,
) -> RuntimeWorkspaceEnv:
    return InMemoryWorkspaceEnv(_create_memory_state(files), cwd)


def create_local_workspace_env(*, root: str | Path, cwd: str = "/") -> RuntimeWorkspaceEnv:
    return LocalWorkspaceEnv(Path(root), cwd)


def define_runtime_command(
    name: str,
    execute: RuntimeCommandHandler,
    *,
    env: Mapping[str, str] | None = None,
    description: str = "",
    provenance: Mapping[str, str] | RuntimeGrantProvenance | None = None,
    scope: RuntimeGrantScopePolicy | Mapping[str, Any] | None = None,
    output_limit_bytes: int | None = None,
) -> RuntimeCommandGrant:
    clean_name = name.strip()
    if not clean_name or any(char.isspace() for char in clean_name):
        raise ValueError("Runtime command names must be non-empty and contain no whitespace")
    return RuntimeCommandGrant(
        name=clean_name,
        execute=execute,
        env=dict(env or {}),
        description=description,
        provenance=provenance,
        scope=scope,
        output_limit_bytes=normalize_output_limit(output_limit_bytes),
    )


def create_local_runtime_command_grant(
    name: str,
    executable: str,
    *,
    args: Sequence[str] | None = None,
    inherit_env: Sequence[str] | None = None,
    timeout_ms: int | None = None,
    env: Mapping[str, str] | None = None,
    description: str = "",
    provenance: Mapping[str, str] | RuntimeGrantProvenance | None = None,
    scope: RuntimeGrantScopePolicy | Mapping[str, Any] | None = None,
    output_limit_bytes: int | None = None,
) -> RuntimeCommandGrant:
    clean_executable = executable.strip()
    if not clean_executable:
        raise ValueError("Local runtime command executable must be non-empty")
    fixed_args = list(args or ())
    inherited_env = pick_process_env(inherit_env or ())

    def execute_local(command_args: Sequence[str], context: RuntimeCommandContext) -> RuntimeExecResult:
        return _run_process(
            clean_executable,
            [*fixed_args, *command_args],
            cwd=context.host_cwd or context.cwd,
            env=context.env,
            timeout_ms=combine_timeout_ms(timeout_ms, context.timeout_ms),
        )

    return define_runtime_command(
        name,
        execute_local,
        env={**inherited_env, **dict(env or {})},
        description=description,
        provenance=provenance,
        scope=scope,
        output_limit_bytes=output_limit_bytes,
    )


@dataclass(slots=True)
class _MemoryFile:
    content: bytes
    mtime: float


@dataclass(slots=True)
class _MemoryState:
    files: dict[str, _MemoryFile]
    dirs: dict[str, float]


class InMemoryWorkspaceEnv:
    def __init__(
        self,
        state: _MemoryState,
        cwd: str,
        commands: Sequence[RuntimeCommandGrant] = (),
        grant_event_sink: RuntimeGrantEventSinkLike | None = None,
    ) -> None:
        self._state = state
        self._cwd = _normalize_virtual_path(cwd, "/")
        self._commands = _command_map(commands)
        self._grant_event_sink = grant_event_sink
        self._closed = False
        _ensure_memory_parent_dirs(self._state, self._cwd)

    @property
    def cwd(self) -> str:
        return self._cwd

    def exec(self, command: str, options: RuntimeExecOptions | None = None) -> RuntimeExecResult:
        self._assert_open()
        exec_options = options or RuntimeExecOptions()
        granted = _maybe_run_granted_command(
            self._commands,
            command,
            exec_options,
            self.resolve_path(exec_options.cwd) if exec_options.cwd else self.cwd,
            None,
            self._grant_event_sink,
        )
        if granted is not None:
            return granted
        return RuntimeExecResult(
            stdout="",
            stderr=f"In-memory workspace does not provide shell execution: {command}",
            exit_code=127,
        )

    def scope(
        self,
        *,
        cwd: str | None = None,
        commands: Sequence[RuntimeCommandGrant] | None = None,
        grant_event_sink: RuntimeGrantEventSinkLike | None = None,
        grant_inheritance: RuntimeGrantInheritanceMode = "scope",
    ) -> RuntimeWorkspaceEnv:
        self._assert_open()
        return InMemoryWorkspaceEnv(
            self._state,
            self.resolve_path(cwd) if cwd else self.cwd,
            _merge_command_grants(
                _inherited_command_grants(self._commands.values(), grant_inheritance),
                commands or (),
            ),
            grant_event_sink or self._grant_event_sink,
        )

    def read_file(self, file_path: str) -> str:
        return self.read_file_bytes(file_path).decode()

    def read_file_bytes(self, file_path: str) -> bytes:
        self._assert_open()
        resolved = self.resolve_path(file_path)
        file = self._state.files.get(resolved)
        if file is None:
            raise FileNotFoundError(f"File not found: {resolved}")
        return bytes(file.content)

    def write_file(self, file_path: str, content: str | bytes) -> None:
        self._assert_open()
        resolved = self.resolve_path(file_path)
        _write_memory_file(self._state, resolved, content)

    def stat(self, file_path: str) -> RuntimeFileStat:
        self._assert_open()
        resolved = self.resolve_path(file_path)
        file = self._state.files.get(resolved)
        if file is not None:
            return RuntimeFileStat(
                is_file=True,
                is_directory=False,
                is_symbolic_link=False,
                size=len(file.content),
                mtime=file.mtime,
            )
        dir_mtime = self._state.dirs.get(resolved)
        if dir_mtime is not None:
            return RuntimeFileStat(
                is_file=False,
                is_directory=True,
                is_symbolic_link=False,
                size=0,
                mtime=dir_mtime,
            )
        raise FileNotFoundError(f"Path not found: {resolved}")

    def readdir(self, dir_path: str) -> list[str]:
        self._assert_open()
        resolved = self.resolve_path(dir_path)
        if resolved not in self._state.dirs:
            raise FileNotFoundError(f"Directory not found: {resolved}")
        entries = set()
        for candidate in [*self._state.dirs.keys(), *self._state.files.keys()]:
            if candidate != resolved and posixpath.dirname(candidate) == resolved:
                entries.add(posixpath.basename(candidate))
        return sorted(entries)

    def exists(self, file_path: str) -> bool:
        self._assert_open()
        resolved = self.resolve_path(file_path)
        return resolved in self._state.files or resolved in self._state.dirs

    def mkdir(self, dir_path: str, *, recursive: bool = False) -> None:
        self._assert_open()
        resolved = self.resolve_path(dir_path)
        parent = posixpath.dirname(resolved)
        if resolved in self._state.files:
            raise FileExistsError(f"File exists: {resolved}")
        if resolved in self._state.dirs:
            if recursive:
                return
            raise FileExistsError(f"Directory exists: {resolved}")
        if not recursive and parent not in self._state.dirs:
            if parent in self._state.files:
                raise NotADirectoryError(f"Not a directory: {parent}")
            raise FileNotFoundError(f"Parent directory not found: {parent}")
        _ensure_memory_parent_dirs(self._state, resolved if recursive else parent)
        self._state.dirs[resolved] = _mtime_now()

    def rm(self, file_path: str, *, recursive: bool = False, force: bool = False) -> None:
        self._assert_open()
        resolved = self.resolve_path(file_path)
        if self._state.files.pop(resolved, None) is not None:
            return
        if resolved not in self._state.dirs:
            if force:
                return
            raise FileNotFoundError(f"Path not found: {resolved}")
        children = [
            candidate
            for candidate in [*self._state.files.keys(), *self._state.dirs.keys()]
            if candidate != resolved and candidate.startswith(f"{resolved}/")
        ]
        if children and not recursive:
            raise OSError(f"Directory not empty: {resolved}")
        for child in children:
            self._state.files.pop(child, None)
            self._state.dirs.pop(child, None)
        if resolved != "/":
            self._state.dirs.pop(resolved, None)

    def resolve_path(self, file_path: str) -> str:
        return _normalize_virtual_path(file_path, self.cwd)

    def cleanup(self) -> None:
        self._closed = True

    def _assert_open(self) -> None:
        if self._closed:
            raise RuntimeError("Workspace environment has been cleaned up")


class LocalWorkspaceEnv:
    def __init__(
        self,
        root: Path,
        cwd: str,
        commands: Sequence[RuntimeCommandGrant] = (),
        grant_event_sink: RuntimeGrantEventSinkLike | None = None,
    ) -> None:
        self._root = root.resolve()
        self._cwd = _normalize_virtual_path(cwd, "/")
        self._commands = _command_map(commands)
        self._grant_event_sink = grant_event_sink

    @property
    def cwd(self) -> str:
        return self._cwd

    def exec(self, command: str, options: RuntimeExecOptions | None = None) -> RuntimeExecResult:
        exec_options = options or RuntimeExecOptions()
        virtual_cwd = self.resolve_path(exec_options.cwd) if exec_options.cwd else self.cwd
        host_cwd = self._to_host_path(virtual_cwd)
        granted = _maybe_run_granted_command(
            self._commands,
            command,
            exec_options,
            virtual_cwd,
            str(host_cwd),
            self._grant_event_sink,
        )
        if granted is not None:
            return granted
        timeout = exec_options.timeout_ms / 1000 if exec_options.timeout_ms is not None else None
        try:
            completed = subprocess.run(
                command,
                cwd=host_cwd,
                env={**os.environ, **dict(exec_options.env)},
                shell=True,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            return RuntimeExecResult(
                stdout=exc.stdout if isinstance(exc.stdout, str) else "",
                stderr=exc.stderr if isinstance(exc.stderr, str) and exc.stderr else "Command timed out",
                exit_code=124,
            )
        return RuntimeExecResult(stdout=completed.stdout, stderr=completed.stderr, exit_code=completed.returncode)

    def scope(
        self,
        *,
        cwd: str | None = None,
        commands: Sequence[RuntimeCommandGrant] | None = None,
        grant_event_sink: RuntimeGrantEventSinkLike | None = None,
        grant_inheritance: RuntimeGrantInheritanceMode = "scope",
    ) -> RuntimeWorkspaceEnv:
        return LocalWorkspaceEnv(
            self._root,
            self.resolve_path(cwd) if cwd else self.cwd,
            _merge_command_grants(
                _inherited_command_grants(self._commands.values(), grant_inheritance),
                commands or (),
            ),
            grant_event_sink or self._grant_event_sink,
        )

    def read_file(self, file_path: str) -> str:
        return self._to_host_path(self.resolve_path(file_path)).read_text(encoding="utf-8")

    def read_file_bytes(self, file_path: str) -> bytes:
        return self._to_host_path(self.resolve_path(file_path)).read_bytes()

    def write_file(self, file_path: str, content: str | bytes) -> None:
        host_path = self._to_host_path(self.resolve_path(file_path))
        host_path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, str):
            host_path.write_text(content, encoding="utf-8")
        else:
            host_path.write_bytes(content)

    def stat(self, file_path: str) -> RuntimeFileStat:
        host_path = self._to_host_path(self.resolve_path(file_path))
        path_stat = host_path.lstat()
        return RuntimeFileStat(
            is_file=stat_mode.S_ISREG(path_stat.st_mode),
            is_directory=stat_mode.S_ISDIR(path_stat.st_mode),
            is_symbolic_link=stat_mode.S_ISLNK(path_stat.st_mode),
            size=path_stat.st_size,
            mtime=path_stat.st_mtime,
        )

    def readdir(self, dir_path: str) -> list[str]:
        return sorted(child.name for child in self._to_host_path(self.resolve_path(dir_path)).iterdir())

    def exists(self, file_path: str) -> bool:
        host_path = self._to_host_path(self.resolve_path(file_path))
        return host_path.exists() or host_path.is_symlink()

    def mkdir(self, dir_path: str, *, recursive: bool = False) -> None:
        self._to_host_path(self.resolve_path(dir_path)).mkdir(parents=recursive, exist_ok=recursive)

    def rm(self, file_path: str, *, recursive: bool = False, force: bool = False) -> None:
        host_path = self._to_host_path(self.resolve_path(file_path))
        try:
            path_stat = host_path.lstat()
        except FileNotFoundError as exc:
            if force:
                return
            raise FileNotFoundError(f"Path not found: {self.resolve_path(file_path)}") from exc
        if stat_mode.S_ISDIR(path_stat.st_mode):
            if recursive:
                shutil.rmtree(host_path)
            else:
                host_path.rmdir()
            return
        host_path.unlink()

    def resolve_path(self, file_path: str) -> str:
        return _normalize_virtual_path(file_path, self.cwd)

    def cleanup(self) -> None:
        # Local workspaces are caller-owned. Cleanup is intentionally a no-op.
        return None

    def _to_host_path(self, virtual_path: str) -> Path:
        relative = virtual_path.lstrip("/")
        host_path = self._root / relative
        try:
            host_path.relative_to(self._root)
        except ValueError as exc:
            raise ValueError(f"Path escapes workspace root: {virtual_path}") from exc
        return host_path


def _create_memory_state(files: Mapping[str, str | bytes] | None) -> _MemoryState:
    state = _MemoryState(files={}, dirs={"/": _mtime_now()})
    for file_path, content in (files or {}).items():
        resolved = _normalize_virtual_path(file_path, "/")
        _write_memory_file(state, resolved, content)
    return state


def _normalize_virtual_path(file_path: str | None, cwd: str) -> str:
    raw_path = file_path or "."
    base = cwd if cwd.startswith("/") else f"/{cwd}"
    candidate = raw_path if raw_path.startswith("/") else posixpath.join(base, raw_path)
    normalized = posixpath.normpath(candidate)
    absolute = normalized if normalized.startswith("/") else f"/{normalized}"
    return absolute[:-1] if len(absolute) > 1 and absolute.endswith("/") else absolute


def _ensure_memory_parent_dirs(state: _MemoryState, dir_path: str) -> None:
    current = "/"
    state.dirs.setdefault(current, _mtime_now())
    for part in dir_path.split("/"):
        if not part:
            continue
        current = f"/{part}" if current == "/" else f"{current}/{part}"
        if current in state.files:
            raise NotADirectoryError(f"Not a directory: {current}")
        state.dirs.setdefault(current, _mtime_now())


def _write_memory_file(state: _MemoryState, resolved: str, content: str | bytes) -> None:
    if resolved in state.dirs:
        raise IsADirectoryError(f"Is a directory: {resolved}")
    _ensure_memory_parent_dirs(state, posixpath.dirname(resolved))
    state.files[resolved] = _MemoryFile(content=_to_bytes(content), mtime=_mtime_now())


def _to_bytes(content: str | bytes) -> bytes:
    return content.encode() if isinstance(content, str) else bytes(content)


def _mtime_now() -> float:
    return time.time()


def _command_map(commands: Iterable[RuntimeCommandGrant]) -> dict[str, RuntimeCommandGrant]:
    return {command.name: command for command in commands}


def _merge_command_grants(
    base: Iterable[RuntimeCommandGrant],
    overrides: Iterable[RuntimeCommandGrant],
) -> list[RuntimeCommandGrant]:
    result = _command_map(base)
    for command in overrides:
        result[command.name] = command
    return list(result.values())


def _inherited_command_grants(
    commands: Iterable[RuntimeCommandGrant],
    mode: RuntimeGrantInheritanceMode = "scope",
) -> list[RuntimeCommandGrant]:
    if mode != "child_task":
        return list(commands)
    return [command for command in commands if inherits_to_child_tasks(command.scope)]


def _maybe_run_granted_command(
    commands: Mapping[str, RuntimeCommandGrant],
    command_line: str,
    options: RuntimeExecOptions,
    cwd: str,
    host_cwd: str | None,
    grant_event_sink: RuntimeGrantEventSinkLike | None,
) -> RuntimeExecResult | None:
    try:
        tokens = shlex.split(command_line)
    except ValueError:
        return None
    if not tokens:
        return None
    grant = commands.get(tokens[0])
    if grant is None:
        return None
    command_env = {**dict(options.env), **dict(grant.env)}
    secrets = secret_values(command_env)
    args = summarize_args(tokens[1:], secrets)
    redaction = base_grant_redaction(command_env, args)
    emit_runtime_grant_event(
        grant_event_sink,
        RuntimeGrantEvent(
            kind="command",
            phase="start",
            name=grant.name,
            cwd=cwd,
            args_summary=args.summary,
            redaction=redaction,
            provenance=grant.provenance,
        ),
    )
    try:
        context = RuntimeCommandContext(
            cwd=cwd,
            env=command_env,
            host_cwd=host_cwd,
            timeout_ms=options.timeout_ms,
        )
        result = _normalize_exec_result(grant.execute(tokens[1:], context))
        stdout = preview_text(result.stdout, secrets, _runtime_command_output_limit(grant))
        stderr = preview_text(result.stderr, secrets, _runtime_command_output_limit(grant))
        emit_runtime_grant_event(
            grant_event_sink,
            RuntimeGrantEvent(
                kind="command",
                phase="end",
                name=grant.name,
                cwd=cwd,
                args_summary=args.summary,
                exit_code=result.exit_code,
                stdout=stdout.text,
                stderr=stderr.text,
                redaction={
                    **redaction,
                    "stdout": stdout.metadata.to_dict(),
                    "stderr": stderr.metadata.to_dict(),
                },
                provenance=grant.provenance,
            ),
        )
        return result
    except Exception as exc:
        message = preview_text(str(exc), secrets, _runtime_command_output_limit(grant))
        emit_runtime_grant_event(
            grant_event_sink,
            RuntimeGrantEvent(
                kind="command",
                phase="error",
                name=grant.name,
                cwd=cwd,
                args_summary=args.summary,
                error=message.text,
                redaction={**redaction, "error": message.metadata.to_dict()},
                provenance=grant.provenance,
            ),
        )
        raise


def _normalize_exec_result(value: RuntimeCommandResult) -> RuntimeExecResult:
    if isinstance(value, RuntimeExecResult):
        return value
    return RuntimeExecResult(
        stdout=str(value.get("stdout", "")),
        stderr=str(value.get("stderr", "")),
        exit_code=_read_exit_code(value.get("exit_code", value.get("exitCode", 0))),
    )


def _read_exit_code(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    return 0


def _runtime_command_output_limit(grant: RuntimeCommandGrant) -> int:
    return normalize_output_limit(grant.output_limit_bytes)


def _run_process(
    executable: str,
    args: Sequence[str],
    *,
    cwd: str,
    env: Mapping[str, str],
    timeout_ms: int | None = None,
) -> RuntimeExecResult:
    timeout = timeout_ms / 1000 if timeout_ms is not None else None
    try:
        completed = subprocess.run(
            [executable, *args],
            cwd=cwd,
            env=dict(env),
            shell=False,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return RuntimeExecResult(
            stdout=exc.stdout if isinstance(exc.stdout, str) else "",
            stderr=exc.stderr if isinstance(exc.stderr, str) and exc.stderr else "Command timed out",
            exit_code=124,
        )
    return RuntimeExecResult(stdout=completed.stdout, stderr=completed.stderr, exit_code=completed.returncode)
