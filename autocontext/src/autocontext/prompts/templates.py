from __future__ import annotations

from dataclasses import dataclass

from autocontext.prompts.context_budget import ContextBudget
from autocontext.scenarios.base import Observation


@dataclass(frozen=True)
class PromptBundle:
    competitor: str
    analyst: str
    coach: str
    architect: str


# Analyst/architect constraint bullets shared with rlm/prompts.py — keep in sync
_COMPETITOR_CONSTRAINT_SUFFIX = (
    "\n\nConstraints:\n"
    "- Do NOT repeat any strategy from the registry that resulted in rollback\n"
    "- Do NOT set parameters outside the valid ranges defined in the strategy interface\n"
    "- Do NOT omit reasoning for each parameter choice\n"
    "- Do NOT ignore patterns identified in the score trajectory\n"
    "- Do NOT propose a strategy without considering the evaluation criteria"
)

_ANALYST_CONSTRAINT_SUFFIX = (
    "\n\nConstraints:\n"
    "- Do NOT report findings without supporting evidence from match data\n"
    "- Do NOT omit root cause analysis for score regressions\n"
    "- Do NOT repeat recommendations already addressed in the current playbook\n"
    "- Do NOT provide vague recommendations — each must specify concrete parameter changes"
)

_COACH_CONSTRAINT_SUFFIX = (
    "\n\nConstraints:\n"
    "- Do NOT remove working strategies from the playbook without justification\n"
    "- Do NOT omit the required structural markers (PLAYBOOK, LESSONS, COMPETITOR_HINTS START/END)\n"
    "- Do NOT contradict lessons that have been validated across multiple generations\n"
    "- Do NOT provide hints that repeat previously rolled-back approaches"
)

_ARCHITECT_CONSTRAINT_SUFFIX = (
    "\n\nConstraints:\n"
    "- Do NOT propose tools that duplicate existing tool functionality\n"
    "- Do NOT generate code with syntax errors or undefined dependencies\n"
    "- Do NOT remove or break existing tools without archiving them first\n"
    "- Do NOT propose changes without an impact hypothesis"
)


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
    coach_hint_feedback: str = "",
    recent_analysis: str = "",
    analyst_feedback: str = "",
    score_trajectory: str = "",
    strategy_registry: str = "",
    progress_json: str = "",
    experiment_log: str = "",
    dead_ends: str = "",
    research_protocol: str = "",
    session_reports: str = "",
    architect_tool_usage_report: str = "",
    constraint_mode: bool = False,
    context_budget_tokens: int = 0,
    notebook_contexts: dict[str, str] | None = None,
) -> PromptBundle:
    _nb = dict(notebook_contexts or {})
    if context_budget_tokens > 0:
        budget = ContextBudget(max_tokens=context_budget_tokens)
        budgeted = budget.apply({
            "playbook": current_playbook,
            "trajectory": score_trajectory,
            "lessons": operational_lessons,
            "tools": available_tools,
            "analysis": recent_analysis,
            "analyst_feedback": analyst_feedback,
            "hints": coach_competitor_hints,
            "coach_hint_feedback": coach_hint_feedback,
            "experiment_log": experiment_log,
            "dead_ends": dead_ends,
            "research_protocol": research_protocol,
            "session_reports": session_reports,
            "tool_usage_report": architect_tool_usage_report,
            "notebook_competitor": _nb.get("competitor", ""),
            "notebook_analyst": _nb.get("analyst", ""),
            "notebook_coach": _nb.get("coach", ""),
            "notebook_architect": _nb.get("architect", ""),
        })
        current_playbook = budgeted["playbook"]
        score_trajectory = budgeted["trajectory"]
        operational_lessons = budgeted["lessons"]
        available_tools = budgeted["tools"]
        recent_analysis = budgeted["analysis"]
        analyst_feedback = budgeted["analyst_feedback"]
        coach_competitor_hints = budgeted["hints"]
        coach_hint_feedback = budgeted["coach_hint_feedback"]
        experiment_log = budgeted["experiment_log"]
        dead_ends = budgeted["dead_ends"]
        research_protocol = budgeted["research_protocol"]
        session_reports = budgeted["session_reports"]
        architect_tool_usage_report = budgeted["tool_usage_report"]
        _nb = {
            "competitor": budgeted["notebook_competitor"],
            "analyst": budgeted["notebook_analyst"],
            "coach": budgeted["notebook_coach"],
            "architect": budgeted["notebook_architect"],
        }

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
    analyst_feedback_block = (
        f"{analyst_feedback.strip()}\n\n"
        if analyst_feedback
        else ""
    )
    coach_hint_feedback_block = (
        f"{coach_hint_feedback.strip()}\n\n"
        if coach_hint_feedback
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
    experiment_log_block = (
        f"Experiment log:\n{experiment_log}\n\n"
        if experiment_log
        else ""
    )
    dead_ends_block = (
        f"Known dead ends (DO NOT repeat these approaches):\n{dead_ends}\n\n"
        if dead_ends
        else ""
    )
    protocol_block = (
        f"Research protocol (current focus and constraints):\n{research_protocol}\n\n"
        if research_protocol
        else ""
    )
    session_reports_block = (
        f"Prior session reports:\n{session_reports}\n\n"
        if session_reports
        else ""
    )
    tool_usage_block = (
        f"{architect_tool_usage_report.strip()}\n\n"
        if architect_tool_usage_report
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
        f"{dead_ends_block}"
        f"{progress_block}"
        f"{experiment_log_block}"
        f"{protocol_block}"
        f"{session_reports_block}"
    )
    hints_block = (
        f"Coach hints for competitor:\n{coach_competitor_hints}\n\n"
        if coach_competitor_hints
        else ""
    )
    competitor_constraint = _COMPETITOR_CONSTRAINT_SUFFIX if constraint_mode else ""
    analyst_constraint = _ANALYST_CONSTRAINT_SUFFIX if constraint_mode else ""
    coach_constraint = _COACH_CONSTRAINT_SUFFIX if constraint_mode else ""
    architect_constraint = _ARCHITECT_CONSTRAINT_SUFFIX if constraint_mode else ""
    competitor_nb = (
        f"Session notebook context:\n{_nb['competitor']}\n\n" if _nb.get("competitor") else ""
    )
    analyst_nb = (
        f"Session notebook context:\n{_nb['analyst']}\n\n" if _nb.get("analyst") else ""
    )
    coach_nb = (
        f"Session notebook context:\n{_nb['coach']}\n\n" if _nb.get("coach") else ""
    )
    architect_nb = (
        f"Session notebook context:\n{_nb['architect']}\n\n" if _nb.get("architect") else ""
    )
    return PromptBundle(
        competitor=base_context
        + hints_block
        + competitor_nb
        + competitor_constraint
        + "Describe your strategy reasoning and recommend specific parameter values.",
        analyst=base_context
        + analyst_feedback_block
        + analyst_nb
        + analyst_constraint
        + (
            "Analyze strengths/failures and return markdown with sections: "
            "Findings, Root Causes, Actionable Recommendations."
        ),
        coach=base_context
        + coach_hint_feedback_block
        + coach_nb
        + coach_constraint
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
        + tool_usage_block
        + architect_nb
        + architect_constraint
        + (
            "Propose infrastructure/tooling improvements in markdown with sections: "
            "Observed Bottlenecks, Tool Proposals, Impact Hypothesis. "
            "Then append a JSON code block with shape "
            '{"tools":[{"name":"<snake_case>","description":"<text>","code":"<python code>"}]}. '
            "If no new tools, return tools as empty array."
            " You may CREATE new tools or UPDATE existing tools by using the same name.\n\n"
            "Additionally, you may propose harness validators — executable Python checks "
            "that run against each strategy BEFORE tournament matches. Each validator must "
            "define `validate_strategy(strategy: dict, scenario) -> tuple[bool, list[str]]`. "
            "Wrap harness specs between markers:\n\n"
            "<!-- HARNESS_START -->\n"
            '{"harness":[{"name":"<snake_case>","description":"<text>",'
            '"code":"def validate_strategy(strategy, scenario):\\n    ..."}]}\n'
            "<!-- HARNESS_END -->\n\n"
            "If no harness validators, omit the HARNESS markers entirely."
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
