from __future__ import annotations

from autocontext.agents.subagent_runtime import SubagentRuntime, SubagentTask
from autocontext.agents.types import RoleExecution


class AnalystRunner:
    def __init__(self, runtime: SubagentRuntime, model: str) -> None:
        self.runtime = runtime
        self.model = model

    def run(self, prompt: str) -> RoleExecution:
        return self.runtime.run_task(
            SubagentTask(
                role="analyst",
                model=self.model,
                prompt=prompt,
                max_tokens=1200,
                temperature=0.2,
            )
        )
