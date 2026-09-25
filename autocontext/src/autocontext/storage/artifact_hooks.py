from __future__ import annotations

from pathlib import Path
from typing import Any

from autocontext.extensions import HookBus, HookEvents


def emit_artifact_write(
    hook_bus: HookBus | None,
    *,
    path: Path,
    format: str,
    content: str | None = None,
    payload: dict[str, Any] | None = None,
    append: bool = False,
    heading: str = "",
    buffered: bool = False,
    managed_roots: tuple[Path, ...] = (),
) -> tuple[Path, str | None, dict[str, Any] | None, str]:
    if hook_bus is None:
        return path, content, payload, heading
    event_payload: dict[str, Any] = {
        "path": str(path),
        "format": format,
        "append": append,
        "buffered": buffered,
    }
    if content is not None:
        event_payload["content"] = content
    if payload is not None:
        event_payload["payload"] = dict(payload)
    if heading:
        event_payload["heading"] = heading
    event = hook_bus.emit(HookEvents.ARTIFACT_WRITE, event_payload)
    event.raise_if_blocked()
    # The payload path is in the caller's frame (relative to the working directory when the
    # roots are relative), so a returned path is read in that same frame, as in the TS runtime.
    next_path = Path(str(event.payload.get("path", path)))
    next_path = _validate_redirect(path, next_path, managed_roots)
    next_content = event.payload.get("content", content)
    next_payload = event.payload.get("payload", payload)
    next_heading = str(event.payload.get("heading", heading))
    if next_content is not None:
        next_content = str(next_content)
    if next_payload is not None and not isinstance(next_payload, dict):
        next_payload = payload
    return next_path, next_content, next_payload, next_heading


def _validate_redirect(original_path: Path, next_path: Path, managed_roots: tuple[Path, ...]) -> Path:
    original_root = _find_containing_root(original_path, managed_roots)
    if original_root is None:
        return next_path
    resolved = next_path.resolve(strict=False)
    try:
        resolved.relative_to(original_root)
    except ValueError as exc:
        raise RuntimeError(
            f"artifact hook redirected {original_path} outside managed root {original_root}: {next_path}"
        ) from exc
    return resolved


def _find_containing_root(path: Path, roots: tuple[Path, ...]) -> Path | None:
    resolved_path = path.resolve(strict=False)
    for root in roots:
        resolved_root = root.resolve(strict=False)
        try:
            resolved_path.relative_to(resolved_root)
        except ValueError:
            continue
        return resolved_root
    return None
