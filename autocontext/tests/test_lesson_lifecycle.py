from pathlib import Path

from autocontext.knowledge.lifecycle import (
    approve_lesson,
    build_lifecycle,
    curate_lesson,
    reject_lesson,
)
from autocontext.storage.artifacts import ArtifactStore


def _store(tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / "claude_skills",
    )


def _playbook(*lessons: str) -> str:
    bullets = "\n".join(f"- {lesson}" for lesson in lessons)
    return f"intro\n<!-- LESSONS_START -->\n{bullets}\n<!-- LESSONS_END -->\noutro"


def test_build_lifecycle_derives_from_playbook_and_skill_markdown(tmp_path: Path) -> None:
    artifacts = _store(tmp_path)
    artifacts.write_playbook("scn", _playbook("fresh", "shared"))
    artifacts.persist_skill_note("scn", 0, "advance", "- shared\n- skill only")
    artifacts.append_dead_end("scn", "tried Y, lost")

    view = build_lifecycle(artifacts=artifacts, scenario="scn", current_generation=20)

    assert [v["text"] for v in view["active"]] == ["fresh", "shared", "skill only"]
    assert view["pending"] == []
    assert view["stale"] == []
    assert view["deadEnd"] and "tried Y" in view["deadEnd"][0]["text"]
    assert view["active"][0]["id"].startswith("lesson_")
    assert view["active"][0]["source"].endswith("playbook.md")


def test_read_skills_ignores_structured_lesson_store(tmp_path: Path) -> None:
    from autocontext.knowledge.lessons import ApplicabilityMeta

    artifacts = _store(tmp_path)
    artifacts.persist_skill_note("scn", 0, "advance", "- markdown lesson")
    artifacts.lesson_store.add_lesson(
        "scn",
        "- stale structured shadow",
        ApplicabilityMeta(created_at="", generation=1, best_score=0.1),
    )

    skills = artifacts.read_skills("scn")

    assert "markdown lesson" in skills
    assert "stale structured shadow" not in skills


def test_approve_is_noop_for_derived_live_lesson(tmp_path: Path) -> None:
    artifacts = _store(tmp_path)
    artifacts.write_playbook("scn", _playbook("already live"))
    [lesson] = build_lifecycle(artifacts=artifacts, scenario="scn", current_generation=1)["active"]

    assert approve_lesson(artifacts=artifacts, scenario="scn", lesson_id=lesson["id"], current_generation=1) == "active"
    assert artifacts._playbook_store("scn").version_count("playbook.md") == 0
    assert approve_lesson(artifacts=artifacts, scenario="scn", lesson_id="missing", current_generation=1) is None


def test_reject_and_delete_remove_markdown_lesson_and_version_playbook(tmp_path: Path) -> None:
    artifacts = _store(tmp_path)
    artifacts.write_playbook("scn", _playbook("remove me", "keep me"))
    active = build_lifecycle(artifacts=artifacts, scenario="scn", current_generation=1)["active"]
    [target] = [v for v in active if v["text"] == "remove me"]

    assert reject_lesson(artifacts=artifacts, scenario="scn", lesson_id=target["id"]) is True

    assert "remove me" not in artifacts.read_playbook("scn")
    assert "keep me" in artifacts.read_playbook("scn")
    assert artifacts._playbook_store("scn").version_count("playbook.md") == 1
    assert reject_lesson(artifacts=artifacts, scenario="scn", lesson_id=target["id"]) is False


def test_curate_stale_annotates_without_deleting_or_prompting(tmp_path: Path) -> None:
    artifacts = _store(tmp_path)
    artifacts.write_playbook("scn", _playbook("stale me"))
    artifacts.persist_skill_note("scn", 0, "advance", "- stale me")
    [lesson] = build_lifecycle(artifacts=artifacts, scenario="scn", current_generation=1)["active"]

    assert (
        curate_lesson(
            artifacts=artifacts,
            scenario="scn",
            lesson_id=lesson["id"],
            action="stale",
            current_generation=9,
        )
        == "stale"
    )

    view = build_lifecycle(artifacts=artifacts, scenario="scn", current_generation=9)
    assert view["active"] == []
    assert [v["text"] for v in view["stale"]] == ["stale me"]
    assert "stale me" in artifacts.read_playbook("scn")
    assert "stale me" not in artifacts.read_skills("scn")


def test_curate_dead_end_moves_markdown_lesson(tmp_path: Path) -> None:
    artifacts = _store(tmp_path)
    artifacts.write_playbook("scn", _playbook("dead lesson"))
    [lesson] = build_lifecycle(artifacts=artifacts, scenario="scn", current_generation=1)["active"]

    assert (
        curate_lesson(
            artifacts=artifacts,
            scenario="scn",
            lesson_id=lesson["id"],
            action="deadEnd",
            current_generation=9,
        )
        == "deadEnd"
    )

    assert "dead lesson" not in artifacts.read_playbook("scn")
    assert "dead lesson" in artifacts.read_dead_ends("scn")
    assert build_lifecycle(artifacts=artifacts, scenario="scn", current_generation=9)["active"] == []


def test_curate_missing_or_unknown_action_returns_none(tmp_path: Path) -> None:
    artifacts = _store(tmp_path)
    artifacts.write_playbook("scn", _playbook("keep"))

    assert curate_lesson(artifacts=artifacts, scenario="scn", lesson_id="missing", action="delete", current_generation=1) is None
    assert curate_lesson(artifacts=artifacts, scenario="scn", lesson_id="missing", action="bad", current_generation=1) is None


def test_curate_activate_restores_a_stale_lesson(tmp_path: Path) -> None:
    artifacts = _store(tmp_path)
    artifacts.write_playbook("scn", _playbook("bring me back"))
    [lesson] = build_lifecycle(artifacts=artifacts, scenario="scn", current_generation=1)["active"]
    curate_lesson(artifacts=artifacts, scenario="scn", lesson_id=lesson["id"], action="stale", current_generation=2)

    assert (
        curate_lesson(
            artifacts=artifacts,
            scenario="scn",
            lesson_id=lesson["id"],
            action="activate",
            current_generation=3,
        )
        == "active"
    )
    view = build_lifecycle(artifacts=artifacts, scenario="scn", current_generation=3)
    assert [v["text"] for v in view["active"]] == ["bring me back"]
    assert view["stale"] == []


def test_curate_activate_restores_a_dead_end_to_the_lessons_block(tmp_path: Path) -> None:
    artifacts = _store(tmp_path)
    artifacts.write_playbook("scn", _playbook("doomed", "survivor"))
    [doomed, _] = build_lifecycle(artifacts=artifacts, scenario="scn", current_generation=1)["active"]
    curate_lesson(artifacts=artifacts, scenario="scn", lesson_id=doomed["id"], action="deadEnd", current_generation=2)
    [dead] = build_lifecycle(artifacts=artifacts, scenario="scn", current_generation=2)["deadEnd"]

    assert (
        curate_lesson(
            artifacts=artifacts,
            scenario="scn",
            lesson_id=dead["id"],
            action="activate",
            current_generation=3,
        )
        == "active"
    )
    view = build_lifecycle(artifacts=artifacts, scenario="scn", current_generation=3)
    assert view["deadEnd"] == []
    assert sorted(v["text"] for v in view["active"]) == ["doomed", "survivor"]


def test_curate_delete_removes_a_dead_end(tmp_path: Path) -> None:
    artifacts = _store(tmp_path)
    artifacts.write_playbook("scn", _playbook("doomed"))
    [doomed] = build_lifecycle(artifacts=artifacts, scenario="scn", current_generation=1)["active"]
    curate_lesson(artifacts=artifacts, scenario="scn", lesson_id=doomed["id"], action="deadEnd", current_generation=2)
    [dead] = build_lifecycle(artifacts=artifacts, scenario="scn", current_generation=2)["deadEnd"]

    assert (
        curate_lesson(
            artifacts=artifacts,
            scenario="scn",
            lesson_id=dead["id"],
            action="delete",
            current_generation=3,
        )
        == "deleted"
    )
    assert build_lifecycle(artifacts=artifacts, scenario="scn", current_generation=3)["deadEnd"] == []
