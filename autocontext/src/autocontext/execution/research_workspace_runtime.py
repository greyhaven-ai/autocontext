"""Process-isolated execution kernel for capable research workspaces."""

from __future__ import annotations

import ast
import builtins
import contextlib
import io
import multiprocessing
import os
import subprocess
import traceback
import urllib.parse
import urllib.request
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from autocontext.execution.research_workspace_models import WorkspaceResourceLimits
from autocontext.offline import require_online

_BLOCKED_NAMES = frozenset(
    {
        "breakpoint",
        "compile",
        "delattr",
        "eval",
        "exec",
        "exit",
        "getattr",
        "globals",
        "help",
        "input",
        "locals",
        "memoryview",
        "quit",
        "setattr",
        "vars",
    }
)
_BLOCKED_MODULES = frozenset({"ctypes", "importlib", "multiprocessing", "os", "pathlib", "socket", "subprocess", "sys"})
_INFRA_NAMES = frozenset(
    {
        "__builtins__",
        "__name__",
        "answer",
        "host_call",
        "network_fetch",
        "run_subprocess",
        "workspace_read_bytes",
        "workspace_read_text",
        "workspace_write_bytes",
        "workspace_write_text",
    }
)
_SKIP = object()
_PROCESS_START_TIMEOUT_SECONDS = 5.0


def run_in_child(payload: Mapping[str, Any], timeout_seconds: float) -> dict[str, Any] | None:
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=True)
    process = context.Process(target=_child_entrypoint, args=(child,), daemon=True)
    process.start()
    child.close()
    try:
        if not parent.poll(_PROCESS_START_TIMEOUT_SECONDS):
            _terminate_process(process)
            return None
        ready = parent.recv()
        if ready != {"ready": True}:
            return ready if isinstance(ready, dict) else {"error": "invalid child startup response"}
        parent.send(dict(payload))
        if not parent.poll(timeout_seconds):
            _terminate_process(process)
            return None
        response = parent.recv()
        process.join(timeout=1.0)
        return response if isinstance(response, dict) else {"error": "invalid child response"}
    finally:
        parent.close()
        if process.is_alive():
            _terminate_process(process)


def _terminate_process(process: Any) -> None:
    process.terminate()
    process.join(timeout=1.0)
    if process.is_alive():
        process.kill()
        process.join(timeout=1.0)


def _child_entrypoint(connection: Any) -> None:
    try:
        connection.send({"ready": True})
        payload = connection.recv()
        connection.send(_execute_child(payload))
    except BaseException as exc:  # noqa: BLE001
        connection.send({"stdout": "", "error": f"{type(exc).__name__}: {exc}", "answer": {}})
    finally:
        connection.close()


def _execute_child(payload: Mapping[str, Any]) -> dict[str, Any]:
    capabilities = frozenset(payload["capabilities"])
    allowed_imports = frozenset(payload["allowed_imports"])
    allowed_commands = frozenset(payload["allowed_commands"])
    allowed_hosts = frozenset(payload["allowed_network_hosts"])
    limits: WorkspaceResourceLimits = payload["limits"]
    root = Path(str(payload["workspace_root"])).resolve()
    safe_builtins = {
        name: getattr(builtins, name)
        for name in dir(builtins)
        if not name.startswith("_") and name not in _BLOCKED_NAMES and name != "open"
    }

    def safe_import(name: str, globals: Any = None, locals: Any = None, fromlist: Any = (), level: int = 0) -> Any:
        del globals, locals, fromlist
        top_level = name.split(".", 1)[0]
        if level or "package_import" not in capabilities or top_level not in allowed_imports or top_level in _BLOCKED_MODULES:
            raise PermissionError(f"import denied: {name}")
        return builtins.__import__(name)

    def resolve_path(file_path: str, capability: str) -> Path:
        if capability not in capabilities:
            raise PermissionError(f"{capability} capability was not granted")
        candidate = (root / file_path).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise PermissionError(f"path escapes workspace root: {file_path}") from exc
        return candidate

    def read_bytes(file_path: str) -> bytes:
        data = resolve_path(file_path, "workspace_read").read_bytes()
        if len(data) > limits.max_file_bytes:
            raise ValueError("file exceeds workspace byte limit")
        return data

    def write_bytes(file_path: str, content: bytes) -> None:
        if len(content) > limits.max_file_bytes:
            raise ValueError("file exceeds workspace byte limit")
        path = resolve_path(file_path, "workspace_write")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def run_command(argv: Iterable[str], timeout_seconds: float | None = None) -> dict[str, Any]:
        if "subprocess" not in capabilities:
            raise PermissionError("subprocess capability was not granted")
        args = [str(item) for item in argv]
        if not args or args[0] not in allowed_commands:
            raise PermissionError(f"command denied: {args[0] if args else ''}")
        timeout = min(timeout_seconds or limits.subprocess_timeout_seconds, limits.subprocess_timeout_seconds)
        completed = subprocess.run(  # noqa: S603
            args,
            cwd=root,
            env={"PATH": os.environ.get("PATH", ""), "LANG": "C.UTF-8"},
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {"stdout": completed.stdout, "stderr": completed.stderr, "exit_code": completed.returncode}

    def network_fetch(url: str) -> bytes:
        if "network" not in capabilities:
            raise PermissionError("network capability was not granted")
        require_online("use research workspace network access")
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.hostname not in allowed_hosts:
            raise PermissionError(f"network host denied: {parsed.hostname or ''}")
        with urllib.request.urlopen(url, timeout=limits.timeout_seconds) as response:  # noqa: S310
            data = bytes(response.read(limits.max_network_bytes + 1))
        if len(data) > limits.max_network_bytes:
            raise ValueError("network response exceeds byte limit")
        return data

    safe_builtins["__import__"] = safe_import
    namespace: dict[str, Any] = {
        "__name__": "__autocontext_research__",
        "__builtins__": safe_builtins,
        "answer": {"content": "", "ready": False},
        **copy_plain_mapping(payload.get("variables", {})),
    }
    if "workspace_read" in capabilities:
        namespace["workspace_read_bytes"] = read_bytes
        namespace["workspace_read_text"] = lambda path: read_bytes(path).decode("utf-8")
    if "workspace_write" in capabilities:
        namespace["workspace_write_bytes"] = write_bytes
        namespace["workspace_write_text"] = lambda path, content: write_bytes(path, str(content).encode("utf-8"))
    if "subprocess" in capabilities:
        namespace["run_subprocess"] = run_command
    if "network" in capabilities:
        namespace["network_fetch"] = network_fetch
    for source in payload.get("helper_sources", ()):
        exec(compile(source, "<workspace-helper>", "exec"), namespace, namespace)  # noqa: S102

    stdout = io.StringIO()
    error: str | None = None
    try:
        module = ast.parse(str(payload["code"]), mode="exec")
        body = list(module.body)
        trailing = body.pop() if body and isinstance(body[-1], ast.Expr) else None
        with contextlib.redirect_stdout(stdout):
            if body:
                exec(compile(ast.Module(body=body, type_ignores=[]), "<research-workspace>", "exec"), namespace, namespace)  # noqa: S102
            if isinstance(trailing, ast.Expr):
                value = eval(compile(ast.Expression(trailing.value), "<research-workspace>", "eval"), namespace, namespace)  # noqa: S307
                if value is not None:
                    print(repr(value))
    except BaseException:  # noqa: BLE001
        error = traceback.format_exc()
    rendered_stdout = stdout.getvalue()
    if len(rendered_stdout) > limits.max_stdout_chars:
        rendered_stdout = rendered_stdout[: limits.max_stdout_chars] + "\n... [truncated]"
    variables, skipped = _export_namespace(namespace)
    return {
        "stdout": rendered_stdout,
        "error": error,
        "answer": dict(namespace.get("answer", {})),
        "variables": variables,
        "skipped": skipped,
        "host_calls": (),
        "stdout_chars": len(rendered_stdout),
        "variable_count": len(variables),
    }


def validate_capable_code(code: str, allowed_imports: frozenset[str]) -> None:
    try:
        module = ast.parse(code, mode="exec")
    except SyntaxError:
        return
    for node in ast.walk(module):
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise PermissionError(f"dunder attribute access denied: {node.attr}")
        if isinstance(node, ast.Name) and (node.id in _BLOCKED_NAMES or node.id in _BLOCKED_MODULES):
            raise PermissionError(f"name denied: {node.id}")
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [alias.name for alias in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            for name in names:
                top_level = name.split(".", 1)[0]
                if top_level not in allowed_imports or top_level in _BLOCKED_MODULES:
                    raise PermissionError(f"import denied: {name}")


def new_helper_sources(code: str, existing: Iterable[str]) -> list[str]:
    module = ast.parse(code, mode="exec")
    known = set(existing)
    sources: list[str] = []
    for node in module.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            source = ast.unparse(node)
            if source not in known:
                sources.append(source)
    return sources


def copy_plain_mapping(source: Mapping[str, Any]) -> dict[str, Any]:
    copied: dict[str, Any] = {}
    for name, value in source.items():
        plain = _copy_plain(value)
        if plain is not _SKIP:
            copied[str(name)] = plain
    return copied


def _copy_plain(value: Any, depth: int = 0) -> Any:
    if depth > 20:
        return _SKIP
    if type(value) in {type(None), bool, int, float, str, bytes}:
        return value
    if type(value) is list:
        copied = [_copy_plain(item, depth + 1) for item in value]
        return copied if all(item is not _SKIP for item in copied) else _SKIP
    if type(value) is tuple:
        copied = [_copy_plain(item, depth + 1) for item in value]
        return tuple(copied) if all(item is not _SKIP for item in copied) else _SKIP
    if type(value) is dict:
        copied_dict: dict[Any, Any] = {}
        for key, item in value.items():
            copied_key = _copy_plain(key, depth + 1)
            copied_item = _copy_plain(item, depth + 1)
            if copied_key is _SKIP or copied_item is _SKIP:
                return _SKIP
            copied_dict[copied_key] = copied_item
        return copied_dict
    if type(value) in {set, frozenset}:
        copied = [_copy_plain(item, depth + 1) for item in value]
        if any(item is _SKIP for item in copied):
            return _SKIP
        return type(value)(copied)
    return _SKIP


def _export_namespace(namespace: Mapping[str, Any]) -> tuple[dict[str, Any], tuple[str, ...]]:
    variables: dict[str, Any] = {}
    skipped: list[str] = []
    for name, value in namespace.items():
        if name in _INFRA_NAMES or name.startswith("_") or callable(value) or isinstance(value, type(os)):
            continue
        copied = _copy_plain(value)
        if copied is _SKIP:
            skipped.append(name)
        else:
            variables[name] = copied
    return variables, tuple(sorted(skipped))


def resource_detail(response: Mapping[str, Any]) -> str:
    return f"stdout_chars={response.get('stdout_chars', 0)} variables={response.get('variable_count', 0)}"
