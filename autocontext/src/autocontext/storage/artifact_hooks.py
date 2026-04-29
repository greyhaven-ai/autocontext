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
    next_path = Path(str(event.payload.get("path", path)))
    next_content = event.payload.get("content", content)
    next_payload = event.payload.get("payload", payload)
    next_heading = str(event.payload.get("heading", heading))
    if next_content is not None:
        next_content = str(next_content)
    if next_payload is not None and not isinstance(next_payload, dict):
        next_payload = payload
    return next_path, next_content, next_payload, next_heading
