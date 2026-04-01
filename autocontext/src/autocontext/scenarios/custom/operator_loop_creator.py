from __future__ import annotations

from dataclasses import asdict

from autocontext.scenarios.custom._family_creator_shim import FamilyCreatorShim
from autocontext.scenarios.custom.family_pipeline import validate_for_family
from autocontext.scenarios.custom.operator_loop_spec import OperatorLoopSpec


def validate_operator_loop_spec(spec: OperatorLoopSpec) -> list[str]:
    return validate_for_family("operator_loop", asdict(spec))


class OperatorLoopCreator(FamilyCreatorShim):
    family = "operator_loop"
