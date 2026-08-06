from __future__ import annotations

import logging

from autocontext.agents.subagent_runtime import SubagentRuntime, SubagentTask
from autocontext.agents.types import RoleExecution
from autocontext.harness.core.output_parser import extract_delimited_section

logger = logging.getLogger(__name__)


def parse_coach_sections(content: str) -> tuple[str, str, str]:
    """Extract (playbook, lessons, competitor_hints) from structured coach output.

    Falls back gracefully: if markers are missing, the entire content is
    treated as the playbook; lessons and hints default to empty strings.
    """
    playbook = extract_delimited_section(content, "<!-- PLAYBOOK_START -->", "<!-- PLAYBOOK_END -->")
    lessons = extract_delimited_section(content, "<!-- LESSONS_START -->", "<!-- LESSONS_END -->")
    hints = extract_delimited_section(content, "<!-- COMPETITOR_HINTS_START -->", "<!-- COMPETITOR_HINTS_END -->")

    if playbook is None:
        if "<!-- PLAYBOOK_START -->" in content:
            # AC-904: START without END is the truncation signature; persisting
            # the fragment would make a cut-off response the playbook. Fail
            # closed by discarding the update.
            logger.warning("coach output truncated: PLAYBOOK_START without PLAYBOOK_END; discarding update")
            playbook = ""
        else:
            # Legacy fallback: no markers at all means the model ignored the
            # format and the entire content is the playbook.
            playbook = content.strip()

    return playbook, lessons or "", hints or ""


class CoachRunner:
    def __init__(self, runtime: SubagentRuntime, model: str) -> None:
        self.runtime = runtime
        self.model = model

    def run(self, prompt: str, *, system: str = "") -> RoleExecution:
        return self.runtime.run_task(
            SubagentTask(
                role="coach",
                model=self.model,
                prompt=prompt,
                max_tokens=2000,
                temperature=0.4,
                system=system,
            )
        )
