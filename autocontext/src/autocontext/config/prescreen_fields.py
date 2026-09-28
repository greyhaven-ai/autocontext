"""Settings for the judge pre-screen (docs/internal/judge-prescreen-design.md)."""

from __future__ import annotations

from pydantic import BaseModel, Field  # type: ignore[import-not-found]


class PrescreenFields(BaseModel):
    judge_ledger_enabled: bool = Field(
        default=False,
        description="Record every improvement-loop judge verdict in the judge_ledger table (judge pre-screen Phase 0)",
    )
