from __future__ import annotations

from dataclasses import dataclass

from mts.scenarios.base import Observation


@dataclass(frozen=True)
class PromptBundle:
    competitor: str
    analyst: str
    coach: str
    architect: str


def build_prompt_bundle(
    scenario_rules: str,
    strategy_interface: str,
    evaluation_criteria: str,
    previous_summary: str,
    observation: Observation,
    current_playbook: str,
    available_tools: str,
    operational_lessons: str = "",
    replay_narrative: str = "",
    coach_competitor_hints: str = "",
    recent_analysis: str = "",
    score_trajectory: str = "",
    strategy_registry: str = "",
) -> PromptBundle:
    lessons_block = (
        f"Operational lessons (from prior generations):\n{operational_lessons}\n\n"
        if operational_lessons
        else ""
    )
    analysis_block = (
        f"Most recent generation analysis:\n{recent_analysis}\n\n"
        if recent_analysis
        else ""
    )
    replay_block = (
        f"Previous match replay:\n{replay_narrative}\n\n"
        if replay_narrative
        else ""
    )
    trajectory_block = (
        f"Score trajectory:\n{score_trajectory}\n\n"
        if score_trajectory
        else ""
    )
    registry_block = (
        f"Strategy-score registry:\n{strategy_registry}\n\n"
        if strategy_registry
        else ""
    )
    base_context = (
        f"Scenario rules:\n{scenario_rules}\n\n"
        f"Strategy interface:\n{strategy_interface}\n\n"
        f"Evaluation criteria:\n{evaluation_criteria}\n\n"
        f"Observation narrative:\n{observation.narrative}\n\n"
        f"Observation state:\n{observation.state}\n\n"
        f"Constraints:\n{observation.constraints}\n\n"
        f"Current playbook:\n{current_playbook}\n\n"
        f"{lessons_block}"
        f"{analysis_block}"
        f"{replay_block}"
        f"Available tools:\n{available_tools}\n\n"
        f"Previous generation summary:\n{previous_summary}\n"
        f"{trajectory_block}"
        f"{registry_block}"
    )
    hints_block = (
        f"Coach hints for competitor:\n{coach_competitor_hints}\n\n"
        if coach_competitor_hints
        else ""
    )
    return PromptBundle(
        competitor=base_context
        + hints_block
        + (
            "Describe your strategy reasoning and recommend specific parameter values."
        ),
        analyst=base_context
        + (
            "Analyze strengths/failures and return markdown with sections: "
            "Findings, Root Causes, Actionable Recommendations."
        ),
        coach=base_context
        + (
            "You are the playbook coach. Produce THREE structured sections:\n\n"
            "1. A COMPLETE replacement playbook between markers. Consolidate all prior guidance, "
            "deduplicate, and remove stale advice. This replaces the current playbook entirely.\n\n"
            "<!-- PLAYBOOK_START -->\n"
            "(Your consolidated playbook here: Strategy Updates, Prompt Optimizations, "
            "Next Generation Checklist)\n"
            "<!-- PLAYBOOK_END -->\n\n"
            "2. Operational lessons learned between markers. Each lesson should be a concrete, "
            "prescriptive rule derived from what worked or failed.\n\n"
            "<!-- LESSONS_START -->\n"
            "(e.g. '- When aggression > 0.8 with defense < 0.4, scores drop.')\n"
            "<!-- LESSONS_END -->\n\n"
            "3. Concrete competitor hints between markers. Specific parameter ranges or "
            "strategies the competitor should try next.\n\n"
            "<!-- COMPETITOR_HINTS_START -->\n"
            "(Specific parameter ranges or strategies the competitor should try next)\n"
            "<!-- COMPETITOR_HINTS_END -->"
        ),
        architect=base_context
        + (
            "Propose infrastructure/tooling improvements in markdown with sections: "
            "Observed Bottlenecks, Tool Proposals, Impact Hypothesis. "
            "Then append a JSON code block with shape "
            '{"tools":[{"name":"<snake_case>","description":"<text>","code":"<python code>"}]}. '
            "If no new tools, return tools as empty array."
            " You may CREATE new tools or UPDATE existing tools by using the same name."
        ),
    )
