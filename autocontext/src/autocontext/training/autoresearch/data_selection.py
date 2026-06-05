"""Training-data curation for autoresearch (pure domain, no MLX/torch).

Two record-level transforms applied before tokenization:

- ``dedupe_records`` removes duplicate constructions. Identical strategies inflate
  the effective dataset and bias the model toward whatever was sampled most often;
  near-duplicates do the same more subtly. Keeps the highest-scoring representative.
- ``select_top_fraction`` keeps only the highest-scoring fraction of records
  (elite filtering). Training the generator on the best constructions is the core
  move of rejection-sampling fine-tuning / ReST / expert iteration: imitate the
  top of the distribution rather than its mean.

``curate_records`` composes them (dedupe first, then elite filter). All functions
are pure and operate on the JSONL record dicts (``strategy`` + ``score`` fields).
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from typing import Any


def _strategy_key(record: dict[str, Any]) -> str:
    """Canonical, order-insensitive string for a record's strategy."""
    return json.dumps(record.get("strategy"), sort_keys=True)


def _score(record: dict[str, Any]) -> float:
    try:
        return float(record.get("score", 0.0))
    except (TypeError, ValueError):
        return 0.0


def _shingles(text: str, size: int = 4) -> set[str]:
    """Character n-gram shingles for cheap near-duplicate detection."""
    if len(text) <= size:
        return {text}
    return {text[i : i + size] for i in range(len(text) - size + 1)}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def dedupe_records(records: Sequence[dict[str, Any]], *, near_threshold: float = 1.0) -> list[dict[str, Any]]:
    """Remove duplicate constructions, keeping the highest-scoring representative.

    ``near_threshold == 1.0`` (default) removes only exact duplicates (identical
    canonical strategy JSON). ``near_threshold < 1.0`` additionally drops records
    whose strategy shingle-Jaccard similarity to an already-kept record is at least
    the threshold. Order of survivors follows descending score so the strongest
    representative of each group is the one retained.

    Raises ``ValueError`` for ``near_threshold`` outside ``(0, 1]`` (a threshold of
    0 would collapse all records to a single representative).
    """
    if not 0.0 < near_threshold <= 1.0:
        raise ValueError(f"near_threshold must be in (0, 1], got {near_threshold}")
    # Sort by score desc so the first record seen for any (near-)duplicate group is
    # the best one; ties keep original order (stable sort).
    ordered = sorted(records, key=_score, reverse=True)

    kept: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    kept_shingles: list[set[str]] = []
    do_near = near_threshold < 1.0
    for record in ordered:
        key = _strategy_key(record)
        if key in seen_keys:
            continue
        if do_near:
            shingles = _shingles(key)
            if any(_jaccard(shingles, ks) >= near_threshold for ks in kept_shingles):
                continue
            kept_shingles.append(shingles)
        seen_keys.add(key)
        kept.append(record)
    return kept


def select_top_fraction(records: Sequence[dict[str, Any]], fraction: float) -> list[dict[str, Any]]:
    """Keep the highest-scoring ``fraction`` of records (at least one).

    ``fraction == 1.0`` returns all records unchanged (preserving order); otherwise
    the top ``ceil(n * fraction)`` records by score are returned, highest first.

    Raises ``ValueError`` for ``fraction`` outside ``(0, 1]`` rather than clamping
    (a clamped fraction would silently collapse the dataset to its single top record).
    """
    if not 0.0 < fraction <= 1.0:
        raise ValueError(f"fraction must be in (0, 1], got {fraction}")
    if fraction == 1.0:
        return list(records)
    n = len(records)
    if n == 0:
        return []
    keep = max(1, math.ceil(n * fraction))
    return sorted(records, key=_score, reverse=True)[:keep]


def curate_records(
    records: Sequence[dict[str, Any]],
    *,
    elite_fraction: float = 1.0,
    dedupe: bool = False,
    near_threshold: float = 1.0,
) -> list[dict[str, Any]]:
    """Dedupe (optional) then elite-filter. No-op when both are at their defaults."""
    out: list[dict[str, Any]] = list(records)
    if dedupe:
        out = dedupe_records(out, near_threshold=near_threshold)
    out = select_top_fraction(out, elite_fraction)
    return out


def prepare_training_records(
    records: Sequence[dict[str, Any]],
    *,
    augmenter_spec: str = "",
    elite_fraction: float = 1.0,
    dedupe: bool = False,
    near_threshold: float = 1.0,
) -> list[dict[str, Any]]:
    """Augment (optional) then curate the training records.

    The augmentation seam expands the records by a domain symmetry/transform referenced
    via an ``"module:function"`` spec (see ``augment.py``); curation then dedupes +
    elite-filters the expanded set, so symmetry-equivalent duplicates are pruned and
    the elite fraction is taken over the augmented pool. A no-op when all args default.
    """
    from autocontext.training.autoresearch.augment import apply_augmentation, resolve_augmenter

    out = apply_augmentation(list(records), resolve_augmenter(augmenter_spec))
    return curate_records(out, elite_fraction=elite_fraction, dedupe=dedupe, near_threshold=near_threshold)
