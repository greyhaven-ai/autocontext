"""Domain-agnostic loop controller for pause/resume, gate override, hints, chat."""

from __future__ import annotations

import queue
import threading
from collections import deque
from dataclasses import dataclass


@dataclass(slots=True)
class _ChatRequest:
    role: str
    message: str
    response: queue.Queue[tuple[bool, str]]


@dataclass(slots=True)
class _ChatResponseReservation:
    request: _ChatRequest
    role: str
    response: str


@dataclass(slots=True)
class _ValueReservation:
    version: int
    value: str | None


class LoopController:
    """Thread-safe control interface for the generation loop."""

    def __init__(self) -> None:
        self._pause_event = threading.Event()
        self._pause_event.set()  # starts running (not paused)
        self._lock = threading.Lock()
        self._gate_override: str | None = None
        self._gate_override_version = 0
        self._gate_override_reservation: _ValueReservation | None = None
        self._pending_hint: str | None = None
        self._pending_hint_version = 0
        self._pending_hint_reservation: _ValueReservation | None = None
        self._pending_chat: deque[_ChatRequest] = deque()
        self._reserved_chat: _ChatRequest | None = None
        self._inflight_chat: deque[_ChatRequest] = deque()
        self._chat_response_reservation: _ChatResponseReservation | None = None
        self._chat_abort_message: str | None = None
        self._stop_requested = False
        self._stop_command_id: str | None = None
        self._stop_reason: str | None = None

    def pause(self) -> None:
        self._pause_event.clear()

    def resume(self) -> None:
        self._pause_event.set()

    def is_paused(self) -> bool:
        return not self._pause_event.is_set()

    def wait_if_paused(self) -> None:
        """Block the calling thread until resumed."""
        self._pause_event.wait()

    def set_gate_override(self, decision: str) -> None:
        with self._lock:
            self._gate_override = decision
            self._gate_override_version += 1

    def take_gate_override(self) -> str | None:
        with self._lock:
            if self._gate_override_reservation is not None:
                raise RuntimeError("gate override is reserved for delivery")
            value = self._gate_override
            self._gate_override = None
            return value

    def reserve_gate_override(self) -> tuple[_ValueReservation, str | None]:
        """Observe the override without consuming it until IPC delivery commits."""
        with self._lock:
            if self._gate_override_reservation is not None:
                raise RuntimeError("gate override is already reserved")
            reservation = _ValueReservation(
                self._gate_override_version,
                self._gate_override,
            )
            self._gate_override_reservation = reservation
            return reservation, reservation.value

    def commit_gate_override(self, reservation: _ValueReservation) -> None:
        with self._lock:
            if self._gate_override_reservation is not reservation:
                raise RuntimeError("gate override reservation is no longer active")
            self._gate_override_reservation = None
            if self._gate_override_version == reservation.version:
                self._gate_override = None

    def rollback_gate_override(self, reservation: _ValueReservation) -> None:
        with self._lock:
            if self._gate_override_reservation is reservation:
                self._gate_override_reservation = None

    def inject_hint(self, text: str) -> None:
        with self._lock:
            self._pending_hint = text
            self._pending_hint_version += 1

    def take_hint(self) -> str | None:
        with self._lock:
            if self._pending_hint_reservation is not None:
                raise RuntimeError("hint is reserved for delivery")
            value = self._pending_hint
            self._pending_hint = None
            return value

    def reserve_hint(self) -> tuple[_ValueReservation, str | None]:
        """Observe the hint without consuming it until IPC delivery commits."""
        with self._lock:
            if self._pending_hint_reservation is not None:
                raise RuntimeError("hint is already reserved")
            reservation = _ValueReservation(
                self._pending_hint_version,
                self._pending_hint,
            )
            self._pending_hint_reservation = reservation
            return reservation, reservation.value

    def commit_hint(self, reservation: _ValueReservation) -> None:
        with self._lock:
            if self._pending_hint_reservation is not reservation:
                raise RuntimeError("hint reservation is no longer active")
            self._pending_hint_reservation = None
            if self._pending_hint_version == reservation.version:
                self._pending_hint = None

    def rollback_hint(self, reservation: _ValueReservation) -> None:
        with self._lock:
            if self._pending_hint_reservation is reservation:
                self._pending_hint_reservation = None

    def submit_chat(self, role: str, message: str) -> str:
        """Submit a chat request and block until the loop thread responds."""
        request = self.admit_chat(role, message)
        return self.wait_for_chat_response(request)

    def admit_chat(self, role: str, message: str) -> _ChatRequest:
        """Enqueue a request without blocking so admission can be run-scoped."""
        request = _ChatRequest(role, message, queue.Queue(maxsize=1))
        with self._lock:
            if self._chat_abort_message is not None:
                raise RuntimeError(self._chat_abort_message)
            self._pending_chat.append(request)
        return request

    def wait_for_chat_response(self, request: _ChatRequest) -> str:
        """Wait for a previously admitted chat request to finish or abort."""
        ok, response = request.response.get()
        if not ok:
            raise RuntimeError(response)
        return response

    def poll_chat(self) -> tuple[str, str] | None:
        """Non-blocking check for pending chat requests."""
        reservation = self.reserve_chat()
        if reservation is None:
            return None
        request, role, message = reservation
        self.commit_chat(request)
        return role, message

    def reserve_chat(self) -> tuple[_ChatRequest, str, str] | None:
        """Reserve the oldest chat until its IPC response is delivered."""
        with self._lock:
            if self._reserved_chat is not None:
                raise RuntimeError("a chat request is already reserved")
            if not self._pending_chat:
                return None
            request = self._pending_chat.popleft()
            self._reserved_chat = request
            return request, request.role, request.message

    def commit_chat(self, request: _ChatRequest) -> None:
        with self._lock:
            if self._reserved_chat is not request:
                raise RuntimeError("chat reservation is no longer active")
            self._reserved_chat = None
            self._inflight_chat.append(request)

    def rollback_chat(self, request: _ChatRequest) -> None:
        with self._lock:
            if self._reserved_chat is request:
                self._reserved_chat = None
                self._pending_chat.appendleft(request)

    def reserve_chat_response(
        self,
        role: str,
        response: str,
    ) -> _ChatResponseReservation:
        """Validate a response without waking its submitter before IPC ack."""
        with self._lock:
            if self._chat_response_reservation is not None:
                raise RuntimeError("a chat response is already reserved")
            if not self._inflight_chat:
                raise RuntimeError("no chat request is awaiting a response")
            request = self._inflight_chat[0]
            if role != request.role:
                raise ValueError("chat response role does not match the request")
            reservation = _ChatResponseReservation(request, role, response)
            self._chat_response_reservation = reservation
            return reservation

    def commit_chat_response(self, reservation: _ChatResponseReservation) -> None:
        with self._lock:
            if (
                self._chat_response_reservation is not reservation
                or not self._inflight_chat
                or self._inflight_chat[0] is not reservation.request
            ):
                raise RuntimeError("chat response reservation is no longer active")
            self._chat_response_reservation = None
            self._inflight_chat.popleft()
            reservation.request.response.put_nowait((True, reservation.response))

    def rollback_chat_response(self, reservation: _ChatResponseReservation) -> None:
        with self._lock:
            if self._chat_response_reservation is reservation:
                self._chat_response_reservation = None

    def respond_chat(self, role: str, response: str) -> None:
        with self._lock:
            if self._chat_response_reservation is not None:
                raise RuntimeError("chat response is reserved for delivery")
            if not self._inflight_chat:
                raise RuntimeError("no chat request is awaiting a response")
            request = self._inflight_chat[0]
            if role != request.role:
                raise ValueError("chat response role does not match the request")
            self._inflight_chat.popleft()
            request.response.put_nowait((True, response))

    def _drain_chat_requests_locked(self) -> list[_ChatRequest]:
        requests = list(self._pending_chat)
        self._pending_chat.clear()
        if self._reserved_chat is not None:
            requests.append(self._reserved_chat)
            self._reserved_chat = None
        requests.extend(self._inflight_chat)
        self._inflight_chat.clear()
        self._chat_response_reservation = None
        return requests

    def _clear_run_values_locked(self) -> None:
        # Incrementing versions also invalidates commits from reservations that
        # belonged to the prior run, even when a new value is installed later.
        self._pending_hint = None
        self._pending_hint_version += 1
        self._pending_hint_reservation = None
        self._gate_override = None
        self._gate_override_version += 1
        self._gate_override_reservation = None

    def begin_run_session(self) -> None:
        """Atomically reset all controller state for a newly-owned run."""
        with self._lock:
            stale_requests = self._drain_chat_requests_locked()
            self._clear_run_values_locked()
            self._stop_requested = False
            self._stop_command_id = None
            self._stop_reason = None
            self._chat_abort_message = None
            self._pause_event.set()
            for request in stale_requests:
                request.response.put_nowait(
                    (False, "previous interactive chat session ended")
                )

    def begin_chat_session(self) -> None:
        """Compatibility alias for the complete run-session reset."""
        self.begin_run_session()

    def abort_pending_chats(self, reason: str) -> None:
        """Wake every pending/in-flight submitter when a run terminates."""
        with self._lock:
            reason = self._chat_abort_message or reason
            requests = self._drain_chat_requests_locked()
            self._clear_run_values_locked()
            self._chat_abort_message = reason
            self._pause_event.set()
            for request in requests:
                request.response.put_nowait((False, reason))

    def pending_chat_count(self) -> int:
        """Return queued plus reserved/in-flight chat requests for diagnostics."""
        with self._lock:
            return (
                len(self._pending_chat)
                + (1 if self._reserved_chat is not None else 0)
                + len(self._inflight_chat)
            )

    def request_stop(self, command_id: str | None = None, reason: str | None = None) -> None:
        with self._lock:
            self._stop_requested = True
            self._stop_command_id = command_id
            self._stop_reason = reason
        self._pause_event.set()  # wake a thread parked in wait_if_paused()

    def stop_requested(self) -> bool:
        with self._lock:
            return self._stop_requested

    def stop_details(self) -> tuple[str | None, str | None]:
        with self._lock:
            return self._stop_command_id, self._stop_reason

    def clear_stop(self) -> None:
        """Reset stop state so a reused controller does not leak a prior run's stop."""
        with self._lock:
            self._stop_requested = False
            self._stop_command_id = None
            self._stop_reason = None
