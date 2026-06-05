"""Pluggable training-record augmentation seam (domain-agnostic).

Symmetry / transform augmentation multiplies effective training data: for many
research domains a construction has equivalent variants under a group action (e.g.
affine maps over F_q for cap-sets), each with the same score. Generating those
variants as extra training records is a cheap, high-leverage data multiplier.

The transforms are domain-specific, so they do NOT live here. Core provides only
the seam: an augmenter is referenced by a ``"package.module:function"`` spec and
resolved by dynamic import, so the actual symmetry code lives in the consumer repo
and core stays domain-agnostic. The augmenter takes the training records and returns
the (expanded) list to train on, giving it full control (it may cap, dedupe, etc.).
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

# An augmenter maps the training records onto the (expanded) records to train on.
RecordAugmenter = Callable[[list[dict[str, Any]]], list[dict[str, Any]]]


def resolve_augmenter(spec: str) -> RecordAugmenter | None:
    """Resolve a ``"package.module:function"`` spec to a record-augmenter callable.

    Returns ``None`` for an empty spec (no augmentation). Raises ``ValueError`` on a
    malformed spec, an unimportable module, or a missing/non-callable attribute, so a
    typo fails fast instead of silently skipping augmentation.
    """
    spec = spec.strip()
    if not spec:
        return None
    if spec.count(":") != 1:
        raise ValueError(f"augmenter spec must be 'package.module:function', got {spec!r}")
    module_path, func_name = spec.split(":")
    if not module_path or not func_name:
        raise ValueError(f"augmenter spec must be 'package.module:function', got {spec!r}")
    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        raise ValueError(f"could not import augmenter module {module_path!r}: {exc}") from exc
    fn = getattr(module, func_name, None)
    if not callable(fn):
        raise ValueError(f"augmenter {spec!r} did not resolve to a callable")
    return fn  # type: ignore[no-any-return]


def apply_augmentation(records: list[dict[str, Any]], augmenter: RecordAugmenter | None) -> list[dict[str, Any]]:
    """Apply ``augmenter`` to ``records`` (no-op when ``None``), validating the output.

    The augmenter must return a non-empty list of record dicts; anything else is a
    contract violation and raises ``ValueError`` (rather than silently training on
    bad/empty data). Logs the before/after counts so the expansion is visible.
    """
    if augmenter is None:
        return list(records)
    augmented = augmenter(list(records))
    if not isinstance(augmented, list) or not augmented:
        raise ValueError("augmenter must return a non-empty list of records")
    if not all(isinstance(r, dict) for r in augmented):
        raise ValueError("augmenter must return a list of record dicts")
    logger.info("training.autoresearch.augment: %d records -> %d after augmentation", len(records), len(augmented))
    return augmented
