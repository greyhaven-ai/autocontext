"""BlobRef — structured artifact locator (AC-518)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class BlobRef:
    """Reference to a blob artifact with optional local and remote locations."""

    kind: str  # "trace", "checkpoint", "report", "model", "export", ...
    digest: str  # "sha256:<hex>"
    size_bytes: int
    local_path: str = ""
    remote_uri: str = ""  # e.g. "hf://org/repo/blobs/key"
    content_type: str = ""
    created_at: str = ""
    retention_class: str = ""  # "ephemeral", "durable", "archive"

    @property
    def is_hydrated(self) -> bool:
        """True if the blob is available locally."""
        return bool(self.local_path) and Path(self.local_path).exists()

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "digest": self.digest,
            "size_bytes": self.size_bytes,
            "local_path": self.local_path,
            "remote_uri": self.remote_uri,
            "content_type": self.content_type,
            "created_at": self.created_at,
            "retention_class": self.retention_class,
            "is_hydrated": self.is_hydrated,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BlobRef:
        return cls(
            kind=data.get("kind", ""),
            digest=data.get("digest", ""),
            size_bytes=data.get("size_bytes", 0),
            local_path=data.get("local_path", ""),
            remote_uri=data.get("remote_uri", ""),
            content_type=data.get("content_type", ""),
            created_at=data.get("created_at", ""),
            retention_class=data.get("retention_class", ""),
        )
