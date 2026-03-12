from __future__ import annotations

import logging

from autocontext.agents.subagent_runtime import SubagentRuntime, SubagentTask
from autocontext.agents.types import RoleExecution

LOGGER = logging.getLogger(__name__)


class CompetitorRunner:
    def __init__(self, runtime: SubagentRuntime, model: str):
        self.runtime = runtime
        self.model = model

    def run(self, prompt: str, tool_context: str = "") -> tuple[str, RoleExecution]:
        final_prompt = prompt
        if tool_context:
            final_prompt += f"\n\nAvailable tools and hints:\n{tool_context}\n"
        execution = self.runtime.run_task(
            SubagentTask(
                role="competitor",
                model=self.model,
                prompt=final_prompt,
                max_tokens=800,
                temperature=0.2,
            )
        )
        return execution.content, execution

    def revise(self, original_prompt: str, revision_prompt: str, tool_context: str = "") -> tuple[str, RoleExecution]:
        """Re-run competitor with revision feedback appended."""
        combined = f"{original_prompt}\n\n--- REVISION REQUIRED ---\n{revision_prompt}"
        return self.run(combined, tool_context=tool_context)

    def refine_strategy(self, refinement_prompt: str, tool_context: str = "") -> tuple[str, RoleExecution]:
        """Refine an existing strategy given match feedback (tree search)."""
        return self.run(refinement_prompt, tool_context=tool_context)
