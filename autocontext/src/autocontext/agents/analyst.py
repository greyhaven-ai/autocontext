from __future__ import annotations

from autocontext.agents.role_schemas import ANALYST_SCHEMA
from autocontext.agents.subagent_runtime import SubagentRuntime, SubagentTask
from autocontext.agents.types import RoleExecution


class AnalystRunner:
    def __init__(self, runtime: SubagentRuntime, model: str, max_tokens: int = 1200) -> None:
        self.runtime = runtime
        self.model = model
        self.max_tokens = max_tokens

    def run(self, prompt: str, *, system: str = "") -> RoleExecution:
        return self.runtime.run_task(
            SubagentTask(
                role="analyst",
                output_schema=ANALYST_SCHEMA,
                model=self.model,
                prompt=prompt,
                max_tokens=self.max_tokens,
                temperature=0.2,
                system=system,
            )
        )
