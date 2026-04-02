"""Research evidence persistence — JSON-file store (AC-500).

Persists ResearchBrief snapshots for audit trail, cross-session learning,
and prompt context windows. Uses one JSON file per brief, indexed by a
lightweight manifest.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from autocontext.research.consultation import ResearchBrief

logger = logging.getLogger(__name__)

BRIEFS_DIR = "research_briefs"
MANIFEST_FILE = "manifest.json"


class BriefRef(BaseModel):
    """Pointer to a persisted brief."""

    brief_id: str
    session_id: str
    goal: str
    created_at: str
    finding_count: int = 0

    model_config = {"frozen": True}


class ResearchStore:
    """File-backed research brief persistence.

    Layout:
        root/
          research_briefs/
            manifest.json          — [{brief_id, session_id, goal, ...}]
            <brief_id>.json        — serialized ResearchBrief
    """

    def __init__(self, root: Path) -> None:
        self._dir = root / BRIEFS_DIR
        self._dir.mkdir(parents=True, exist_ok=True)
        self._manifest_path = self._dir / MANIFEST_FILE

    def save_brief(self, session_id: str, brief: ResearchBrief) -> BriefRef:
        brief_id = uuid.uuid4().hex[:12]
        ref = BriefRef(
            brief_id=brief_id,
            session_id=session_id,
            goal=brief.goal,
            created_at=datetime.now(UTC).isoformat(),
            finding_count=len(brief.findings),
        )

        brief_path = self._dir / f"{brief_id}.json"
        brief_path.write_text(brief.model_dump_json(indent=2), encoding="utf-8")

        manifest = self._load_manifest()
        manifest.append(ref.model_dump())
        self._write_manifest(manifest)

        logger.debug("Saved brief %s for session %s (%d findings)", brief_id, session_id, len(brief.findings))
        return ref

    def load_brief(self, brief_id: str) -> ResearchBrief | None:
        brief_path = self._dir / f"{brief_id}.json"
        if not brief_path.exists():
            return None
        data = json.loads(brief_path.read_text(encoding="utf-8"))
        return ResearchBrief.model_validate(data)

    def list_briefs(self, session_id: str) -> list[BriefRef]:
        manifest = self._load_manifest()
        return [BriefRef.model_validate(e) for e in manifest if e["session_id"] == session_id]

    def brief_count(self) -> int:
        return len(self._load_manifest())

    def delete_brief(self, brief_id: str) -> bool:
        brief_path = self._dir / f"{brief_id}.json"
        if not brief_path.exists():
            return False
        brief_path.unlink()
        manifest = [e for e in self._load_manifest() if e["brief_id"] != brief_id]
        self._write_manifest(manifest)
        return True

    def _load_manifest(self) -> list[dict[str, Any]]:
        if not self._manifest_path.exists():
            return []
        data: list[dict[str, Any]] = json.loads(self._manifest_path.read_text(encoding="utf-8"))
        return data

    def _write_manifest(self, manifest: list[dict[str, Any]]) -> None:
        self._manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
