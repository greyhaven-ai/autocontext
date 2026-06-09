"""Tests for the /api/knowledge/{scenario} read + write endpoints."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from autocontext.server.app import create_app


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("AUTOCONTEXT_KNOWLEDGE_ROOT", str(tmp_path / "knowledge"))
    monkeypatch.setenv("AUTOCONTEXT_DB_PATH", str(tmp_path / "runs.sqlite3"))
    monkeypatch.setenv("AUTOCONTEXT_AGENT_PROVIDER", "deterministic")
    return TestClient(create_app())


def test_put_writes_knowledge_files_and_get_round_trips(client: TestClient, tmp_path: Path) -> None:
    payload = {"playbook": "## Plan", "hints": "- keep the flag", "deadEnds": "- brute force"}
    response = client.put("/api/knowledge/grid_ctf", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["scenario"] == "grid_ctf"
    assert sorted(body["written"]) == ["dead_ends.md", "hints.md", "playbook.md"]

    base = tmp_path / "knowledge" / "grid_ctf"
    assert (base / "playbook.md").read_text(encoding="utf-8") == "## Plan"
    assert (base / "dead_ends.md").read_text(encoding="utf-8") == "- brute force"

    got = client.get("/api/knowledge/grid_ctf").json()
    assert got["playbook"] == "## Plan"
    assert got["hints"] == "- keep the flag"
    assert got["deadEnds"] == "- brute force"


def test_put_writes_only_provided_string_fields(client: TestClient, tmp_path: Path) -> None:
    response = client.put("/api/knowledge/grid_ctf", json={"hints": "- only hints", "playbook": 42})
    assert response.status_code == 200
    assert response.json()["written"] == ["hints.md"]
    base = tmp_path / "knowledge" / "grid_ctf"
    assert (base / "hints.md").exists()
    assert not (base / "playbook.md").exists()


def test_put_hints_overrides_structured_hint_state(client: TestClient, tmp_path: Path) -> None:
    # ArtifactStore.read_hints prefers hint_state.json over hints.md, so an
    # edit that only wrote hints.md would be silently ignored by the loop
    # (reviewer P2 on PR #1059). Seed structured state, then PUT new hints.
    from autocontext.knowledge.hint_volume import HintManager, HintVolumePolicy
    from autocontext.storage.artifacts import ArtifactStore

    knowledge_root = tmp_path / "knowledge"
    store = ArtifactStore(
        runs_root=tmp_path / "runs",
        knowledge_root=knowledge_root,
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / "claude_skills",
    )
    manager = HintManager.from_hint_text("- old structured hint", policy=HintVolumePolicy())
    store.write_hint_manager("grid_ctf", manager)
    assert (knowledge_root / "grid_ctf" / "hint_state.json").exists()
    assert "old structured hint" in store.read_hints("grid_ctf")

    response = client.put("/api/knowledge/grid_ctf", json={"hints": "- edited hint"})
    assert response.status_code == 200

    assert not (knowledge_root / "grid_ctf" / "hint_state.json").exists()
    assert store.read_hints("grid_ctf") == "- edited hint"


def test_put_without_hints_preserves_hint_state(client: TestClient, tmp_path: Path) -> None:
    from autocontext.knowledge.hint_volume import HintManager, HintVolumePolicy
    from autocontext.storage.artifacts import ArtifactStore

    knowledge_root = tmp_path / "knowledge"
    store = ArtifactStore(
        runs_root=tmp_path / "runs",
        knowledge_root=knowledge_root,
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / "claude_skills",
    )
    store.write_hint_manager("grid_ctf", HintManager.from_hint_text("- keep me", policy=HintVolumePolicy()))

    response = client.put("/api/knowledge/grid_ctf", json={"playbook": "## Plan only"})
    assert response.status_code == 200
    assert (knowledge_root / "grid_ctf" / "hint_state.json").exists()
    assert "keep me" in store.read_hints("grid_ctf")


def test_put_rejects_invalid_scenario_ids(client: TestClient) -> None:
    assert client.put("/api/knowledge/bad!name", json={"hints": "x"}).status_code == 400
    assert client.put("/api/knowledge/dots..dots", json={"hints": "x"}).status_code == 400
    assert client.put(f"/api/knowledge/{'a' * 129}", json={"hints": "x"}).status_code == 400
