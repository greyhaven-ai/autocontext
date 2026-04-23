from __future__ import annotations


def test_compact_prompt_components_keeps_recent_experiment_sections() -> None:
    from autocontext.knowledge.compaction import compact_prompt_components

    components = {
        "experiment_log": (
            "## RLM Experiment Log\n\n"
            "### Generation 1\n"
            + ("noise line\n" * 120)
            + "\n### Generation 7\n"
            + "- Root cause: overfitting to stale hints\n"
            + "- Keep broader opening exploration\n"
        ),
    }

    compacted = compact_prompt_components(components)

    assert "Generation 7" in compacted["experiment_log"]
    assert "overfitting to stale hints" in compacted["experiment_log"]
    assert len(compacted["experiment_log"]) < len(components["experiment_log"])


def test_compact_prompt_components_extracts_key_session_report_lines() -> None:
    from autocontext.knowledge.compaction import compact_prompt_components

    components = {
        "session_reports": (
            "# Session Report: run_old\n"
            "Long narrative that meanders without much signal.\n"
            + ("filler paragraph\n" * 80)
            + "\n## Findings\n"
            "- Preserve the rollback guard after failed harness mutations.\n"
            "- Prefer notebook freshness filtering before prompt injection.\n"
        ),
    }

    compacted = compact_prompt_components(components)

    assert "rollback guard" in compacted["session_reports"]
    assert "freshness filtering" in compacted["session_reports"]
    assert len(compacted["session_reports"]) < len(components["session_reports"])


def test_compact_prompt_components_keeps_recent_lessons() -> None:
    from autocontext.knowledge.compaction import compact_prompt_components

    components = {
        "lessons": "## Lessons\n" + "\n".join(
            [f"- old lesson {i} " + ("x" * 120) for i in range(1, 120)]
            + ["- newest lesson keep me"]
        ),
    }

    compacted = compact_prompt_components(components)

    assert "newest lesson keep me" in compacted["lessons"]
    assert "- old lesson 117 " in compacted["lessons"]
    assert "- old lesson 1 " not in compacted["lessons"]


def test_compact_prompt_components_preserves_trailing_dimension_section() -> None:
    from autocontext.knowledge.compaction import compact_prompt_components

    table_rows = [
        f"| {i} | 0.5000 | 0.6000 | 1500.0 | advance | +0.0100 |"
        for i in range(1, 120)
    ]
    components = {
        "trajectory": "\n".join(
            [
                "## Score Trajectory",
                "",
                "| Gen | Mean | Best | Elo | Gate | Delta |",
                "|-----|------|------|--------|------|-------|",
                *table_rows,
                "",
                "## Dimension Trajectory (Best Match)",
                "",
                "```text",
                ("aggression: up then down " * 20).strip(),
                ("defense: stable high signal " * 20).strip(),
                "```",
            ]
        ),
    }

    compacted = compact_prompt_components(components)

    assert "## Dimension Trajectory (Best Match)" in compacted["trajectory"]
    assert "aggression: up then down" in compacted["trajectory"]
    assert compacted["trajectory"].index("## Dimension Trajectory (Best Match)") > compacted["trajectory"].index(
        "| Gen | Mean | Best | Elo | Gate | Delta |"
    )
