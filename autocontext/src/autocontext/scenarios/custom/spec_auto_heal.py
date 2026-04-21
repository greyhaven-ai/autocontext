"""Auto-heal agent task specs that reference external data without sample_input (AC-309).

When the LLM designer generates a task prompt that references external data
(e.g., "you will be provided with") but doesn't populate sample_input, this
module generates a synthetic placeholder and patches the spec so validation
passes.

Functions:
- needs_sample_input(): detect when auto-heal is needed
- generate_synthetic_sample_input(): create a structured placeholder
- heal_spec_sample_input(): auto-heal the spec in place
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from typing import Any

from autocontext.scenarios.custom.agent_task_spec import AgentTaskSpec
from autocontext.scenarios.custom.agent_task_validator import (
    _ALWAYS_EXTERNAL_PATTERNS,
    _CONTEXTUAL_DATA_PATTERNS,
    _has_inline_data_after,
)

_AUTOMATIC_RUNTIME_CONTEXT_KEYS = frozenset(
    {
        "task_name",
        "output_format",
        "sample_input",
        "context_preparation",
        "reference_context",
        "reference_sources",
    }
)


def needs_sample_input(spec: AgentTaskSpec) -> bool:
    """Detect when a spec needs auto-generated sample_input.

    Returns True when:
    - sample_input is None
    - task_prompt references external data
    - No substantial inline data follows the reference
    """
    if spec.sample_input is not None:
        return False

    prompt_lower = spec.task_prompt.lower()

    # Always-external patterns
    for pattern in _ALWAYS_EXTERNAL_PATTERNS:
        if pattern in prompt_lower:
            return True

    # Contextual patterns — only if no inline data follows
    for pattern in _CONTEXTUAL_DATA_PATTERNS:
        if pattern in prompt_lower and not _has_inline_data_after(spec.task_prompt, pattern):
            return True

    return False


def _extract_domain_hints(task_prompt: str, description: str = "") -> list[str]:
    """Extract domain-relevant nouns from prompt and description."""
    text = f"{task_prompt} {description}".lower()
    words = re.sub(r"[^a-z0-9\s]", " ", text).split()
    stop = {"the", "a", "an", "and", "or", "of", "for", "to", "in", "on", "with", "is", "are", "will", "be"}
    return [w for w in words if w not in stop and len(w) > 3][:10]


def generate_synthetic_sample_input(
    task_prompt: str,
    description: str = "",
) -> str:
    """Generate a synthetic placeholder sample_input from task context.

    Produces a JSON structure with placeholder fields derived from
    domain hints in the prompt. This is a deterministic heuristic,
    not an LLM call.
    """
    hints = _extract_domain_hints(task_prompt, description)

    # Build a simple JSON sample from domain hints
    sample: dict[str, Any] = {}
    for i, hint in enumerate(hints[:5]):
        if hint in ("data", "records", "items", "list", "entries"):
            sample[hint] = [f"sample_{hint}_1", f"sample_{hint}_2"]
        elif hint in ("patient", "customer", "user", "client"):
            sample[hint] = {"name": f"Sample {hint.title()}", "id": f"{hint}-001"}
        elif hint in ("drug", "medication", "interaction"):
            sample[hint] = [f"sample_{hint}_A", f"sample_{hint}_B"]
        else:
            sample[f"field_{i + 1}_{hint}"] = f"sample_{hint}_value"

    if not sample:
        sample = {
            "input_data": [
                {"id": "sample-1", "value": "placeholder data point 1"},
                {"id": "sample-2", "value": "placeholder data point 2"},
            ],
        }

    return json.dumps(sample, indent=2)


def heal_spec_sample_input(
    spec: AgentTaskSpec,
    description: str = "",
) -> AgentTaskSpec:
    """Auto-heal a spec by generating synthetic sample_input if needed.

    Returns the original spec if no healing is needed (sample_input already
    present or prompt doesn't reference external data).
    """
    if not needs_sample_input(spec):
        return spec

    synthetic = generate_synthetic_sample_input(spec.task_prompt, description)
    return replace(spec, sample_input=synthetic)


def heal_spec_runtime_context_requirements(spec: AgentTaskSpec) -> AgentTaskSpec:
    """Drop runtime context keys the generated agent-task surface cannot hydrate.

    Generated agent-task classes can automatically provide only a small fixed set
    of state keys during solve/improve execution. If the LLM designer invents
    additional required context keys such as `patient_case` or
    `judge_ground_truth_interactions`, the task becomes impossible to execute.
    In that case, keep only satisfiable keys and clear context-preparation
    instructions when nothing executable remains.
    """
    if not spec.required_context_keys:
        return spec

    supported_keys = [key for key in spec.required_context_keys if key in _AUTOMATIC_RUNTIME_CONTEXT_KEYS]
    if len(supported_keys) == len(spec.required_context_keys):
        return spec

    return replace(
        spec,
        context_preparation=(spec.context_preparation if supported_keys else None),
        required_context_keys=(supported_keys or None),
    )
