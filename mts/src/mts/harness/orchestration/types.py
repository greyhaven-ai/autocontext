"""Orchestration types — role specifications and pipeline configuration."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class RoleSpec:
    """Defines a single role in the pipeline."""

    name: str
    depends_on: tuple[str, ...] = ()
    model: str = ""
    max_tokens: int = 2048
    temperature: float = 0.2


@dataclass(slots=True)
class PipelineConfig:
    """Declarative pipeline definition with validated role DAG."""

    roles: list[RoleSpec]

    def __post_init__(self) -> None:
        from mts.harness.orchestration.dag import RoleDAG

        RoleDAG(self.roles).validate()
