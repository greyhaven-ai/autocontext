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
    from mts.notifications.base import Notifier

from mts.execution.improvement_loop import ImprovementLoop, ImprovementResult
from mts.execution.judge import LLMJudge
from mts.providers.base import LLMProvider
from mts.scenarios.agent_task import AgentTaskInterface, AgentTaskResult
from mts.storage.sqlite_store import SQLiteStore

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class TaskConfig:
    """Configuration for a queued task run."""

    max_rounds: int = 5
    quality_threshold: float = 0.9
    reference_context: str | None = None
    required_concepts: list[str] | None = None
    calibration_examples: list[dict] | None = None
    initial_output: str | None = None
    rubric: str | None = None
    task_prompt: str | None = None
    revision_prompt: str | None = None

    @classmethod
    def from_json(cls, data: str | None) -> TaskConfig:
        if not data:
            return cls()
        parsed = json.loads(data)
        return cls(
            max_rounds=parsed.get("max_rounds", 5),
            quality_threshold=parsed.get("quality_threshold", 0.9),
            reference_context=parsed.get("reference_context"),
            required_concepts=parsed.get("required_concepts"),
            calibration_examples=parsed.get("calibration_examples"),
            initial_output=parsed.get("initial_output"),
            rubric=parsed.get("rubric"),
            task_prompt=parsed.get("task_prompt"),
            revision_prompt=parsed.get("revision_prompt"),
        )


def _serialize_result(result: ImprovementResult, duration_ms: int | None = None) -> str:
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
    if duration_ms is not None:
        data["duration_ms"] = duration_ms
    return json.dumps(data)


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
        model: str = "claude-sonnet-4-20250514",
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
        model: str = "claude-sonnet-4-20250514",
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
        """Main loop. Returns the number of tasks processed."""
        self._setup_signals()
        consecutive_empty = 0

        logger.info("task runner started (poll_interval=%.1fs)", self.poll_interval)

        while not self._shutdown:
            task = self.store.dequeue_task()

            if task is None:
                consecutive_empty += 1
                if self.max_consecutive_empty > 0 and consecutive_empty >= self.max_consecutive_empty:
                    logger.info("max consecutive empty polls reached, shutting down")
                    break
                logger.debug("no tasks, sleeping %.1fs", self.poll_interval)
                self._sleep(self.poll_interval)
                continue

            consecutive_empty = 0
            self._process_task(task)
            self._tasks_processed += 1

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

            loop = ImprovementLoop(
                task=agent_task,
                max_rounds=config.max_rounds,
                quality_threshold=config.quality_threshold,
            )

            start_time = time.monotonic()
            result = loop.run(
                initial_output=initial_output,
                state={},
                reference_context=config.reference_context,
                required_concepts=config.required_concepts,
                calibration_examples=config.calibration_examples,
            )
            duration_ms = int((time.monotonic() - start_time) * 1000)

            self.store.complete_task(
                task_id=task_id,
                best_score=result.best_score,
                best_output=result.best_output,
                total_rounds=result.total_rounds,
                met_threshold=result.met_threshold,
                result_json=_serialize_result(result, duration_ms=duration_ms),
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

    def _emit_completion_event(
        self, task_id: str, spec_name: str, result: ImprovementResult
    ) -> None:
        if not self.notifier:
            return
        try:
            from mts.notifications.base import EventType, NotificationEvent

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
            from mts.notifications.base import EventType, NotificationEvent

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
    max_rounds: int = 5,
    quality_threshold: float = 0.9,
    initial_output: str | None = None,
    priority: int = 0,
) -> str:
    """Convenience function to enqueue a task. Returns the task ID."""
    task_id = str(uuid.uuid4())
    config = {
        "max_rounds": max_rounds,
        "quality_threshold": quality_threshold,
        "task_prompt": task_prompt,
        "rubric": rubric,
        "reference_context": reference_context,
        "required_concepts": required_concepts,
        "initial_output": initial_output,
    }
    # Remove None values
    config = {k: v for k, v in config.items() if v is not None}
    store.enqueue_task(task_id=task_id, spec_name=spec_name, priority=priority, config=config)
    return task_id
