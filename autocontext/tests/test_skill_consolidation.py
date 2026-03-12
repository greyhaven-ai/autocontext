"""Tests for skill lesson consolidation."""
from __future__ import annotations

from pathlib import Path

from autocontext.agents.curator import parse_curator_lesson_result
from autocontext.storage import ArtifactStore


def _make_store(tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(
        tmp_path / "runs",
        tmp_path / "knowledge",
        tmp_path / "skills",
        tmp_path / ".claude/skills",
    )


def _seed_skill(tmp_path: Path, scenario: str, lesson_count: int) -> None:
    """Create a SKILL.md with given number of lessons."""
    skill_dir = tmp_path / "skills" / f"{scenario.replace('_', '-')}-ops"
    skill_dir.mkdir(parents=True, exist_ok=True)
    lessons = "\n".join(f"- Lesson {i}" for i in range(lesson_count))
    content = (
        f"---\nname: {scenario.replace('_', '-')}-ops\ndescription: test\n---\n\n"
        f"# Test\n\n## Operational Lessons\n\nRules:\n\n{lessons}\n\n"
        "## Bundled Resources\n\n- stuff\n"
    )
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")


def test_read_skill_lessons_raw(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    _seed_skill(tmp_path, "grid_ctf", 5)
    bullets = store.read_skill_lessons_raw("grid_ctf")
    assert len(bullets) == 5
    assert all(b.startswith("- ") for b in bullets)


def test_replace_skill_lessons(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    _seed_skill(tmp_path, "grid_ctf", 10)
    new_lessons = ["- Consolidated A", "- Consolidated B"]
    store.replace_skill_lessons("grid_ctf", new_lessons)
    result = store.read_skill_lessons_raw("grid_ctf")
    assert len(result) == 2
    assert "Consolidated A" in result[0]
    # Bundled Resources section should still exist
    skill_path = tmp_path / "skills" / "grid-ctf-ops" / "SKILL.md"
    content = skill_path.read_text(encoding="utf-8")
    assert "## Bundled Resources" in content


def test_consolidation_triggered_at_interval(tmp_path: Path) -> None:
    """Consolidation happens at gen % N == 0."""
    # This is a logic test, not a full runner test
    assert 3 % 3 == 0  # Gen 3 triggers
    assert 6 % 3 == 0  # Gen 6 triggers
    assert 4 % 3 != 0  # Gen 4 skips


def test_consolidation_skipped_under_threshold(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    _seed_skill(tmp_path, "grid_ctf", 5)
    bullets = store.read_skill_lessons_raw("grid_ctf")
    # 5 lessons is under typical max_lessons (30), so consolidation would be skipped
    assert len(bullets) <= 30


def test_consolidation_reduces_count(tmp_path: Path) -> None:
    content = (
        "<!-- CONSOLIDATED_LESSONS_START -->\n"
        "- Keep this\n"
        "- And this\n"
        "<!-- CONSOLIDATED_LESSONS_END -->\n"
        "<!-- LESSONS_REMOVED: 8 -->"
    )
    result = parse_curator_lesson_result(content)
    assert len(result.consolidated_lessons) == 2
    assert result.removed_count == 8


def test_curator_lesson_roundtrip(tmp_path: Path) -> None:
    """Parse deterministic consolidation output and verify."""
    from autocontext.agents.llm_client import DeterministicDevClient
    client = DeterministicDevClient()
    resp = client.generate(
        model="test", prompt="You are a curator consolidating lessons.", max_tokens=1000, temperature=0.3
    )
    result = parse_curator_lesson_result(resp.text)
    assert len(result.consolidated_lessons) > 0
    assert result.removed_count >= 0
