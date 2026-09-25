"""Shared role cadence and dependency policy for generation execution."""

from __future__ import annotations

import uuid

from autocontext.harness.core.types import RoleExecution, RoleUsage
from autocontext.harness.orchestration.dag import RoleDAG
from autocontext.harness.orchestration.types import RoleSpec


def architect_due(generation: int, every_n_generations: int) -> bool:
    return generation % every_n_generations == 0


def skipped_architect(generation: int, every_n_generations: int) -> RoleExecution:
    return RoleExecution(
        role="architect",
        content='Architect skipped by cadence; no infrastructure changes.\n```json\n{"tools": []}\n```',
        usage=RoleUsage(input_tokens=0, output_tokens=0, latency_ms=0, model=""),
        subagent_id=uuid.uuid4().hex,
        status="skipped",
        metadata={"reason": "architect_cadence", "generation": generation, "every_n_generations": every_n_generations},
    )


def feedback_roles(*, depends_on: tuple[str, ...] = ()) -> list[RoleSpec]:
    return [
        RoleSpec(name="analyst", depends_on=depends_on),
        RoleSpec(name="architect", depends_on=depends_on),
        RoleSpec(name="coach", depends_on=("analyst",)),
    ]


def feedback_dag() -> RoleDAG:
    return RoleDAG(feedback_roles())
