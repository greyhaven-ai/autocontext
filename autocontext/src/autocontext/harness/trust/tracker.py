"""Trust tracker — tracks trust scores for all roles with persistence and audit integration."""

from __future__ import annotations

import threading
from pathlib import Path

from autocontext.harness.audit.types import AuditCategory, AuditEntry
from autocontext.harness.audit.writer import AppendOnlyAuditWriter
from autocontext.harness.meta.profiler import PerformanceProfiler
from autocontext.harness.trust.policy import TrustPolicy
from autocontext.harness.trust.types import TrustBudget, TrustScore
from autocontext.util.json_io import read_json, write_json


class TrustTracker:
    """Tracks trust scores for all roles with persistence and audit integration."""

    def __init__(
        self,
        policy: TrustPolicy,
        profiler: PerformanceProfiler | None = None,
        audit_writer: AppendOnlyAuditWriter | None = None,
    ) -> None:
        self._policy = policy
        self._profiler = profiler
        self._audit_writer = audit_writer
        self._scores: dict[str, TrustScore] = {}
        self._lock = threading.Lock()

    def evaluate_all(self) -> dict[str, TrustScore]:
        """Evaluate all roles from the profiler and update internal scores.

        If profiler is set, get all_profiles(), evaluate each, detect tier changes,
        audit tier changes, and update internal scores. Returns current scores.
        """
        if self._profiler is None:
            return {}

        profiles = self._profiler.all_profiles()
        new_scores: dict[str, TrustScore] = {}

        with self._lock:
            for role, profile in profiles.items():
                new_score = self._policy.evaluate(profile)
                old_score = self._scores.get(role)

                # Detect tier change and audit
                if old_score is not None and old_score.tier != new_score.tier:
                    self._audit_tier_change(role, old_score.tier.value, new_score.tier.value)

                new_scores[role] = new_score

            self._scores.update(new_scores)
            return dict(self._scores)

    def score_for(self, role: str) -> TrustScore | None:
        """Look up the trust score for a specific role."""
        with self._lock:
            return self._scores.get(role)

    def budget_for(self, role: str) -> TrustBudget | None:
        """Look up the trust budget for a specific role."""
        with self._lock:
            score = self._scores.get(role)
        if score is None:
            return None
        return self._policy.budget_for(score)

    def all_scores(self) -> dict[str, TrustScore]:
        """Return a copy of all current trust scores."""
        with self._lock:
            return dict(self._scores)

    def save(self, path: Path) -> None:
        """Persist current trust scores to a JSON file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            data = {role: score.to_dict() for role, score in self._scores.items()}
        write_json(path, data)

    def load(self, path: Path) -> None:
        """Restore trust scores from a JSON file."""
        if not path.exists():
            return
        raw = read_json(path)
        with self._lock:
            self._scores = {role: TrustScore.from_dict(d) for role, d in raw.items()}

    def summary(self) -> str:
        """Return a human-readable table of current trust scores."""
        with self._lock:
            scores = dict(self._scores)

        if not scores:
            return "No trust scores available."

        lines = [
            "| Role | Tier | Raw | Confidence | Observations |",
            "|------|------|-----|------------|--------------|",
        ]
        for role in sorted(scores):
            s = scores[role]
            lines.append(
                f"| {s.role} | {s.tier.value} | {s.raw_score:.4f} | {s.confidence:.4f} | {s.observations} |"
            )
        return "\n".join(lines)

    def _audit_tier_change(self, role: str, old_tier: str, new_tier: str) -> None:
        """Write an audit entry for a tier change. Must be called under lock."""
        if self._audit_writer is None:
            return
        entry = AuditEntry(
            timestamp=AuditEntry.now(),
            category=AuditCategory.CONFIG_CHANGE,
            actor="trust_tracker",
            action=f"tier_change:{role}",
            detail=f"{old_tier} -> {new_tier}",
        )
        self._audit_writer.append(entry)
