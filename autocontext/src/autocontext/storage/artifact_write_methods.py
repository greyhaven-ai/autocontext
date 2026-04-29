from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

from autocontext.extensions import HookBus
from autocontext.storage.artifact_hooks import emit_artifact_write
from autocontext.storage.buffered_writer import BufferedWriter


class _ArtifactWriteHost(Protocol):
    _hook_bus: HookBus | None
    _writer: BufferedWriter | None

    def _mirror_bytes(self, path: Path, data: bytes) -> None: ...
    def write_json(self, path: Path, payload: dict[str, Any]) -> None: ...
    def write_markdown(self, path: Path, content: str) -> None: ...
    def append_markdown(self, path: Path, content: str, heading: str) -> None: ...


class ArtifactWriteMethods:
    """Generic artifact write methods shared by ArtifactStore."""

    _hook_bus: HookBus | None
    _writer: BufferedWriter | None

    def write_json(self: _ArtifactWriteHost, path: Path, payload: dict[str, Any]) -> None:
        path, content_override, hook_payload, _ = emit_artifact_write(
            self._hook_bus, path=path, format="json", payload=payload
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = hook_payload if hook_payload is not None else payload
        content = content_override if content_override is not None else json.dumps(payload, indent=2, sort_keys=True)
        path.write_text(content, encoding="utf-8")
        self._mirror_bytes(path, content.encode("utf-8"))

    def write_markdown(self: _ArtifactWriteHost, path: Path, content: str) -> None:
        path, hook_content, _, _ = emit_artifact_write(
            self._hook_bus, path=path, format="markdown", content=content
        )
        content = hook_content or ""
        path.parent.mkdir(parents=True, exist_ok=True)
        rendered = content.strip() + "\n"
        path.write_text(rendered, encoding="utf-8")
        self._mirror_bytes(path, rendered.encode("utf-8"))

    def append_markdown(self: _ArtifactWriteHost, path: Path, content: str, heading: str) -> None:
        path, hook_content, _, heading = emit_artifact_write(
            self._hook_bus, path=path, format="markdown", content=content, append=True, heading=heading
        )
        content = hook_content or ""
        path.parent.mkdir(parents=True, exist_ok=True)
        chunk = f"\n## {heading}\n\n{content.strip()}\n"
        if path.exists():
            with path.open("a", encoding="utf-8") as handle:
                handle.write(chunk)
            return
        path.write_text(chunk.lstrip("\n"), encoding="utf-8")

    def flush_writes(self: _ArtifactWriteHost) -> None:
        if self._writer is not None:
            self._writer.flush()

    def shutdown_writer(self: _ArtifactWriteHost) -> None:
        if self._writer is not None:
            self._writer.shutdown()
            self._writer = None

    def buffered_write_json(self: _ArtifactWriteHost, path: Path, payload: dict[str, Any]) -> None:
        path, content_override, hook_payload, _ = emit_artifact_write(
            self._hook_bus, path=path, format="json", payload=payload, buffered=self._writer is not None
        )
        payload = hook_payload if hook_payload is not None else payload
        content = content_override if content_override is not None else json.dumps(payload, indent=2, sort_keys=True)
        if self._writer is None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            self._mirror_bytes(path, content.encode("utf-8"))
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        self._writer.write_text(path, content)
        self._mirror_bytes(path, content.encode("utf-8"))

    def buffered_write_markdown(self: _ArtifactWriteHost, path: Path, content: str) -> None:
        path, hook_content, _, _ = emit_artifact_write(
            self._hook_bus, path=path, format="markdown", content=content, buffered=self._writer is not None
        )
        content = hook_content or ""
        if self._writer is None:
            path.parent.mkdir(parents=True, exist_ok=True)
            rendered = content.strip() + "\n"
            path.write_text(rendered, encoding="utf-8")
            self._mirror_bytes(path, rendered.encode("utf-8"))
            return
        rendered = content.strip() + "\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        self._writer.write_text(path, rendered)
        self._mirror_bytes(path, rendered.encode("utf-8"))

    def buffered_append_markdown(self: _ArtifactWriteHost, path: Path, content: str, heading: str) -> None:
        path, hook_content, _, heading = emit_artifact_write(
            self._hook_bus,
            path=path,
            format="markdown",
            content=content,
            append=True,
            heading=heading,
            buffered=self._writer is not None,
        )
        content = hook_content or ""
        if self._writer is None:
            path.parent.mkdir(parents=True, exist_ok=True)
            chunk = f"\n## {heading}\n\n{content.strip()}\n"
            if path.exists():
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(chunk)
                return
            path.write_text(chunk.lstrip("\n"), encoding="utf-8")
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        chunk = f"\n## {heading}\n\n{content.strip()}\n"
        self._writer.append_text(path, chunk)
