"""per-target serving resolution and a health-checkable supervisor.

Ambient trains and serves per TARGET, and publish.py slots each candidate by its
target name (the real scenario is preserved as scenario_family). So resolving the
live model for a target is a scenario-routing lookup keyed on the target name.

resolve_active_serving is a thin wrapper over the scenario-routing resolver that
returns the active model for a target (or the fallback when none is live).

ServerSupervisor carries the health/rollback logic a real vLLM launch needs, with
launch itself left to a later slice (plan 5b/ops). It is unit-tested with fakes:
poll_interval_fn defaults to a no-op so tests never sleep.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from autocontext.providers.scenario_routing import (
    RoutingDecision,
    ScenarioRoutingContext,
    resolve_provider_for_context,
)
from autocontext.training.model_registry import ModelRegistry


def resolve_active_serving(registry: ModelRegistry, target_name: str, backend: str) -> RoutingDecision:
    """Resolve the live serving model for a target.

    Because ambient candidates are slotted by target.name, routing on the target
    name resolves the ACTIVE model for that target (or the fallback when none is live).
    """
    ctx = ScenarioRoutingContext(scenario=target_name, backend=backend)
    return resolve_provider_for_context(ctx, registry)


def _noop_interval(_attempt: int) -> None:
    return None


@dataclass(slots=True)
class ServerSupervisor:
    """Health-poll and rollback logic for a served ambient model.

    Real process launch is out of scope here; start_fn/stop_fn are carried for the
    launch slice that wires this to a concrete vLLM server.
    """

    health_fn: Callable[[], bool]
    start_fn: Callable[[], None] | None = None
    stop_fn: Callable[[], None] | None = None
    poll_attempts: int = 5
    poll_interval_fn: Callable[[int], None] | None = None

    def wait_until_healthy(self) -> bool:
        """Poll health_fn up to poll_attempts times, returning True on the first healthy poll.

        Between attempts poll_interval_fn(attempt) is invoked when set (a test can count
        the calls; a real supervisor sleeps there). Returns False if never healthy.
        """
        interval = self.poll_interval_fn or _noop_interval
        for attempt in range(self.poll_attempts):
            if self.health_fn():
                return True
            if attempt < self.poll_attempts - 1:
                interval(attempt)
        return False

    def rollback(self, registry: ModelRegistry, target_name: str, previous_artifact_id: str) -> None:
        """Re-activate the previous model for a target after a failed serve.

        previous_artifact_id is the incumbent the promote flow demoted and kept warm.
        An empty id means there is no warm rollback target, which is a programming error.
        """
        if not previous_artifact_id:
            raise ValueError(f"no previous artifact to roll back to for target {target_name!r}")
        registry.activate(previous_artifact_id)
