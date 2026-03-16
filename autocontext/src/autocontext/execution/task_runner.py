"""Task runner daemon for always-on evaluation.

Polls a SQLite-backed task queue, runs ImprovementLoop for each task,
and stores results. Designed to run as a long-lived background process.
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import signal
import time
import traceback
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from autocontext.notifications.base import Notifier

from autocontext.execution.agent_task_evolution import (
    AgentTaskEvolutionRunner,
    AgentTaskGenerationEvaluation,
    AgentTaskTrajectory,
)
from autocontext.execution.improvement_loop import ImprovementLoop, ImprovementResult
from autocontext.execution.judge import LLMJudge
from autocontext.execution.objective_verification import (
    ObjectiveVerificationConfig,
    run_objective_verification,
)
from autocontext.providers.base import LLMProvider
from autocontext.scenarios.agent_task import AgentTaskInterface, AgentTaskResult
from autocontext.storage.sqlite_store import SQLiteStore

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class TaskConfig:
    """Configuration for a queued task run."""

    generations: int = 1
    max_rounds: int = 5
    quality_threshold: float = 0.9
    min_rounds: int = 1
    reference_context: str | None = None
    required_concepts: list[str] | None = None
    calibration_examples: list[dict] | None = None
    initial_output: str | None = None
    rubric: str | None = None
    task_prompt: str | None = None
    revision_prompt: str | None = None
    objective_verification: dict[str, Any] | None = None

    @classmethod
    def from_json(cls, data: str | None) -> TaskConfig:
        if not data:
            return cls()
        parsed = json.loads(data)
        return cls(
            generations=parsed.get("generations", 1),
            max_rounds=parsed.get("max_rounds", 5),
            quality_threshold=parsed.get("quality_threshold", 0.9),
            min_rounds=parsed.get("min_rounds", 1),
            reference_context=parsed.get("reference_context"),
            required_concepts=parsed.get("required_concepts"),
            calibration_examples=parsed.get("calibration_examples"),
            initial_output=parsed.get("initial_output"),
            rubric=parsed.get("rubric"),
            task_prompt=parsed.get("task_prompt"),
            revision_prompt=parsed.get("revision_prompt"),
            objective_verification=parsed.get("objective_verification"),
        )


def _serialize_result(
    result: ImprovementResult,
    objective_verification: dict[str, Any] | None = None,
) -> str:
    """Serialize an ImprovementResult to JSON."""
    rounds = []
    for r in result.rounds:
        rounds.append({
            "round_number": r.round_number,
            "score": r.score,
            "reasoning": r.reasoning,
            "dimension_scores": r.dimension_scores,
            "is_revision": r.is_revision,
        })
    data: dict[str, object] = {
        "rounds": rounds,
        "best_score": result.best_score,
        "best_round": result.best_round,
        "total_rounds": result.total_rounds,
        "met_threshold": result.met_threshold,
    }
    if result.duration_ms is not None:
        data["duration_ms"] = result.duration_ms
    if result.judge_calls:
        data["judge_calls"] = result.judge_calls
    if objective_verification is not None:
        data["objective_verification"] = objective_verification
    return json.dumps(data)


def _serialize_evolution_result(
    trajectory: AgentTaskTrajectory,
    generation_results: list[ImprovementResult],
    objective_verification: dict[str, Any] | None = None,
) -> str:
    """Serialize a multi-generation AgentTask evolution run to JSON."""
    final_rounds: list[dict[str, object]] = []
    if generation_results:
        final_result = generation_results[-1]
        final_rounds = [
            {
                "round_number": r.round_number,
                "score": r.score,
                "reasoning": r.reasoning,
                "dimension_scores": r.dimension_scores,
                "is_revision": r.is_revision,
            }
            for r in final_result.rounds
        ]

    generation_summaries = [
        {
            "generation": idx + 1,
            "best_score": result.best_score,
            "best_round": result.best_round,
            "total_rounds": result.total_rounds,
            "met_threshold": result.met_threshold,
        }
        for idx, result in enumerate(generation_results)
    ]

    data: dict[str, Any] = {
        "mode": "agent_task_multi_generation",
        "trajectory": trajectory.to_dict(),
        "generations": generation_summaries,
        "rounds": final_rounds,
        "best_score": trajectory.metadata.get("best_score", trajectory.final_score),
        "met_threshold": any(result.met_threshold for result in generation_results),
        "total_rounds": sum(result.total_rounds for result in generation_results),
    }
    if objective_verification is not None:
        data["objective_verification"] = objective_verification
    return json.dumps(data)


def _build_objective_payload(
    output: str,
    rubric_score: float,
    config: TaskConfig,
) -> dict[str, Any] | None:
    """Run optional objective verification for a task result."""
    if not config.objective_verification:
        return None
    verification_config = ObjectiveVerificationConfig.from_dict(config.objective_verification)
    if not verification_config.ground_truth:
        return None
    return run_objective_verification(
        output=output,
        rubric_score=rubric_score,
        config=verification_config,
    )


class SimpleAgentTask(AgentTaskInterface):
    """A simple agent task built from config (no codegen needed).

    Used by the task runner when tasks are defined via queue config
    rather than registered scenario classes.
    """

    def __init__(
        self,
        task_prompt: str,
        rubric: str,
        provider: LLMProvider,
        model: str = "",
        revision_prompt: str | None = None,
    ) -> None:
        self._task_prompt = task_prompt
        self._rubric = rubric
        self._provider = provider
        self._model = model
        self._revision_prompt = revision_prompt

    def get_task_prompt(self, state: dict) -> str:
        return self._task_prompt

    def get_rubric(self) -> str:
        return self._rubric

    def initial_state(self, seed: int | None = None) -> dict:
        return {}

    def describe_task(self) -> str:
        return self._task_prompt

    def evaluate_output(
        self,
        output: str,
        state: dict,
        reference_context: str | None = None,
        required_concepts: list[str] | None = None,
        calibration_examples: list[dict] | None = None,
        pinned_dimensions: list[str] | None = None,
    ) -> AgentTaskResult:
        judge = LLMJudge(
            model=self._model,
            rubric=self._rubric,
            provider=self._provider,
        )
        judge_result = judge.evaluate(
            task_prompt=self._task_prompt,
            agent_output=output,
            reference_context=reference_context,
            required_concepts=required_concepts,
            calibration_examples=calibration_examples,
            pinned_dimensions=pinned_dimensions,
        )
        return AgentTaskResult(
            score=judge_result.score,
            reasoning=judge_result.reasoning,
            dimension_scores=judge_result.dimension_scores,
            internal_retries=judge_result.internal_retries,
        )

    def generate_output(self, state: dict) -> str:
        """Generate initial output using the provider."""
        result = self._provider.complete(
            system_prompt="You are a skilled writer and analyst. Complete the task precisely.",
            user_prompt=self._task_prompt,
            model=self._model,
        )
        return result.text

    def revise_output(self, output: str, judge_result: AgentTaskResult, state: dict) -> str:
        """Revise output using judge feedback."""
        revision_instruction = self._revision_prompt or (
            "Revise the following output based on the judge's feedback. "
            "Maintain what works, fix what doesn't."
        )
        prompt = (
            f"{revision_instruction}\n\n"
            f"## Original Output\n{output}\n\n"
            f"## Judge Score: {judge_result.score:.2f}\n"
            f"## Judge Feedback\n{judge_result.reasoning}\n\n"
            f"## Task\n{self._task_prompt}\n\n"
            "Produce an improved version:"
        )
        result = self._provider.complete(
            system_prompt=(
                "You are revising content based on expert feedback. Improve the output. "
                "IMPORTANT: Return ONLY the revised content. Do NOT include analysis, "
                "explanations, headers like '## Revised Output', or self-assessment. "
                "Just output the improved version directly."
            ),
            user_prompt=prompt,
            model=self._model,
        )
        return result.text


class TaskRunner:
    """Daemon that polls the task queue and runs improvement loops.

    Usage::

        runner = TaskRunner(store=store, provider=provider)
        runner.run()  # Blocks until shutdown signal
    """

    def __init__(
        self,
        store: SQLiteStore,
        provider: LLMProvider,
        model: str = "",
        poll_interval: float = 60.0,
        max_consecutive_empty: int = 0,  # 0 = run forever
        notifier: Notifier | None = None,
        concurrency: int = 1,
    ) -> None:
        self.store = store
        self.provider = provider
        self.model = model
        self.poll_interval = poll_interval
        self.max_consecutive_empty = max_consecutive_empty
        self.notifier = notifier
        self.concurrency = max(1, concurrency)
        self._shutdown = False
        self._tasks_processed = 0

    def run(self) -> int:
        """Main loop. Returns the number of tasks processed.

        When ``concurrency`` > 1, uses :meth:`run_batch` to process
        multiple tasks in parallel via a thread pool.
        """
        self._setup_signals()
        consecutive_empty = 0

        logger.info(
            "task runner started (poll_interval=%.1fs, concurrency=%d)",
            self.poll_interval, self.concurrency,
        )

        while not self._shutdown:
            processed = self.run_batch(self.concurrency)

            if processed == 0:
                consecutive_empty += 1
                if self.max_consecutive_empty > 0 and consecutive_empty >= self.max_consecutive_empty:
                    logger.info("max consecutive empty polls reached, shutting down")
                    break
                logger.debug("no tasks, sleeping %.1fs", self.poll_interval)
                self._sleep(self.poll_interval)
                continue

            consecutive_empty = 0

        logger.info("task runner stopped. processed %d tasks", self._tasks_processed)
        return self._tasks_processed

    def run_once(self) -> dict[str, Any] | None:
        """Process a single task from the queue. Returns the task dict or None."""
        task = self.store.dequeue_task()
        if task is None:
            return None
        self._process_task(task)
        self._tasks_processed += 1
        return self.store.get_task(task["id"])

    def run_batch(self, limit: int | None = None) -> int:
        """Process up to *limit* (default: ``self.concurrency``) tasks concurrently.

        Uses ``concurrent.futures.ThreadPoolExecutor`` so that each task
        runs in its own thread.  Returns the number of tasks successfully
        processed (failed tasks are not counted).
        """
        max_tasks = limit if limit is not None else self.concurrency
        tasks: list[dict[str, Any]] = []
        for _ in range(max_tasks):
            task = self.store.dequeue_task()
            if task is None:
                break
            tasks.append(task)
        if not tasks:
            return 0

        succeeded = 0
        if len(tasks) == 1:
            # Skip thread pool overhead for single tasks
            try:
                self._process_task(tasks[0])
                succeeded = 1
            except Exception:
                logger.exception("task %s raised", tasks[0].get("id", "?"))
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(tasks)) as pool:
                futures = {pool.submit(self._process_task, t): t for t in tasks}
                for future in concurrent.futures.as_completed(futures):
                    task = futures[future]
                    try:
                        future.result()
                        succeeded += 1
                    except Exception:
                        logger.exception("task %s raised in batch", task.get("id", "?"))

        self._tasks_processed += succeeded
        return succeeded

    def shutdown(self) -> None:
        """Signal the runner to stop after current task completes."""
        self._shutdown = True

    def _process_task(self, task: dict[str, Any]) -> None:
        task_id = task["id"]
        spec_name = task["spec_name"]
        logger.info("processing task %s (spec=%s)", task_id, spec_name)

        try:
            config = TaskConfig.from_json(task.get("config_json"))

            agent_task = SimpleAgentTask(
                task_prompt=config.task_prompt or f"Complete the task: {spec_name}",
                rubric=config.rubric or "Evaluate quality, accuracy, and completeness on a 0-1 scale.",
                provider=self.provider,
                model=self.model,
                revision_prompt=config.revision_prompt,
            )

            # Generate initial output if not provided
            initial_output = config.initial_output
            if not initial_output:
                logger.info("generating initial output for task %s", task_id)
                initial_output = agent_task.generate_output({})
            if config.generations > 1:
                result = self._run_task_multi_generation(
                    task_id=task_id,
                    agent_task=agent_task,
                    spec_name=spec_name,
                    initial_output=initial_output,
                    config=config,
                )
            else:
                loop = ImprovementLoop(
                    task=agent_task,
                    max_rounds=config.max_rounds,
                    quality_threshold=config.quality_threshold,
                    min_rounds=config.min_rounds,
                )

                result = loop.run(
                    initial_output=initial_output,
                    state={},
                    reference_context=config.reference_context,
                    required_concepts=config.required_concepts,
                    calibration_examples=config.calibration_examples,
                )
                objective_payload = _build_objective_payload(
                    result.best_output,
                    result.best_score,
                    config,
                )

                self.store.complete_task(
                    task_id=task_id,
                    best_score=result.best_score,
                    best_output=result.best_output,
                    total_rounds=result.total_rounds,
                    met_threshold=result.met_threshold,
                    result_json=_serialize_result(result, objective_payload),
                )

            logger.info(
                "task %s completed: score=%.2f rounds=%d threshold_met=%s",
                task_id, result.best_score, result.total_rounds, result.met_threshold,
            )

            self._emit_completion_event(task_id, spec_name, result)

        except Exception:
            logger.exception("task %s failed", task_id)
            error_msg = traceback.format_exc()
            self.store.fail_task(task_id, error_msg)
            self._emit_failure_event(task_id, spec_name, error_msg)

    def _run_task_multi_generation(
        self,
        task_id: str,
        agent_task: SimpleAgentTask,
        spec_name: str,
        initial_output: str,
        config: TaskConfig,
    ) -> ImprovementResult:
        """Run first-class multi-generation learning for an AgentTask."""
        generation_results: dict[int, ImprovementResult] = {}

        def generate_fn(prompt: str, generation: int) -> str:
            result = self.provider.complete(
                system_prompt=(
                    "You are a skilled writer and analyst. Complete the task precisely and "
                    "apply any accumulated lessons to improve on prior attempts."
                ),
                user_prompt=prompt,
                model=self.model or self.provider.default_model(),
            )
            return result.text

        def evaluate_fn(output: str, generation: int) -> AgentTaskGenerationEvaluation:
            loop = ImprovementLoop(
                task=agent_task,
                max_rounds=config.max_rounds,
                quality_threshold=config.quality_threshold,
                min_rounds=config.min_rounds,
            )
            loop_result = loop.run(
                initial_output=output,
                state={},
                reference_context=config.reference_context,
                required_concepts=config.required_concepts,
                calibration_examples=config.calibration_examples,
            )
            generation_results[generation] = loop_result
            best_round_result = next(
                (round_result for round_result in loop_result.rounds if round_result.round_number == loop_result.best_round),
                loop_result.rounds[-1],
            )
            return AgentTaskGenerationEvaluation(
                output=loop_result.best_output,
                score=loop_result.best_score,
                reasoning=best_round_result.reasoning,
                dimension_scores=best_round_result.dimension_scores,
                round_count=loop_result.total_rounds,
                met_threshold=loop_result.met_threshold,
                metadata={
                    "best_round": loop_result.best_round,
                    "judge_failures": loop_result.judge_failures,
                },
            )

        evolution = AgentTaskEvolutionRunner(
            task_prompt=agent_task.get_task_prompt({}),
            generate_fn=generate_fn,
            evaluate_fn=evaluate_fn,
            initial_output=initial_output,
            task_name=spec_name,
        )
        trajectory, state = evolution.run_with_state(config.generations)

        ordered_results = [generation_results[idx] for idx in sorted(generation_results)]
        total_rounds = sum(result.total_rounds for result in ordered_results)
        met_threshold = any(result.met_threshold for result in ordered_results)
        best_score = state.best_score
        best_output = state.best_output
        objective_payload = _build_objective_payload(best_output, best_score, config)

        self.store.complete_task(
            task_id=task_id,
            best_score=best_score,
            best_output=best_output,
            total_rounds=total_rounds,
            met_threshold=met_threshold,
            result_json=_serialize_evolution_result(
                trajectory,
                ordered_results,
                objective_payload,
            ),
        )

        best_generation = max(
            ordered_results,
            key=lambda result: result.best_score,
        )
        return ImprovementResult(
            rounds=best_generation.rounds,
            best_output=best_output,
            best_score=best_score,
            best_round=best_generation.best_round,
            total_rounds=total_rounds,
            met_threshold=met_threshold,
            judge_failures=sum(result.judge_failures for result in ordered_results),
            termination_reason="threshold_met" if met_threshold else "max_rounds",
            total_internal_retries=sum(result.total_internal_retries for result in ordered_results),
            duration_ms=sum(result.duration_ms or 0 for result in ordered_results),
            judge_calls=sum(result.judge_calls for result in ordered_results),
            dimension_trajectory={},
        )

    def _emit_completion_event(
        self, task_id: str, spec_name: str, result: ImprovementResult
    ) -> None:
        if not self.notifier:
            return
        try:
            from autocontext.notifications.base import EventType, NotificationEvent

            event_type = EventType.THRESHOLD_MET if result.met_threshold else EventType.COMPLETION
            event = NotificationEvent(
                type=event_type,
                task_name=spec_name,
                task_id=task_id,
                score=result.best_score,
                round_count=result.total_rounds,
                output_preview=result.best_output[:500] if result.best_output else "",
            )
            self.notifier.notify(event)
        except Exception as exc:
            logger.warning("notification failed: %s", exc)

    def _emit_failure_event(self, task_id: str, spec_name: str, error: str) -> None:
        if not self.notifier:
            return
        try:
            from autocontext.notifications.base import EventType, NotificationEvent

            event = NotificationEvent(
                type=EventType.FAILURE,
                task_name=spec_name,
                task_id=task_id,
                error=error,
            )
            self.notifier.notify(event)
        except Exception as exc:
            logger.warning("notification failed: %s", exc)

    def _setup_signals(self) -> None:
        """Register signal handlers for graceful shutdown."""
        try:
            signal.signal(signal.SIGINT, self._handle_signal)
            signal.signal(signal.SIGTERM, self._handle_signal)
        except (OSError, ValueError):
            # Can't set signals in non-main thread or some environments
            pass

    def _handle_signal(self, signum: int, frame: Any) -> None:
        logger.info("received signal %d, shutting down after current task", signum)
        self._shutdown = True

    def _sleep(self, seconds: float) -> None:
        """Interruptible sleep."""
        end = time.monotonic() + seconds
        while time.monotonic() < end and not self._shutdown:
            time.sleep(min(1.0, end - time.monotonic()))


def enqueue_task(
    store: SQLiteStore,
    spec_name: str,
    task_prompt: str | None = None,
    rubric: str | None = None,
    reference_context: str | None = None,
    required_concepts: list[str] | None = None,
    generations: int = 1,
    max_rounds: int = 5,
    quality_threshold: float = 0.9,
    min_rounds: int = 1,
    initial_output: str | None = None,
    objective_verification: dict[str, Any] | None = None,
    priority: int = 0,
) -> str:
    """Convenience function to enqueue a task. Returns the task ID."""
    task_id = str(uuid.uuid4())
    config = {
        "generations": generations,
        "max_rounds": max_rounds,
        "quality_threshold": quality_threshold,
        "min_rounds": min_rounds,
        "task_prompt": task_prompt,
        "rubric": rubric,
        "reference_context": reference_context,
        "required_concepts": required_concepts,
        "initial_output": initial_output,
        "objective_verification": objective_verification,
    }
    # Remove None values
    config = {k: v for k, v in config.items() if v is not None}
    store.enqueue_task(task_id=task_id, spec_name=spec_name, priority=priority, config=config)
    return task_id
