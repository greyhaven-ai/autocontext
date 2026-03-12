"""JSON file persistence for agent identities."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from autocontext.harness.identity.types import AgentIdentity, SoulDocument

DEFAULT_SOULS: dict[str, SoulDocument] = {
    "competitor": SoulDocument(
        role="competitor",
        purpose="Generate winning strategies through experimentation and adaptation",
        principles=("Explore before exploiting", "Learn from every match outcome", "Adapt strategies based on evidence"),
        constraints=("Must produce valid strategy JSON", "Must respect scenario constraints"),
    ),
    "analyst": SoulDocument(
        role="analyst",
        purpose="Extract actionable insights from match data to guide strategy evolution",
        principles=("Ground analysis in data", "Identify root causes not symptoms", "Prioritize actionable recommendations"),
        constraints=("Must produce structured markdown analysis", "Must reference specific match data"),
    ),
    "coach": SoulDocument(
        role="coach",
        purpose="Evolve the strategic playbook based on analyst insights and match outcomes",
        principles=("Build on what works", "Prune what doesn't", "Maintain strategic coherence"),
        constraints=("Must produce playbook with required delimiters", "Must preserve proven strategies"),
    ),
    "architect": SoulDocument(
        role="architect",
        purpose="Design and evolve tooling that amplifies agent capabilities",
        principles=("Tools should solve recurring problems", "Simplicity over complexity", "Archive before replacing"),
        constraints=("Must produce valid Python tool code", "Must follow tool interface conventions"),
    ),
}


class IdentityStore:
    """JSON file persistence for agent identities."""

    def __init__(self, identity_dir: Path) -> None:
        self._dir = identity_dir
        self._lock = threading.Lock()

    def _path_for(self, role: str) -> Path:
        return self._dir / f"{role}_identity.json"

    def save(self, identity: AgentIdentity) -> None:
        """Write *identity* as JSON. Creates the directory if needed."""
        with self._lock:
            self._dir.mkdir(parents=True, exist_ok=True)
            self._path_for(identity.role).write_text(json.dumps(identity.to_dict(), indent=2))

    def load(self, role: str) -> AgentIdentity | None:
        """Return the stored identity for *role*, or ``None`` if the file does not exist."""
        with self._lock:
            path = self._path_for(role)
            if not path.exists():
                return None
            data = json.loads(path.read_text())
            return AgentIdentity.from_dict(data)

    def load_or_create(self, role: str) -> AgentIdentity:
        """Load existing identity or create a fresh one with default soul (if known)."""
        with self._lock:
            path = self._path_for(role)
            if path.exists():
                data = json.loads(path.read_text())
                return AgentIdentity.from_dict(data)

            now = AgentIdentity.now()
            identity = AgentIdentity(
                role=role,
                soul=DEFAULT_SOULS.get(role),
                traits=(),
                trust_tier="probation",
                total_generations=0,
                total_advances=0,
                created_at=now,
                last_updated=now,
                history=(),
            )
            self._dir.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(identity.to_dict(), indent=2))
            return identity

    def load_all(self) -> dict[str, AgentIdentity]:
        """Glob for ``*_identity.json`` and return a dict keyed by role."""
        with self._lock:
            result: dict[str, AgentIdentity] = {}
            if not self._dir.exists():
                return result
            for path in sorted(self._dir.glob("*_identity.json")):
                data = json.loads(path.read_text())
                identity = AgentIdentity.from_dict(data)
                result[identity.role] = identity
            return result

    def exists(self, role: str) -> bool:
        """Check whether an identity file exists for *role*."""
        with self._lock:
            return self._path_for(role).exists()
