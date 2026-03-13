"""Tests for knowledge coherence verification (MTS-23)."""
from __future__ import annotations

from pathlib import Path

from autocontext.knowledge.coherence import check_coherence


def test_coherent_state(tmp_path: Path) -> None:
    """Clean knowledge state passes all checks."""
    knowledge = tmp_path / "grid_ctf"
    knowledge.mkdir()
    (knowledge / "playbook.md").write_text("# Playbook\nUse balanced approach.\n")
    tools_dir = knowledge / "tools"
    tools_dir.mkdir()
    (tools_dir / "scorer.py").write_text("def score(): pass\n")

    report = check_coherence(
        scenario_name="grid_ctf",
        knowledge_root=tmp_path,
    )
    assert len(report.issues) == 0


def test_empty_playbook(tmp_path: Path) -> None:
    """Empty playbook is flagged."""
    knowledge = tmp_path / "grid_ctf"
    knowledge.mkdir()
    (knowledge / "playbook.md").write_text("")

    report = check_coherence(
        scenario_name="grid_ctf",
        knowledge_root=tmp_path,
    )
    assert any("playbook" in i.lower() for i in report.issues)


def test_missing_knowledge_dir(tmp_path: Path) -> None:
    """Missing knowledge dir is not an error (first run)."""
    report = check_coherence(
        scenario_name="grid_ctf",
        knowledge_root=tmp_path,
    )
    assert len(report.issues) == 0


def test_empty_tools_dir(tmp_path: Path) -> None:
    """Empty tools dir with playbook referencing tools is flagged."""
    knowledge = tmp_path / "grid_ctf"
    knowledge.mkdir()
    (knowledge / "playbook.md").write_text(
        "# Playbook\nUse the custom scorer tool for evaluation.\n"
    )
    tools_dir = knowledge / "tools"
    tools_dir.mkdir()
    # No actual tool files

    report = check_coherence(
        scenario_name="grid_ctf",
        knowledge_root=tmp_path,
    )
    assert any("tool" in i.lower() for i in report.issues)


def test_contradictory_lessons_flagged(tmp_path: Path) -> None:
    """Directly contradictory lessons are flagged via simple keyword check."""
    knowledge = tmp_path / "grid_ctf"
    knowledge.mkdir()
    (knowledge / "playbook.md").write_text("# Playbook\nContent.\n")
    skills = tmp_path / "skills" / "grid-ctf-ops"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text(
        "## Operational Lessons\n"
        "- Always increase aggression above 0.8\n"
        "- Never increase aggression above 0.7\n"
    )

    report = check_coherence(
        scenario_name="grid_ctf",
        knowledge_root=tmp_path,
        skills_root=tmp_path / "skills",
    )
    assert any("contradict" in i.lower() for i in report.issues)


def test_structured_lessons_take_precedence_over_legacy_skill_text(tmp_path: Path) -> None:
    """Applicable structured lessons should drive coherence checks when present."""
    from autocontext.knowledge.lessons import ApplicabilityMeta, Lesson, LessonStore

    knowledge = tmp_path / "grid_ctf"
    knowledge.mkdir()
    (knowledge / "playbook.md").write_text("# Playbook\nContent.\n")
    skills_root = tmp_path / "skills"
    skill_dir = skills_root / "grid-ctf-ops"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "## Operational Lessons\n"
        "- Always increase aggression above 0.8\n"
        "- Never increase aggression above 0.7\n"
    )

    store = LessonStore(knowledge_root=tmp_path, skills_root=skills_root)
    store.write_lessons(
        "grid_ctf",
        [
            Lesson(
                id="fresh",
                text="- Always increase aggression above 0.8",
                meta=ApplicabilityMeta(
                    created_at="2026-03-13T10:00:00Z",
                    generation=5,
                    best_score=0.8,
                    last_validated_gen=5,
                ),
            ),
            Lesson(
                id="invalidated",
                text="- Never increase aggression above 0.7",
                meta=ApplicabilityMeta(
                    created_at="2026-03-13T10:00:00Z",
                    generation=5,
                    best_score=0.8,
                    last_validated_gen=-1,
                ),
            ),
        ],
    )

    report = check_coherence(
        scenario_name="grid_ctf",
        knowledge_root=tmp_path,
        skills_root=skills_root,
    )
    assert not any("contradict" in i.lower() for i in report.issues)
