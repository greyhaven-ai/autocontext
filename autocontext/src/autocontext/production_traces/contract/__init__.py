"""Contract sub-package: branded IDs + generated Pydantic models + JSON Schemas.

The models module is auto-generated from the canonical TS schemas via
`ts/scripts/sync-python-production-traces-schemas.mjs`. Do NOT edit
`models.py` by hand — CI enforces drift-free regeneration.
"""

from autocontext.production_traces.contract.branded_ids import (
    AppId,
    FeedbackRefId,
    ProductionTraceId,
    SessionIdHash,
    UserIdHash,
)
from autocontext.production_traces.contract.models import ProductionTrace

__all__ = [
    "AppId",
    "FeedbackRefId",
    "ProductionTrace",
    "ProductionTraceId",
    "SessionIdHash",
    "UserIdHash",
]
