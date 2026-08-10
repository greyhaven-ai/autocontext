from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from importlib import import_module
from typing import Any

from pydantic import ValidationError

from autocontext.agents.feedback_loops import AnalystRating
from autocontext.agents.subagent_runtime import SubagentRuntime, SubagentTask
from autocontext.agents.types import RoleExecution
from autocontext.harness.core.output_parser import extract_json

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


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class CuratorPlaybookDecision:
    decision: str  # "accept" | "reject" | "merge"
    playbook: str  # Resulting playbook content
    score: int  # Quality score 1-10
    reasoning: str
    parse_success: bool = True


@dataclass(slots=True)
class CuratorLessonResult:
    consolidated_lessons: list[str]
    removed_count: int
    reasoning: str


def parse_curator_playbook_decision(content: str) -> CuratorPlaybookDecision:
    """Parse structured curator playbook assessment output."""
    decision_match = _DECISION_RE.search(content)
    # AC-904: no decision marker means the response is unparseable (often
    # truncated); the quality gate must fail CLOSED, never accept-by-default.
    if decision_match is None:
        logger.warning("curator output missing CURATOR_DECISION marker (possibly truncated); failing closed to reject")
    decision = decision_match.group(1).lower() if decision_match else "reject"

    playbook_match = _PLAYBOOK_RE.search(content)
    playbook = playbook_match.group(1).strip() if playbook_match else ""

    score_match = _SCORE_RE.search(content)
    score = int(score_match.group(1)) if score_match else 5

    return CuratorPlaybookDecision(
        decision=decision,
        playbook=playbook,
        score=score,
        reasoning=content,
        parse_success=decision_match is not None,
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


_CURATOR_ASSESSMENT_CONSTRAINT = (
    "Constraints:\n"
    "- Do NOT accept a playbook that removes validated high-scoring strategies\n"
    "- Do NOT reject a playbook without comparing specific coverage gaps\n"
    "- Do NOT merge without preserving the highest-scoring strategy components\n\n"
)

_CURATOR_CONSOLIDATION_CONSTRAINT = (
    "Constraints:\n"
    "- Do NOT remove lessons that are supported by score improvements\n"
    "- Do NOT merge semantically distinct lessons into a single vague bullet\n"
    "- Do NOT keep lessons that directly contradict each other without resolution\n\n"
)


def _structural_hint_prompt(hint_style: str) -> str:
    return str(import_module("autocontext.knowledge.soft_hints").structural_hint_prompt(hint_style))


_CURATOR_ANALYST_RATING_CONSTRAINT = (
    "Constraints:\n"
    "- Do NOT give high scores without citing concrete evidence from the analyst report\n"
    "- Do NOT reward vague recommendations or unsupported claims\n"
    "- Do NOT collapse actionability, specificity, and correctness into the same score without justification\n\n"
)


class KnowledgeCurator:
    def __init__(
        self,
        runtime: SubagentRuntime,
        model: str,
        max_tokens: int = 3000,
        rating_max_tokens: int = 1200,
        consolidation_max_tokens: int = 4000,
    ) -> None:
        self.runtime = runtime
        self.model = model
        self.max_tokens = max_tokens
        self.rating_max_tokens = rating_max_tokens
        self.consolidation_max_tokens = consolidation_max_tokens

    def assess_playbook_quality(
        self,
        current_playbook: str,
        proposed_playbook: str,
        score_trajectory: str,
        recent_analysis: str,
        constraint_mode: bool = False,
        harness_quality_section: str = "",
        skeptic_review_section: str = "",
        hint_style: str = "default",
    ) -> tuple[CuratorPlaybookDecision, RoleExecution]:
        """Compare current vs proposed playbook. Return accept/reject/merge decision."""
        constraint_preamble = _CURATOR_ASSESSMENT_CONSTRAINT if constraint_mode else ""
        hint_policy = _structural_hint_prompt(hint_style)
        hint_policy_block = f"{hint_policy}\n\n" if hint_policy else ""
        prompt = (
            constraint_preamble
            + hint_policy_block
            + "You are a curator assessing playbook quality. Compare the CURRENT and PROPOSED playbooks.\n\n"
            "Score both on: coverage, specificity, actionability (1-10 each).\n"
            "Decide: accept (proposed is better), reject (current is better), or merge (combine best parts).\n\n"
            f"CURRENT PLAYBOOK:\n{current_playbook}\n\n"
            f"PROPOSED PLAYBOOK:\n{proposed_playbook}\n\n"
        )
        if score_trajectory:
            prompt += f"SCORE TRAJECTORY:\n{score_trajectory}\n\n"
        if recent_analysis:
            prompt += f"RECENT ANALYSIS:\n{recent_analysis}\n\n"
        if skeptic_review_section:
            prompt += f"{skeptic_review_section}\n"
        if harness_quality_section:
            prompt += f"{harness_quality_section}\n"
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
                max_tokens=self.max_tokens,
                temperature=0.3,
            )
        )
        decision = parse_curator_playbook_decision(exec_result.content)
        return decision, exec_result

    def rate_analyst_output(
        self,
        analyst_markdown: str,
        *,
        generation: int,
        score_summary: str = "",
        constraint_mode: bool = False,
    ) -> tuple[AnalystRating, RoleExecution]:
        """Rate analyst quality so the next analyst prompt gets concrete curator feedback."""
        constraint_preamble = _CURATOR_ANALYST_RATING_CONSTRAINT if constraint_mode else ""
        prompt = (
            constraint_preamble + "You are a curator rating the quality of the analyst's report.\n\n"
            "Score the report from 1-5 on:\n"
            "- actionability: how directly the recommendations can be used\n"
            "- specificity: how concrete and evidence-backed the findings are\n"
            "- correctness: how well the analysis matches the available evidence\n\n"
            "Return a JSON object with keys: actionability, specificity, correctness, rationale.\n\n"
        )
        if score_summary:
            prompt += f"SCORE SUMMARY:\n{score_summary}\n\n"
        prompt += f"ANALYST REPORT:\n{analyst_markdown}\n"
        exec_result = self.runtime.run_task(
            SubagentTask(
                role="curator",
                model=self.model,
                prompt=prompt,
                max_tokens=self.rating_max_tokens,
                temperature=0.2,
            )
        )
        decoded = extract_json(exec_result.content, on_failure="none")
        if decoded is None:
            # No exc_info here, unlike the ValidationError branch below and
            # unlike this line before the migration. It used to sit inside
            # `except json.JSONDecodeError`, where there was a live exception
            # to attach; extract_json(on_failure="none") swallows that
            # exception internally and reports failure as a None return, so
            # there is no active exception at this point and exc_info=True
            # would append a bare "NoneType: None" to every such warning.
            logger.warning("curator analyst-rating parse failed; using default scores")
            payload: dict[str, Any] = {}
        else:
            payload = decoded
        try:
            rating = AnalystRating.from_dict({"generation": generation, **payload})
        except ValidationError:
            # This site's contract is fail-soft, and it has to be: the rating is
            # FEEDBACK routed into the next generation's analyst prompt, not the
            # artifact the loop scores. Its caller
            # (loop.stage_helpers.persistence_helpers._maybe_rate_analyst_output)
            # runs mid-generation with no handler, so raising here would abort a
            # whole generation over a cosmetic score. Contrast translator.translate,
            # which deliberately uses on_failure="raise" because the strategy it
            # parses IS the scored artifact.
            #
            # A payload that PARSES as JSON but not as a rating (e.g.
            # {"actionability": "not-a-number"}) has always been able to reach
            # from_dict and raise; what widened it was migrating this site onto
            # extract_json, which recovers unfenced prose JSON the old
            # json.loads(strip_json_fences(...)) could not, so inputs that used
            # to fail closed at the JSONDecodeError now arrive here with a
            # non-default payload. Degrade to defaults instead, which is what
            # every other malformed-output path at this site already does.
            logger.warning("curator analyst-rating payload failed schema validation; using default scores", exc_info=True)
            rating = AnalystRating.from_dict({"generation": generation})
        return rating, exec_result

    def consolidate_lessons(
        self,
        existing_lessons: list[str],
        max_lessons: int,
        score_trajectory: str,
        constraint_mode: bool = False,
    ) -> tuple[CuratorLessonResult, RoleExecution]:
        """Deduplicate semantically, rank by evidence, cap at max_lessons."""
        lessons_text = "\n".join(existing_lessons)
        constraint_preamble = _CURATOR_CONSOLIDATION_CONSTRAINT if constraint_mode else ""
        prompt = (
            constraint_preamble + "You are a curator consolidating operational lessons. "
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
                max_tokens=self.consolidation_max_tokens,
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
