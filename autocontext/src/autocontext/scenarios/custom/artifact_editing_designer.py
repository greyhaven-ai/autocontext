from __future__ import annotations

import json
import re

from autocontext.agents.types import LlmFn
from autocontext.scenarios.custom.artifact_editing_spec import (
    ArtifactEditingSpec,
    ArtifactSpecModel,
)

ARTIFACT_SPEC_START = "<!-- ARTIFACT_EDITING_SPEC_START -->"
ARTIFACT_SPEC_END = "<!-- ARTIFACT_EDITING_SPEC_END -->"

_EXAMPLE_SPEC = {
    "task_description": "Update a YAML service config to add a database section without changing unrelated settings.",
    "rubric": "Evaluate correctness of the edited artifacts, satisfaction of validation rules, and minimal unnecessary changes.",
    "validation_rules": [
        'config/app.yaml must contain "database:"',
        'config/app.yaml must contain "host:"',
        'config/app.yaml must contain "port:"',
    ],
    "artifacts": [
        {
            "path": "config/app.yaml",
            "content": "app:\n  name: myapp\n  port: 8080\n",
            "content_type": "yaml",
        }
    ],
}

ARTIFACT_EDITING_DESIGNER_SYSTEM = (
    "You are a scenario designer for autocontext. "
    "Given a natural-language request for an artifact-editing task, produce an "
    "ArtifactEditingSpec JSON wrapped in delimiters.\n\n"
    f"{ARTIFACT_SPEC_START}\n{{ ... }}\n{ARTIFACT_SPEC_END}\n\n"
    "Schema:\n"
    "{\n"
    '  "task_description": "what the agent should change in the artifacts",\n'
    '  "rubric": "how the final edited artifacts should be judged",\n'
    '  "validation_rules": ["path/to/file must contain \\"snippet\\""],\n'
    '  "artifacts": [\n'
    "    {\n"
    '      "path": "config/app.yaml",\n'
    '      "content": "current file contents",\n'
    '      "content_type": "yaml"\n'
    "    }\n"
    "  ]\n"
    "}\n\n"
    "Rules:\n"
    "- model the task around editing concrete artifacts, not writing prose about them\n"
    "- include at least one artifact with realistic initial content\n"
    "- express validation rules as path-scoped must-contain or must-not-contain checks when possible\n"
    "- keep the rubric focused on artifact correctness, validator success, and precision of edits\n\n"
    f"Example:\n{ARTIFACT_SPEC_START}\n{json.dumps(_EXAMPLE_SPEC, indent=2)}\n{ARTIFACT_SPEC_END}\n"
)


def parse_artifact_editing_spec(text: str) -> ArtifactEditingSpec:
    pattern = re.escape(ARTIFACT_SPEC_START) + r"\s*(.*?)\s*" + re.escape(ARTIFACT_SPEC_END)
    match = re.search(pattern, text, re.DOTALL)
    if not match:
        raise ValueError("response does not contain ARTIFACT_EDITING_SPEC delimiters")
    data = json.loads(match.group(1).strip())
    return ArtifactEditingSpec(
        task_description=data["task_description"],
        rubric=data["rubric"],
        validation_rules=data["validation_rules"],
        artifacts=[
            ArtifactSpecModel(
                path=raw["path"],
                content=raw["content"],
                content_type=raw["content_type"],
                metadata=raw.get("metadata", {}),
            )
            for raw in data["artifacts"]
        ],
    )


def design_artifact_editing(description: str, llm_fn: LlmFn) -> ArtifactEditingSpec:
    response = llm_fn(ARTIFACT_EDITING_DESIGNER_SYSTEM, f"User description:\n{description}")
    return parse_artifact_editing_spec(response)
