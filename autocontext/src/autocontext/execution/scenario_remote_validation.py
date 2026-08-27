"""Scenario-specific validation for completed remote task payloads."""

from __future__ import annotations

import math
from collections.abc import Mapping

from autocontext.scenarios.base import ReplayEnvelope, Result


def malformed_scenario_output(metadata: Mapping[str, str], payload: Mapping[str, object]) -> str:
    """Validate the exact scenario response before a success ledger can escape."""

    if metadata.get("task_kind") != "scenario_match":
        return ""
    result = payload.get("result")
    replay = payload.get("replay")
    if not isinstance(result, Mapping) or not isinstance(replay, Mapping):
        return "malformed scenario output: result and replay objects are required"
    try:
        typed_result = Result.model_validate(result)
        typed_replay = ReplayEnvelope.model_validate(replay)
    except (TypeError, ValueError):
        return "malformed scenario output: result or replay failed typed validation"
    score = result.get("score")
    if isinstance(score, bool) or not math.isfinite(typed_result.score):
        return "malformed scenario output: result.score must be finite"
    replay_seed = replay.get("seed")
    if isinstance(replay_seed, bool) or not isinstance(replay_seed, int):
        return "malformed scenario output: replay scenario and seed are required"
    expected_scenario = metadata.get("scenario")
    if expected_scenario is not None and typed_replay.scenario != expected_scenario:
        return "malformed scenario output: replay scenario provenance mismatch"
    expected_seed = metadata.get("seed")
    if expected_seed is not None and str(typed_replay.seed) != str(expected_seed):
        return "malformed scenario output: replay seed provenance mismatch"
    expected_fixture_digest = metadata.get("fixture_digest")
    if expected_fixture_digest is not None and payload.get("fixture_digest") != expected_fixture_digest:
        return "malformed scenario output: prepared fixture attestation mismatch"
    return ""


__all__ = ["malformed_scenario_output"]
