"""Generated and scaffolded agent tasks report the judge's full serving identity (AC-1022).

Generated ``evaluate_output``, and template scaffolds before 0.19.0, copied only ``evaluator_epoch``
from ``LLMJudge.evaluate()``, so scores wrote hash-only registry records and empty per-round
provenance. These tests drive the real judge through a fake provider; no model is called.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

from autocontext.config.settings import AppSettings
from autocontext.execution.evaluator_epoch_registry import EvaluatorEpochRegistry, observe_epoch_quarantined
from autocontext.execution.improvement_loop import ImprovementLoop
from autocontext.execution.judge_spec import JudgeServingSpec, fixture_provenance
from autocontext.providers.callable_wrapper import CallableProvider
from autocontext.scenarios.agent_task import AgentTaskInterface, AgentTaskResult
from autocontext.scenarios.custom.agent_task_codegen import generate_agent_task_class
from autocontext.scenarios.custom.agent_task_revision import patch_legacy_generated_evaluate_output
from autocontext.scenarios.custom.agent_task_spec import AgentTaskSpec
from autocontext.scenarios.custom.registry import load_all_custom_scenarios
from autocontext.scenarios.templates import TemplateLoader

TASK_PROMPT = "Summarize the release notes for operators."
RUBRIC = "Score accuracy and operator usefulness."
REFERENCE = "Release 0.19 renames the judge cache flag."
CONCEPTS = ["judge cache flag"]
OUTPUT = "Operators must rename the judge cache flag."
JUDGE_MODEL = "fake-judge"

# An agent_task.py persisted by the 0.19.0 template, whose evaluate_output judged inline. It was
# generated from the constants above.
LEGACY_SOURCE = Path(__file__).parent / "fixtures" / "legacy_generated_agent_task.py"
# An agent_task.py scaffolded by 0.18.0 from the rag-accuracy template. Its evaluate_output judged
# inline and fell back to the template's calibration examples and rubric dimensions. The module
# binds load_settings and get_provider at import, so tests load it inside _fake_judge.
LEGACY_SCAFFOLD = Path(__file__).parent / "fixtures" / "legacy_scaffolded_agent_task.py"
RAG_DIMENSIONS = (
    "retrieval_relevance",
    "answer_grounding",
    "citation_accuracy",
    "hallucination_detection",
    "parameter_justification",
)


@contextmanager
def _fake_judge(**settings: Any) -> Iterator[None]:
    # The shared runtime resolves settings and the provider at call time through these names, which
    # the execution validator patches too. A provider default distinct from the configured judge
    # model makes model resolution observable.
    provider = CallableProvider(lambda *_: '{"score": 0.8, "reasoning": "ok"}', model_name="provider-default")
    with (
        patch("autocontext.config.load_settings", return_value=AppSettings(judge_model=JUDGE_MODEL, **settings)),
        patch("autocontext.providers.registry.get_provider", return_value=provider),
    ):
        yield


def _assert_reports_serving_identity(result: AgentTaskResult) -> None:
    assert result.evaluator_spec is not None
    spec = JudgeServingSpec.model_validate_json(result.evaluator_spec)
    spec.require_epoch(result.evaluator_epoch)
    assert spec.judge_model == JUDGE_MODEL
    assert result.execution_provenance.get("identity_status") == "verified"
    # The task's class defaults are the reference evidence the judge saw.
    assert result.fixture_provenance == fixture_provenance(TASK_PROMPT, OUTPUT, REFERENCE, CONCEPTS)


def _load_persisted(knowledge_root: Path, source: str, name: str = "release_notes") -> type[AgentTaskInterface]:
    """Load ``source`` the way a custom agent task loads from ``knowledge/_custom_scenarios``."""
    scenario_dir = knowledge_root / "_custom_scenarios" / name
    scenario_dir.mkdir(parents=True)
    (scenario_dir / "agent_task.py").write_text(source, encoding="utf-8")
    (scenario_dir / "scenario_type.txt").write_text("agent_task", encoding="utf-8")
    return load_all_custom_scenarios(knowledge_root)[name]


def _upgraded_llm_fn_placeholder_task(tmp_path: Path) -> AgentTaskInterface:
    class _PlaceholderTask(AgentTaskInterface):
        """The pre-AC-241 generated shape, which AC-310 upgrades at load."""

        _task_prompt = TASK_PROMPT
        _rubric = RUBRIC
        _judge_model = ""
        _reference_context = REFERENCE
        _required_concepts = CONCEPTS

        def get_task_prompt(self, state: dict) -> str:
            return self._task_prompt

        def evaluate_output(self, output: str, state: dict, **kwargs: object) -> AgentTaskResult:
            raise NotImplementedError("llm_fn must be injected at runtime")

        def get_rubric(self) -> str:
            return self._rubric

        def initial_state(self, seed: int | None = None) -> dict:
            return {}

        def describe_task(self) -> str:
            return self._task_prompt

    source_path = tmp_path / "agent_task.py"
    source_path.write_text('raise NotImplementedError("llm_fn must be injected at runtime")', encoding="utf-8")
    return patch_legacy_generated_evaluate_output(_PlaceholderTask, source_path)()


def test_generated_task_reports_the_judges_serving_identity(tmp_path: Path) -> None:
    spec = AgentTaskSpec(
        task_prompt=TASK_PROMPT,
        judge_rubric=RUBRIC,
        reference_context=REFERENCE,
        required_concepts=CONCEPTS,
    )
    task = _load_persisted(tmp_path, generate_agent_task_class(spec, name="release_notes"))()

    with _fake_judge():
        result = task.evaluate_output(OUTPUT, {})

    _assert_reports_serving_identity(result)


def test_llm_fn_placeholder_upgrade_reports_the_judges_serving_identity(tmp_path: Path) -> None:
    task = _upgraded_llm_fn_placeholder_task(tmp_path)

    with _fake_judge():
        result = task.evaluate_output(OUTPUT, {})

    _assert_reports_serving_identity(result)


def test_llm_fn_placeholder_upgrade_judges_with_the_configured_settings(tmp_path: Path) -> None:
    task = _upgraded_llm_fn_placeholder_task(tmp_path)

    with _fake_judge(judge_samples=2):
        result = task.evaluate_output(OUTPUT, {})

    assert result.execution_provenance["samples"] == 2
    # Two samples give the evaluator guardrail a disagreement check to run.
    assert result.evaluator_guardrail is not None


def test_persisted_inline_task_is_upgraded_to_report_the_judges_serving_identity(tmp_path: Path) -> None:
    task = _load_persisted(tmp_path, LEGACY_SOURCE.read_text(encoding="utf-8"))()

    with _fake_judge():
        result = task.evaluate_output(OUTPUT, {})

    _assert_reports_serving_identity(result)


def test_upgrade_keeps_the_epoch_and_backfills_its_hash_only_record(tmp_path: Path) -> None:
    """Earlier runs of a persisted task wrote hash-only records; the upgrade must not mint a new epoch."""
    epochs_root = tmp_path / "_evaluator_epochs"
    legacy: dict[str, Any] = {}
    exec(compile(LEGACY_SOURCE.read_text(encoding="utf-8"), str(LEGACY_SOURCE), "exec"), legacy)  # noqa: S102
    with _fake_judge():
        earlier = legacy["ReleaseNotesAgentTask"]().evaluate_output(OUTPUT, {})
    assert earlier.evaluator_spec is None
    assert observe_epoch_quarantined(epochs_root, "release_notes", earlier.evaluator_epoch) is False

    task = _load_persisted(tmp_path, LEGACY_SOURCE.read_text(encoding="utf-8"))()
    with _fake_judge():
        result = ImprovementLoop(task, max_rounds=1).run(OUTPUT, {})

    assert result.evaluator_spec is not None
    assert result.evaluator_epoch == earlier.evaluator_epoch
    # What the agent-task score write sites record for the loop's result.
    quarantined = observe_epoch_quarantined(
        epochs_root,
        "release_notes",
        result.evaluator_epoch,
        serving_spec=result.evaluator_spec,
    )
    assert quarantined is False
    record = EvaluatorEpochRegistry(epochs_root).load("release_notes", earlier.evaluator_epoch)
    assert record.serving_spec == result.evaluator_spec
    assert [entry["fixture_provenance"] for entry in result.evaluation_provenance] == [
        fixture_provenance(TASK_PROMPT, OUTPUT, REFERENCE, CONCEPTS),
    ]


def test_persisted_scaffolded_task_is_upgraded_to_report_the_judges_serving_identity(tmp_path: Path) -> None:
    with _fake_judge():
        task = _load_persisted(tmp_path, LEGACY_SCAFFOLD.read_text(encoding="utf-8"), name="policy_answers")()
        result = task.evaluate_output(OUTPUT, {})

    assert result.evaluator_spec is not None
    spec = JudgeServingSpec.model_validate_json(result.evaluator_spec)
    spec.require_epoch(result.evaluator_epoch)
    # The template's rubric dimensions and calibration examples are served when the caller passes none.
    assert spec.pinned_dimensions == RAG_DIMENSIONS
    assert len(spec.serving_examples) == 2
    assert result.execution_provenance.get("identity_status") == "verified"
    # The template's empty reference context reaches the judge as no reference context.
    assert result.fixture_provenance == fixture_provenance(task.get_task_prompt({}), OUTPUT, None, [])


def test_scaffold_upgrade_keeps_the_epoch_and_backfills_its_hash_only_record(tmp_path: Path) -> None:
    epochs_root = tmp_path / "_evaluator_epochs"
    legacy: dict[str, Any] = {}
    with _fake_judge():
        exec(compile(LEGACY_SCAFFOLD.read_text(encoding="utf-8"), str(LEGACY_SCAFFOLD), "exec"), legacy)  # noqa: S102
        earlier = legacy["TemplateAgentTask"]().evaluate_output(OUTPUT, {})
    assert earlier.evaluator_spec is None
    assert observe_epoch_quarantined(epochs_root, "policy_answers", earlier.evaluator_epoch) is False

    with _fake_judge():
        task = _load_persisted(tmp_path, LEGACY_SCAFFOLD.read_text(encoding="utf-8"), name="policy_answers")()
        result = ImprovementLoop(task, max_rounds=1).run(OUTPUT, {})

    assert result.evaluator_spec is not None
    assert result.evaluator_epoch == earlier.evaluator_epoch
    quarantined = observe_epoch_quarantined(
        epochs_root,
        "policy_answers",
        result.evaluator_epoch,
        serving_spec=result.evaluator_spec,
    )
    assert quarantined is False
    record = EvaluatorEpochRegistry(epochs_root).load("policy_answers", earlier.evaluator_epoch)
    assert record.serving_spec == result.evaluator_spec


def test_scaffolded_task_reports_the_judges_serving_identity(tmp_path: Path) -> None:
    loader = TemplateLoader()
    loader.scaffold("rag-accuracy", tmp_path / "_custom_scenarios" / "policy_answers")
    with _fake_judge():
        task = load_all_custom_scenarios(tmp_path)["policy_answers"]()
        result = task.evaluate_output(OUTPUT, {})

    assert result.evaluator_spec is not None
    spec = JudgeServingSpec.model_validate_json(result.evaluator_spec)
    spec.require_epoch(result.evaluator_epoch)
    template_dimensions = loader.get_template("rag-accuracy").rubric_dimensions or []
    assert spec.pinned_dimensions == tuple(dimension.name for dimension in template_dimensions)
    assert result.execution_provenance.get("identity_status") == "verified"
    assert result.fixture_provenance == fixture_provenance(task.get_task_prompt({}), OUTPUT, None, [])
