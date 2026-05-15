"""Refinement prompt for tree search mode (AC-79)."""

from __future__ import annotations


def build_refinement_prompt(
    scenario_rules: str,
    strategy_interface: str,
    evaluation_criteria: str,
    parent_strategy: str,
    match_feedback: str,
    current_playbook: str = "",
    score_trajectory: str = "",
    operational_lessons: str = "",
    imported_signatures: str = "",
) -> str:
    """Build a prompt for refining an existing strategy (tree search mode).

    Unlike the initial competitor prompt, this asks the LLM to improve an
    existing strategy based on match results rather than generating from scratch.

    ``imported_signatures`` is the rendered output of
    :func:`autocontext.loop.signature_surfacer.render_signatures` (AC-768) — a
    prompt block listing the signatures of local-module symbols actually imported
    by ``parent_strategy``. Empty string omits the block.
    """
    playbook_block = f"Current playbook:\n{current_playbook}\n\n" if current_playbook else ""
    trajectory_block = f"Score trajectory:\n{score_trajectory}\n\n" if score_trajectory else ""
    lessons_block = f"Operational lessons:\n{operational_lessons}\n\n" if operational_lessons else ""
    signatures_block = f"{imported_signatures}\n\n" if imported_signatures else ""
    return (
        f"Scenario rules:\n{scenario_rules}\n\n"
        f"Strategy interface:\n{strategy_interface}\n\n"
        f"Evaluation criteria:\n{evaluation_criteria}\n\n"
        f"{playbook_block}"
        f"{trajectory_block}"
        f"{lessons_block}"
        f"{signatures_block}"
        "--- STRATEGY REFINEMENT ---\n\n"
        "You are refining an existing strategy, not creating one from scratch.\n\n"
        f"Current strategy to refine:\n<strategy>\n{parent_strategy}\n</strategy>\n\n"
        f"Recent match results for this strategy:\n<match_feedback>\n{match_feedback}\n</match_feedback>\n\n"
        "Produce an improved version that addresses the weaknesses shown in the results.\n"
        "Keep what works, fix what doesn't.\n"
        "Describe your reasoning for each change, then provide the refined strategy."
    )
