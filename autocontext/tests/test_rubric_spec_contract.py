from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from autocontext.execution.judge import LLMJudge
from autocontext.execution.rubric_spec import (
    CompiledRubric,
    RubricSpec,
    compile_rubric_spec,
    legacy_rubric_spec,
    lint_rubric_spec,
    propose_rubric_patches,
)
from autocontext.providers.base import CompletionResult, LLMProvider

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = REPO_ROOT / "docs" / "rubric-spec-parity-fixtures.json"
SCHEMA_PATH = REPO_ROOT / "docs" / "rubric-spec.json"


class _MockProvider(LLMProvider):
    def __init__(self, response_text: str) -> None:
        self.response_text = response_text
        self.user_prompts: list[str] = []

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> CompletionResult:
        self.user_prompts.append(user_prompt)
        return CompletionResult(text=self.response_text, model=model or "mock-v1")

    def default_model(self) -> str:
        return "mock-v1"


def _fixtures() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(FIXTURE_PATH.read_text(encoding="utf-8")))


def _summary(compiled: CompiledRubric) -> dict[str, Any]:
    return compiled.to_summary()


def test_contract_artifacts_define_shared_rubric_spec() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    fixtures = _fixtures()

    assert schema["title"] == "RubricSpec"
    assert schema["properties"]["schema_version"] == {"const": 1}
    assert set(fixtures["fixtures"]) == {
        "legacy_string",
        "multi_criterion_numeric",
        "binary_disqualifier",
        "scoped_corpus",
        "invalid_lint_warnings",
    }


def test_legacy_string_rubric_wraps_as_one_overall_criterion() -> None:
    fixtures = _fixtures()
    spec = legacy_rubric_spec(fixtures["fixtures"]["legacy_string"])

    assert spec.rubric_id == "legacy-string-rubric"
    assert spec.criteria[0].id == "overall"
    assert _summary(compile_rubric_spec(spec)) == fixtures["expected"]["legacy_string"]["compiled_summary"]


@pytest.mark.parametrize(
    "fixture_name",
    ["multi_criterion_numeric", "binary_disqualifier", "scoped_corpus"],
)
def test_typed_rubric_fixtures_compile_to_expected_summary(fixture_name: str) -> None:
    fixtures = _fixtures()
    spec = RubricSpec.model_validate(fixtures["fixtures"][fixture_name])

    assert _summary(compile_rubric_spec(spec)) == fixtures["expected"][fixture_name]["compiled_summary"]


def test_lint_reports_invalid_rubric_before_live_judge_calls() -> None:
    fixtures = _fixtures()
    spec = RubricSpec.model_validate(fixtures["fixtures"]["invalid_lint_warnings"])

    finding_codes = sorted(finding.code for finding in lint_rubric_spec(spec))

    assert finding_codes == fixtures["expected"]["invalid_lint_warnings"]["finding_codes"]
    with pytest.raises(ValueError, match="invalid rubric"):
        compile_rubric_spec(spec)


def test_runtime_validation_matches_source_schema_required_scale_fields() -> None:
    fixtures = _fixtures()
    missing_scale_id = json.loads(json.dumps(fixtures["fixtures"]["multi_criterion_numeric"]))
    del missing_scale_id["criteria"][0]["scale_id"]
    bad_scale_kind = json.loads(json.dumps(fixtures["fixtures"]["multi_criterion_numeric"]))
    bad_scale_kind["scales"][0]["kind"] = "ordinal"

    with pytest.raises(ValueError, match="scale_id"):
        RubricSpec.model_validate(missing_scale_id)
    with pytest.raises(ValueError, match="scale kind"):
        RubricSpec.model_validate(bad_scale_kind)


def test_llm_judge_uses_typed_criterion_ids_as_dimensions() -> None:
    fixtures = _fixtures()
    spec = RubricSpec.model_validate(fixtures["fixtures"]["multi_criterion_numeric"])
    provider = _MockProvider(
        "<!-- JUDGE_RESULT_START -->\n"
        '{"score": 0.7, "reasoning": "ok", '
        '"dimensions": {"correctness": 0.8, "evidence": 0.6, "invented": 1}}\n'
        "<!-- JUDGE_RESULT_END -->"
    )

    judge = LLMJudge(model="mock-v1", rubric=spec, provider=provider)
    result = judge.evaluate("Answer the question", "The answer")

    assert result.dimension_scores == {
        "correctness": 0.8,
        "evidence": 0.6,
        "clarity": 0.0,
    }
    assert result.dimensions_were_generated is False
    assert "Required Dimensions" in provider.user_prompts[0]
    assert "correctness, evidence, clarity" in provider.user_prompts[0]


def test_rubric_patch_proposals_are_experimental_and_structurally_safe() -> None:
    fixtures = _fixtures()
    spec = RubricSpec.model_validate(fixtures["fixtures"]["multi_criterion_numeric"])
    anchors = [
        {
            "criterion_id": "correctness",
            "human_score": 0.2,
            "judge_score": 0.8,
            "human_notes": "Penalize unsupported claims",
        },
        {
            "criterion_id": "correctness",
            "human_score": 0.3,
            "judge_score": 0.7,
            "human_notes": "Require direct evidence",
        },
        {"criterion_id": "clarity", "human_score": 0.9, "judge_score": 0.88, "human_notes": "Clear enough"},
    ]

    with pytest.raises(ValueError, match="experimental"):
        propose_rubric_patches(spec, anchors)

    proposal = propose_rubric_patches(spec, anchors, experimental=True)

    assert proposal.requires_human_review is True
    assert proposal.patches[0].path == "/criteria/correctness/description"
    assert proposal.patches[0].op == "append"
    assert "criterion_id" not in " ".join(patch.path for patch in proposal.patches)
    assert proposal.metrics["agreement"] < 1
    assert proposal.metrics["discrimination"] > 0
