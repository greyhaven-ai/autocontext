"""AC-1018: frozen, bounded comparison; no production routing changes.

Run from autocontext/: .venv/bin/python benchmarks/policy_reuse/run.py --output PATH
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import random
import statistics
import subprocess
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from autocontext.execution.policy_executor import PolicyExecutor
from autocontext.execution.policy_refinement import PolicyRefinementLoop
from autocontext.harness.core.output_parser import extract_json
from autocontext.providers.base import CompletionResult, LLMProvider
from autocontext.scenarios.grid_ctf.scenario import GridCtfScenario

HERE = Path(__file__).resolve().parent
PACKAGE = HERE.parents[1]
REPO = PACKAGE.parent


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


def public_criteria() -> str:
    return (
        "Objective deterministic verifier: capture=clamp(0.55*aggression+0.45*path_bias+noise,0,1); "
        "survival=clamp(1-0.4*aggression+0.4*defense,0,1); "
        "energy=clamp(1-0.3*aggression+0.1*defense,0,1); "
        "score=0.6*capture+0.25*survival+0.15*energy. Noise is seeded uniform[-0.07,0.07]. "
        "Maximize score. Seed is provenance, not something to hardcode or exploit. "
        "One action terminates the episode."
    )


class PilotScenario(GridCtfScenario):
    def __init__(self, shifted: bool = False) -> None:
        self.shifted = shifted
        self.limit = 0.8 if shifted else 1.4
        self.contract = "grid-ctf-limit-0.8-v2" if shifted else "grid-ctf-limit-1.4-v1"

    def initial_state(self, seed=None):
        return {**super().initial_state(seed), "contract": self.contract, "combined_limit": self.limit}

    def describe_strategy_interface(self):
        return ("Return JSON with exactly aggression, defense, path_bias: finite numbers in [0,1]. "
                f"aggression + defense must be <= {self.limit}. Contract: {self.contract}.")

    def describe_evaluation_criteria(self):
        return public_criteria()

    def validate_actions(self, state, player_id, actions):
        if set(actions) != {"aggression", "defense", "path_bias"}:
            return False, "wrong action schema"
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in actions.values()):
            return False, "non-finite or non-numeric action"
        valid, reason = super().validate_actions(state, player_id, actions)
        if not valid:
            return valid, reason
        if actions["aggression"] + actions["defense"] > self.limit + 1e-12:
            return False, "combined action limit changed"
        return True, "ok"

    def step(self, state, actions):
        result = super().step(state, actions)
        result["timeline"].append({"event": "executed_action", "action": dict(actions)})
        return result


class PiProvider(LLMProvider):
    """Experimental adapter: each call is an ephemeral, tool-free Pi completion."""
    def __init__(self, protocol: dict, output: Path) -> None:
        self.protocol = protocol
        self.output = output
        self.calls: list[dict] = []
        self.phase = "synthesis"
        (output / "calls").mkdir()

    def default_model(self):
        return self.protocol["model"]

    @property
    def name(self):
        return self.protocol["provider"]

    def complete(self, system_prompt, user_prompt, model=None, temperature=0.0, max_tokens=4096):
        if len(self.calls) >= self.protocol["max_model_calls_total"]:
            raise RuntimeError("predeclared model-call budget exhausted")
        if sum(c.get("total_tokens", 0) for c in self.calls) >= self.protocol["observed_token_stop_threshold"]:
            raise RuntimeError("observed token stop threshold reached")
        index = len(self.calls) + 1
        prefix = self.output / "calls" / f"{index:03d}"
        request = {"system_prompt": system_prompt, "user_prompt": user_prompt, "model": model or self.default_model(),
                   "provider": self.name, "thinking": self.protocol["thinking"], "phase": self.phase,
                   "temperature_requested_but_not_supported_by_cli": temperature,
                   "max_tokens_requested_but_not_enforced_by_cli": max_tokens}
        write_json(prefix.with_suffix(".request.json"), request)
        args = ["pi", "--print", "--mode", "json", "--no-session", "--no-tools", "--no-extensions",
                "--no-skills", "--no-prompt-templates", "--no-context-files", "--approve", "--offline",
                "--provider", self.name, "--model", request["model"], "--thinking", self.protocol["thinking"],
                "--system-prompt", system_prompt, user_prompt]
        row = {"call": index, "phase": self.phase, "request_sha256": digest(prefix.with_suffix(".request.json").read_bytes()),
               "status": "started", "total_tokens": 0, "usage_known": False}
        self.calls.append(row)
        started = time.monotonic()
        try:
            proc = subprocess.run(args, cwd=self.output, capture_output=True, text=True,
                                  timeout=self.protocol["model_call_timeout_seconds"],
                                  env={**os.environ, "PI_TELEMETRY": "0"})
            prefix.with_suffix(".events.jsonl").write_text(proc.stdout)
            prefix.with_suffix(".stderr.txt").write_text(proc.stderr)
            messages = []
            for line in proc.stdout.splitlines():
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("type") == "message_end" and event.get("message", {}).get("role") == "assistant":
                    messages.append(event["message"])
            # No tools are enabled; count all returned assistant usage, including failed attempts.
            usages = [m["usage"] for m in messages if isinstance(m.get("usage"), dict)]
            row["usage_known"] = bool(usages)
            for field in ("input", "output", "cacheRead", "cacheWrite", "totalTokens"):
                row[field] = sum(u.get(field, 0) for u in usages)
            row["total_tokens"] = row["totalTokens"] or sum(row[k] for k in ("input", "output", "cacheRead", "cacheWrite"))
            row["served_models"] = sorted({m.get("model", "unknown") for m in messages})
            if proc.returncode or not messages:
                raise RuntimeError(f"Pi completion failed (exit {proc.returncode}); see saved trace {index}")
            message = messages[-1]
            if message.get("stopReason") in ("error", "aborted"):
                raise RuntimeError(f"Pi completion {message.get('stopReason')}; see saved trace {index}")
            answer = "\n".join(b["text"] for b in message.get("content", []) if b.get("type") == "text")
            if not answer.strip():
                raise RuntimeError("Pi produced no text answer")
            row["status"] = "completed"
            return CompletionResult(text=answer, model=message.get("model"), usage={"input_tokens": row["input"],
                                    "output_tokens": row["output"]}, stop_reason=message.get("stopReason"))
        except Exception as exc:
            row["status"] = "failed"
            row["error"] = str(exc)
            raise
        finally:
            row["seconds"] = time.monotonic() - started
            write_json(self.output / "call-ledger.json", self.calls)
            print(json.dumps({k: row[k] for k in ("call", "phase", "status", "seconds", "total_tokens")}), flush=True)


class TrainingExecutor(PolicyExecutor):
    def __init__(self, scenario, protocol) -> None:
        super().__init__(scenario, timeout_per_match=protocol["episode_timeout_seconds"], max_moves_per_match=1)
        self.rows: list[dict] = []

    def execute_match(self, policy_source, seed=None):
        start = time.monotonic()
        result = super().execute_match(policy_source, seed)
        self.rows.append({"seed": seed, "policy_sha256": digest(policy_source.encode()),
                          "seconds": time.monotonic() - start, **asdict(result)})
        return result


def episode(arm, seed, scenario, policy, provider, protocol):
    before = len(provider.calls)
    started = time.monotonic()
    state = scenario.initial_state(seed)
    row = {"arm": arm, "seed": seed, "split": "shifted" if scenario.shifted else "heldout",
           "initial_state": state, "state_sha256": digest(json.dumps(state, sort_keys=True).encode()),
           "score": 0.0, "errors": [], "illegal_actions": 0, "abstained": False, "invalidated": False}
    try:
        applicable = state["contract"] == "grid-ctf-limit-1.4-v1"
        if arm == "policy" and not applicable:
            row["invalidated"] = True
            row["reason"] = "serving contract differs from frozen policy contract"
        elif arm == "model" or (arm == "hybrid" and not applicable):
            row["abstained"] = arm == "hybrid"
            prompt = (scenario.describe_rules() + "\n" + scenario.describe_strategy_interface() + "\n" +
                      scenario.describe_evaluation_criteria() + "\nState: " + json.dumps(state))
            for _attempt in range(protocol["max_action_attempts"]):
                remaining = protocol["episode_timeout_seconds"] - (time.monotonic() - started)
                if remaining <= 0:
                    raise TimeoutError("episode wall-time budget exhausted")
                previous_timeout = provider.protocol["model_call_timeout_seconds"]
                provider.protocol["model_call_timeout_seconds"] = min(previous_timeout, remaining)
                try:
                    response = provider.complete("Choose one action to maximize the objective score. Return only JSON.", prompt)
                finally:
                    provider.protocol["model_call_timeout_seconds"] = previous_timeout
                try:
                    action = extract_json(response.text)
                    valid, reason = scenario.validate_actions(state, "challenger", action)
                except (ValueError, TypeError, AttributeError) as exc:
                    valid, reason = False, str(exc)
                if valid:
                    state = scenario.step(state, action)
                    result = scenario.get_result(state)
                    row.update(score=result.score, action=action, replay=result.replay)
                    break
                row["illegal_actions"] += 1
                prompt += f"\nPrevious response was invalid: {reason}. Retry with valid JSON."
            else:
                row["errors"].append("no valid action within attempt budget")
        else:
            executor = PolicyExecutor(scenario, timeout_per_match=protocol["episode_timeout_seconds"], max_moves_per_match=1)
            result = executor.execute_match(policy, seed)
            row.update(score=result.score, errors=result.errors,
                       illegal_actions=result.illegal_action_count, replay=result.replay)
    except Exception as exc:
        row["errors"].append(str(exc))
    row["seconds"] = time.monotonic() - started
    row["calls"] = list(range(before + 1, len(provider.calls) + 1))
    row["model_calls"] = len(row["calls"])
    row["tokens"] = sum(c["total_tokens"] for c in provider.calls[before:])
    row["success"] = row["score"] >= protocol["quality_floor"] and not row["errors"] and not row["illegal_actions"]
    return row


def quantile(values, q):
    if not values:
        return None
    values = sorted(values)
    return values[min(len(values) - 1, max(0, math.ceil(q * len(values)) - 1))]


def summarize(rows, training, calls, synthesis_seconds, protocol):
    training_calls = [c for c in calls if c["phase"] == "synthesis"]
    setup = {"seconds": synthesis_seconds, "model_calls": len(training_calls),
             "tokens": sum(c["total_tokens"] for c in training_calls), "training_episodes": len(training)}
    stats = {}
    for split in ("heldout", "shifted"):
        for arm in ("model", "policy", "hybrid"):
            group = [r for r in rows if r["split"] == split and r["arm"] == arm]
            successes = sum(r["success"] for r in group)
            stats[f"{split}/{arm}"] = {
                "episodes": len(group), "mean_score": statistics.mean(r["score"] for r in group),
                "successes": successes, "success_rate": successes / len(group),
                "errors": sum(bool(r["errors"]) for r in group), "illegal_actions": sum(r["illegal_actions"] for r in group),
                "abstentions": sum(r["abstained"] for r in group), "invalidations": sum(r["invalidated"] for r in group),
                "model_calls": sum(r["model_calls"] for r in group), "tokens": sum(r["tokens"] for r in group),
                "total_seconds": sum(r["seconds"] for r in group),
                "p50_seconds": quantile([r["seconds"] for r in group], 0.5),
                "p95_seconds": quantile([r["seconds"] for r in group], 0.95),
                "dollars_per_success": None,
            }
    model = {r["seed"]: r for r in rows if r["arm"] == "model" and r["split"] == "heldout"}
    comparisons = {}
    for arm in ("policy", "hybrid"):
        group = [r for r in rows if r["arm"] == arm and r["split"] == "heldout"]
        diffs = [r["score"] - model[r["seed"]]["score"] for r in group]
        rng = random.Random(protocol["bootstrap_seed"])
        boot = [statistics.mean(rng.choices(diffs, k=len(diffs))) for _ in range(protocol["bootstrap_resamples"])]
        comparisons[arm] = {"paired_score_difference": statistics.mean(diffs),
                            "conditional_seed_bootstrap_95_interval": [quantile(boot, .025), quantile(boot, .975)]}
    horizons = {}
    # Project identical supported tasks from measured means; include all synthesis and training costs.
    for arm in ("model", "policy", "hybrid"):
        stat = stats[f"heldout/{arm}"]
        initial = setup if arm != "model" else {"seconds": 0, "model_calls": 0, "tokens": 0}
        horizons[arm] = [{"episodes": n, **{
            metric: initial[metric] + n * stat[{"seconds": "total_seconds"}.get(metric, metric)] / stat["episodes"]
            for metric in ("seconds", "model_calls", "tokens")},
            "expected_successes": n * stat["success_rate"]}
            for n in protocol["repetition_horizons"]]
    break_even = {}
    for arm in ("policy", "hybrid"):
        break_even[arm] = {}
        for resource, field in (("seconds", "total_seconds"), ("model_calls", "model_calls"), ("tokens", "tokens")):
            savings = (stats["heldout/model"][field] - stats[f"heldout/{arm}"][field]) / stats["heldout/model"]["episodes"]
            break_even[arm][resource] = math.floor(setup[resource] / savings) + 1 if savings > 0 else None
    observed_crossing = {}
    for arm in ("policy", "hybrid"):
        group = sorted((r for r in rows if r["arm"] == arm and r["split"] == "heldout"), key=lambda r: r["seed"])
        observed_crossing[arm] = {}
        for resource in ("seconds", "model_calls", "tokens"):
            policy_total, model_total = setup[resource], 0.0
            crossing = None
            for index, row in enumerate(group, 1):
                policy_total += row[resource]
                model_total += model[row["seed"]][resource]
                if crossing is None and policy_total < model_total:
                    crossing = index
            observed_crossing[arm][resource] = crossing
    # Actual totals pay setup separately for each policy arm, as if deployed independently.
    amortized = {}
    for arm in ("model", "policy", "hybrid"):
        group = [r for r in rows if r["arm"] == arm]
        count = sum(r["success"] for r in group)
        initial = setup if arm != "model" else {"seconds": 0, "model_calls": 0, "tokens": 0}
        amortized[arm] = {resource + "_per_success":
                         (initial[resource] + sum(r[resource] for r in group)) / count if count else None
                         for resource in ("seconds", "model_calls", "tokens")}
    go = all(stats[f"heldout/{arm}"]["mean_score"] >= protocol["quality_floor"]
             and comparisons[arm]["conditional_seed_bootstrap_95_interval"][0] >= -protocol["acceptable_mean_regression"]
             and stats[f"heldout/{arm}"]["errors"] == stats[f"heldout/{arm}"]["illegal_actions"] == 0
             and break_even[arm]["seconds"] is not None and break_even[arm]["seconds"] <= 100
             and break_even[arm]["tokens"] is not None and break_even[arm]["tokens"] <= 100
             for arm in ("policy", "hybrid"))
    go = go and stats["shifted/policy"]["invalidations"] == len(protocol["shifted_seeds"])
    go = go and stats["shifted/hybrid"]["abstentions"] == len(protocol["shifted_seeds"])
    return {"setup": setup, "stats": stats, "comparisons": comparisons, "projected_horizons": horizons,
            "projected_break_even_episodes": break_even, "observed_first_resource_crossing_episodes": observed_crossing,
            "actual_resources_per_success_with_setup": amortized,
            "pilot_go": go, "all_call_usage_known": all(c["usage_known"] for c in calls),
            "dollars": None, "scope": protocol["limitations"]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    protocol_bytes = (HERE / "protocol.json").read_bytes()
    protocol = json.loads(protocol_bytes)
    (output / "protocol.json").write_bytes(protocol_bytes)
    files = [Path(__file__), PACKAGE / "src/autocontext/scenarios/grid_ctf/scenario.py",
             PACKAGE / "src/autocontext/execution/policy_executor.py", PACKAGE / "src/autocontext/execution/policy_refinement.py",
             PACKAGE / "uv.lock"]
    identity = {"started_at": datetime.now(UTC).isoformat(), "protocol_sha256": digest(protocol_bytes),
                "git_base": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
                "source_hashes": {str(p.relative_to(REPO)): digest(p.read_bytes()) for p in files},
                "python": platform.python_version(), "platform": platform.platform(),
                "pi": subprocess.check_output(["pi", "--version"], text=True).strip(),
                "provider": protocol["provider"], "model": protocol["model"], "thinking": protocol["thinking"],
                "objective_evaluator": "GridCtfScenario.step/get_result at the recorded source digest"}
    write_json(output / "identity.json", identity)
    (output / ".pi").mkdir()
    write_json(output / ".pi/settings.json", {"retry": {"enabled": False, "maxRetries": 0,
                                                        "provider": {"maxRetries": 0}}})
    provider = PiProvider(protocol, output)
    training = TrainingExecutor(PilotScenario(), protocol)
    started = time.monotonic()
    initial = "def choose_action(state):\n    return {'aggression': 0.2, 'defense': 0.2, 'path_bias': 0.2}\n"
    loop = PolicyRefinementLoop(PilotScenario(), training, provider,
                                max_iterations=protocol["max_refinement_iterations"],
                                matches_per_iteration=len(protocol["training_seeds"]), model=protocol["model"])
    assert loop._evaluation_seeds == protocol["training_seeds"]
    result = loop.refine(initial)
    synthesis_seconds = time.monotonic() - started
    write_json(output / "training.json", {"result": asdict(result), "episodes": training.rows, "seconds": synthesis_seconds})
    (output / "frozen-policy.py").write_text(result.best_policy)
    write_json(output / "frozen-policy-identity.json", {"policy_sha256": digest(result.best_policy.encode()),
               "contract": PilotScenario().contract, "frozen_at": datetime.now(UTC).isoformat(),
               "training_only_selection": True, "synthesized_candidate_selected": result.best_policy != initial})
    provider.phase = "evaluation"
    rows = []
    for shifted, seeds in ((False, protocol["heldout_seeds"]), (True, protocol["shifted_seeds"])):
        for index, seed in enumerate(seeds):
            arms = ["model", "policy", "hybrid"]
            arms = arms[index % 3:] + arms[:index % 3]
            for arm in arms:
                row = episode(arm, seed, PilotScenario(shifted), result.best_policy, provider, protocol)
                rows.append(row)
                write_json(output / "episodes.json", rows)
                print(json.dumps({k: row[k] for k in ("arm", "seed", "score", "success", "model_calls")}), flush=True)
    for seed in protocol["heldout_seeds"] + protocol["shifted_seeds"]:
        assert len({r["state_sha256"] for r in rows if r["seed"] == seed}) == 1
    summary = summarize(rows, training.rows, provider.calls, synthesis_seconds, protocol)
    summary["pilot_go"] &= result.best_policy != initial and summary["all_call_usage_known"]
    write_json(output / "summary.json", summary)
    print(json.dumps({"output": str(output), "pilot_go": summary["pilot_go"]}), flush=True)


if __name__ == "__main__":
    main()
