from __future__ import annotations

import logging

from mts.agents.subagent_runtime import SubagentRuntime, SubagentTask
from mts.agents.types import RoleExecution

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
