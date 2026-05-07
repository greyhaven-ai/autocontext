"""Runtime-session writer facade for Python runtime observability."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, Self

from autocontext.session.coordinator import Coordinator
from autocontext.session.runtime_events import (
    RuntimeSessionEvent,
    RuntimeSessionEventLog,
    RuntimeSessionEventStore,
    RuntimeSessionEventType,
)

DEFAULT_CHILD_TASK_MAX_DEPTH = 4


class RuntimeSessionEventSink(Protocol):
    """Observer for live runtime-session events."""

    def on_runtime_session_event(self, event: RuntimeSessionEvent, log: RuntimeSessionEventLog) -> None:
        """Receive a newly appended runtime-session event."""


@dataclass(frozen=True)
class RuntimeSessionPromptHandlerInput:
    session_id: str
    prompt: str
    role: str
    cwd: str
    session_log: RuntimeSessionEventLog


@dataclass(frozen=True)
class RuntimeSessionPromptHandlerOutput:
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


RuntimeSessionPromptHandler = Callable[
    [RuntimeSessionPromptHandlerInput],
    RuntimeSessionPromptHandlerOutput | str,
]


@dataclass(frozen=True)
class RuntimeSessionPromptResult:
    session_id: str
    role: str
    cwd: str
    text: str
    is_error: bool
    error: str
    session_log: RuntimeSessionEventLog


@dataclass(frozen=True)
class RuntimeChildTaskHandlerInput:
    task_id: str
    child_session_id: str
    parent_session_id: str
    worker_id: str
    prompt: str
    role: str
    cwd: str
    depth: int
    max_depth: int
    session_log: RuntimeSessionEventLog


@dataclass(frozen=True)
class RuntimeChildTaskHandlerOutput:
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


RuntimeChildTaskHandler = Callable[
    [RuntimeChildTaskHandlerInput],
    RuntimeChildTaskHandlerOutput | str,
]


@dataclass(frozen=True)
class RuntimeChildTaskResult:
    task_id: str
    child_session_id: str
    parent_session_id: str
    worker_id: str
    role: str
    cwd: str
    text: str
    is_error: bool
    error: str
    depth: int
    max_depth: int
    child_session_log: RuntimeSessionEventLog


class RuntimeSession:
    """Aggregate facade that records prompt/response and child-task events."""

    def __init__(
        self,
        *,
        goal: str,
        log: RuntimeSessionEventLog,
        coordinator: Coordinator,
        event_store: RuntimeSessionEventStore | None = None,
        event_sink: RuntimeSessionEventSink | None = None,
        depth: int = 0,
        max_depth: int = DEFAULT_CHILD_TASK_MAX_DEPTH,
    ) -> None:
        self.goal = goal
        self.log = log
        self.coordinator = coordinator
        self._event_store = event_store
        self._event_sink = event_sink
        self._depth = _normalize_depth(depth, "depth")
        self._max_depth = _normalize_depth(max_depth, "max_depth")
        _observe_runtime_session_log(self.log, self._event_store, self._event_sink)

    @classmethod
    def create(
        cls,
        *,
        goal: str,
        session_id: str | None = None,
        event_store: RuntimeSessionEventStore | None = None,
        event_sink: RuntimeSessionEventSink | None = None,
        metadata: dict[str, Any] | None = None,
        depth: int = 0,
        max_depth: int = DEFAULT_CHILD_TASK_MAX_DEPTH,
    ) -> Self:
        clean_session_id = session_id or f"runtime:{uuid.uuid4().hex[:12]}"
        log = RuntimeSessionEventLog.create(
            session_id=clean_session_id,
            metadata={**(metadata or {}), "goal": goal},
        )
        return cls(
            goal=goal,
            log=log,
            coordinator=Coordinator.create(clean_session_id, goal),
            event_store=event_store,
            event_sink=event_sink,
            depth=depth,
            max_depth=max_depth,
        )

    @classmethod
    def load(
        cls,
        *,
        session_id: str,
        event_store: RuntimeSessionEventStore,
        event_sink: RuntimeSessionEventSink | None = None,
        depth: int = 0,
        max_depth: int = DEFAULT_CHILD_TASK_MAX_DEPTH,
    ) -> Self | None:
        log = event_store.load(session_id)
        if log is None:
            return None
        goal = _read_str(log.metadata.get("goal"))
        return cls(
            goal=goal,
            log=log,
            coordinator=Coordinator.create(log.session_id, goal),
            event_store=event_store,
            event_sink=event_sink,
            depth=depth,
            max_depth=max_depth,
        )

    @property
    def session_id(self) -> str:
        return self.log.session_id

    def submit_prompt(
        self,
        *,
        prompt: str,
        handler: RuntimeSessionPromptHandler,
        role: str = "assistant",
        cwd: str = "",
    ) -> RuntimeSessionPromptResult:
        request_id = uuid.uuid4().hex[:12]
        prompt_event = self.log.append(
            RuntimeSessionEventType.PROMPT_SUBMITTED,
            {
                "requestId": request_id,
                "prompt": prompt,
                "role": role,
                "cwd": cwd,
            },
        )

        try:
            output = _normalize_prompt_output(
                handler(
                    RuntimeSessionPromptHandlerInput(
                        session_id=self.session_id,
                        prompt=prompt,
                        role=role,
                        cwd=cwd,
                        session_log=self.log,
                    )
                )
            )
            self.log.append(
                RuntimeSessionEventType.ASSISTANT_MESSAGE,
                {
                    "requestId": request_id,
                    "promptEventId": prompt_event.event_id,
                    "text": output.text,
                    "metadata": dict(output.metadata),
                    "role": role,
                    "cwd": cwd,
                },
            )
            result = self._prompt_result(role=role, cwd=cwd, text=output.text, is_error=False, error="")
            self.save()
            return result
        except Exception as exc:
            message = str(exc)
            self.log.append(
                RuntimeSessionEventType.ASSISTANT_MESSAGE,
                {
                    "requestId": request_id,
                    "promptEventId": prompt_event.event_id,
                    "text": "",
                    "error": message,
                    "isError": True,
                    "role": role,
                    "cwd": cwd,
                },
            )
            result = self._prompt_result(role=role, cwd=cwd, text="", is_error=True, error=message)
            self.save()
            return result

    def run_child_task(
        self,
        *,
        prompt: str,
        role: str,
        handler: RuntimeChildTaskHandler,
        task_id: str | None = None,
        cwd: str = "",
    ) -> RuntimeChildTaskResult:
        return RuntimeChildTaskRunner(
            coordinator=self.coordinator,
            parent_log=self.log,
            event_store=self._event_store,
            event_sink=self._event_sink,
            depth=self._depth,
            max_depth=self._max_depth,
        ).run(prompt=prompt, role=role, handler=handler, task_id=task_id, cwd=cwd)

    def list_child_logs(self) -> list[RuntimeSessionEventLog]:
        return self._event_store.list_children(self.session_id) if self._event_store is not None else []

    def save(self) -> None:
        if self._event_store is not None:
            self._event_store.save(self.log)

    def _prompt_result(
        self,
        *,
        role: str,
        cwd: str,
        text: str,
        is_error: bool,
        error: str,
    ) -> RuntimeSessionPromptResult:
        return RuntimeSessionPromptResult(
            session_id=self.session_id,
            role=role,
            cwd=cwd,
            text=text,
            is_error=is_error,
            error=error,
            session_log=self.log,
        )


class RuntimeChildTaskRunner:
    """Runs a child task while preserving parent/child event lineage."""

    def __init__(
        self,
        *,
        coordinator: Coordinator,
        parent_log: RuntimeSessionEventLog,
        event_store: RuntimeSessionEventStore | None = None,
        event_sink: RuntimeSessionEventSink | None = None,
        depth: int = 0,
        max_depth: int = DEFAULT_CHILD_TASK_MAX_DEPTH,
    ) -> None:
        self._coordinator = coordinator
        self._parent_log = parent_log
        self._event_store = event_store
        self._event_sink = event_sink
        self._depth = _normalize_depth(depth, "depth")
        self._max_depth = _normalize_depth(max_depth, "max_depth")

    def run(
        self,
        *,
        prompt: str,
        role: str,
        handler: RuntimeChildTaskHandler,
        task_id: str | None = None,
        cwd: str = "",
    ) -> RuntimeChildTaskResult:
        clean_task_id = task_id or uuid.uuid4().hex[:12]
        worker = self._coordinator.delegate(prompt, role)
        self._coordinator.start_worker(worker.worker_id)
        child_depth = self._depth + 1
        child_session_id = f"task:{self._parent_log.session_id}:{clean_task_id}"
        child_log = RuntimeSessionEventLog.create(
            session_id=child_session_id,
            parent_session_id=self._parent_log.session_id,
            task_id=clean_task_id,
            worker_id=worker.worker_id,
            metadata={
                "role": role,
                "cwd": cwd,
                "depth": child_depth,
                "maxDepth": self._max_depth,
            },
        )
        _observe_runtime_session_log(child_log, self._event_store, self._event_sink)

        self._parent_log.append(
            RuntimeSessionEventType.CHILD_TASK_STARTED,
            {
                "taskId": clean_task_id,
                "childSessionId": child_session_id,
                "workerId": worker.worker_id,
                "role": role,
                "cwd": cwd,
                "depth": child_depth,
                "maxDepth": self._max_depth,
            },
        )
        child_log.append(
            RuntimeSessionEventType.PROMPT_SUBMITTED,
            {
                "prompt": prompt,
                "role": role,
                "cwd": cwd,
                "depth": child_depth,
                "maxDepth": self._max_depth,
            },
        )

        if self._depth >= self._max_depth:
            return self._fail_child_task(
                task_id=clean_task_id,
                child_session_id=child_session_id,
                worker_id=worker.worker_id,
                role=role,
                cwd=cwd,
                depth=child_depth,
                child_log=child_log,
                message=f"Maximum child task depth ({self._max_depth}) exceeded",
            )

        try:
            output = _normalize_child_output(
                handler(
                    RuntimeChildTaskHandlerInput(
                        task_id=clean_task_id,
                        child_session_id=child_session_id,
                        parent_session_id=self._parent_log.session_id,
                        worker_id=worker.worker_id,
                        prompt=prompt,
                        role=role,
                        cwd=cwd,
                        depth=child_depth,
                        max_depth=self._max_depth,
                        session_log=child_log,
                    )
                )
            )
            child_log.append(
                RuntimeSessionEventType.ASSISTANT_MESSAGE,
                {
                    "text": output.text,
                    "metadata": dict(output.metadata),
                    "depth": child_depth,
                    "maxDepth": self._max_depth,
                },
            )
            self._coordinator.complete_worker(worker.worker_id, output.text)
            self._parent_log.append(
                RuntimeSessionEventType.CHILD_TASK_COMPLETED,
                {
                    "taskId": clean_task_id,
                    "childSessionId": child_session_id,
                    "workerId": worker.worker_id,
                    "role": role,
                    "cwd": cwd,
                    "result": output.text,
                    "isError": False,
                    "depth": child_depth,
                    "maxDepth": self._max_depth,
                },
            )
            result = self._result(
                task_id=clean_task_id,
                child_session_id=child_session_id,
                worker_id=worker.worker_id,
                role=role,
                cwd=cwd,
                text=output.text,
                is_error=False,
                error="",
                depth=child_depth,
                child_log=child_log,
            )
            self._persist(child_log)
            return result
        except Exception as exc:
            return self._fail_child_task(
                task_id=clean_task_id,
                child_session_id=child_session_id,
                worker_id=worker.worker_id,
                role=role,
                cwd=cwd,
                depth=child_depth,
                child_log=child_log,
                message=str(exc),
            )

    def _fail_child_task(
        self,
        *,
        task_id: str,
        child_session_id: str,
        worker_id: str,
        role: str,
        cwd: str,
        depth: int,
        child_log: RuntimeSessionEventLog,
        message: str,
    ) -> RuntimeChildTaskResult:
        self._coordinator.fail_worker(worker_id, message)
        child_log.append(
            RuntimeSessionEventType.ASSISTANT_MESSAGE,
            {
                "text": "",
                "error": message,
                "isError": True,
                "depth": depth,
                "maxDepth": self._max_depth,
            },
        )
        self._parent_log.append(
            RuntimeSessionEventType.CHILD_TASK_COMPLETED,
            {
                "taskId": task_id,
                "childSessionId": child_session_id,
                "workerId": worker_id,
                "role": role,
                "cwd": cwd,
                "result": "",
                "error": message,
                "isError": True,
                "depth": depth,
                "maxDepth": self._max_depth,
            },
        )
        result = self._result(
            task_id=task_id,
            child_session_id=child_session_id,
            worker_id=worker_id,
            role=role,
            cwd=cwd,
            text="",
            is_error=True,
            error=message,
            depth=depth,
            child_log=child_log,
        )
        self._persist(child_log)
        return result

    def _result(
        self,
        *,
        task_id: str,
        child_session_id: str,
        worker_id: str,
        role: str,
        cwd: str,
        text: str,
        is_error: bool,
        error: str,
        depth: int,
        child_log: RuntimeSessionEventLog,
    ) -> RuntimeChildTaskResult:
        return RuntimeChildTaskResult(
            task_id=task_id,
            child_session_id=child_session_id,
            parent_session_id=self._parent_log.session_id,
            worker_id=worker_id,
            role=role,
            cwd=cwd,
            text=text,
            is_error=is_error,
            error=error,
            depth=depth,
            max_depth=self._max_depth,
            child_session_log=child_log,
        )

    def _persist(self, child_log: RuntimeSessionEventLog) -> None:
        if self._event_store is None:
            return
        self._event_store.save(self._parent_log)
        self._event_store.save(child_log)


def _observe_runtime_session_log(
    log: RuntimeSessionEventLog,
    event_store: RuntimeSessionEventStore | None,
    event_sink: RuntimeSessionEventSink | None,
) -> None:
    if event_store is None and event_sink is None:
        return

    def on_event(event: RuntimeSessionEvent, current_log: RuntimeSessionEventLog) -> None:
        if event_store is not None:
            event_store.save(current_log)
        if event_sink is not None:
            try:
                event_sink.on_runtime_session_event(event, current_log)
            except Exception:
                pass

    log.subscribe(on_event)


def _normalize_prompt_output(output: RuntimeSessionPromptHandlerOutput | str) -> RuntimeSessionPromptHandlerOutput:
    if isinstance(output, RuntimeSessionPromptHandlerOutput):
        return output
    return RuntimeSessionPromptHandlerOutput(text=output)


def _normalize_child_output(output: RuntimeChildTaskHandlerOutput | str) -> RuntimeChildTaskHandlerOutput:
    if isinstance(output, RuntimeChildTaskHandlerOutput):
        return output
    return RuntimeChildTaskHandlerOutput(text=output)


def _normalize_depth(value: int, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        msg = f"{name} must be a non-negative integer"
        raise ValueError(msg)
    return value


def _read_str(value: Any) -> str:
    return value if isinstance(value, str) else ""
