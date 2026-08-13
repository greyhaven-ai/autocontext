from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from autocontext.cli_wire import run_show_wire_payload, run_status_wire_payload

DOCS_ROOT = Path(__file__).resolve().parents[2] / "docs"
FIXTURE_ROOT = DOCS_ROOT / "cli-fixtures"


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_python_run_wire_helpers_match_shared_fixtures() -> None:
    status = _json(FIXTURE_ROOT / "run-status-v1.json")
    show = _json(FIXTURE_ROOT / "run-show-v1.json")

    assert run_status_wire_payload(status["run"], [status["latest_generation"]]) == status
    assert run_show_wire_payload(show["run"], show["generation"]) == show
