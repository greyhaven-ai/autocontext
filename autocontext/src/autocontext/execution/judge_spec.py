"""Immutable serving identity, separate from evaluation fixture and sampling provenance.

The canonical wire format contains only strings, string arrays and null: no float
serialization or language-specific object ordering can change a digest. Examples
are the exact ordered strings rendered into the judge prompt, not database rows.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict


class JudgeServingSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal["autocontext.judge-serving.v1"] = "autocontext.judge-serving.v1"
    compiled_rubric: str
    judge_provider: str
    judge_model: str
    prompt_template_version: str
    serving_examples: tuple[str, ...] = ()
    pinned_dimensions: tuple[str, ...] = ()
    score_transformations: tuple[str, ...]
    # Reserved for the handler fingerprint owned by AC-1001; never hook source or credentials.
    extension_fingerprint: str | None = None
    # A reusable private judging contract (not a per-task reference/answer).
    evaluation_context_hash: str | None = None

    def canonical_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    @property
    def epoch_id(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    def require_epoch(self, epoch_id: str) -> None:
        if self.epoch_id != epoch_id:
            raise ValueError("Judge serving specification does not match evaluator epoch")


def fixture_provenance(
    task_prompt: str, agent_output: str, reference_context: str | None, required_concepts: list[str],
) -> dict[str, str]:
    """Task-specific evidence stays outside the reusable evaluator identity."""
    payload = json.dumps(
        {"task_prompt": task_prompt, "agent_output": agent_output,
         "reference_context": reference_context, "required_concepts": required_concepts},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    return {"schema_version": "autocontext.judge-fixture.v1", "sha256": hashlib.sha256(payload.encode()).hexdigest()}
