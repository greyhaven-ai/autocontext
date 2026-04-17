from __future__ import annotations

import re
import textwrap

from autocontext.scenarios.custom.agent_task_spec import (
    AgentTaskSpec,
    normalize_agent_task_runtime_fields,
)


def _class_name(name: str) -> str:
    parts = name.split("_")
    return "".join(p.capitalize() for p in parts) + "AgentTask"


def _safe_identifier(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]", "_", name)


def generate_agent_task_class(spec: AgentTaskSpec, name: str = "custom_agent_task") -> str:
    """Generate Python source for an AgentTaskInterface subclass from spec.

    Args:
        spec: The agent task specification.
        name: Snake-case name for the generated class.

    Returns:
        Python source code string.
    """
    spec = normalize_agent_task_runtime_fields(spec)

    cls_name = _class_name(name)
    safe_name = _safe_identifier(name)

    task_prompt_repr = repr(spec.task_prompt)
    rubric_repr = repr(spec.judge_rubric)
    ref_context_repr = repr(spec.reference_context)
    ref_sources_repr = repr(spec.reference_sources)
    req_concepts_repr = repr(spec.required_concepts)
    ctx_prep_repr = repr(spec.context_preparation)
    req_ctx_keys_repr = repr(spec.required_context_keys)
    max_rounds_repr = repr(spec.max_rounds)
    quality_threshold_repr = repr(spec.quality_threshold)
    revision_prompt_repr = repr(spec.revision_prompt)
    sample_input_repr = repr(spec.sample_input)

    source = textwrap.dedent(f'''\
        from __future__ import annotations

        from autocontext.execution.judge import LLMJudge
        from autocontext.scenarios.agent_task import AgentTaskInterface, AgentTaskResult


        class {cls_name}(AgentTaskInterface):
            """Generated agent task: {safe_name}."""

            name = "{safe_name}"
            _task_prompt = {task_prompt_repr}
            _rubric = {rubric_repr}
            _output_format = {repr(spec.output_format)}
            _judge_model = {repr(spec.judge_model)}
            _reference_context = {ref_context_repr}
            _reference_sources = {ref_sources_repr}
            _required_concepts = {req_concepts_repr}
            _context_preparation = {ctx_prep_repr}
            _required_context_keys = {req_ctx_keys_repr}
            _max_rounds = {max_rounds_repr}
            _quality_threshold = {quality_threshold_repr}
            _revision_prompt = {revision_prompt_repr}
            _sample_input = {sample_input_repr}

            def get_task_prompt(self, state: dict) -> str:
                prompt = self._task_prompt
                if self._sample_input:
                    prompt += "\\n\\n## Input Data\\n" + self._sample_input
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
                )

            def get_rubric(self) -> str:
                return self._rubric

            def initial_state(self, seed: int | None = None) -> dict:
                state = {{"task_name": "{safe_name}", "output_format": self._output_format}}
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
                            errors.append(f"missing required context key: '{{key}}'")
                return errors

            def revise_output(
                self,
                output: str,
                judge_result: AgentTaskResult,
                state: dict,
            ) -> str:
                from autocontext.scenarios.custom.agent_task_revision import revise_generated_output

                return revise_generated_output(self, output, judge_result, state)
    ''')
    return source
