from __future__ import annotations

import json
import re
from collections.abc import Callable

from mts.scenarios.custom.agent_task_spec import AgentTaskSpec

SPEC_START = "<!-- AGENT_TASK_SPEC_START -->"
SPEC_END = "<!-- AGENT_TASK_SPEC_END -->"

_EXAMPLE_SPEC = {
    "task_prompt": (
        "Write a Python function that takes a list of integers and returns "
        "the second largest unique value. Handle edge cases like empty lists "
        "and lists with fewer than two unique values."
    ),
    "judge_rubric": (
        "Evaluate on: (1) Correctness — does the function return the right answer "
        "for normal and edge cases? (2) Code quality — is it readable, well-named, "
        "and idiomatic Python? (3) Edge case handling — does it handle empty lists, "
        "single-element lists, and duplicate values gracefully?"
    ),
    "output_format": "code",
    "judge_model": "claude-sonnet-4-20250514",
    "difficulty_tiers": None,
    "reference_context": None,
    "reference_sources": None,
    "required_concepts": None,
}

AGENT_TASK_DESIGNER_SYSTEM = (
    "You are a scenario designer for MTS, an agent evaluation system. "
    "Given a natural language description, produce an AgentTaskSpec JSON "
    "that defines a task prompt, evaluation rubric, output format, and judge model.\n\n"
    f"The output must be valid JSON wrapped in delimiters:\n"
    f"{SPEC_START}\n{{ ... }}\n{SPEC_END}\n\n"
    "## AgentTaskSpec Schema\n\n"
    "```json\n"
    "{\n"
    '  "task_prompt": "The full prompt given to the agent being evaluated",\n'
    '  "judge_rubric": "Detailed rubric for the LLM judge to score the output",\n'
    '  "output_format": "free_text | json_schema | code",\n'
    '  "judge_model": "claude-sonnet-4-20250514",\n'
    '  "difficulty_tiers": null,\n'
    '  "reference_context": "Authoritative domain knowledge for judging factual accuracy (optional, null if not needed)",\n'
    '  "reference_sources": ["list of source URLs or references (optional)"],\n'
    '  "required_concepts": ["key concepts the output must correctly address (optional)"]\n'
    "}\n"
    "```\n\n"
    "## Rules\n\n"
    "- `task_prompt` must be clear, detailed, and self-contained\n"
    "- `judge_rubric` must list specific evaluation dimensions with criteria\n"
    "- `output_format` must be one of: free_text, json_schema, code\n"
    "- `judge_model` should be a valid model identifier\n"
    "- `reference_context` (optional) — authoritative domain knowledge the judge should use to verify factual accuracy. "
    "Include this when the task requires domain-specific knowledge that the judge LLM may not have. "
    "When provided, the judge will score factual_accuracy as a mandatory dimension.\n"
    "- `reference_sources` (optional) — list of source URLs or citations for the reference context\n"
    "- `required_concepts` (optional) — key concepts the output must correctly address\n\n"
    f"## Example\n\n{SPEC_START}\n"
    f"{json.dumps(_EXAMPLE_SPEC, indent=2)}\n"
    f"{SPEC_END}\n\n"
    "Now design an agent task scenario for the user's description.\n"
)


def parse_agent_task_spec(text: str) -> AgentTaskSpec:
    """Parse an AgentTaskSpec from LLM response text."""
    pattern = re.escape(SPEC_START) + r"\s*(.*?)\s*" + re.escape(SPEC_END)
    match = re.search(pattern, text, re.DOTALL)
    if not match:
        raise ValueError("response does not contain AGENT_TASK_SPEC delimiters")
    raw = match.group(1).strip()
    data = json.loads(raw)
    return AgentTaskSpec(
        task_prompt=data["task_prompt"],
        judge_rubric=data["judge_rubric"],
        output_format=data.get("output_format", "free_text"),
        judge_model=data.get("judge_model", "claude-sonnet-4-20250514"),
        difficulty_tiers=data.get("difficulty_tiers"),
        reference_context=data.get("reference_context"),
        reference_sources=data.get("reference_sources"),
        required_concepts=data.get("required_concepts"),
        calibration_examples=data.get("calibration_examples"),
    )


def design_agent_task(description: str, llm_fn: Callable[[str, str], str]) -> AgentTaskSpec:
    """Design an agent task spec from a natural language description.

    Args:
        description: Natural language description of the task.
        llm_fn: Callable(system_prompt, user_prompt) -> response text.

    Returns:
        Parsed AgentTaskSpec.
    """
    user_prompt = f"User description:\n{description}"
    response = llm_fn(AGENT_TASK_DESIGNER_SYSTEM, user_prompt)
    return parse_agent_task_spec(response)
