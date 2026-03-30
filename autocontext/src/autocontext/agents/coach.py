from __future__ import annotations

from autocontext.agents.subagent_runtime import SubagentRuntime, SubagentTask
from autocontext.agents.types import RoleExecution
from autocontext.harness.core.output_parser import extract_delimited_section


def parse_coach_sections(content: str) -> tuple[str, str, str]:
    """Extract (playbook, lessons, competitor_hints) from structured coach output.

    Falls back gracefully: if markers are missing, the entire content is
    treated as the playbook; lessons and hints default to empty strings.
    """
    playbook = extract_delimited_section(content, "<!-- PLAYBOOK_START -->", "<!-- PLAYBOOK_END -->")
    lessons = extract_delimited_section(content, "<!-- LESSONS_START -->", "<!-- LESSONS_END -->")
    hints = extract_delimited_section(content, "<!-- COMPETITOR_HINTS_START -->", "<!-- COMPETITOR_HINTS_END -->")

    # Fallback: no playbook markers → entire content IS the playbook
    if playbook is None:
        playbook = content.strip()

    return playbook, lessons or "", hints or ""


class CoachRunner:
    def __init__(self, runtime: SubagentRuntime, model: str) -> None:
        self.runtime = runtime
        self.model = model

    def run(self, prompt: str) -> RoleExecution:
        return self.runtime.run_task(
            SubagentTask(
                role="coach",
                model=self.model,
                prompt=prompt,
                max_tokens=2000,
                temperature=0.4,
            )
        )
