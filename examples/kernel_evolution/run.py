"""Run the deterministic, single-problem kernel-evolution vertical slice.

From the ``autocontext/`` package directory:

    uv run --frozen python ../examples/kernel_evolution/run.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from autocontext.kernel_evolution import (
    ExternalKernelBenchmarkRunner,
    KernelBenchmarkEvaluator,
    KernelBenchmarkEvaluatorConfig,
    KernelEvolutionConfig,
    KernelEvolutionRunner,
)

EXAMPLE_DIR = Path(__file__).resolve().parent
PROBLEM = EXAMPLE_DIR / "problem.json"
CONFIRMATION_PROBLEM = EXAMPLE_DIR / "confirmation_problem.json"
ADAPTER = EXAMPLE_DIR / "fake_kernelbench_adapter.py"
BASELINE = EXAMPLE_DIR / "baseline.py"

CANDIDATES = [
    """\
# fake-kernel-correct: false
# fake-kernel-latency-ms: 0.010
class ModelNew:
    def __call__(self, left, right):
        return left
""",
    """\
# fake-kernel-correct: true
# fake-kernel-latency-ms: 0.097
class ModelNew:
    def __call__(self, left, right):
        return [a + b for a, b in zip(left, right, strict=True)]
""",
    """\
# fake-kernel-correct: true
# fake-kernel-latency-ms: 0.088
class ModelNew:
    def __call__(self, left, right):
        return [a + b for a, b in zip(left, right, strict=True)]
""",
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("runs/kernel-evolution"))
    args = parser.parse_args()

    def make_evaluator(problem: Path) -> KernelBenchmarkEvaluator:
        external = ExternalKernelBenchmarkRunner(
            [
                sys.executable,
                str(ADAPTER),
                "--candidate",
                "{candidate}",
                "--incumbent",
                "{incumbent}",
                "--artifact-identity-version",
                "{artifact_identity_version}",
                "--candidate-artifact-digest",
                "{candidate_artifact_digest}",
                "--incumbent-artifact-digest",
                "{incumbent_artifact_digest}",
                "--candidate-source-digest",
                "{candidate_source_digest}",
                "--incumbent-source-digest",
                "{incumbent_source_digest}",
                "--candidate-source-suffix",
                "{candidate_source_suffix}",
                "--incumbent-source-suffix",
                "{incumbent_source_suffix}",
                "--candidate-entrypoint",
                "{candidate_entrypoint}",
                "--incumbent-entrypoint",
                "{incumbent_entrypoint}",
                "--report",
                "{report}",
                "--problem",
                str(problem),
            ],
            trusted_unsafe=True,
            immutable_paths=[ADAPTER, problem],
        )
        return KernelBenchmarkEvaluator(
            external,
            KernelBenchmarkEvaluatorConfig(
                problem_id="kernelbench-demo-level1-problem1",
                min_timing_blocks=10,
                bootstrap_samples=2_000,
            ),
        )

    evaluator = make_evaluator(PROBLEM)
    confirmation_evaluator = make_evaluator(CONFIRMATION_PROBLEM)

    def confirm(candidate, incumbent):
        fresh_baseline = confirmation_evaluator.evaluate(incumbent, incumbent)
        if not fresh_baseline.eligible:
            return None
        return confirmation_evaluator.evaluate(
            candidate,
            incumbent,
            expected_scope_id=fresh_baseline.hardware_scope_id,
            expected_baseline_id=fresh_baseline.baseline_id,
            expected_protocol_id=fresh_baseline.protocol_id,
        )

    def generate(_prompt: str, generation: int) -> str:
        return CANDIDATES[generation]

    runner = KernelEvolutionRunner(
        KernelEvolutionConfig(
            problem_id="kernelbench-demo-level1-problem1",
            task_prompt=(
                "Optimize the fixed vector-add ModelNew kernel. Preserve its ABI and semantics; "
                "return only complete source. Benchmark feedback is authoritative."
            ),
            baseline_source=BASELINE.read_text(encoding="utf-8"),
            min_relative_improvement=0.05,
        ),
        generate,
        evaluator,
        args.output,
        confirmation_fn=confirm,
    )
    result = runner.run(proposals=len(CANDIDATES))

    print(f"run: {runner.run_dir}")
    print(f"scope: {result.hardware_scope_id}")
    print()
    print(f"{'generation':>10}  {'decision':>9}  {'reason':>26}  {'improvement':>12}")
    for attempt in result.attempts:
        improvement = "-" if attempt.relative_improvement is None else f"{attempt.relative_improvement:.2%}"
        print(f"{attempt.generation:>10}  {attempt.decision:>9}  {attempt.reason:>26}  {improvement:>12}")
    print()
    print(f"champion: {result.champion_artifact_digest}")
    print(f"speedup vs reference: {result.champion_speedup_vs_reference:.3f}x")


if __name__ == "__main__":
    main()
