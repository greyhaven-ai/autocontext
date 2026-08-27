"""Trusted SSH executor — runs strategy matches on user-owned machines.

Explicit, auditable remote execution for trusted hosts. Not a generic
sandbox — the operator must register and authorize machines.
"""

from __future__ import annotations

import base64
import json
import logging
import shlex
from collections.abc import Mapping
from typing import Any

from autocontext.execution.executors.local import LocalExecutor
from autocontext.integrations.ssh.client import SSHClient
from autocontext.scenarios.base import (
    ExecutionLimits,
    Observation,
    ReplayEnvelope,
    Result,
    ScenarioInterface,
)

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
        fallback_executor: LocalExecutor | None = None,
    ) -> None:
        self.client = client
        self.allow_fallback = allow_fallback
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        self.fallback_executor = fallback_executor or LocalExecutor()

    def execute(
        self,
        scenario: ScenarioInterface,
        strategy: Mapping[str, Any],
        seed: int,
        limits: ExecutionLimits,
    ) -> tuple[Result, ReplayEnvelope]:
        return self._execute(
            scenario,
            strategy,
            seed,
            limits,
            initial_state=None,
            initial_observation=None,
            fixture_digest=None,
        )

    def execute_prepared_fixture(
        self,
        scenario: ScenarioInterface,
        strategy: Mapping[str, Any],
        seed: int,
        limits: ExecutionLimits,
        *,
        initial_state: Mapping[str, Any],
        initial_observation: Observation,
        fixture_digest: str,
    ) -> tuple[Result, ReplayEnvelope]:
        return self._execute(
            scenario,
            strategy,
            seed,
            limits,
            initial_state=initial_state,
            initial_observation=initial_observation,
            fixture_digest=fixture_digest,
        )

    def _execute(
        self,
        scenario: ScenarioInterface,
        strategy: Mapping[str, Any],
        seed: int,
        limits: ExecutionLimits,
        *,
        initial_state: Mapping[str, Any] | None,
        initial_observation: Observation | None,
        fixture_digest: str | None,
    ) -> tuple[Result, ReplayEnvelope]:
        self.client.ensure_working_directory()

        command = self._build_eval_command(
            scenario_name=scenario.name,
            strategy=dict(strategy),
            seed=seed,
            initial_state=initial_state,
            initial_observation=initial_observation,
            fixture_digest=fixture_digest,
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
                    f"SSH execution failed on {self.client.config.name}: exit {result.exit_code} — {result.stderr[:200]}"
                )
            return self._execute_local_fallback(
                scenario,
                strategy,
                seed,
                limits,
                initial_state=initial_state,
                initial_observation=initial_observation,
                fixture_digest=fixture_digest,
            )

        try:
            parsed = json.loads(result.stdout)
            if not isinstance(parsed, dict) or "result" not in parsed or "replay" not in parsed:
                raise ValueError("SSH response missing required 'result'/'replay' fields")
            if fixture_digest is not None and parsed.get("fixture_digest") != fixture_digest:
                raise ValueError("SSH response lacks the prepared fixture attestation")
            return (
                Result.model_validate(parsed["result"]),
                ReplayEnvelope.model_validate(parsed["replay"]),
            )
        except (json.JSONDecodeError, ValueError, KeyError) as exc:
            logger.warning("SSH output parse error on %s: %s", self.client.config.name, exc)
            if not self.allow_fallback:
                raise RuntimeError(f"SSH output parse error: {exc}") from exc
            return self._execute_local_fallback(
                scenario,
                strategy,
                seed,
                limits,
                initial_state=initial_state,
                initial_observation=initial_observation,
                fixture_digest=fixture_digest,
            )

    def _build_eval_command(
        self,
        *,
        scenario_name: str,
        strategy: dict[str, Any],
        seed: int,
        initial_state: Mapping[str, Any] | None = None,
        initial_observation: Observation | None = None,
        fixture_digest: str | None = None,
    ) -> str:
        """Build a self-contained Python evaluation command."""
        payload = {
            "scenario_name": scenario_name,
            "strategy": strategy,
            "seed": seed,
            "initial_state": dict(initial_state) if initial_state is not None else None,
            "initial_observation": (initial_observation.model_dump(mode="json") if initial_observation is not None else None),
            "fixture_digest": fixture_digest,
        }
        encoded = base64.b64encode(json.dumps(payload, sort_keys=True).encode()).decode()
        working_dir = self.client.config.working_directory
        script = (
            "import base64, json; "
            f"payload = json.loads(base64.b64decode({encoded!r}).decode()); "
            "from autocontext.scenarios import SCENARIO_REGISTRY; "
            "scenario_cls = SCENARIO_REGISTRY[payload['scenario_name']]; "
            "scenario = scenario_cls(); "
            "result = (scenario.execute_match(payload['strategy'], payload['seed']) "
            "if payload['initial_state'] is None else "
            "scenario.execute_match_from_state(payload['strategy'], payload['seed'], "
            "payload['initial_state'])); "
            "replay = {'scenario': scenario.name, 'seed': payload['seed'], "
            "'narrative': scenario.replay_to_narrative(result.replay), 'timeline': result.replay}; "
            "output = {'result': result.model_dump(), 'replay': replay}; "
            "output.update({'fixture_digest': payload['fixture_digest']}) "
            "if payload['fixture_digest'] is not None else None; "
            "print(json.dumps(output))"
        )
        return f"cd {shlex.quote(working_dir)} && PYTHONPATH=src python3 -c {shlex.quote(script)}"

    def _execute_local_fallback(
        self,
        scenario: ScenarioInterface,
        strategy: Mapping[str, Any],
        seed: int,
        limits: ExecutionLimits,
        *,
        initial_state: Mapping[str, Any] | None,
        initial_observation: Observation | None,
        fixture_digest: str | None,
    ) -> tuple[Result, ReplayEnvelope]:
        logger.warning("Falling back to local execution for scenario %s after SSH failure", scenario.name)
        if initial_state is not None:
            assert initial_observation is not None
            assert fixture_digest is not None
            return self.fallback_executor.execute_prepared_fixture(
                scenario=scenario,
                strategy=strategy,
                seed=seed,
                limits=limits,
                initial_state=initial_state,
                initial_observation=initial_observation,
                fixture_digest=fixture_digest,
            )
        return self.fallback_executor.execute(
            scenario=scenario,
            strategy=strategy,
            seed=seed,
            limits=limits,
        )
