"""Trusted SSH executor — runs strategy matches on user-owned machines.

Explicit, auditable remote execution for trusted hosts. Not a generic
sandbox — the operator must register and authorize machines.
"""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import Mapping
from typing import Any

from autocontext.integrations.ssh.client import SSHClient
from autocontext.scenarios.base import ExecutionLimits, ReplayEnvelope, Result, ScenarioInterface

logger = logging.getLogger(__name__)


class SSHExecutor:
    """ExecutionEngine implementation that runs matches over SSH.

    Follows the PrimeIntellectExecutor pattern: serialize payload,
    execute remotely, parse result/replay from JSON stdout.
    """

    def __init__(
        self,
        client: SSHClient,
        *,
        allow_fallback: bool = True,
        max_retries: int = 2,
        backoff_seconds: float = 0.75,
    ) -> None:
        self.client = client
        self.allow_fallback = allow_fallback
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds

    def execute(
        self,
        scenario: ScenarioInterface,
        strategy: Mapping[str, Any],
        seed: int,
        limits: ExecutionLimits,
    ) -> tuple[Result, ReplayEnvelope]:
        self.client.ensure_working_directory()

        command = self._build_eval_command(
            scenario_name=scenario.name,
            strategy=dict(strategy),
            seed=seed,
        )

        result = self.client.execute_command(command, timeout=limits.timeout_seconds)

        if result.exit_code != 0:
            logger.warning(
                "SSH execution failed on %s (exit %d): %s",
                self.client.config.name,
                result.exit_code,
                result.stderr[:200],
            )
            if not self.allow_fallback:
                raise RuntimeError(
                    f"SSH execution failed on {self.client.config.name}: "
                    f"exit {result.exit_code} — {result.stderr[:200]}"
                )
            return self._fallback_response(scenario.name, seed)

        try:
            parsed = json.loads(result.stdout)
            if not isinstance(parsed, dict) or "result" not in parsed or "replay" not in parsed:
                raise ValueError("SSH response missing required 'result'/'replay' fields")
            return (
                Result.model_validate(parsed["result"]),
                ReplayEnvelope.model_validate(parsed["replay"]),
            )
        except (json.JSONDecodeError, ValueError, KeyError) as exc:
            logger.warning("SSH output parse error on %s: %s", self.client.config.name, exc)
            if not self.allow_fallback:
                raise RuntimeError(f"SSH output parse error: {exc}") from exc
            return self._fallback_response(scenario.name, seed)

    def _build_eval_command(
        self,
        *,
        scenario_name: str,
        strategy: dict[str, Any],
        seed: int,
    ) -> str:
        """Build a self-contained Python evaluation command."""
        payload = {"scenario_name": scenario_name, "strategy": strategy, "seed": seed}
        encoded = base64.b64encode(json.dumps(payload, sort_keys=True).encode()).decode()
        working_dir = self.client.config.working_directory
        return (
            f"cd {working_dir} && "
            f"python3 -c \""
            f"import base64, json; "
            f"payload = json.loads(base64.b64decode('{encoded}').decode()); "
            f"from autocontext.scenarios import SCENARIO_REGISTRY; "
            f"from autocontext.scenarios.base import ExecutionLimits; "
            f"scenario = SCENARIO_REGISTRY[payload['scenario_name']]; "
            f"result, replay = scenario.execute_match(payload['strategy'], {{}}, payload['seed']); "
            f"print(json.dumps({{'result': result.model_dump(), 'replay': replay.model_dump()}}))"
            f"\""
        )

    @staticmethod
    def _fallback_response(scenario_name: str, seed: int) -> tuple[Result, ReplayEnvelope]:
        """Return an explicit failure shape when remote execution fails."""
        result = Result(
            score=0.0,
            winner="incumbent",
            summary="SSH execution failed or unavailable",
            replay=[{"event": "ssh_execution_failed"}],
            metrics={"remote_available": 0.0},
            validation_errors=["SSH execution failed"],
        )
        replay = ReplayEnvelope(
            scenario=scenario_name,
            seed=seed,
            narrative="Remote SSH execution failed; fallback result generated.",
            timeline=[{"event": "ssh_execution_failed"}],
        )
        return result, replay
