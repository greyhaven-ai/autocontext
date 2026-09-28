# agent_task.py as persisted by autocontext 0.19.0 codegen: evaluate_output judges inline and
# forwards only evaluator_epoch. Everything below this header is the generated source, verbatim.
from __future__ import annotations

from autocontext.execution.judge import LLMJudge
from autocontext.scenarios.agent_task import AgentTaskInterface, AgentTaskResult


class ReleaseNotesAgentTask(AgentTaskInterface):
    """Generated custom agent task."""

    name = "release_notes"
    _task_prompt = 'Summarize the release notes for operators.'
    _rubric = 'Score accuracy and operator usefulness.'
    _output_format = 'free_text'
    _judge_model = ''
    _reference_context = 'Release 0.19 renames the judge cache flag.'
    _reference_sources = None
    _required_concepts = ['judge cache flag']
    _context_preparation = None
    _required_context_keys = None
    _max_rounds = 1
    _quality_threshold = 0.9
    _revision_prompt = None
    _sample_input = None

    def get_task_prompt(self, state: dict) -> str:
        prompt = self._task_prompt
        if self._sample_input:
            prompt += "\n\n## Input Data\n" + self._sample_input
        return prompt

    def evaluate_output(
        self,
        output: str,
        state: dict,
        reference_context: str | None = None,
        required_concepts: list[str] | None = None,
        calibration_examples: list[dict] | None = None,
        pinned_dimensions: list[str] | None = None,
    ) -> AgentTaskResult:
        from autocontext.config import load_settings
        from autocontext.execution.evaluator_guardrail import evaluate_evaluator_guardrail
        from autocontext.providers.registry import get_provider

        settings = load_settings()
        provider = get_provider(settings)
        runtime_judge_model = (
            settings.judge_model
            if isinstance(getattr(settings, "judge_model", None), str)
            else ""
        )
        judge_samples = (
            settings.judge_samples
            if isinstance(getattr(settings, "judge_samples", None), int)
            else 1
        )
        judge_temperature = (
            float(settings.judge_temperature)
            if isinstance(getattr(settings, "judge_temperature", None), int | float)
            else 0.0
        )
        judge_disagreement_threshold = (
            float(settings.judge_disagreement_threshold)
            if isinstance(getattr(settings, "judge_disagreement_threshold", None), int | float)
            else 0.15
        )
        judge_bias_probes_enabled = (
            settings.judge_bias_probes_enabled
            if isinstance(getattr(settings, "judge_bias_probes_enabled", None), bool)
            else False
        )
        effective_model = self._judge_model or runtime_judge_model or provider.default_model()
        judge = LLMJudge(
            model=effective_model,
            rubric=self._rubric,
            provider=provider,
            samples=judge_samples,
            max_tokens=int(getattr(settings, "judge_max_tokens", 4096)),
            temperature=judge_temperature,
            disagreement_threshold=judge_disagreement_threshold,
        )
        # Use passed-in context or fall back to class defaults
        ref_ctx = reference_context or self._reference_context
        req_con = required_concepts or self._required_concepts
        result = judge.evaluate(
            self.get_task_prompt(state),
            output,
            reference_context=ref_ctx,
            required_concepts=req_con,
            calibration_examples=calibration_examples,
            pinned_dimensions=pinned_dimensions,
        )
        evaluator_guardrail = evaluate_evaluator_guardrail(
            result,
            provider=provider,
            model=effective_model,
            rubric=self._rubric,
            candidate_output=output,
            bias_probes_enabled=judge_bias_probes_enabled,
        )
        return AgentTaskResult(
            score=result.score,
            reasoning=result.reasoning,
            dimension_scores=result.dimension_scores,
            internal_retries=result.internal_retries,
            evaluator_guardrail=(
                evaluator_guardrail.to_dict()
                if evaluator_guardrail is not None
                else None
            ),
            evaluator_epoch=result.evaluator_epoch,
        )

    def get_rubric(self) -> str:
        return self._rubric

    def initial_state(self, seed: int | None = None) -> dict:
        state = {"task_name": "release_notes", "output_format": self._output_format}
        if self._sample_input:
            state["sample_input"] = self._sample_input
        return state

    def describe_task(self) -> str:
        return self._task_prompt

    def prepare_context(self, state: dict) -> dict:
        if self._context_preparation:
            state["context_preparation"] = self._context_preparation
        if self._reference_context:
            state["reference_context"] = self._reference_context
        if self._reference_sources:
            state["reference_sources"] = self._reference_sources
        return state

    def validate_context(self, state: dict) -> list[str]:
        errors: list[str] = []
        if self._required_context_keys:
            for key in self._required_context_keys:
                if key not in state or not state[key]:
                    errors.append(f"missing required context key: '{key}'")
        return errors

    def revise_output(
        self,
        output: str,
        judge_result: AgentTaskResult,
        state: dict,
    ) -> str:
        from autocontext.scenarios.custom.agent_task_revision import revise_generated_output

        return revise_generated_output(self, output, judge_result, state)
