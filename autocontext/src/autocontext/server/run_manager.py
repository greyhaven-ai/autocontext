from __future__ import annotations

import logging
import threading
import uuid
from pathlib import Path
from typing import Any, Literal

from autocontext.config import AppSettings, load_settings
from autocontext.loop.controller import LoopController
from autocontext.loop.events import EventStreamEmitter
from autocontext.loop.generation_runner import GenerationRunner
from autocontext.scenarios import SCENARIO_REGISTRY

logger = logging.getLogger(__name__)

StopOutcome = Literal["accepted", "duplicate", "scope_mismatch", "not_active"]


class RunManager:
    """Manages dynamic run creation for the interactive server."""

    def __init__(self, controller: LoopController, events: EventStreamEmitter, settings: AppSettings | None = None) -> None:
        self.controller = controller
        self.events = events
        self.settings = settings or load_settings()
        self._thread: threading.Thread | None = None
        self._active = False
        self._active_client_run_id: str | None = None
        self._processed_stop_command_ids: set[str] = set()
        # Serializes the run lifecycle transition (start / teardown) against
        # stop validation + controller mutation, so a stop for an old run cannot
        # validate, then land on the reused controller after a new run started.
        self._lock = threading.Lock()
        self._migrations_dir = Path(__file__).resolve().parents[2] / "migrations"

    @property
    def is_active(self) -> bool:
        return self._active

    def stop_run(self, client_run_id: str | None, command_id: str | None, reason: str | None) -> StopOutcome:
        with self._lock:
            if not self._active:
                return "not_active"
            if client_run_id is not None and client_run_id != self._active_client_run_id:
                return "scope_mismatch"
            if command_id is not None and command_id in self._processed_stop_command_ids:
                return "duplicate"
            if command_id is not None:
                self._processed_stop_command_ids.add(command_id)
            self.controller.request_stop(command_id, reason)
            return "accepted"

    def list_scenarios(self) -> list[str]:
        return sorted(SCENARIO_REGISTRY.keys())

    def get_environment_info(self) -> dict[str, Any]:
        """Return environment metadata for TUI display."""
        scenarios: list[dict[str, str]] = []
        for name in sorted(SCENARIO_REGISTRY.keys()):
            scenario_cls = SCENARIO_REGISTRY[name]
            instance = scenario_cls()
            # Dual-interface registry: game scenarios expose describe_rules(),
            # agent-task scenarios expose describe_task(). Guard for both.
            if hasattr(instance, "describe_rules"):
                description = instance.describe_rules()
            elif hasattr(instance, "describe_task"):
                description = instance.describe_task()
            else:
                description = name
            scenarios.append(
                {
                    "name": name,
                    "description": description,
                }
            )

        pi_configured = bool(self.settings.primeintellect_api_key)
        executors: list[dict[str, Any]] = [
            {
                "mode": "local",
                "available": True,
                "description": "Local process execution with sandbox isolation",
            },
            {
                "mode": "primeintellect",
                "available": pi_configured,
                "description": "Remote execution via PrimeIntellect sandbox API",
                "resources": {
                    "docker_image": self.settings.primeintellect_docker_image,
                    "cpu_cores": self.settings.primeintellect_cpu_cores,
                    "memory_gb": self.settings.primeintellect_memory_gb,
                    "disk_gb": self.settings.primeintellect_disk_size_gb,
                    "timeout_minutes": self.settings.primeintellect_timeout_minutes,
                },
            },
        ]

        return {
            "scenarios": scenarios,
            "executors": executors,
            "current_executor": self.settings.executor_mode,
            "agent_provider": self.settings.agent_provider,
        }

    def start_run(
        self,
        scenario: str,
        generations: int,
        run_id: str | None = None,
        *,
        require_playbook_approval: bool = False,
        client_run_id: str | None = None,
    ) -> str:
        if scenario not in SCENARIO_REGISTRY:
            supported = ", ".join(sorted(SCENARIO_REGISTRY.keys()))
            raise ValueError(f"Unknown scenario '{scenario}'. Available: {supported}")

        actual_run_id = run_id or f"tui_{uuid.uuid4().hex[:8]}"
        runner = GenerationRunner(self.settings)
        runner.migrate(self._migrations_dir)
        runner.controller = self.controller
        # Share the event emitter so subscribers get events from this run
        runner.events = self.events

        with self._lock:
            if self._active:
                raise RuntimeError("A run is already active. Wait for it to finish or stop it.")
            # The controller is reused across runs; clear any prior run's stop state
            # so a stop that terminated an earlier run cannot leak into this one.
            self.controller.clear_stop()
            # StopCmd always carries a non-empty client_run_id, but StartRunCmd may
            # omit it. Fall back to the server run id (returned in run_accepted) so an
            # unscoped run is still addressable for stop instead of always mismatching.
            self._active_client_run_id = client_run_id or actual_run_id
            self._processed_stop_command_ids = set()
            self._active = True

        def _target() -> None:
            try:
                summary = runner.run(
                    scenario_name=scenario,
                    generations=generations,
                    run_id=actual_run_id,
                    require_playbook_approval=require_playbook_approval,
                )
                logger.info("Run %s completed: best_score=%.4f", summary.run_id, summary.best_score)
            except Exception:
                logger.exception("Run %s failed", actual_run_id)
            finally:
                with self._lock:
                    # Clear run-scoped state before flipping _active off last, so a
                    # concurrent start_run cannot observe this run's stale scope.
                    self._active_client_run_id = None
                    self._processed_stop_command_ids.clear()
                    self._active = False

        self._thread = threading.Thread(target=_target, daemon=True)
        self._thread.start()
        return actual_run_id
