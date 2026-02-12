from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from mts.agents.subagent_runtime import SubagentRuntime, SubagentTask
from mts.agents.types import RoleExecution


def parse_architect_tool_specs(content: str) -> list[dict[str, Any]]:
    start = content.find("```json")
    end = content.rfind("```")
    if start == -1 or end == -1 or end <= start:
        return []
    body = content[start + 7 : end].strip()
    try:
        decoded = json.loads(body)
    except json.JSONDecodeError:
        return []
    if not isinstance(decoded, Mapping):
        return []
    tools = decoded.get("tools")
    if not isinstance(tools, list):
        return []
    valid_tools: list[dict[str, Any]] = []
    for item in tools:
        if not isinstance(item, Mapping):
            continue
        name = item.get("name")
        description = item.get("description")
        code = item.get("code")
        if not isinstance(name, str) or not isinstance(description, str) or not isinstance(code, str):
            continue
        valid_tools.append({"name": name, "description": description, "code": code})
    return valid_tools


class ArchitectRunner:
    def __init__(self, runtime: SubagentRuntime, model: str):
        self.runtime = runtime
        self.model = model

    def run(self, prompt: str) -> RoleExecution:
        return self.runtime.run_task(
            SubagentTask(
                role="architect",
                model=self.model,
                prompt=prompt,
                max_tokens=1600,
                temperature=0.4,
            )
        )
