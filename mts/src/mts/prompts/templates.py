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
    progress_json: str = "",
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
    progress_block = (
        f"Progress snapshot:\n```json\n{progress_json}\n```\n\n"
        if progress_json
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
        f"{progress_block}"
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


def code_strategy_competitor_suffix(strategy_interface: str) -> str:
    """Return competitor prompt suffix for code strategy mode."""
    return (
        "\n\n--- CODE STRATEGY MODE ---\n"
        "Instead of returning parameter values, write a Python function body that "
        "computes actions dynamically based on the game state.\n\n"
        "Available external functions you can call:\n"
        "- `get_observation(state)` \u2192 dict with keys: narrative, state, constraints\n"
        "- `initial_state(seed)` \u2192 dict with the initial game state\n\n"
        "Your code receives two variables:\n"
        "- `state`: the current game state dict\n"
        "- `observation`: the observation dict from get_observation(state)\n\n"
        f"Strategy interface for reference:\n{strategy_interface}\n\n"
        "Your code MUST assign to `result` \u2014 a dict matching the strategy interface.\n\n"
        "Wrap your code in a ```python code fence.\n"
        "Example:\n"
        "```python\n"
        "obs = observation\n"
        "if obs['state'].get('resource_density', 0) > 0.5:\n"
        "    result = {'aggression': 0.8, 'defense': 0.4}\n"
        "else:\n"
        "    result = {'aggression': 0.5, 'defense': 0.7}\n"
        "```"
    )
