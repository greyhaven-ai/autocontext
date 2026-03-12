"""Tests for autoresearch program.md template rendering (MTS-178)."""
from __future__ import annotations


def test_template_renders_with_all_variables() -> None:
    """render_program() substitutes all template variables without leftover placeholders."""
    from autocontext.training.autoresearch.program import render_program

    result = render_program(
        scenario="grid_ctf",
        strategy_schema='{"aggression": float, "defense": float}',
        playbook_summary="Use high aggression with moderate defense.",
        dead_ends_summary="Pure defense strategies always lose.",
        time_budget="300",
        memory_limit="4096",
    )
    assert isinstance(result, str)
    assert len(result) > 100
    # No unreplaced template variables
    assert "{scenario}" not in result
    assert "{strategy_schema}" not in result
    assert "{playbook_summary}" not in result
    assert "{dead_ends_summary}" not in result
    assert "{time_budget}" not in result
    assert "{memory_limit}" not in result


def test_rendered_program_contains_scenario_and_schema() -> None:
    """Rendered program.md includes scenario name and strategy schema."""
    from autocontext.training.autoresearch.program import render_program

    result = render_program(
        scenario="othello",
        strategy_schema='{"corner_priority": float}',
        playbook_summary="Focus on corners.",
        dead_ends_summary="No known dead ends.",
        time_budget="600",
        memory_limit="8192",
    )
    assert "othello" in result
    assert "corner_priority" in result


def test_dead_ends_and_playbook_injected() -> None:
    """Dead ends and playbook summary are injected into program output."""
    from autocontext.training.autoresearch.program import render_program

    dead_ends = "DEAD_END_MARKER: Random strategies fail consistently."
    playbook = "PLAYBOOK_MARKER: Aggressive corner control is optimal."

    result = render_program(
        scenario="grid_ctf",
        strategy_schema="{}",
        playbook_summary=playbook,
        dead_ends_summary=dead_ends,
        time_budget="120",
        memory_limit="2048",
    )
    assert "DEAD_END_MARKER" in result
    assert "PLAYBOOK_MARKER" in result


def test_program_contains_key_sections() -> None:
    """Program template contains required instruction sections."""
    from autocontext.training.autoresearch.program import render_program

    result = render_program(
        scenario="grid_ctf",
        strategy_schema="{}",
        playbook_summary="summary",
        dead_ends_summary="none",
        time_budget="300",
        memory_limit="4096",
    )
    # Key sections from the spec
    assert "train.py" in result
    assert "READ-ONLY" in result or "read-only" in result.lower()
    assert "avg_score" in result
    assert "valid_rate" in result
    assert "peak_memory_mb" in result
    # Convergence nudge
    assert "10" in result or "discard" in result.lower()
