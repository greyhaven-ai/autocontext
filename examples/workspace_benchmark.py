"""AC-901 benchmark: persistent interpreter workspace vs serialize-everything.

Deterministic (no LLM, no network): a fixed slate of priority-function
candidates runs against a cap-sets-style greedy harness over a pool of
F_3^7 vectors. Baseline mode carries the pool inside the whole-program
best_output, re-serialized into every enriched prompt; workspace mode
keeps the pool as a persistent interpreter variable referenced by name.

Run from the ``autocontext/`` package directory:

    uv run --frozen python ../examples/workspace_benchmark.py
"""

from __future__ import annotations

import random
import time

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

GENERATIONS = 5


def make_pool(n: int = 250, dim: int = 7, seed: int = 7) -> list[tuple[int, ...]]:
    rng = random.Random(seed)
    return sorted({tuple(rng.randrange(3) for _ in range(dim)) for _ in range(n)})


def _parse_size(result) -> int:
    if result.error is None and result.answer.get("content"):
        return int(result.answer["content"])
    return 0


def run_workspace(pool: list[tuple[int, ...]]) -> tuple[float, list[int], float]:
    def factory() -> InterpreterWorkspace:
        return InterpreterWorkspace(seed={"pool": list(pool)})

    def workspace_evaluate(program: str, gen: int, ws: InterpreterWorkspace) -> AgentTaskGenerationEvaluation:
        size = _parse_size(ws.run(program))
        return AgentTaskGenerationEvaluation(output=program, score=size / 100.0, reasoning="deterministic")

    runner = AgentTaskEvolutionRunner(
        task_prompt="Find a large cap set from the workspace variable `pool`.",
        generate_fn=lambda prompt, gen: SLOTS[gen % len(SLOTS)],
        evaluate_fn=lambda output, gen: AgentTaskGenerationEvaluation(output=output, score=0.0, reasoning=""),
        slot=FunctionSlot(harness=GREEDY_HARNESS),
        workspace_factory=factory,
        workspace_evaluate_fn=workspace_evaluate,
    )
    start = time.perf_counter()
    _, state = runner.run_with_state(GENERATIONS)
    elapsed = time.perf_counter() - start
    return state.best_score, [len(p) for p in state.metadata["generation_prompts"]], elapsed


def run_baseline(pool: list[tuple[int, ...]]) -> tuple[float, list[int], float]:
    def generate_fn(prompt: str, gen: int) -> str:
        return f"pool = {pool!r}\n\n{SLOTS[gen % len(SLOTS)]}\n\n{GREEDY_HARNESS}"

    def evaluate_fn(program: str, gen: int) -> AgentTaskGenerationEvaluation:
        scratch = InterpreterWorkspace()
        try:
            size = _parse_size(scratch.run(program))
        finally:
            scratch.close()
        return AgentTaskGenerationEvaluation(output=program, score=size / 100.0, reasoning="deterministic")

    runner = AgentTaskEvolutionRunner(
        task_prompt="Find a large cap set from the pool defined in the program.",
        generate_fn=generate_fn,
        evaluate_fn=evaluate_fn,
    )
    start = time.perf_counter()
    _, state = runner.run_with_state(GENERATIONS)
    elapsed = time.perf_counter() - start
    return state.best_score, [len(p) for p in state.metadata["generation_prompts"]], elapsed


def main() -> None:
    pool = make_pool()
    ws_score, ws_prompts, ws_time = run_workspace(pool)
    base_score, base_prompts, base_time = run_baseline(pool)

    print(f"pool: {len(pool)} vectors in F_3^7, {GENERATIONS} generations, deterministic slate")
    print()
    print(f"{'generation':>10} | {'baseline prompt chars':>22} | {'workspace prompt chars':>23}")
    for i, (b, w) in enumerate(zip(base_prompts, ws_prompts, strict=True), start=1):
        print(f"{i:>10} | {b:>22} | {w:>23}")
    mean_base = sum(base_prompts) / len(base_prompts)
    mean_ws = sum(ws_prompts) / len(ws_prompts)
    print(f"{'mean':>10} | {mean_base:>22.0f} | {mean_ws:>23.0f}")
    print()
    print(f"best score: baseline {base_score:.2f}, workspace {ws_score:.2f} (must match)")
    print(f"wall time:  baseline {base_time:.3f}s, workspace {ws_time:.3f}s")
    print(f"prompt compression: workspace mean is {mean_ws / mean_base:.1%} of baseline mean")


if __name__ == "__main__":
    main()
