from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from prime_sandboxes import AsyncSandboxClient, CreateSandboxRequest

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PrimeIntellectClient:
    """PrimeIntellect sandbox lifecycle client backed by prime-sandboxes SDK."""

    api_key: str
    docker_image: str = "python:3.11-slim"
    cpu_cores: float = 1.0
    memory_gb: float = 2.0
    disk_size_gb: float = 5.0
    timeout_minutes: int = 30
    max_wait_attempts: int = 60
    network_access: bool = True
    allow_fallback: bool = True

    def warm_provision(self, environment_name: str, max_retries: int = 2, backoff_seconds: float = 0.75) -> dict[str, Any]:
        del max_retries, backoff_seconds
        try:
            asyncio.run(self._probe())
            return {"environment": environment_name, "status": "ready"}
        except Exception as exc:
            logger.debug("integrations.primeintellect.client: caught Exception", exc_info=True)
            return self.unavailable_state(environment_name, str(exc))

    def execute_strategy(
        self,
        *,
        scenario_name: str,
        strategy: dict[str, Any],
        seed: int,
        timeout_seconds: float,
        max_memory_mb: int,
        network_access: bool,
        max_retries: int = 2,
        backoff_seconds: float = 0.75,
    ) -> dict[str, Any]:
        attempt = 0
        while True:
            try:
                return asyncio.run(
                    self._execute_strategy_once(
                        scenario_name=scenario_name,
                        strategy=strategy,
                        seed=seed,
                        timeout_seconds=timeout_seconds,
                        max_memory_mb=max_memory_mb,
                        network_access=network_access,
                    )
                )
            except Exception:
                logger.debug("integrations.primeintellect.client: caught Exception", exc_info=True)
                attempt += 1
                if not self.allow_fallback:
                    raise
                if attempt > max_retries:
                    return self.fallback_local_response(scenario_name, seed)
                time.sleep(backoff_seconds * attempt)

    async def _probe(self) -> None:
        async with AsyncSandboxClient(api_key=self.api_key) as client:
            await client.list(per_page=1, exclude_terminated=True)

    async def _execute_strategy_once(
        self,
        *,
        scenario_name: str,
        strategy: dict[str, Any],
        seed: int,
        timeout_seconds: float,
        max_memory_mb: int,
        network_access: bool,
    ) -> dict[str, Any]:
        sandbox_id: str | None = None
        async with AsyncSandboxClient(api_key=self.api_key) as client:
            request = CreateSandboxRequest(
                name=f"autocontext-{scenario_name}-{seed}",
                docker_image=self.docker_image,
                cpu_cores=self.cpu_cores,
                memory_gb=min(self.memory_gb, max(0.25, float(max_memory_mb) / 1024.0)),
                disk_size_gb=self.disk_size_gb,
                timeout_minutes=max(self.timeout_minutes, max(1, int(timeout_seconds // 60) + 1)),
                network_access=network_access and self.network_access,
            )
            sandbox = await client.create(request)
            sandbox_id = sandbox.id
            try:
                await client.wait_for_creation(sandbox_id, max_attempts=self.max_wait_attempts)
                command = self._build_eval_command(
                    scenario_name=scenario_name,
                    strategy=strategy,
                    seed=seed,
                )
                command_response = await client.execute_command(
                    sandbox_id=sandbox_id,
                    command=command,
                    timeout=max(1, int(timeout_seconds)),
                )
                if command_response.exit_code != 0:
                    raise RuntimeError(
                        "primeintellect sandbox command failed: "
                        f"{command_response.stderr.strip() or 'no stderr'}"
                    )
                parsed = json.loads(command_response.stdout)
                if not isinstance(parsed, dict) or "result" not in parsed or "replay" not in parsed:
                    raise ValueError("primeintellect sandbox response missing required fields")
                return {"result": parsed["result"], "replay": parsed["replay"]}
            finally:
                try:
                    await client.delete(sandbox_id)
                except Exception:
                    logger.debug("integrations.primeintellect.client: suppressed Exception", exc_info=True)

    def fallback_local_response(self, scenario_name: str, seed: int) -> dict[str, Any]:
        """Explicitly return a failure shape for caller-side recovery paths."""
        return {
            "result": {
                "score": 0.0,
                "winner": "incumbent",
                "summary": "primeintellect execution unavailable",
                "replay": [{"event": "remote_unavailable"}],
                "metrics": {"remote_available": 0.0},
                "validation_errors": ["remote execution unavailable"],
            },
            "replay": {
                "scenario": scenario_name,
                "seed": seed,
                "narrative": "Remote execution unavailable; fallback result generated.",
                "timeline": [{"event": "remote_unavailable"}],
            },
        }

    def unavailable_state(self, environment_name: str, reason: str) -> dict[str, Any]:
        return {
            "environment": environment_name,
            "status": "failed",
            "error": reason,
        }

    def _build_eval_command(self, *, scenario_name: str, strategy: dict[str, Any], seed: int) -> str:
        payload = {"scenario_name": scenario_name, "strategy": strategy, "seed": seed}
        encoded = base64.b64encode(json.dumps(payload, sort_keys=True).encode("utf-8")).decode("ascii")
        script = f"""import base64
import json
import random

payload = json.loads(base64.b64decode("{encoded}").decode())
scenario = payload["scenario_name"]
strategy = payload["strategy"]
seed = int(payload["seed"])
rng = random.Random(seed)

if scenario == "grid_ctf":
    aggression = float(strategy["aggression"])
    defense = float(strategy["defense"])
    path_bias = float(strategy["path_bias"])
    stochastic = rng.uniform(-0.07, 0.07)
    capture = max(0.0, min(1.0, 0.55 * aggression + 0.45 * path_bias + stochastic))
    survive = max(0.0, min(1.0, 1.0 - aggression * 0.4 + defense * 0.4))
    energy = max(0.0, min(1.0, 1.0 - aggression * 0.3 + defense * 0.1))
    score = max(0.0, min(1.0, capture * 0.6 + survive * 0.25 + energy * 0.15))
    timeline = [{{
        "event": "turn_complete",
        "turn": 1,
        "capture_progress": round(capture, 4),
        "defender_survival": round(survive, 4),
        "energy_efficiency": round(energy, 4),
    }}]
    result = {{
        "score": round(score, 4),
        "winner": "challenger" if score >= 0.55 else "incumbent",
        "summary": f"GridCTF score {{score:.4f}}",
        "replay": timeline,
        "metrics": {{
            "capture_progress": round(capture, 4),
            "defender_survival": round(survive, 4),
            "energy_efficiency": round(energy, 4),
        }},
        "validation_errors": [],
    }}
    replay = {{
        "scenario": "grid_ctf",
        "seed": seed,
        "narrative": (
            f"Capture phase ended with progress {{capture:.2f}}, "
            f"defender survival {{survive:.2f}}, and energy efficiency {{energy:.2f}}."
        ),
        "timeline": timeline,
    }}
elif scenario == "othello":
    mobility = float(strategy["mobility_weight"])
    corner = float(strategy["corner_weight"])
    stability = float(strategy["stability_weight"])
    noise = rng.uniform(-0.05, 0.05)
    score = max(0.0, min(1.0, (mobility * 0.35) + (corner * 0.4) + (stability * 0.25) + noise))
    timeline = [{{
        "event": "opening_evaluated",
        "mobility": round(mobility, 4),
        "corner": round(corner, 4),
        "stability": round(stability, 4),
    }}]
    result = {{
        "score": round(score, 4),
        "winner": "challenger" if score >= 0.52 else "incumbent",
        "summary": f"Othello opening score {{score:.4f}}",
        "replay": timeline,
        "metrics": {{
            "mobility": round(mobility, 4),
            "corner_pressure": round(corner, 4),
            "stability": round(stability, 4),
        }},
        "validation_errors": [],
    }}
    replay = {{
        "scenario": "othello",
        "seed": seed,
        "narrative": (
            f"Opening policy emphasized mobility {{mobility:.2f}}, "
            f"corner pressure {{corner:.2f}}, and stability {{stability:.2f}}."
        ),
        "timeline": timeline,
    }}
else:
    result = {{
        "score": 0.0,
        "winner": "incumbent",
        "summary": "unsupported scenario",
        "replay": [{{"event": "unsupported_scenario"}}],
        "metrics": {{"remote_available": 0.0}},
        "validation_errors": [f"unsupported scenario: {{scenario}}"],
    }}
    replay = {{
        "scenario": scenario,
        "seed": seed,
        "narrative": "Scenario unsupported by remote evaluator.",
        "timeline": [{{"event": "unsupported_scenario"}}],
    }}

print(json.dumps({{"result": result, "replay": replay}}))
"""
        return "python - <<'PY'\n" + script + "\nPY"
