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
    "You are a scenario designer for autocontext, an agent evaluation system. "
    "Given a natural language description, produce an AgentTaskSpec JSON "
    "that defines a task prompt, evaluation rubric, output format, and optional judge model.\n\n"
    f"The output must be valid JSON wrapped in delimiters:\n"
    f"{SPEC_START}\n{{ ... }}\n{SPEC_END}\n\n"
    "## AgentTaskSpec Schema\n\n"
    "```json\n"
    "{\n"
    '  "task_prompt": "The full prompt given to the agent being evaluated",\n'
    '  "judge_rubric": "Detailed rubric for the LLM judge to score the output",\n'
    '  "output_format": "free_text | json_schema | code",\n'
    '  "judge_model": "",\n'
    '  "difficulty_tiers": null,\n'
    '  "reference_context": "Authoritative domain knowledge for judging factual accuracy (optional, null if not needed)",\n'
    '  "reference_sources": ["list of source URLs or references (optional)"],\n'
    '  "required_concepts": ["key concepts the output must correctly address (optional)"],\n'
    '  "sample_input": "Realistic sample input data for data-dependent tasks (optional, null if not needed)",\n'
    '  "context_preparation": "Instructions for gathering context before generation (optional, null if not needed)",\n'
    '  "required_context_keys": ["state keys that must be present after context preparation (optional)"],\n'
    '  "max_rounds": 1,\n'
    '  "quality_threshold": 0.9,\n'
    '  "revision_prompt": "Instructions for revising output based on judge feedback (optional)"\n'
    "}\n"
    "```\n\n"
    "## Rules\n\n"
    "- `task_prompt` must be clear, detailed, and self-contained\n"
    '- `task_prompt` must be FULLY self-contained: never say "you will be provided with..." or reference '
    "external data without including it. If the task depends on input data, populate `sample_input` with "
    "realistic example data and embed it directly in the prompt\n"
    "- `sample_input` (optional, null if not needed) — realistic sample input data for data-dependent tasks. "
    "Populate this whenever the task requires the agent to process specific input "
    "(e.g. an outage report, a code snippet, a dataset)\n"
    "- `judge_rubric` must list specific evaluation dimensions with criteria\n"
    "- `output_format` must be one of: free_text, json_schema, code\n"
    "- `judge_model` is optional; use an empty string to fall back to the configured judge/default provider model\n"
    "- `reference_context` (optional) — authoritative domain knowledge the judge should use to verify factual accuracy. "
    "Include this when the task requires domain-specific knowledge that the judge LLM may not have. "
    "When provided, the judge will score factual_accuracy as a mandatory dimension.\n"
    "- `reference_sources` (optional) — list of source URLs or citations for the reference context\n"
    "- `required_concepts` (optional) — key concepts the output must correctly address\n"
    "- `context_preparation` (optional) — instructions for gathering/loading context before the agent generates output. "
    "Use this when the task requires research, document loading, or other preparation steps.\n"
    "- `required_context_keys` (optional) — state dictionary keys that must be present after context preparation. "
    "Used to validate that preparation completed successfully.\n"
    "- `calibration_examples` — You MUST include at least 2 calibration examples: one low-quality output "
    "(~0.3 score) and one high-quality output (~0.9 score). Each example must have `human_score`, "
    "`human_notes`, and `agent_output` fields. These anchor the judge's scoring scale and are critical "
    "for consistent evaluation.\n"
    "- `max_rounds` (optional, default 1) — maximum improvement rounds. Set >1 to enable iterative refinement.\n"
    "- `quality_threshold` (optional, default 0.9) — stop improving when score >= this value.\n"
    "- `revision_prompt` (optional) — instructions for how the agent should revise its output based on judge feedback.\n\n"
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
