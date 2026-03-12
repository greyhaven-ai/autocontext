from __future__ import annotations

from autocontext.notebook.types import SessionNotebook


def format_notebook_context(notebook: SessionNotebook) -> str:
    """Render a SessionNotebook as markdown for prompt injection."""
    sections: list[str] = []
    sections.append(f"## Session Notebook: {notebook.session_id}")
    sections.append(f"\n### Scenario\n{notebook.scenario_name}")

    if notebook.current_objective:
        sections.append(f"\n### Current Objective\n{notebook.current_objective}")

    if notebook.current_hypotheses:
        items = "\n".join(f"- {h}" for h in notebook.current_hypotheses)
        sections.append(f"\n### Active Hypotheses\n{items}")

    if notebook.best_score is not None:
        best_parts = [f"Score: {notebook.best_score}"]
        if notebook.best_run_id:
            best_parts.append(f"Run: {notebook.best_run_id}")
        if notebook.best_generation is not None:
            best_parts.append(f"Generation: {notebook.best_generation}")
        sections.append(f"\n### Best Known State\n{' | '.join(best_parts)}")

    if notebook.unresolved_questions:
        items = "\n".join(f"- {q}" for q in notebook.unresolved_questions)
        sections.append(f"\n### Unresolved Questions\n{items}")

    if notebook.operator_observations:
        items = "\n".join(f"- {o}" for o in notebook.operator_observations)
        sections.append(f"\n### Operator Observations\n{items}")

    if notebook.follow_ups:
        items = "\n".join(f"- {f}" for f in notebook.follow_ups)
        sections.append(f"\n### Follow-ups\n{items}")

    return "\n".join(sections)
