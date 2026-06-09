"""Live end-to-end demo of the autocontext recursive loop on local MLX.

Closes the loop the PRs built: train a small mlx-lm LoRA adapter on a scenario's
verifier-scored strategies, PUBLISH + AUTO-ACTIVATE it in the model registry, then have
the scenario-bound resolver AUTO-SERVE it as the agent (no hardcoded path) and show the
served model proposes better strategies than the untrained base.

    run N   = base Qwen2.5-0.5B-Instruct proposing grid_ctf strategies
    train   = LoRA SFT on the elite (verifier-scored) strategies the loop accumulates
    publish = register + activate the adapter (records base_model + score_conditioned)
    bridge  = scenario_bound resolver -> plan_local_client -> MLXLMClient(base, adapter)
    run N+1 = the AUTO-RESOLVED served adapter proposing grid_ctf strategies

Requires the mlx extra + mlx-lm:  uv pip install mlx mlx-lm
Run (from the package root):  uv run python scripts/demo_recursive_loop.py
"""

from __future__ import annotations

import json
import random
import statistics
import tempfile
import time
from pathlib import Path

from autocontext.agents.llm_client import MLXLMClient
from autocontext.agents.scenario_bound_clients import _build_planned_client, _resolve_local_record, plan_local_client
from autocontext.config.settings import AppSettings
from autocontext.scenarios import SCENARIO_REGISTRY
from autocontext.training.autoresearch.mlxlm_backend import (
    DEFAULT_BASE_MODEL,
    run_mlxlm_training,
    scenario_task_prompt,
)
from autocontext.training.autoresearch.sequence_format import extract_json_object
from autocontext.training.backends import default_backend_registry
from autocontext.training.model_registry import (
    ModelRegistry,
    TrainingCompletionOutput,
    publish_training_output,
)

SCENARIO = "grid_ctf"
N_SAMPLES = 8  # strategies generated per measurement
TRAIN_STEPS = 80
N_TRAIN_RECORDS = 60


def banner(msg: str) -> None:
    print(f"\n{'=' * 78}\n{msg}\n{'=' * 78}", flush=True)


def print_measure(label: str, m: dict) -> None:
    print(
        f"{label}: mean={m['mean']:.4f}  best={m['best']:.4f}  valid={m['valid_rate']:.0%}  scores={m['scores']}",
        flush=True,
    )


def measure(client, scenario, task_prompt: str, *, n: int) -> dict:
    """Generate n strategies through the REAL agent client, score each via the verifier.

    ``client`` is an MLXLMClient (base-only for run N, base+adapter for run N+1) so the demo
    exercises the actual serving path -- including format_mlxlm_prompt's chat-template wrap,
    which an instruct model needs to emit parseable JSON."""
    scores: list[float] = []
    valid = 0
    for i in range(n):
        try:
            resp = client.generate(model="", prompt=task_prompt, max_tokens=128, temperature=0.7)
            strategy = extract_json_object(resp.text)
            if strategy is None:
                continue
            ok, _ = scenario.validate_actions(scenario.initial_state(seed=0), "challenger", strategy)
            if not ok:
                continue
            valid += 1
            scores.append(scenario.execute_match(strategy, seed=i).score)
        except Exception as exc:  # noqa: BLE001 - demo: surface and continue
            print(f"  (sample {i}: {type(exc).__name__})", flush=True)
            continue
    return {
        "mean": statistics.fmean(scores) if scores else 0.0,
        "best": max(scores) if scores else 0.0,
        "valid_rate": valid / n,
        "scores": [round(s, 3) for s in scores],
    }


def build_elite_training_set(scenario, path: Path, *, n_records: int) -> float:
    """Sample the strategy space, score with the real verifier, keep the elite as training data.

    Represents the verifier-scored trajectories the loop accumulates over generations. Returns
    the mean score of the kept elite (what the adapter is taught to reproduce)."""
    rng = random.Random(0)
    candidates = []
    for _ in range(n_records * 8):
        a = round(rng.uniform(0.0, 1.0), 3)
        d = round(rng.uniform(0.0, min(1.0, 1.4 - a)), 3)  # honor aggression + defense <= 1.4
        p = round(rng.uniform(0.0, 1.0), 3)
        strat = {"aggression": a, "defense": d, "path_bias": p}
        score = scenario.execute_match(strat, seed=0).score
        candidates.append((score, strat))
    candidates.sort(key=lambda x: x[0], reverse=True)
    elite = candidates[:n_records]
    with path.open("w") as f:
        for i, (score, strat) in enumerate(elite):
            f.write(
                json.dumps({"run_id": f"elite_{i // 10}", "scenario": SCENARIO, "strategy": strat, "score": score, "context": {}})
                + "\n"
            )
    return statistics.fmean(s for s, _ in elite)


def main() -> None:
    t0 = time.time()
    scenario = SCENARIO_REGISTRY[SCENARIO]()
    task_prompt = scenario_task_prompt(scenario)
    base = DEFAULT_BASE_MODEL

    banner(f"autocontext recursive loop — live demo on local MLX\nscenario={SCENARIO}  base={base}")
    print(f"task prompt:\n  {task_prompt}", flush=True)

    # --- run N: the untrained base model is the agent ---------------------------------------
    banner("RUN N — baseline: base model proposes strategies (no trained model yet)")
    before = measure(MLXLMClient(base), scenario, task_prompt, n=N_SAMPLES)
    print_measure("base model", before)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        data_path = tmp_path / "training_data.jsonl"
        out_dir = tmp_path / "mlxlm_run"
        knowledge_root = tmp_path / "knowledge"
        knowledge_root.mkdir()

        # --- accumulate the verifier-scored elite, then train an adapter on it ---------------
        banner(f"TRAIN — LoRA SFT on the elite {N_TRAIN_RECORDS} verifier-scored strategies")
        elite_mean = build_elite_training_set(scenario, data_path, n_records=N_TRAIN_RECORDS)
        print(f"elite training set: {N_TRAIN_RECORDS} strategies, mean verifier score={elite_mean:.4f}", flush=True)
        print(f"fine-tuning {base} for {TRAIN_STEPS} steps ...", flush=True)
        metrics = run_mlxlm_training(
            scenario_name=SCENARIO,
            data_path=data_path,
            output_dir=out_dir,
            time_budget=900,
            memory_limit_mb=16384,
            train_steps=TRAIN_STEPS,
            base_model=base,
            assess_samples=N_SAMPLES,
            assess_temperature=0.7,
        )
        adapter_dir = out_dir / "adapters"
        print(
            f"trained. in-training assessment: avg_score={metrics['avg_score']:.4f}  "
            f"valid_rate={metrics['valid_rate']:.0%}  ({metrics['training_seconds']:.0f}s)",
            flush=True,
        )

        # --- publish + auto-activate (the recursive loop's hand-off) -------------------------
        banner("PUBLISH — register + auto-activate the adapter in the model registry")
        registry = ModelRegistry(knowledge_root)
        completion = TrainingCompletionOutput(
            run_id="demo-run",
            checkpoint_path=str(adapter_dir),
            backend="mlxlm",
            scenario=SCENARIO,
            scenario_family="game",
            runtime_types=default_backend_registry().get("mlxlm").supported_runtime_types(),
            training_metrics={"avg_score": metrics["avg_score"]},
            metadata={"base_model": base, "score_conditioned": False},
        )
        record = publish_training_output(completion, registry, artifacts_root=None, auto_activate=True)
        print(f"published: artifact={record.artifact_id}  backend={record.backend}  state={record.activation_state}", flush=True)
        print(f"  runtime_types={record.runtime_types}  base_model={record.metadata.get('base_model')!r}", flush=True)

        # --- the BRIDGE: resolve + route purely from the registry (no hardcoded path) --------
        banner("BRIDGE — scenario_bound resolver auto-selects the trained adapter")
        settings = AppSettings(agent_provider="mlx", mlx_model_path="", knowledge_root=knowledge_root)
        resolved = _resolve_local_record(settings, SCENARIO)
        assert resolved is not None, "resolver failed to find the active adapter"
        plan = plan_local_client(resolved)
        assert plan is not None, "router could not plan a client for the record"
        print(f"resolved active record -> kind={plan.kind!r}  base={plan.model!r}", flush=True)
        print(f"  adapter_path={plan.adapter_path}  score_conditioned={plan.score_conditioned}", flush=True)
        print("  => AUTOCONTEXT_AGENT_PROVIDER=mlx would now serve MLXLMClient(base, adapter)", flush=True)

        # --- run N+1: the auto-served adapter is the agent -----------------------------------
        banner("RUN N+1 — the auto-resolved served adapter proposes strategies")
        served_client = _build_planned_client(plan, settings)  # the bridge's real client construction
        after = measure(served_client, scenario, task_prompt, n=N_SAMPLES)
        print_measure("served adapter", after)

        # --- verdict -------------------------------------------------------------------------
        banner("VERDICT")
        delta = after["mean"] - before["mean"]
        print(f"  run N   (base model)      mean score = {before['mean']:.4f}", flush=True)
        print(f"  run N+1 (served adapter)  mean score = {after['mean']:.4f}", flush=True)
        print(f"  delta                                = {delta:+.4f}  ({delta / max(before['mean'], 1e-9):+.1%})", flush=True)
        print(f"  loop closed: train -> publish -> auto-resolve -> serve, in {time.time() - t0:.0f}s", flush=True)
        print(f"\n  {'IMPROVED ✓' if delta > 0 else 'NO IMPROVEMENT'} — N+1 {'>' if delta > 0 else '<='} N", flush=True)


if __name__ == "__main__":
    main()
