"""Tests for the deterministic, pure repair functions (AC-878).

These pin the structural-only / relocation-only / validation-only behaviour:
no repair may fabricate task content, and every RepairResult must validate
against the generated schema model.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from autocontext.control_plane.contract_probes._base import ArtifactContractProbeInputs
from autocontext.harness_optimization.contract.models import RepairResult
from autocontext.harness_optimization.repairs import (
    finish_guard,
    loop_guard,
    repair_artifact_landing,
    repair_tool_call_json,
)


def _roundtrip(result: RepairResult) -> None:
    """Assert the result validates against the RepairResult schema, both ways."""

    assert isinstance(result, RepairResult)
    reloaded = RepairResult.model_validate(result.model_dump())
    assert reloaded == result
    # JSON round-trip too, mirroring how the gate would persist it.
    assert RepairResult.model_validate_json(result.model_dump_json()) == result


# ---------------------------------------------------------------------------
# repair_tool_call_json
# ---------------------------------------------------------------------------


def test_tool_call_valid_json_is_not_applicable() -> None:
    raw = '{"tool": "read", "path": "a.py"}'
    value, result = repair_tool_call_json(raw)
    assert value == raw
    assert result.status == "not_applicable"
    assert result.before == {"valid": True}
    _roundtrip(result)


def test_tool_call_trailing_comma_is_repaired() -> None:
    value, result = repair_tool_call_json('{"a":1,}')
    assert result.status == "applied"
    assert value is not None
    parsed = json.loads(value)
    # The value is preserved exactly; only the trailing comma was removed.
    assert parsed == {"a": 1}
    assert result.before == {"valid": False}
    assert result.after == {"valid": True}
    _roundtrip(result)


def test_tool_call_trailing_comma_in_array_is_repaired() -> None:
    value, result = repair_tool_call_json('{"items": [1, 2, 3,]}')
    assert result.status == "applied"
    assert value is not None
    assert json.loads(value) == {"items": [1, 2, 3]}
    _roundtrip(result)


def test_tool_call_code_fence_is_stripped() -> None:
    raw = '```json\n{"a": 1, "b": "x"}\n```'
    value, result = repair_tool_call_json(raw)
    assert result.status == "applied"
    assert value is not None
    assert json.loads(value) == {"a": 1, "b": "x"}
    _roundtrip(result)


def test_tool_call_bare_code_fence_is_stripped() -> None:
    raw = '```\n{"a": 1}\n```'
    value, result = repair_tool_call_json(raw)
    assert result.status == "applied"
    assert value is not None
    assert json.loads(value) == {"a": 1}
    _roundtrip(result)


def test_tool_call_fence_wrapping_trailing_comma_is_repaired() -> None:
    raw = '```json\n{"a": 1,}\n```'
    value, result = repair_tool_call_json(raw)
    assert result.status == "applied"
    assert value is not None
    assert json.loads(value) == {"a": 1}
    _roundtrip(result)


def test_tool_call_truncated_single_unclosed_object_is_closed() -> None:
    value, result = repair_tool_call_json('{"a":1')
    assert result.status == "applied"
    assert value is not None
    assert value.endswith("}")
    assert json.loads(value) == {"a": 1}
    _roundtrip(result)


def test_tool_call_truncated_single_unclosed_array_is_closed() -> None:
    # A single unclosed array (exactly one opener on the stack) -> close it.
    value, result = repair_tool_call_json("[1, 2, 3")
    assert result.status == "applied"
    assert value is not None
    assert json.loads(value) == [1, 2, 3]
    _roundtrip(result)


def test_tool_call_two_unclosed_is_ambiguous_and_skipped() -> None:
    # Object and array both left open -> multiple plausible closings -> skip.
    value, result = repair_tool_call_json('{"a": [1, 2, 3')
    assert value is None
    assert result.status == "skipped"
    assert result.after == {"valid": False}
    _roundtrip(result)


def test_tool_call_garbage_is_skipped() -> None:
    value, result = repair_tool_call_json("this is not json at all")
    assert value is None
    assert result.status == "skipped"
    assert result.reason == "ambiguous or unrecoverable tool json"
    _roundtrip(result)


def test_tool_call_missing_value_is_not_fabricated() -> None:
    # A missing field VALUE is never guessed; structural closing yields
    # invalid json, so the repair skips rather than inventing a value.
    value, result = repair_tool_call_json('{"a":')
    assert value is None
    assert result.status == "skipped"
    _roundtrip(result)


def test_tool_call_repair_never_changes_a_field_value() -> None:
    # A comma inside a string literal must survive untouched, and no key or
    # value may be added, removed, or altered by the structural repair.
    raw = '{"note": "hello, world]", "n": 1,}'
    value, result = repair_tool_call_json(raw)
    assert result.status == "applied"
    assert value is not None
    parsed = json.loads(value)
    assert parsed == {"note": "hello, world]", "n": 1}
    # No fabricated content: every key in the repaired output was in the input.
    assert set(parsed.keys()) == {"note", "n"}


# ---------------------------------------------------------------------------
# repair_artifact_landing
# ---------------------------------------------------------------------------


def _expected(path: str, content: str) -> ArtifactContractProbeInputs:
    return ArtifactContractProbeInputs(
        path=path,
        content=content,
        required_substrings=("REPORT", "conclusion"),
    )


def test_artifact_landing_passing_contract_is_not_applicable() -> None:
    expected = _expected("report.md", "REPORT\nthe conclusion is here")
    target, result = repair_artifact_landing(expected=expected, produced={})
    assert target is None
    assert result.status == "not_applicable"
    _roundtrip(result)


def test_artifact_landing_right_content_wrong_path_is_relocated() -> None:
    # Nothing landed at report.md, but the right content is at draft.md.
    expected = _expected("report.md", "")
    produced = {
        "notes.txt": "some unrelated scratch notes",
        "draft.md": "REPORT\nthe conclusion is here",
    }
    target, result = repair_artifact_landing(expected=expected, produced=produced)
    assert target == "draft.md"
    assert result.status == "applied"
    assert result.target == "draft.md"
    assert result.after == {"landed": True, "source_path": "draft.md"}
    _roundtrip(result)


def test_artifact_landing_no_matching_content_is_skipped() -> None:
    expected = _expected("report.md", "")
    produced = {"notes.txt": "nothing relevant here at all"}
    target, result = repair_artifact_landing(expected=expected, produced=produced)
    assert target is None
    assert result.status == "skipped"
    _roundtrip(result)


def test_artifact_landing_only_relocates_existing_content() -> None:
    # The relocation target must be a path that is actually in `produced`;
    # the repair never invents a path or content.
    expected = _expected("report.md", "")
    produced = {"elsewhere/report.md": "REPORT\nthe conclusion is here"}
    target, result = repair_artifact_landing(expected=expected, produced=produced)
    assert target in produced
    assert result.status == "applied"


# ---------------------------------------------------------------------------
# finish_guard
# ---------------------------------------------------------------------------


def test_finish_guard_rejects_unmet_completion() -> None:
    result = finish_guard(claimed_done=True, completion_ok=False, reason_if_not="target theorem absent")
    assert result.status == "applied"
    assert "target theorem absent" in result.reason
    assert result.after == {"accepted_done": False}
    _roundtrip(result)


def test_finish_guard_accepts_valid_completion() -> None:
    result = finish_guard(claimed_done=True, completion_ok=True, reason_if_not="unused")
    assert result.status == "not_applicable"
    _roundtrip(result)


def test_finish_guard_no_claim_is_not_applicable() -> None:
    result = finish_guard(claimed_done=False, completion_ok=False, reason_if_not="unused")
    assert result.status == "not_applicable"
    _roundtrip(result)


# ---------------------------------------------------------------------------
# loop_guard
# ---------------------------------------------------------------------------


def test_loop_guard_detects_identical_trailing_actions() -> None:
    result = loop_guard(recent_actions=["read a", "read a", "read a"], max_repeat=3)
    assert result.status == "applied"
    assert result.target == "read a"
    assert result.before == {"repeat_count": 3}
    assert result.after == {"loop_break": True}
    _roundtrip(result)


def test_loop_guard_counts_only_the_tail_run() -> None:
    result = loop_guard(
        recent_actions=["x", "read a", "read a", "read a"],
        max_repeat=3,
    )
    assert result.status == "applied"
    assert result.before == {"repeat_count": 3}
    _roundtrip(result)


def test_loop_guard_varied_actions_is_not_applicable() -> None:
    result = loop_guard(recent_actions=["a", "b", "c", "a"], max_repeat=3)
    assert result.status == "not_applicable"
    _roundtrip(result)


def test_loop_guard_below_threshold_is_not_applicable() -> None:
    result = loop_guard(recent_actions=["a", "a"], max_repeat=3)
    assert result.status == "not_applicable"
    _roundtrip(result)


def test_loop_guard_empty_actions_is_not_applicable() -> None:
    result = loop_guard(recent_actions=[], max_repeat=3)
    assert result.status == "not_applicable"
    _roundtrip(result)


# ---------------------------------------------------------------------------
# shared cross-language parity fixture (AC-878)
# ---------------------------------------------------------------------------
#
# The SAME repo-root fixture is loaded by the TypeScript suite
# (``ts/tests/harness-optimization/repairs.test.ts``). Both languages assert the
# same status (and relocation target / reason substring) for every recorded
# input, which is what proves the two implementations make identical repair
# decisions on identical inputs. The reason strings are IDENTICAL across
# languages, so the ``reason_contains`` substring holds on both sides.

# Walk up to the repo root: autocontext/tests/ -> autocontext/ -> <repo root>.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_REPAIR_FIXTURE = _REPO_ROOT / "fixtures" / "harness-optimization" / "repair-cases" / "repair-cases.json"

_REPAIR_CASES = json.loads(_REPAIR_FIXTURE.read_text())


def _assert_decision(status: str, reason: str, target: str, expected: dict[str, Any]) -> None:
    assert status == expected["status"]
    if "reason_contains" in expected:
        assert expected["reason_contains"] in reason
    if "target" in expected:
        assert target == expected["target"]


def _artifact_inputs(contract: dict[str, Any]) -> ArtifactContractProbeInputs:
    kwargs: dict[str, Any] = {"path": contract["path"], "content": contract["content"]}
    if "expected_line_ending" in contract:
        kwargs["expected_line_ending"] = contract["expected_line_ending"]
    if "required_substrings" in contract:
        kwargs["required_substrings"] = tuple(contract["required_substrings"])
    if "forbidden_substrings" in contract:
        kwargs["forbidden_substrings"] = tuple(contract["forbidden_substrings"])
    if "required_json_fields" in contract:
        kwargs["required_json_fields"] = tuple(contract["required_json_fields"])
    return ArtifactContractProbeInputs(**kwargs)


@pytest.mark.parametrize("case", _REPAIR_CASES["tool_call"], ids=lambda c: c["name"])
def test_shared_fixture_tool_call(case: dict[str, Any]) -> None:
    _value, result = repair_tool_call_json(case["raw"])
    _assert_decision(result.status, result.reason, result.target, case["expected"])
    _roundtrip(result)


@pytest.mark.parametrize("case", _REPAIR_CASES["artifact_landing"], ids=lambda c: c["name"])
def test_shared_fixture_artifact_landing(case: dict[str, Any]) -> None:
    _path, result = repair_artifact_landing(
        expected=_artifact_inputs(case["expected_contract"]),
        produced=dict(case["produced"]),
    )
    _assert_decision(result.status, result.reason, result.target, case["expected"])
    _roundtrip(result)


@pytest.mark.parametrize("case", _REPAIR_CASES["finish_guard"], ids=lambda c: c["name"])
def test_shared_fixture_finish_guard(case: dict[str, Any]) -> None:
    result = finish_guard(
        claimed_done=case["claimed_done"],
        completion_ok=case["completion_ok"],
        reason_if_not=case["reason_if_not"],
    )
    _assert_decision(result.status, result.reason, result.target, case["expected"])
    _roundtrip(result)
