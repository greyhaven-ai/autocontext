"""Scenario-family registry and typed creation contracts (AC-245).

Provides a first-class ScenarioFamily abstraction so that creation
pipelines target explicit families (game, agent_task, simulation, …)
instead of collapsing complex requests into ad-hoc task shapes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ScenarioFamily:
    """Metadata for a scenario family."""

    name: str
    description: str
    interface_class: type
    evaluation_mode: str  # e.g. "tournament", "llm_judge", "trace_evaluation"
    output_modes: list[str]  # e.g. ["json_strategy"], ["free_text", "code"], ["action_trace"]
    scenario_type_marker: str  # value written to scenario_type.txt
    capabilities: list[str] = field(default_factory=list)
    supports_knowledge_accumulation: bool = True
    supports_playbook: bool = False


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

FAMILY_REGISTRY: dict[str, ScenarioFamily] = {}


def register_family(family: ScenarioFamily) -> None:
    """Register a scenario family. Raises ValueError on duplicate."""
    if family.name in FAMILY_REGISTRY:
        raise ValueError(f"Scenario family '{family.name}' is already registered")
    if any(existing.scenario_type_marker == family.scenario_type_marker for existing in FAMILY_REGISTRY.values()):
        raise ValueError(
            f"Scenario type marker '{family.scenario_type_marker}' is already registered"
        )
    FAMILY_REGISTRY[family.name] = family


def get_family(name: str) -> ScenarioFamily:
    """Look up a family by name. Raises KeyError if not found."""
    if name not in FAMILY_REGISTRY:
        raise KeyError(f"Unknown scenario family '{name}'. Available: {list(FAMILY_REGISTRY)}")
    return FAMILY_REGISTRY[name]


def list_families() -> list[ScenarioFamily]:
    """Return all registered families."""
    return list(FAMILY_REGISTRY.values())


def get_family_by_marker(marker: str) -> ScenarioFamily:
    """Look up a family by persisted scenario_type marker."""
    for family in FAMILY_REGISTRY.values():
        if family.scenario_type_marker == marker:
            return family
    raise KeyError(f"Unknown scenario type marker '{marker}'")


def get_family_marker(name: str) -> str:
    """Return the persisted scenario_type marker for a family."""
    return get_family(name).scenario_type_marker


def detect_family(scenario: Any) -> ScenarioFamily | None:
    """Detect which family a scenario instance belongs to.

    Checks more specific families first (simulation before game)
    to handle inheritance correctly.
    """
    # Sort so that subclasses are tested before base classes.
    # A family whose interface_class is a subclass of another's
    # should be checked first.
    sorted_families = sorted(
        FAMILY_REGISTRY.values(),
        key=lambda f: _inheritance_depth(f.interface_class),
        reverse=True,
    )
    for family in sorted_families:
        if isinstance(scenario, family.interface_class):
            return family
    return None


def _inheritance_depth(cls: type) -> int:
    """Return the MRO depth of a class (deeper = more specific)."""
    return len(cls.__mro__)


# ---------------------------------------------------------------------------
# Built-in families — registered at import time
# ---------------------------------------------------------------------------

def _register_builtins() -> None:
    from autocontext.scenarios.agent_task import AgentTaskInterface
    from autocontext.scenarios.artifact_editing import ArtifactEditingInterface
    from autocontext.scenarios.base import ScenarioInterface
    from autocontext.scenarios.simulation import SimulationInterface

    register_family(ScenarioFamily(
        name="game",
        description="Tournament-evaluated game scenarios with Elo-based progression",
        interface_class=ScenarioInterface,
        evaluation_mode="tournament",
        output_modes=["json_strategy"],
        scenario_type_marker="parametric",
        capabilities=["elo_ranking", "playbook", "tournament"],
        supports_playbook=True,
    ))

    register_family(ScenarioFamily(
        name="agent_task",
        description="LLM-judge-evaluated agent tasks with optional improvement loops",
        interface_class=AgentTaskInterface,
        evaluation_mode="llm_judge",
        output_modes=["free_text", "code", "json_schema"],
        scenario_type_marker="agent_task",
        capabilities=["improvement_loop", "revision"],
    ))

    register_family(ScenarioFamily(
        name="simulation",
        description="Action-trace-evaluated simulation scenarios with mock environments",
        interface_class=SimulationInterface,
        evaluation_mode="trace_evaluation",
        output_modes=["action_trace"],
        scenario_type_marker="simulation",
        capabilities=["fault_injection", "action_validation", "playbook", "tournament"],
        supports_playbook=True,
    ))

    register_family(ScenarioFamily(
        name="artifact_editing",
        description="Artifact-state-evaluated scenarios where agents modify files, configs, and schemas",
        interface_class=ArtifactEditingInterface,
        evaluation_mode="artifact_validation",
        output_modes=["artifact_diff"],
        scenario_type_marker="artifact_editing",
        capabilities=["artifact_lineage", "diff_tracking", "validation_pipeline"],
    ))


_register_builtins()
