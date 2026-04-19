from __future__ import annotations

import json
import re

from autocontext.agents.types import LlmFn
from autocontext.scenarios.custom.agent_task_spec import (
    AgentTaskSpec,
    normalize_agent_task_runtime_fields,
)

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
    "judge_model": "",
    "difficulty_tiers": None,
    "reference_context": None,
    "reference_sources": None,
    "required_concepts": None,
    "context_preparation": None,
    "required_context_keys": None,
    "calibration_examples": [
        {
            "human_score": 0.3,
            "human_notes": "Returns max instead of second-largest; no edge case handling",
            "agent_output": "def second_largest(lst):\n    return max(lst)",
        },
        {
            "human_score": 0.9,
            "human_notes": "Correct logic, clean code, handles edge cases with clear error messages",
            "agent_output": (
                "def second_largest(lst):\n"
                "    unique = sorted(set(lst), reverse=True)\n"
                "    if len(unique) < 2:\n"
                "        raise ValueError('Need at least 2 unique values')\n"
                "    return unique[1]"
            ),
        },
    ],
    "max_rounds": 1,
    "quality_threshold": 0.9,
    "revision_prompt": None,
}

AGENT_TASK_DESIGNER_SYSTEM = (
    "You design AgentTaskSpec JSON for autocontext. "
    "Return only one JSON object wrapped in the required delimiters.\n\n"
    f"{SPEC_START}\n{{ ... }}\n{SPEC_END}\n\n"
    "Required fields:\n"
    '- "task_prompt": self-contained prompt for the evaluated agent\n'
    '- "judge_rubric": explicit scoring dimensions and criteria\n'
    '- "output_format": one of free_text, json_schema, or code\n\n'
    '- "calibration_examples": MUST include at least 2 calibration examples '
    "with human_score, human_notes, and agent_output fields\n\n"
    "Optional fields (use null or omit when unnecessary): judge_model, difficulty_tiers, "
    "reference_context, reference_sources, required_concepts, sample_input, "
    "context_preparation, required_context_keys, max_rounds, "
    "quality_threshold, revision_prompt.\n\n"
    "Rules:\n"
    "- Keep the task executable from the prompt, sample_input, reference_context, "
    "and reference_sources alone whenever possible.\n"
    "- If the task depends on concrete input data, include realistic sample_input and make the prompt self-contained.\n"
    "- Use context_preparation and required_context_keys only when the task truly "
    "needs extra runtime-loaded context; otherwise set them to null.\n"
    "- Do not invent impossible external loaders or unsatisfied state keys.\n"
    "- Prefer concise, domain-specific rubrics over generic prose-quality language.\n"
    "- For structured tasks, output_format should usually be json_schema.\n"
    "- If iterative refinement is useful, set max_rounds > 1 and provide a revision_prompt.\n\n"
    "Produce the smallest complete AgentTaskSpec that faithfully captures the user description.\n"
)


def parse_agent_task_spec(text: str) -> AgentTaskSpec:
    """Parse an AgentTaskSpec from LLM response text."""
    pattern = re.escape(SPEC_START) + r"\s*(.*?)\s*" + re.escape(SPEC_END)
    match = re.search(pattern, text, re.DOTALL)
    if not match:
        raise ValueError("response does not contain AGENT_TASK_SPEC delimiters")
    raw = match.group(1).strip()
    data = json.loads(raw)
    return normalize_agent_task_runtime_fields(
        AgentTaskSpec(
            task_prompt=data["task_prompt"],
            judge_rubric=data["judge_rubric"],
            output_format=data.get("output_format", "free_text"),
            judge_model=data.get("judge_model", ""),
            difficulty_tiers=data.get("difficulty_tiers"),
            reference_context=data.get("reference_context"),
            reference_sources=data.get("reference_sources"),
            required_concepts=data.get("required_concepts"),
            calibration_examples=data.get("calibration_examples"),
            context_preparation=data.get("context_preparation"),
            required_context_keys=data.get("required_context_keys"),
            max_rounds=data.get("max_rounds", 1),
            quality_threshold=data.get("quality_threshold", 0.9),
            revision_prompt=data.get("revision_prompt"),
            sample_input=data.get("sample_input"),
        )
    )


def design_agent_task(description: str, llm_fn: LlmFn) -> AgentTaskSpec:
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
