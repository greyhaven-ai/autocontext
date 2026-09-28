# agent_task.py as scaffolded by autocontext 0.18.0 from the rag-accuracy template, with two calibration
# examples passed as overrides: evaluate_output judges inline and forwards only evaluator_epoch.
# Everything below this header is the scaffolded source, verbatim.
"""Auto-generated agent task from template: rag-accuracy."""
from __future__ import annotations

from autocontext.config import load_settings
from autocontext.execution.judge import LLMJudge
from autocontext.providers.registry import get_provider
from autocontext.scenarios.agent_task import AgentTaskInterface, AgentTaskResult


class TemplateAgentTask(AgentTaskInterface):
    """Agent task generated from the rag-accuracy template."""

    name = 'policy_answers'
    _description = """Optimize RAG pipeline configuration for retrieval relevance. The agent tunes parameters like chunk size, overlap, top-k, and embedding strategy to maximize retrieval accuracy and answer grounding."""
    _task_prompt = """You are optimizing a Retrieval-Augmented Generation (RAG) pipeline. Given the following configuration parameters and a set of test queries, produce an optimized configuration that maximizes retrieval relevance and answer quality.
Current configuration: - chunk_size: 512 tokens - chunk_overlap: 50 tokens - top_k: 5 - embedding_model: "text-embedding-3-small" - reranking: disabled - hybrid_search: disabled
Test domain: Technical documentation for a cloud platform.
Produce an optimized configuration with explanations for each parameter choice. Include the rationale for trade-offs between recall and precision."""
    _rubric = """Evaluate the RAG configuration optimization on these dimensions: 1. Retrieval relevance (0.0-1.0): Do the parameter choices maximize the
   likelihood of retrieving relevant chunks for diverse query types?
2. Answer grounding (0.0-1.0): Does the configuration support well-grounded
   answers with proper context windows?
3. Citation accuracy (0.0-1.0): Does the configuration facilitate accurate
   source attribution and chunk traceability?
4. Hallucination detection (0.0-1.0): Does the configuration include
   mechanisms to reduce and detect hallucinated content?
5. Parameter justification (0.0-1.0): Are parameter choices well-justified
   with clear trade-off analysis?

Overall score is a weighted average: retrieval_relevance 0.3, answer_grounding 0.25, citation_accuracy 0.2, hallucination_detection 0.15, parameter_justification 0.1."""
    _output_format = 'json_schema'
    _judge_model = ''
    _max_rounds = 2
    _quality_threshold = 0.8
    _reference_context = """"""
    _required_concepts = None
    _calibration_examples = [{'agent_output': 'Cites the retention policy section.', 'human_notes': 'grounded', 'human_score': 0.9}, {'agent_output': 'Invents a retention period.', 'human_notes': 'hallucinated', 'human_score': 0.1}]
    _revision_prompt = """Review the judge feedback on your RAG configuration. Pay special attention to retrieval relevance and hallucination detection scores. Adjust parameters and add missing mechanisms as suggested."""
    _sample_input = """"""
    _pinned_dimensions = ['retrieval_relevance', 'answer_grounding', 'citation_accuracy', 'hallucination_detection', 'parameter_justification']

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
        settings = load_settings()
        from autocontext.execution.evaluator_guardrail import evaluate_evaluator_guardrail
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
        result = judge.evaluate(
            task_prompt=self.get_task_prompt(state),
            agent_output=output,
            reference_context=reference_context or (self._reference_context or None),
            required_concepts=required_concepts or self._required_concepts,
            calibration_examples=calibration_examples or self._calibration_examples,
            pinned_dimensions=pinned_dimensions or self._pinned_dimensions,
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
        state = {
            "seed": seed or 0,
            "task_name": self.name,
            "template": "rag-accuracy",
            "output_format": self._output_format,
        }
        if self._sample_input:
            state["sample_input"] = self._sample_input
        return state

    def describe_task(self) -> str:
        return self._description

    def prepare_context(self, state: dict) -> dict:
        if self._reference_context:
            state["reference_context"] = self._reference_context
        return state

    def revise_output(
        self,
        output: str,
        judge_result: AgentTaskResult,
        state: dict,
    ) -> str:
        if not self._revision_prompt and self._max_rounds <= 1:
            return output
        settings = load_settings()
        provider = get_provider(settings)
        revision_instruction = self._revision_prompt or (
            "Revise the following output based on the judge's feedback. "
            "Maintain what works and fix what does not."
        )
        prompt = (
            f"{revision_instruction}\n\n"
            f"## Original Output\n{output}\n\n"
            f"## Judge Score: {judge_result.score:.2f}\n"
            f"## Judge Feedback\n{judge_result.reasoning}\n\n"
            f"## Task\n{self.get_task_prompt(state)}\n\n"
            "Produce an improved version:"
        )
        result = provider.complete(
            system_prompt=(
                "You are revising content based on expert feedback. Improve the output. "
                "Return only the revised content."
            ),
            user_prompt=prompt,
            model=self._judge_model,
        )
        return result.text
