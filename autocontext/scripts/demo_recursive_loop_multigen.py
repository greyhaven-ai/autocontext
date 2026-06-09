"""Multi-generation self-improvement: the recursive loop compounding on its OWN output.

The single-step demo (scripts/demo_recursive_loop.py) trains one adapter on a fixed elite set.
This one runs the genuine recursive loop: every generation, the CURRENTLY-SERVED model proposes
strategies, the verifier (the only external signal) scores them, the best of everything proposed
so far becomes the next adapter's training set, that adapter is published + auto-activated, and the
next generation is served by it. No hand-authored training data, no human in the loop -- the agent
bootstraps from its own cold-start proposals toward the verifier's optimum.

    gen 0   base Qwen2.5-0.5B-Instruct proposes; verifier scores; pool = its valid proposals
    gen g   train on the elite of the pool -> publish + auto-activate -> bridge serves it
            -> it proposes -> verifier scores -> add to pool -> repeat

Requires the mlx extra + mlx-lm:  uv pip install mlx mlx-lm
Run (from the package root):  uv run python scripts/demo_recursive_loop_multigen.py
"""

from __future__ import annotations

import json
import statistics
import tempfile
import time
from pathlib import Path

from autocontext.agents.llm_client import MLXLMClient
from autocontext.agents.scenario_bound_clients import _build_planned_client, _resolve_local_record, plan_local_client
from autocontext.config.settings import AppSettings
from autocontext.scenarios import SCENARIO_REGISTRY
from autocontext.training.autoresearch.mlxlm_backend import DEFAULT_BASE_MODEL, run_mlxlm_training, scenario_task_prompt
from autocontext.training.autoresearch.sequence_format import extract_json_object
from autocontext.training.backends import default_backend_registry
from autocontext.training.model_registry import ModelRegistry, TrainingCompletionOutput, publish_training_output

SCENARIO = "grid_ctf"
GENERATIONS = 3
PROPOSALS_PER_GEN = 20
ELITE_FRACTION = 0.5
MIN_ELITE = 16
TRAIN_STEPS = 80
# batch_size=1: the gen-0 pool is small (only the base model's valid cold-start proposals),
# and mlx-lm requires the validation split to hold >= batch_size examples.
BATCH_SIZE = 1


def banner(msg: str) -> None:
    print(f"\n{'=' * 78}\n{msg}\n{'=' * 78}", flush=True)


def propose_and_score(client, scenario, task_prompt: str, *, n: int) -> list[tuple[dict, float]]:
    """The served model proposes n strategies; the verifier scores the valid ones."""
    out: list[tuple[dict, float]] = []
    for i in range(n):
        try:
            resp = client.generate(model="", prompt=task_prompt, max_tokens=128, temperature=0.8)
            strategy = extract_json_object(resp.text)
            if strategy is None:
                continue
            ok, _ = scenario.validate_actions(scenario.initial_state(seed=0), "challenger", strategy)
            if not ok:
                continue
            out.append((strategy, scenario.execute_match(strategy, seed=i).score))
        except Exception as exc:  # noqa: BLE001 - demo: surface and continue
            print(f"  (proposal {i}: {type(exc).__name__})", flush=True)
    return out


def write_elite(pool: list[tuple[dict, float]], path: Path) -> tuple[int, float]:
    """Write the top strategies the agents have proposed so far as the next training set."""
    ranked = sorted(pool, key=lambda x: x[1], reverse=True)
    keep = max(MIN_ELITE, int(len(ranked) * ELITE_FRACTION))
    elite = ranked[:keep]
    with path.open("w") as f:
        for i, (strat, score) in enumerate(elite):
            f.write(
                json.dumps({"run_id": f"gen_{i // 12}", "scenario": SCENARIO, "strategy": strat, "score": score, "context": {}})
                + "\n"
            )
    return len(elite), statistics.fmean(s for _, s in elite)


def summarize(label: str, scored: list[tuple[dict, float]]) -> dict:
    scores = [s for _, s in scored]
    row = {
        "label": label,
        "n": len(scores),
        "mean": statistics.fmean(scores) if scores else 0.0,
        "best": max(scores) if scores else 0.0,
    }
    print(f"{label}: proposals_valid={row['n']}  mean={row['mean']:.4f}  best={row['best']:.4f}", flush=True)
    return row


def main() -> None:
    t0 = time.time()
    scenario = SCENARIO_REGISTRY[SCENARIO]()
    task_prompt = scenario_task_prompt(scenario)
    base = DEFAULT_BASE_MODEL

    banner(f"recursive self-improvement — {GENERATIONS} generations on local MLX\nscenario={SCENARIO}  base={base}")

    pool: list[tuple[dict, float]] = []
    history: list[dict] = []

    # --- gen 0: the cold-start base model is the agent --------------------------------------
    banner("GEN 0 — base model proposes (cold start, no training yet)")
    gen0 = propose_and_score(MLXLMClient(base), scenario, task_prompt, n=PROPOSALS_PER_GEN)
    pool.extend(gen0)
    history.append(summarize("gen 0 (base)", gen0))

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        knowledge_root = tmp_path / "knowledge"
        knowledge_root.mkdir()
        registry = ModelRegistry(knowledge_root)
        settings = AppSettings(agent_provider="mlx", mlx_model_path="", knowledge_root=knowledge_root)
        runtime_types = default_backend_registry().get("mlxlm").supported_runtime_types()

        for g in range(1, GENERATIONS + 1):
            banner(f"GEN {g} — train on the elite of {len(pool)} accumulated proposals, then self-serve")
            data_path = tmp_path / f"train_gen{g}.jsonl"
            out_dir = tmp_path / f"mlxlm_gen{g}"
            n_elite, elite_mean = write_elite(pool, data_path)
            print(
                f"  elite={n_elite} strategies (mean verifier score={elite_mean:.4f})  -> fine-tuning {TRAIN_STEPS} steps ...",
                flush=True,
            )

            run_mlxlm_training(
                scenario_name=SCENARIO,
                data_path=data_path,
                output_dir=out_dir,
                time_budget=900,
                memory_limit_mb=16384,
                train_steps=TRAIN_STEPS,
                batch_size=BATCH_SIZE,
                base_model=base,
                assess_samples=2,  # we run our own measurement below; keep the internal assess cheap
                assess_temperature=0.7,
            )
            completion = TrainingCompletionOutput(
                run_id=f"gen-{g}",
                checkpoint_path=str(out_dir / "adapters"),
                backend="mlxlm",
                scenario=SCENARIO,
                scenario_family="game",
                runtime_types=runtime_types,
                metadata={"base_model": base, "score_conditioned": False},
            )
            record = publish_training_output(completion, registry, artifacts_root=None, auto_activate=True)
            print(f"  published + activated: {record.artifact_id}", flush=True)

            # The bridge: resolve the just-activated adapter purely from the registry and serve it.
            resolved = _resolve_local_record(settings, SCENARIO)
            assert resolved is not None and resolved.artifact_id == record.artifact_id
            client = _build_planned_client(plan_local_client(resolved), settings)

            geng = propose_and_score(client, scenario, task_prompt, n=PROPOSALS_PER_GEN)
            pool.extend(geng)
            history.append(summarize(f"gen {g} (served)", geng))

    # --- trajectory ---------------------------------------------------------------------------
    banner("TRAJECTORY — does the loop compound on its own output?")
    base_mean = history[0]["mean"]
    for row in history:
        delta = row["mean"] - base_mean
        bar = "#" * int(row["mean"] * 50)
        print(f"  {row['label']:<16} mean={row['mean']:.4f}  best={row['best']:.4f}  ({delta:+.4f} vs base)  {bar}", flush=True)
    final = history[-1]["mean"]
    gain = (final - base_mean) / max(base_mean, 1e-9)
    print(f"\n  base -> final: {base_mean:.4f} -> {final:.4f}  ({gain:+.1%})  in {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
