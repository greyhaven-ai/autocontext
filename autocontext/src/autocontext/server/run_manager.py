from __future__ import annotations

import logging
import threading
import uuid
from pathlib import Path

from autocontext.config import AppSettings, load_settings
from autocontext.loop.controller import LoopController
from autocontext.loop.events import EventStreamEmitter
from autocontext.loop.generation_runner import GenerationRunner
from autocontext.scenarios import SCENARIO_REGISTRY

logger = logging.getLogger(__name__)


class RunManager:
    """Manages dynamic run creation for the interactive server."""

    def __init__(self, controller: LoopController, events: EventStreamEmitter, settings: AppSettings | None = None) -> None:
        self.controller = controller
        self.events = events
        self.settings = settings or load_settings()
        self._thread: threading.Thread | None = None
        self._active = False
        self._migrations_dir = Path(__file__).resolve().parents[2] / "migrations"

    @property
    def is_active(self) -> bool:
        return self._active

    def list_scenarios(self) -> list[str]:
        return sorted(SCENARIO_REGISTRY.keys())

    def get_environment_info(self) -> dict[str, object]:
        """Return environment metadata for TUI display."""
        scenarios: list[dict[str, str]] = []
        for name in sorted(SCENARIO_REGISTRY.keys()):
            scenario_cls = SCENARIO_REGISTRY[name]
            instance = scenario_cls()
            scenarios.append({
                "name": name,
                "description": instance.describe_rules(),
            })

        pi_configured = bool(self.settings.primeintellect_api_key)
        executors: list[dict[str, object]] = [
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

    def start_run(self, scenario: str, generations: int, run_id: str | None = None) -> str:
        if self._active:
            raise RuntimeError("A run is already active. Wait for it to finish or stop it.")
        if scenario not in SCENARIO_REGISTRY:
            supported = ", ".join(sorted(SCENARIO_REGISTRY.keys()))
            raise ValueError(f"Unknown scenario '{scenario}'. Available: {supported}")

        actual_run_id = run_id or f"tui_{uuid.uuid4().hex[:8]}"
        runner = GenerationRunner(self.settings)
        runner.migrate(self._migrations_dir)
        runner.controller = self.controller
        # Share the event emitter so subscribers get events from this run
        runner.events = self.events
        self._active = True

        def _target() -> None:
            try:
                summary = runner.run(scenario_name=scenario, generations=generations, run_id=actual_run_id)
                logger.info("Run %s completed: best_score=%.4f", summary.run_id, summary.best_score)
            except Exception:
                logger.exception("Run %s failed", actual_run_id)
            finally:
                self._active = False

        self._thread = threading.Thread(target=_target, daemon=True)
        self._thread.start()
        return actual_run_id
