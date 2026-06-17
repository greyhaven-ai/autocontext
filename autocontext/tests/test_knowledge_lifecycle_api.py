from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from autocontext.knowledge.lessons import ApplicabilityMeta, Lesson


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    import autocontext.server.knowledge_api as api

    # Point the engine's knowledge/skills roots at tmp_path via the real AUTOCONTEXT_* env vars.
    monkeypatch.setenv("AUTOCONTEXT_KNOWLEDGE_ROOT", str(tmp_path / "knowledge"))
    monkeypatch.setenv("AUTOCONTEXT_SKILLS_ROOT", str(tmp_path / "skills"))
    # Reset the module-level lazy singletons so they re-read the patched settings.
    api._ctx = None
    api._solve_mgr = None
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app)


def _seed(client: TestClient) -> None:
    import autocontext.server.knowledge_api as api

    ctx = api._get_ctx()
    ctx.artifacts.lesson_store.write_lessons(
        "scn",
        [
            Lesson(id="a", text="fresh", meta=ApplicabilityMeta(created_at="", generation=1, best_score=0.5)),
            Lesson(
                id="p",
                text="held",
                meta=ApplicabilityMeta(created_at="", generation=1, best_score=0.5, approval_status="pending"),
            ),
        ],
    )


def test_lifecycle_endpoint(client: TestClient) -> None:
    _seed(client)
    resp = client.get("/api/knowledge/scn/lifecycle")
    assert resp.status_code == 200
    body = resp.json()
    assert {les["text"] for les in body["active"]} == {"fresh"}
    assert {les["text"] for les in body["pending"]} == {"held"}


def test_approve_endpoint(client: TestClient) -> None:
    _seed(client)
    resp = client.post("/api/knowledge/scn/lessons/p/approve")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "status": "active"}
    after = client.get("/api/knowledge/scn/lifecycle").json()
    assert {les["text"] for les in after["active"]} == {"fresh", "held"}
    assert after["pending"] == []


def test_reject_endpoint(client: TestClient) -> None:
    _seed(client)
    resp = client.post("/api/knowledge/scn/lessons/p/reject")
    assert resp.status_code == 200 and resp.json()["ok"] is True
    assert client.get("/api/knowledge/scn/lifecycle").json()["pending"] == []


def test_curate_delete_endpoint(client: TestClient) -> None:
    _seed(client)
    resp = client.post("/api/knowledge/scn/lessons/a/curate", json={"action": "delete"})
    assert resp.status_code == 200 and resp.json() == {"ok": True, "status": "deleted"}
    assert client.get("/api/knowledge/scn/lifecycle").json()["active"] == []


def test_curate_missing_is_404(client: TestClient) -> None:
    _seed(client)
    resp = client.post("/api/knowledge/scn/lessons/nope/curate", json={"action": "delete"})
    assert resp.status_code == 404


def test_path_traversal_rejected(client: TestClient, tmp_path: Path) -> None:
    # %2E%2E decodes to ".." — must be rejected, not write files above knowledge_root.
    resp = client.post("/api/knowledge/%2E%2E/lessons/p/approve")
    assert resp.status_code != 200
    assert not (tmp_path / "pending_lessons.json").exists()
    assert not (tmp_path / "lessons.json").exists()


def test_approve_missing_is_404(client: TestClient) -> None:
    _seed(client)
    resp = client.post("/api/knowledge/scn/lessons/nope/approve")
    assert resp.status_code == 404


def test_reject_missing_is_404(client: TestClient) -> None:
    _seed(client)
    resp = client.post("/api/knowledge/scn/lessons/nope/reject")
    assert resp.status_code == 404
