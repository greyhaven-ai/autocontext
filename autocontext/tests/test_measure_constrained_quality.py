from __future__ import annotations

import multiprocessing
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from autocontext.agents.role_schemas import ANALYST_SCHEMA  # noqa: E402
from scripts.measure_constrained_quality import (  # noqa: E402
    _constrained_payload,
    _constrained_request,
    _post,
    _prose_content,
    _score_payload,
)


def test_tool_measurement_requests_strict_arguments() -> None:
    request = _constrained_request("tool", {"model": "stub"}, ANALYST_SCHEMA)

    assert request["tools"][0]["function"]["strict"] is True


def test_native_anthropic_measurement_matches_shipped_wire_shape() -> None:
    request = _constrained_request("anthropic_tool", {"model": "stub"}, ANALYST_SCHEMA)

    tool = request["tools"][0]
    assert tool["name"] == "analyst_output"
    assert tool["strict"] is True
    assert tool["input_schema"]["properties"]["findings"]["minItems"] == 1
    assert "minLength" not in tool["input_schema"]["properties"]["findings"]["items"]
    assert request["tool_choice"] == {"type": "tool", "name": "analyst_output"}


def test_native_anthropic_measurement_extracts_both_arms() -> None:
    assert _prose_content("anthropic_tool", {"content": [{"type": "text", "text": "analysis"}]}) == "analysis"
    payload = {"findings": ["f"], "root_causes": ["r"], "recommendations": ["rec"]}
    response = {"content": [{"type": "tool_use", "name": "analyst_output", "input": payload}]}
    assert _constrained_payload("anthropic_tool", response) == payload


def test_measurement_rejects_wrongly_typed_schema_payload() -> None:
    malformed = {
        "findings": "not-a-list",
        "root_causes": "also-not-a-list",
        "recommendations": "x",
    }

    with pytest.raises(ValueError):
        _score_payload(malformed)


def test_measurement_scores_only_validated_payloads() -> None:
    score = _score_payload(
        {
            "findings": ["Score was 0.41."],
            "root_causes": ["The 2-step lookahead was too short."],
            "recommendations": ["Raise lookahead to 4 steps."],
        }
    )

    assert score["items"] == 3
    assert score["grounded"] == pytest.approx(2 / 3)
    assert score["actionable"] == 1.0


def _blocking_worker(
    url: str,
    api_key: str,
    payload: dict[str, Any],
    deadline: float,
    sender: Any,
    anthropic_api: bool,
) -> None:
    del url, api_key, payload, deadline, sender, anthropic_api
    time.sleep(10.0)


def test_post_deadline_terminates_and_reaps_in_flight_request() -> None:
    before_children = {child.pid for child in multiprocessing.active_children()}
    before_threads = {thread.ident for thread in threading.enumerate()}
    started = time.monotonic()

    with pytest.raises(RuntimeError, match="request failed after 1 attempts"):
        _post(
            "http://unused.invalid/chat/completions",
            "test-key",
            {"model": "stub"},
            attempts=1,
            deadline=0.1,
            _worker=_blocking_worker,
        )

    assert time.monotonic() - started < 2.0
    assert {child.pid for child in multiprocessing.active_children()} <= before_children
    leaked_threads = [
        thread
        for thread in threading.enumerate()
        if thread.ident not in before_threads and not thread.daemon and thread.is_alive()
    ]
    assert leaked_threads == []
