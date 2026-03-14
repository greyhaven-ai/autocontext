from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ArtifactSpecModel:
    path: str
    content: str
    content_type: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ArtifactEditingSpec:
    task_description: str
    rubric: str
    validation_rules: list[str]
    artifacts: list[ArtifactSpecModel]
