from __future__ import annotations

import logging

from autocontext.agents.role_schemas import COACH_SCHEMA
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
            #
            # AC-932: warn, because this is not a neutral fallback. Whatever the
            # model wrote -- preamble, reasoning, an apology -- becomes the
            # playbook and steers the next generation. Measured on llama3.1:8b,
            # 2 of 10 coach responses carried no markers at all, so on an
            # open-weight model this fires often enough to matter. TypeScript
            # fails the opposite way and drops the update entirely; both were
            # silent until now.
            logger.warning(
                "coach output has no playbook markers; treating the entire response as the playbook "
                "(%d chars). Enable constrained output or check the model's format compliance.",
                len(content.strip()),
            )
            playbook = content.strip()

    return playbook, lessons or "", hints or ""


class CoachRunner:
    def __init__(self, runtime: SubagentRuntime, model: str, max_tokens: int = 2000) -> None:
        self.runtime = runtime
        self.model = model
        self.max_tokens = max_tokens

    def run(self, prompt: str, *, system: str = "") -> RoleExecution:
        return self.runtime.run_task(
            SubagentTask(
                role="coach",
                output_schema=COACH_SCHEMA,
                model=self.model,
                prompt=prompt,
                max_tokens=self.max_tokens,
                temperature=0.4,
                system=system,
            )
        )
