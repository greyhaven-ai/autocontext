"""Teacher reasoning-trace collection for distillation (pure + provider-agnostic).

A *teacher* model is prompted to reason about a scenario and then emit a construction.
The teacher is any :class:`~autocontext.providers.base.LLMProvider` (a hosted model, a
local / OpenAI-compatible endpoint, or a callable in tests), so nothing here is tied
to a specific vendor or model. The teacher's construction is never trusted: it is
scored in-scenario (``evaluate_output``), and only constructions clearing a verified
score threshold become training records, each carrying the teacher's rationale.

This is the teacher-distillation counterpart to self-distillation: instead of
bootstrapping from a student's own best samples, the small student inherits reasoning
from a stronger teacher (a higher cold-start), and the two compose.

Pure helpers (prompt building, output parsing, record building) have no provider or
scenario dependency and are unit-tested directly; ``collect`` wires them to a provider
and a scenario.
"""

from __future__ import annotations

import json
import re
from typing import Any

from autocontext.providers.base import LLMProvider
from autocontext.training.autoresearch.sequence_format import resolve_scenario_context, resolve_scenario_name

_TEACHER_SYSTEM = (
    "You are an expert at constructing solutions to combinatorial and optimization problems. "
    "First reason step by step about the structure that makes a strong construction, then give "
    "your construction. Put the construction LAST as a single JSON object in a ```json code block. "
    "The reasoning should explain why the construction works; the JSON is the construction itself."
)

# A fenced ```json ... ``` block (preferred); group 1 is the JSON object.
_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def teacher_task_prompt(scenario: Any) -> str:
    """Resolve the natural-language task instruction for a scenario (teacher prompt body)."""
    get_task_prompt = getattr(scenario, "get_task_prompt", None)
    initial_state = getattr(scenario, "initial_state", None)
    if callable(get_task_prompt) and callable(initial_state):
        try:
            return str(get_task_prompt(initial_state()))
        except Exception:
            pass
    return resolve_scenario_context(scenario)


def build_teacher_prompt(scenario: Any) -> tuple[str, str]:
    """Return ``(system_prompt, user_prompt)`` instructing the teacher to reason then construct."""
    return _TEACHER_SYSTEM, teacher_task_prompt(scenario)


def parse_teacher_output(text: str) -> tuple[str, dict[str, Any]] | None:
    """Split teacher output into ``(reasoning, construction)``.

    The construction is a JSON object, preferentially inside a ```json fenced block,
    otherwise the ``{...}`` span from the first ``{`` to the last ``}``. The reasoning
    is whatever precedes that JSON (the rationale that causally leads to the
    construction). Returns ``None`` if no JSON object parses.
    """
    fenced = _FENCED_JSON_RE.search(text)
    if fenced:
        json_str = fenced.group(1)
        reasoning = text[: fenced.start()].strip()
    else:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None
        json_str = text[start : end + 1]
        reasoning = text[:start].strip()
    try:
        strategy = json.loads(json_str)
    except json.JSONDecodeError:
        return None
    if not isinstance(strategy, dict):
        return None
    return reasoning, strategy


def build_record(
    *,
    scenario: str,
    context: str,
    reasoning: str,
    strategy: dict[str, Any],
    score: float,
    run_id: str,
) -> dict[str, Any]:
    """Build one training record carrying the teacher's rationale + verified score."""
    return {
        "run_id": run_id,
        "scenario": scenario,
        "context": context,
        "reasoning": reasoning,
        "strategy": strategy,
        "score": score,
    }


def collect(
    scenario: Any,
    provider: LLMProvider,
    *,
    n_traces: int,
    model: str | None = None,
    temperature: float = 1.0,
    score_threshold: float = 0.0,
    run_id: str = "teacher",
) -> list[dict[str, Any]]:
    """Collect verified teacher reasoning traces as training records.

    For each of ``n_traces`` attempts: prompt the teacher provider, parse its
    reasoning + construction, score the construction in-scenario, and keep it as a
    record only if the verified score is at least ``score_threshold``. Unparseable
    outputs and provider errors are skipped (never pollute the dataset).
    """
    system, user = build_teacher_prompt(scenario)
    context = teacher_task_prompt(scenario)
    name = resolve_scenario_name(scenario)
    # Dual scenario interface (mirrors assess_strategy_quality): game scenarios score
    # a strategy via execute_match; agent-task scenarios via evaluate_output. Without
    # this, valid teacher JSON for the built-in execute_match scenarios is swallowed.
    is_game = hasattr(scenario, "execute_match")
    records: list[dict[str, Any]] = []
    for i in range(max(0, n_traces)):
        try:
            result = provider.complete(system, user, model=model, temperature=temperature)
        except Exception:
            continue
        parsed = parse_teacher_output(result.text)
        if parsed is None:
            continue
        reasoning, strategy = parsed
        try:
            if is_game:
                score = float(scenario.execute_match(strategy, seed=i).score)
            else:
                score = float(scenario.evaluate_output(output=json.dumps(strategy)).score)
        except Exception:
            continue
        if score < score_threshold:
            continue
        records.append(
            build_record(
                scenario=name,
                context=context,
                reasoning=reasoning,
                strategy=strategy,
                score=score,
                run_id=run_id,
            )
        )
    return records
