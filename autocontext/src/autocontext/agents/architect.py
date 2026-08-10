from __future__ import annotations

import ast
import json
import logging
from collections.abc import Mapping
from typing import Any

from autocontext.agents.role_schemas import ARCHITECT_SCHEMA
from autocontext.agents.subagent_runtime import SubagentRuntime, SubagentTask
from autocontext.agents.types import RoleExecution
from autocontext.harness.core.output_parser import extract_delimited_section, extract_json

logger = logging.getLogger(__name__)


def parse_architect_tool_specs(content: str) -> list[dict[str, Any]]:
    decoded = extract_json(content, on_failure="none")
    if decoded is None:
        # Warn only when there was something object-shaped to fail ON. Before
        # this site was migrated onto extract_json it looked for a literal
        # "```json" and returned [] SILENTLY when there wasn't one, so an empty
        # string, bare prose, or an array-shaped answer produced no log line at
        # all; warning on those now would be pure added volume with nothing
        # actionable in it. extract_json can only ever succeed by returning a
        # Mapping, so with no "{" anywhere the failure was a foregone
        # conclusion rather than a truncated proposal -- which is exactly what
        # this message claims it is.
        if "{" in content:
            logger.warning("architect output JSON block unparseable (possibly truncated); treating as no proposal")
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


_DAG_START = "<!-- DAG_CHANGES_START -->"
_DAG_END = "<!-- DAG_CHANGES_END -->"
_VALID_ACTIONS = {"add_role", "remove_role"}


def parse_dag_changes(content: str) -> list[dict[str, Any]]:
    """Extract DAG change directives from architect output.

    Looks for <!-- DAG_CHANGES_START --> ... <!-- DAG_CHANGES_END --> markers
    containing JSON: {"changes": [{"action": "add_role"|"remove_role", "name": ..., "depends_on": [...]}]}
    """
    body = extract_delimited_section(content, _DAG_START, _DAG_END)
    if body is None:
        return []
    try:
        decoded = json.loads(body)
    except json.JSONDecodeError:
        logger.warning("architect output JSON block unparseable (possibly truncated); treating as no proposal")
        return []
    if not isinstance(decoded, Mapping):
        return []
    changes = decoded.get("changes")
    if not isinstance(changes, list):
        return []
    valid: list[dict[str, Any]] = []
    for item in changes:
        if not isinstance(item, Mapping):
            continue
        action = item.get("action")
        name = item.get("name")
        if action not in _VALID_ACTIONS or not isinstance(name, str):
            continue
        entry: dict[str, Any] = {"action": action, "name": name}
        if action == "add_role":
            deps = item.get("depends_on", [])
            entry["depends_on"] = list(deps) if isinstance(deps, list) else []
        valid.append(entry)
    return valid


_HARNESS_START = "<!-- HARNESS_START -->"
_HARNESS_END = "<!-- HARNESS_END -->"


def parse_architect_harness_specs(content: str) -> list[dict[str, Any]]:
    """Extract harness validator specs from architect output.

    Looks for <!-- HARNESS_START --> ... <!-- HARNESS_END --> markers
    containing JSON: {"harness": [{"name": "...", "description": "...", "code": "..."}]}
    """
    body = extract_delimited_section(content, _HARNESS_START, _HARNESS_END)
    if body is None:
        return []
    try:
        decoded = json.loads(body)
    except json.JSONDecodeError:
        logger.warning("architect output JSON block unparseable (possibly truncated); treating as no proposal")
        return []
    if not isinstance(decoded, Mapping):
        return []
    harness = decoded.get("harness")
    if not isinstance(harness, list):
        return []
    valid: list[dict[str, Any]] = []
    for item in harness:
        if not isinstance(item, Mapping):
            continue
        name = item.get("name")
        code = item.get("code")
        if not isinstance(name, str) or not isinstance(code, str):
            continue
        # AST-validate the code
        try:
            ast.parse(code)
        except SyntaxError:
            continue
        entry: dict[str, Any] = {"name": name, "code": code}
        desc = item.get("description")
        if isinstance(desc, str):
            entry["description"] = desc
        valid.append(entry)
    return valid


class ArchitectRunner:
    def __init__(self, runtime: SubagentRuntime, model: str, max_tokens: int = 1600) -> None:
        self.runtime = runtime
        self.model = model
        self.max_tokens = max_tokens

    def run(self, prompt: str, *, system: str = "") -> RoleExecution:
        return self.runtime.run_task(
            SubagentTask(
                role="architect",
                output_schema=ARCHITECT_SCHEMA,
                model=self.model,
                prompt=prompt,
                max_tokens=self.max_tokens,
                temperature=0.4,
                system=system,
            )
        )
