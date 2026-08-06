"""Per-role output-token budget fields (AC-905).

Extracted from AppSettings to keep config/settings.py under the module
size limit. Defaults preserve the previous hard-coded literals, except
the translator floor which rises from 400/200 to 1024 (strategy JSON at
200 tokens truncated routinely and the failure was silently swallowed
upstream before AC-904).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class OutputBudgetFields(BaseModel):
    """Mixin holding the per-role output-token budgets."""

    competitor_max_tokens: int = Field(default=800, ge=256)
    translator_max_tokens: int = Field(default=1024, ge=256)
    analyst_max_tokens: int = Field(default=1200, ge=256)
    coach_max_tokens: int = Field(default=2000, ge=256)
    architect_max_tokens: int = Field(default=1600, ge=256)
    curator_max_tokens: int = Field(default=3000, ge=256)
    curator_rating_max_tokens: int = Field(default=1200, ge=256)
    curator_consolidation_max_tokens: int = Field(default=4000, ge=256)
    skeptic_max_tokens: int = Field(default=2000, ge=256)
    scenario_designer_max_tokens: int = Field(default=3000, ge=256)
    # solve-on-demand uses a deliberately tighter designer budget
    solve_designer_max_tokens: int = Field(default=1200, ge=256)
    train_codegen_max_tokens: int = Field(default=8000, ge=256)
