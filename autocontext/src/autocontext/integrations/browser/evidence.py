"""Filesystem evidence persistence for browser exploration."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any, TypedDict

from autocontext.integrations.browser.contract.models import BrowserAuditEvent
from autocontext.integrations.browser.validate import validate_browser_audit_event


class BrowserArtifactPaths(TypedDict):
    htmlPath: str | None
    screenshotPath: str | None
    downloadPath: str | None


class BrowserEvidenceStore:
    """Persist browser action evidence beneath a run-local root directory."""

    def __init__(self, root_dir: str | Path) -> None:
        self.root_dir = Path(root_dir).resolve()

    def append_audit_event(self, event: BrowserAuditEvent | dict[str, Any]) -> Path:
        parsed = validate_browser_audit_event(event) if isinstance(event, dict) else event
        out_path = self._session_dir(str(parsed.sessionId)) / "actions.jsonl"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    parsed.model_dump(mode="json"),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
            fh.write("\n")
        return out_path

    def persist_snapshot_artifacts(
        self,
        *,
        session_id: str,
        basename: str,
        html: str | None = None,
        screenshot_base64: str | None = None,
    ) -> BrowserArtifactPaths:
        html_path: Path | None = None
        screenshot_path: Path | None = None

        safe_basename = _safe_path_component(basename, fallback="artifact")

        if html is not None:
            html_path = self._artifact_path(
                session_id=session_id,
                subdir="html",
                filename=f"{safe_basename}.html",
            )
            html_path.parent.mkdir(parents=True, exist_ok=True)
            html_path.write_text(html, encoding="utf-8")

        if screenshot_base64 is not None:
            screenshot_path = self._artifact_path(
                session_id=session_id,
                subdir="screenshots",
                filename=f"{safe_basename}.png",
            )
            screenshot_path.parent.mkdir(parents=True, exist_ok=True)
            screenshot_path.write_bytes(base64.b64decode(screenshot_base64, validate=True))

        return {
            "htmlPath": str(html_path.resolve()) if html_path is not None else None,
            "screenshotPath": str(screenshot_path.resolve()) if screenshot_path is not None else None,
            "downloadPath": None,
        }

    def _session_dir(self, session_id: str) -> Path:
        return self.root_dir / "browser" / "sessions" / _safe_path_component(session_id, fallback="session")

    def _artifact_path(self, *, session_id: str, subdir: str, filename: str) -> Path:
        path = self._session_dir(session_id) / subdir / filename
        resolved = path.resolve()
        if not resolved.is_relative_to(self.root_dir):
            raise ValueError("browser artifact path escaped evidence root")
        return resolved


def _safe_path_component(value: str, *, fallback: str) -> str:
    leaf = Path(str(value)).name
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in leaf)
    safe = safe.strip("._")
    return safe or fallback


__all__ = ["BrowserArtifactPaths", "BrowserEvidenceStore"]
