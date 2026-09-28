"""Capture of improvement-loop judge verdicts for the judge pre-screen.

Phase 0 of docs/internal/judge-prescreen-design.md: one row per real judge call, written to the local
``judge_ledger`` table. Capture never changes loop behavior, and a failed write is logged, never raised.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from autocontext.config.settings import AppSettings
    from autocontext.execution.improvement_results import RoundResult
    from autocontext.scenarios.agent_task import AgentTaskInterface, AgentTaskResult

logger = logging.getLogger(__name__)


class JudgeLedgerStore(Protocol):
    def insert_judge_ledger_row(self, values: Mapping[str, Any]) -> int: ...


def text_hash(text: str) -> str:
    """Stable content id for prompts, rubrics and outputs."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class JudgeLedger:
    """Records one row per real judge call made by one improvement loop."""

    def __init__(self, store: JudgeLedgerStore, *, run_id: str | None, scenario_name: str, scenario_family: str) -> None:
        self.store = store
        self.run_id = run_id
        self.scenario_name = scenario_name
        self.scenario_family = scenario_family
        self.loop_id = uuid.uuid4().hex
        self._task_identity: tuple[str, str, str] | None = None

    def _identity(self, task: AgentTaskInterface, state: dict) -> tuple[str, str, str]:
        # The prompt and rubric are fixed for one loop, so they are read and hashed once.
        if self._task_identity is None:
            prompt = task.get_task_prompt(state)
            self._task_identity = (prompt, text_hash(prompt), text_hash(task.get_rubric()))
        return self._task_identity

    def record(
        self,
        task: AgentTaskInterface,
        state: dict,
        *,
        round_num: int,
        output: str,
        result: AgentTaskResult,
        judge_failed: bool,
        previous: RoundResult | None,
        max_rounds: int,
        quality_threshold: float,
        required_concepts: Sequence[str] | None,
    ) -> None:
        """Append this round's verdict; any failure is logged so capture can never break a run."""
        try:
            prompt, prompt_hash, rubric_hash = self._identity(task, state)
            guardrail = result.evaluator_guardrail
            self.store.insert_judge_ledger_row(
                {
                    "loop_id": self.loop_id,
                    "run_id": self.run_id,
                    "scenario_name": self.scenario_name,
                    "scenario_family": self.scenario_family,
                    "round_number": round_num,
                    "max_rounds": max_rounds,
                    "quality_threshold": quality_threshold,
                    "evaluator_epoch": result.evaluator_epoch,
                    "rubric_hash": rubric_hash,
                    "task_prompt_hash": prompt_hash,
                    "task_prompt": prompt,
                    "output_hash": text_hash(output),
                    "output": output,
                    "required_concepts_json": json.dumps(list(required_concepts or [])),
                    "score": float(result.score),
                    "passed": int(result.score >= quality_threshold),
                    "dimension_scores_json": json.dumps(dict(result.dimension_scores), sort_keys=True),
                    "internal_retries": result.internal_retries,
                    "judge_failed": int(judge_failed),
                    "previous_round_number": previous.round_number if previous is not None else None,
                    "previous_score": previous.score if previous is not None else None,
                    "previous_dimension_scores_json": (
                        json.dumps(dict(previous.dimension_scores), sort_keys=True) if previous is not None else None
                    ),
                    "previous_output_hash": text_hash(previous.output) if previous is not None else None,
                    "fixture_provenance_json": json.dumps(dict(result.fixture_provenance), sort_keys=True),
                    "evaluator_guardrail_json": (
                        json.dumps(guardrail, sort_keys=True, default=str) if guardrail is not None else None
                    ),
                }
            )
        except Exception:
            logger.warning("judge ledger write failed; the improvement loop continues", exc_info=True)


def ledger_for(
    settings: AppSettings,
    store: JudgeLedgerStore,
    *,
    run_id: str | None,
    scenario_name: str,
    scenario_family: str = "agent_task",
) -> JudgeLedger | None:
    """A ledger when ``judge_ledger_enabled`` is exactly True (AUTOCONTEXT_JUDGE_LEDGER_ENABLED), otherwise None."""
    if getattr(settings, "judge_ledger_enabled", False) is not True:
        return None
    return JudgeLedger(store, run_id=run_id, scenario_name=scenario_name, scenario_family=scenario_family)
