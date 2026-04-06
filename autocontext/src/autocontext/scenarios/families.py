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
        raise ValueError(f"Scenario type marker '{family.scenario_type_marker}' is already registered")
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

    Precedence:
      1. Explicit ``family`` class attribute (set by custom codegen).
      2. Structural isinstance probing (legacy / built-in scenarios).

    The explicit-attribute path fixes AC-524: custom-generated scenarios
    from the generic ScenarioCreator extend ScenarioInterface (game base)
    but carry ``family = "operator_loop"`` etc. as a class attribute.
    """
    # 1. Explicit attribute — authoritative when present and registered.
    explicit = getattr(scenario, "family", None)
    if isinstance(explicit, str) and explicit in FAMILY_REGISTRY:
        return FAMILY_REGISTRY[explicit]

    # 2. Structural — sort so subclasses are tested before base classes.
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
    from autocontext.scenarios.coordination import CoordinationInterface
    from autocontext.scenarios.investigation import InvestigationInterface
    from autocontext.scenarios.negotiation import NegotiationInterface
    from autocontext.scenarios.operator_loop import OperatorLoopInterface
    from autocontext.scenarios.schema_evolution import SchemaEvolutionInterface
    from autocontext.scenarios.simulation import SimulationInterface
    from autocontext.scenarios.tool_fragility import ToolFragilityInterface
    from autocontext.scenarios.workflow import WorkflowInterface

    register_family(
        ScenarioFamily(
            name="game",
            description="Tournament-evaluated game scenarios with Elo-based progression",
            interface_class=ScenarioInterface,
            evaluation_mode="tournament",
            output_modes=["json_strategy"],
            scenario_type_marker="parametric",
            capabilities=["elo_ranking", "playbook", "tournament"],
            supports_playbook=True,
        )
    )

    register_family(
        ScenarioFamily(
            name="agent_task",
            description="LLM-judge-evaluated agent tasks with optional improvement loops",
            interface_class=AgentTaskInterface,
            evaluation_mode="llm_judge",
            output_modes=["free_text", "code", "json_schema"],
            scenario_type_marker="agent_task",
            capabilities=["improvement_loop", "revision"],
        )
    )

    register_family(
        ScenarioFamily(
            name="simulation",
            description="Action-trace-evaluated simulation scenarios with mock environments",
            interface_class=SimulationInterface,
            evaluation_mode="trace_evaluation",
            output_modes=["action_trace"],
            scenario_type_marker="simulation",
            capabilities=["fault_injection", "action_validation", "playbook", "tournament"],
            supports_playbook=True,
        )
    )

    register_family(
        ScenarioFamily(
            name="artifact_editing",
            description="Artifact-state-evaluated scenarios where agents modify files, configs, and schemas",
            interface_class=ArtifactEditingInterface,
            evaluation_mode="artifact_validation",
            output_modes=["artifact_diff"],
            scenario_type_marker="artifact_editing",
            capabilities=["artifact_lineage", "diff_tracking", "validation_pipeline"],
        )
    )

    register_family(
        ScenarioFamily(
            name="investigation",
            description="Evidence-chain-evaluated investigation scenarios with red herring detection",
            interface_class=InvestigationInterface,
            evaluation_mode="evidence_evaluation",
            output_modes=["action_trace"],
            scenario_type_marker="investigation",
            capabilities=["evidence_chain", "red_herring_detection", "diagnosis_accuracy"],
        )
    )

    register_family(
        ScenarioFamily(
            name="workflow",
            description="Transactional workflow scenarios with compensation, retry, and side-effect tracking",
            interface_class=WorkflowInterface,
            evaluation_mode="workflow_evaluation",
            output_modes=["action_trace"],
            scenario_type_marker="workflow",
            capabilities=["compensation", "retry", "side_effect_tracking", "rollback"],
        )
    )

    register_family(
        ScenarioFamily(
            name="negotiation",
            description="Negotiation scenarios with hidden preferences, BATNA constraints, and opponent modeling",
            interface_class=NegotiationInterface,
            evaluation_mode="negotiation_evaluation",
            output_modes=["action_trace"],
            scenario_type_marker="negotiation",
            capabilities=["opponent_modeling", "hidden_state", "repeated_rounds", "adaptation"],
        )
    )

    register_family(
        ScenarioFamily(
            name="schema_evolution",
            description="Schema-evolution scenarios where state changes mid-run and agents must detect stale context",
            interface_class=SchemaEvolutionInterface,
            evaluation_mode="schema_adaptation",
            output_modes=["action_trace"],
            scenario_type_marker="schema_evolution",
            capabilities=["stale_detection", "schema_migration", "context_invalidation"],
        )
    )

    register_family(
        ScenarioFamily(
            name="tool_fragility",
            description="Tool-fragility scenarios where APIs drift and agents must adapt to changed tool behaviour",
            interface_class=ToolFragilityInterface,
            evaluation_mode="drift_adaptation",
            output_modes=["action_trace"],
            scenario_type_marker="tool_fragility",
            capabilities=["drift_detection", "failure_attribution", "tool_adaptation"],
        )
    )

    register_family(
        ScenarioFamily(
            name="operator_loop",
            description="Operator-in-the-loop scenarios testing escalation and clarification judgment",
            interface_class=OperatorLoopInterface,
            evaluation_mode="judgment_evaluation",
            output_modes=["action_trace"],
            scenario_type_marker="operator_loop",
            capabilities=["escalation", "clarification", "judgment_scoring"],
        )
    )

    register_family(
        ScenarioFamily(
            name="coordination",
            description="Multi-agent coordination scenarios with partial context, handoff, and merge",
            interface_class=CoordinationInterface,
            evaluation_mode="coordination_evaluation",
            output_modes=["action_trace"],
            scenario_type_marker="coordination",
            capabilities=["partial_context", "handoff", "merge", "duplication_detection"],
        )
    )


_register_builtins()
