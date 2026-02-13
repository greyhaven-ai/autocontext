from __future__ import annotations

import re
from dataclasses import dataclass

from mts.agents.subagent_runtime import SubagentRuntime, SubagentTask
from mts.agents.types import RoleExecution

_DECISION_RE = re.compile(r"<!--\s*CURATOR_DECISION:\s*(accept|reject|merge)\s*-->", re.IGNORECASE)
_PLAYBOOK_RE = re.compile(
    r"<!--\s*CURATOR_PLAYBOOK_START\s*-->(.*?)<!--\s*CURATOR_PLAYBOOK_END\s*-->",
    re.DOTALL,
)
_SCORE_RE = re.compile(r"<!--\s*CURATOR_SCORE:\s*(\d+)\s*-->")
_CONSOLIDATED_RE = re.compile(
    r"<!--\s*CONSOLIDATED_LESSONS_START\s*-->(.*?)<!--\s*CONSOLIDATED_LESSONS_END\s*-->",
    re.DOTALL,
)
_REMOVED_RE = re.compile(r"<!--\s*LESSONS_REMOVED:\s*(\d+)\s*-->")


@dataclass(slots=True)
class CuratorPlaybookDecision:
    decision: str  # "accept" | "reject" | "merge"
    playbook: str  # Resulting playbook content
    score: int  # Quality score 1-10
    reasoning: str


@dataclass(slots=True)
class CuratorLessonResult:
    consolidated_lessons: list[str]
    removed_count: int
    reasoning: str


def parse_curator_playbook_decision(content: str) -> CuratorPlaybookDecision:
    """Parse structured curator playbook assessment output."""
    decision_match = _DECISION_RE.search(content)
    decision = decision_match.group(1).lower() if decision_match else "accept"

    playbook_match = _PLAYBOOK_RE.search(content)
    playbook = playbook_match.group(1).strip() if playbook_match else ""

    score_match = _SCORE_RE.search(content)
    score = int(score_match.group(1)) if score_match else 5

    return CuratorPlaybookDecision(
        decision=decision,
        playbook=playbook,
        score=score,
        reasoning=content,
    )


def parse_curator_lesson_result(content: str) -> CuratorLessonResult:
    """Parse structured curator lesson consolidation output."""
    consolidated_match = _CONSOLIDATED_RE.search(content)
    lessons: list[str] = []
    if consolidated_match:
        for line in consolidated_match.group(1).strip().splitlines():
            stripped = line.strip()
            if stripped.startswith("- "):
                lessons.append(stripped)

    removed_match = _REMOVED_RE.search(content)
    removed_count = int(removed_match.group(1)) if removed_match else 0

    return CuratorLessonResult(
        consolidated_lessons=lessons,
        removed_count=removed_count,
        reasoning=content,
    )


class KnowledgeCurator:
    def __init__(self, runtime: SubagentRuntime, model: str):
        self.runtime = runtime
        self.model = model

    def assess_playbook_quality(
        self,
        current_playbook: str,
        proposed_playbook: str,
        score_trajectory: str,
        recent_analysis: str,
    ) -> tuple[CuratorPlaybookDecision, RoleExecution]:
        """Compare current vs proposed playbook. Return accept/reject/merge decision."""
        prompt = (
            "You are a curator assessing playbook quality. Compare the CURRENT and PROPOSED playbooks.\n\n"
            "Score both on: coverage, specificity, actionability (1-10 each).\n"
            "Decide: accept (proposed is better), reject (current is better), or merge (combine best parts).\n\n"
            f"CURRENT PLAYBOOK:\n{current_playbook}\n\n"
            f"PROPOSED PLAYBOOK:\n{proposed_playbook}\n\n"
        )
        if score_trajectory:
            prompt += f"SCORE TRAJECTORY:\n{score_trajectory}\n\n"
        if recent_analysis:
            prompt += f"RECENT ANALYSIS:\n{recent_analysis}\n\n"
        prompt += (
            "Output your decision using these markers:\n"
            "<!-- CURATOR_DECISION: accept|reject|merge -->\n"
            "<!-- CURATOR_SCORE: N -->\n"
            "If merge, provide the merged playbook:\n"
            "<!-- CURATOR_PLAYBOOK_START -->\n(merged playbook)\n<!-- CURATOR_PLAYBOOK_END -->\n"
        )
        exec_result = self.runtime.run_task(
            SubagentTask(
                role="curator",
                model=self.model,
                prompt=prompt,
                max_tokens=3000,
                temperature=0.3,
            )
        )
        decision = parse_curator_playbook_decision(exec_result.content)
        return decision, exec_result

    def consolidate_lessons(
        self,
        existing_lessons: list[str],
        max_lessons: int,
        score_trajectory: str,
    ) -> tuple[CuratorLessonResult, RoleExecution]:
        """Deduplicate semantically, rank by evidence, cap at max_lessons."""
        lessons_text = "\n".join(existing_lessons)
        prompt = (
            "You are a curator consolidating operational lessons. "
            f"Reduce {len(existing_lessons)} lessons to at most {max_lessons}.\n\n"
            "Deduplicate semantically similar lessons. Rank by evidence strength.\n"
            "Remove outdated or contradicted lessons.\n\n"
            f"EXISTING LESSONS:\n{lessons_text}\n\n"
        )
        if score_trajectory:
            prompt += f"SCORE TRAJECTORY:\n{score_trajectory}\n\n"
        prompt += (
            "Output consolidated lessons between markers:\n"
            "<!-- CONSOLIDATED_LESSONS_START -->\n- lesson 1\n- lesson 2\n...\n<!-- CONSOLIDATED_LESSONS_END -->\n"
            "<!-- LESSONS_REMOVED: N -->\n"
        )
        exec_result = self.runtime.run_task(
            SubagentTask(
                role="curator",
                model=self.model,
                prompt=prompt,
                max_tokens=4000,
                temperature=0.3,
            )
        )
        result = parse_curator_lesson_result(exec_result.content)
        if not result.consolidated_lessons:
            result = CuratorLessonResult(
                consolidated_lessons=existing_lessons[:max_lessons],
                removed_count=max(0, len(existing_lessons) - max_lessons),
                reasoning="Consolidation produced no parseable output; hard-truncated to max_lessons.",
            )
        return result, exec_result
