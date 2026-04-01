from __future__ import annotations

from dataclasses import asdict

from autocontext.scenarios.custom._family_creator_shim import FamilyCreatorShim
from autocontext.scenarios.custom.family_pipeline import validate_for_family
from autocontext.scenarios.custom.simulation_spec import SimulationSpec


def should_use_simulation_family(description: str) -> bool:
    lowered = description.lower()
    keywords = (
        "stateful",
        "simulation",
        "workflow",
        "orchestration",
        "api",
        "rollback",
        "retry",
        "cancellation",
        "transaction",
        "debug",
        "diagnos",
        "evidence",
        "side effect",
    )
    return any(keyword in lowered for keyword in keywords)


def validate_simulation_spec(spec: SimulationSpec) -> list[str]:
    return validate_for_family("simulation", asdict(spec))


class SimulationCreator(FamilyCreatorShim):
    family = "simulation"
