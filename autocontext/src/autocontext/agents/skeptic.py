from __future__ import annotations

import re
from dataclasses import dataclass, field

from autocontext.agents.subagent_runtime import SubagentRuntime, SubagentTask
from autocontext.agents.types import RoleExecution

_RISK_RE = re.compile(r"<!--\s*SKEPTIC_RISK:\s*(high|medium|low)\s*-->", re.IGNORECASE)
_CONCERNS_RE = re.compile(
    r"<!--\s*SKEPTIC_CONCERNS_START\s*-->(.*?)<!--\s*SKEPTIC_CONCERNS_END\s*-->",
    re.DOTALL,
)
_RECOMMENDATION_RE = re.compile(r"<!--\s*SKEPTIC_RECOMMENDATION:\s*(proceed|caution|block)\s*-->", re.IGNORECASE)
_CONFIDENCE_RE = re.compile(r"<!--\s*SKEPTIC_CONFIDENCE:\s*(\d+)\s*-->")


@dataclass(slots=True)
class SkepticReview:
    risk_level: str  # "high" | "medium" | "low"
    concerns: list[str] = field(default_factory=list)
    recommendation: str = "proceed"  # "proceed" | "caution" | "block"
    confidence: int = 5  # 1-10
    reasoning: str = ""
    parse_success: bool = True


def parse_skeptic_review(content: str) -> SkepticReview:
    """Parse structured skeptic output using HTML comment markers."""
    risk_match = _RISK_RE.search(content)
    risk_level = risk_match.group(1).lower() if risk_match else "low"

    concerns_match = _CONCERNS_RE.search(content)
    concerns: list[str] = []
    if concerns_match:
        for line in concerns_match.group(1).strip().splitlines():
            stripped = line.strip()
            if stripped.startswith("- "):
                concerns.append(stripped[2:])

    rec_match = _RECOMMENDATION_RE.search(content)
    recommendation = rec_match.group(1).lower() if rec_match else "proceed"

    conf_match = _CONFIDENCE_RE.search(content)
    confidence = int(conf_match.group(1)) if conf_match else 5
    confidence = max(1, min(10, confidence))

    parse_success = risk_match is not None or rec_match is not None

    return SkepticReview(
        risk_level=risk_level,
        concerns=concerns,
        recommendation=recommendation,
        confidence=confidence,
        reasoning=content,
        parse_success=parse_success,
    )


_SKEPTIC_CONSTRAINT = (
    "Constraints:\n"
    "- Do NOT recommend blocking without citing specific evidence of overfit, regression, or fragility\n"
    "- Do NOT ignore score trajectory context when assessing risk\n"
    "- Do NOT flag concerns that have already been addressed in prior generations\n\n"
)


class SkepticAgent:
    def __init__(self, runtime: SubagentRuntime, model: str) -> None:
        self.runtime = runtime
        self.model = model

    def review(
        self,
        proposed_playbook: str,
        strategy_summary: str,
        score_trajectory: str,
        recent_analysis: str,
        match_results_summary: str = "",
        constraint_mode: bool = False,
    ) -> tuple[SkepticReview, RoleExecution]:
        """Adversarial review of an advance candidate."""
        constraint_preamble = _SKEPTIC_CONSTRAINT if constraint_mode else ""
        prompt = (
            constraint_preamble
            + "You are a skeptic / red-team reviewer. Your job is to argue AGAINST advancing this candidate.\n"
            "Look for: overfit to specific opponents, rubric gaming, stale patterns carried forward, "
            "fragile gains that won't hold, contradictions with prior lessons, and suspicious score jumps.\n\n"
            f"PROPOSED PLAYBOOK:\n{proposed_playbook}\n\n"
            f"STRATEGY SUMMARY:\n{strategy_summary}\n\n"
        )
        if score_trajectory:
            prompt += f"SCORE TRAJECTORY:\n{score_trajectory}\n\n"
        if recent_analysis:
            prompt += f"RECENT ANALYSIS:\n{recent_analysis}\n\n"
        if match_results_summary:
            prompt += f"MATCH RESULTS SUMMARY:\n{match_results_summary}\n\n"
        prompt += (
            "Output your review using these markers:\n"
            "<!-- SKEPTIC_RISK: high|medium|low -->\n"
            "<!-- SKEPTIC_CONCERNS_START -->\n- concern 1\n- concern 2\n<!-- SKEPTIC_CONCERNS_END -->\n"
            "<!-- SKEPTIC_RECOMMENDATION: proceed|caution|block -->\n"
            "<!-- SKEPTIC_CONFIDENCE: N -->\n"
        )
        exec_result = self.runtime.run_task(
            SubagentTask(
                role="skeptic",
                model=self.model,
                prompt=prompt,
                max_tokens=2000,
                temperature=0.4,
            )
        )
        review = parse_skeptic_review(exec_result.content)
        return review, exec_result
