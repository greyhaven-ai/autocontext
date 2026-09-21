"""Content-fingerprint cache for verifier and judge verdicts (AC-902).

Re-evaluating a byte-identical artifact burns compute to learn nothing:
prime-agent refuses to rerun a failed gate when the workspace has not
changed, and this module is the core seam for the same discipline here.
The cache is loop-lifetime by design; durable caching (e.g. the Lean
oracle keyed on file hashes plus toolchain pins) belongs to the adapter
layer that consumes this seam.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any


def content_fingerprint(artifact: str, *, salt: str = "") -> str:
    """Stable identity of a verified artifact.

    ``salt`` distinguishes evaluation configurations (toolchain pin, rubric
    id) so the same text under a different gate is a different fingerprint.
    """
    digest = hashlib.sha256()
    digest.update(salt.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(artifact.encode("utf-8"))
    return digest.hexdigest()


@dataclass(slots=True)
class CachedVerdict:
    """One remembered evaluation outcome for a fingerprint."""

    score: float
    reasoning: str
    dimension_scores: dict[str, float]
    passed: bool
    vetoed: bool = False
    evaluator_epoch: str | None = None
    evaluator_spec: str | None = None
    execution_provenance: dict[str, Any] = field(default_factory=dict)
    fixture_provenance: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class EvaluationCache:
    """In-memory fingerprint -> verdict cache with hit/miss accounting."""

    _entries: dict[str, CachedVerdict] = field(default_factory=dict)
    _hits: int = 0
    _misses: int = 0

    def get(self, fingerprint: str, *, expected_evaluator_epoch: str | None = None) -> CachedVerdict | None:
        cached = self._entries.get(fingerprint)
        # AC-1026 can supply a trusted pinned identity. An old manifest alone
        # cannot establish which evaluator would serve a new request.
        if (cached is None
                or (cached.evaluator_spec is not None and expected_evaluator_epoch is None)
                or (expected_evaluator_epoch is not None and cached.evaluator_epoch != expected_evaluator_epoch)):
            self._misses += 1
            return None
        self._hits += 1
        return cached

    def put(self, fingerprint: str, verdict: CachedVerdict) -> None:
        self._entries[fingerprint] = verdict

    def unchanged_failure(self, fingerprint: str) -> bool:
        """True when this exact artifact already failed: re-running learns nothing."""
        cached = self._entries.get(fingerprint)
        return cached is not None and not cached.passed

    def stats(self) -> dict[str, int]:
        return {"hits": self._hits, "misses": self._misses, "entries": len(self._entries)}
