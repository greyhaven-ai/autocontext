"""Contract sub-package: generated Pydantic models + JSON Schemas.

The models module is auto-generated from the canonical TS schemas via
`ts/scripts/sync-python-harness-optimization-schemas.mjs`. Do NOT edit
`models.py` by hand — CI enforces drift-free regeneration.
"""

from autocontext.harness_optimization.contract.models import (
    CandidateEvidence,
    PromotionScore,
)

__all__ = [
    "CandidateEvidence",
    "PromotionScore",
]
