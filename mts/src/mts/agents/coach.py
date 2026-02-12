from __future__ import annotations

import re

from mts.agents.subagent_runtime import SubagentRuntime, SubagentTask
from mts.agents.types import RoleExecution

_PLAYBOOK_RE = re.compile(
    r"<!--\s*PLAYBOOK_START\s*-->(.*?)<!--\s*PLAYBOOK_END\s*-->",
    re.DOTALL,
)
_LESSONS_RE = re.compile(
    r"<!--\s*LESSONS_START\s*-->(.*?)<!--\s*LESSONS_END\s*-->",
    re.DOTALL,
)
_HINTS_RE = re.compile(
    r"<!--\s*COMPETITOR_HINTS_START\s*-->(.*?)<!--\s*COMPETITOR_HINTS_END\s*-->",
    re.DOTALL,
)


def parse_coach_sections(content: str) -> tuple[str, str, str]:
    """Extract (playbook, lessons, competitor_hints) from structured coach output.

    Falls back gracefully: if markers are missing, the entire content is
    treated as the playbook; lessons and hints default to empty strings.
    """
    playbook_match = _PLAYBOOK_RE.search(content)
    lessons_match = _LESSONS_RE.search(content)
    hints_match = _HINTS_RE.search(content)

    playbook = playbook_match.group(1).strip() if playbook_match else content.strip()
    lessons = lessons_match.group(1).strip() if lessons_match else ""
    hints = hints_match.group(1).strip() if hints_match else ""

    return playbook, lessons, hints


class CoachRunner:
    def __init__(self, runtime: SubagentRuntime, model: str):
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
