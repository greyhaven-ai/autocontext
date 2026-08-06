"""Workspace vs serialize-everything baseline (AC-901 acceptance benchmark).

Cap-sets-style deterministic comparison: the working state is a pool of
F_3^dim vectors, candidates are priority functions for a fixed greedy
harness. Baseline mode carries the pool inside the whole-program
``best_output`` (re-serialized into every enriched prompt); workspace mode
keeps the pool as a persistent interpreter variable referenced by name.
Both must reach the same score; workspace prompts must be far smaller.
"""

from __future__ import annotations

import random

from autocontext.execution.agent_task_evolution import (
    AgentTaskEvolutionRunner,
    AgentTaskGenerationEvaluation,
    FunctionSlot,
)
from autocontext.execution.interpreter_workspace import InterpreterWorkspace

GREEDY_HARNESS = """\
def _is_line(a, b, c):
    return all((x + y + z) % 3 == 0 for x, y, z in zip(a, b, c))

def build_cap(pool, priority):
    chosen = []
    for v in sorted(pool, key=priority, reverse=True):
        if all(not _is_line(v, a, b) for i, a in enumerate(chosen) for b in chosen[:i]):
            chosen.append(v)
    return chosen

cap = build_cap(pool, priority)
answer["content"] = str(len(cap))
answer["ready"] = True
"""

SLOTS = [
    "def priority(v):\n    return 0",
    "def priority(v):\n    return sum(v)",
    "def priority(v):\n    return -sum(v)",
    "def priority(v):\n    return v.count(0)",
    "def priority(v):\n    return sum(x * x for x in v)",
]


def make_pool(n: int = 250, dim: int = 7, seed: int = 7) -> list[tuple[int, ...]]:
    rng = random.Random(seed)
    vectors = {tuple(rng.randrange(3) for _ in range(dim)) for _ in range(n)}
    return sorted(vectors)


def _score(size: int) -> float:
    return size / 100.0


def run_workspace_mode(pool: list[tuple[int, ...]], generations: int) -> tuple[float, list[str], list[int]]:
    """Returns (best_score, generation_prompts, history_lengths_seen)."""
    history_lengths: list[int] = []

    def factory() -> InterpreterWorkspace:
        return InterpreterWorkspace(seed={"pool": list(pool), "history": []})

    def workspace_evaluate(program: str, gen: int, ws: InterpreterWorkspace) -> AgentTaskGenerationEvaluation:
        result = ws.run(program)
        size = int(result.answer["content"]) if result.error is None and result.answer.get("content") else 0
        history_result = ws.run(f"history.append({size})\nlen(history)")
        history_lengths.append(int(history_result.stdout.strip()))
        return AgentTaskGenerationEvaluation(output=program, score=_score(size), reasoning="deterministic")

    runner = AgentTaskEvolutionRunner(
        task_prompt="Find a large cap set from the workspace variable `pool`.",
        generate_fn=lambda prompt, gen: SLOTS[gen % len(SLOTS)],
        evaluate_fn=lambda output, gen: AgentTaskGenerationEvaluation(output=output, score=0.0, reasoning=""),
        slot=FunctionSlot(harness=GREEDY_HARNESS),
        workspace_factory=factory,
        workspace_evaluate_fn=workspace_evaluate,
    )
    _, state = runner.run_with_state(generations)
    return state.best_score, list(state.metadata["generation_prompts"]), history_lengths


def run_baseline_mode(pool: list[tuple[int, ...]], generations: int) -> tuple[float, list[str]]:
    """Whole-program mode: the pool is serialized into every candidate program."""

    def generate_fn(prompt: str, gen: int) -> str:
        return f"pool = {pool!r}\n\n{SLOTS[gen % len(SLOTS)]}\n\n{GREEDY_HARNESS}"

    def evaluate_fn(program: str, gen: int) -> AgentTaskGenerationEvaluation:
        scratch = InterpreterWorkspace()
        try:
            result = scratch.run(program)
            size = int(result.answer["content"]) if result.error is None and result.answer.get("content") else 0
        finally:
            scratch.close()
        return AgentTaskGenerationEvaluation(output=program, score=_score(size), reasoning="deterministic")

    runner = AgentTaskEvolutionRunner(
        task_prompt="Find a large cap set from the pool defined in the program.",
        generate_fn=generate_fn,
        evaluate_fn=evaluate_fn,
    )
    _, state = runner.run_with_state(generations)
    return state.best_score, list(state.metadata["generation_prompts"])


def test_workspace_matches_baseline_score_with_far_smaller_prompts() -> None:
    pool = make_pool()
    generations = 5

    ws_best, ws_prompts, history_lengths = run_workspace_mode(pool, generations)
    base_best, base_prompts = run_baseline_mode(pool, generations)

    assert ws_best > 0
    assert ws_best == base_best

    # The pool never leaks into workspace prompts; the baseline re-serializes
    # it (until semantic compaction caps the carried output, an orthogonal
    # mitigation that shrinks but does not remove the serialized state).
    mean_ws = sum(len(p) for p in ws_prompts) / len(ws_prompts)
    mean_base = sum(len(p) for p in base_prompts) / len(base_prompts)
    assert mean_ws < 0.5 * mean_base

    # Content check: a pool vector's serialization appears in baseline
    # prompts from generation 2 onward (carried best_output) but never in
    # any workspace prompt. The workspace section shows only a truncated
    # 120-char summary (roughly the first five vectors), so an element
    # deeper in the pool can never appear there; it is still early enough
    # to survive compaction's head-keeping truncation in the baseline.
    marker = repr(pool[10])
    assert marker in base_prompts[1]
    assert all(marker not in prompt for prompt in ws_prompts)

    # Persistence: the history variable accumulated one entry per generation
    # inside the same interpreter (state written in generation N is visible
    # in generation N+1).
    assert history_lengths == list(range(1, generations + 1))


def test_workspace_prompts_stay_flat_as_generations_accumulate() -> None:
    pool = make_pool()
    _, ws_prompts, _ = run_workspace_mode(pool, 5)
    # Prompt size stays flat (workspace section lists names, not contents):
    # later generations may add lessons but never the serialized pool.
    assert max(len(p) for p in ws_prompts) < 3000
