from __future__ import annotations

import json
from pathlib import Path

from autocontext.integrations.browser.evidence import BrowserEvidenceStore


def test_append_audit_event_writes_jsonl(tmp_path: Path) -> None:
    store = BrowserEvidenceStore(tmp_path)
    event = {
        "schemaVersion": "1.0",
        "eventId": "evt_1",
        "sessionId": "session_1",
        "actionId": "act_1",
        "kind": "action_result",
        "allowed": True,
        "policyReason": "allowed",
        "timestamp": "2026-04-22T12:00:02Z",
        "message": "navigation allowed",
        "beforeUrl": "about:blank",
        "afterUrl": "https://example.com",
        "artifacts": {
            "htmlPath": None,
            "screenshotPath": None,
            "downloadPath": None,
        },
    }

    path = store.append_audit_event(event)

    assert path.exists()
    assert path.name == "actions.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["eventId"] == "evt_1"


def test_persist_snapshot_artifacts_writes_html_and_png(tmp_path: Path) -> None:
    store = BrowserEvidenceStore(tmp_path)

    result = store.persist_snapshot_artifacts(
        session_id="session_1",
        basename="snap_1",
        html="<html><body>Hello</body></html>",
        screenshot_base64="cG5nLWJ5dGVz",
    )

    assert result["htmlPath"] is not None
    assert result["screenshotPath"] is not None
    html_path = Path(result["htmlPath"])
    screenshot_path = Path(result["screenshotPath"])
    assert html_path.read_text(encoding="utf-8") == "<html><body>Hello</body></html>"
    assert screenshot_path.read_bytes() == b"png-bytes"


def test_persist_snapshot_artifacts_contains_traversal_names(tmp_path: Path) -> None:
    store = BrowserEvidenceStore(tmp_path)

    result = store.persist_snapshot_artifacts(
        session_id="../session_1",
        basename="../../../../../escaped",
        screenshot_base64="cG5nLWJ5dGVz",
    )

    assert result["screenshotPath"] is not None
    screenshot_path = Path(result["screenshotPath"]).resolve()
    assert screenshot_path.is_relative_to(tmp_path.resolve())
    assert screenshot_path.name == "escaped.png"
    assert screenshot_path.read_bytes() == b"png-bytes"
    assert not (tmp_path.parent / "escaped.png").exists()
