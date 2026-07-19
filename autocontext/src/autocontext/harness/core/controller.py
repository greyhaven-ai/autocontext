"""Domain-agnostic loop controller for pause/resume, gate override, hints, chat."""

from __future__ import annotations

import queue
import threading


class LoopController:
    """Thread-safe control interface for the generation loop."""

    def __init__(self) -> None:
        self._pause_event = threading.Event()
        self._pause_event.set()  # starts running (not paused)
        self._lock = threading.Lock()
        self._gate_override: str | None = None
        self._pending_hint: str | None = None
        self._pending_chat: queue.Queue[tuple[str, str]] = queue.Queue()
        self._chat_responses: queue.Queue[tuple[str, str]] = queue.Queue()
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

    def take_gate_override(self) -> str | None:
        with self._lock:
            val = self._gate_override
            self._gate_override = None
            return val

    def inject_hint(self, text: str) -> None:
        with self._lock:
            self._pending_hint = text

    def take_hint(self) -> str | None:
        with self._lock:
            val = self._pending_hint
            self._pending_hint = None
            return val

    def submit_chat(self, role: str, message: str) -> str:
        """Submit a chat request and block until the loop thread responds."""
        self._pending_chat.put((role, message))
        _role, response = self._chat_responses.get()
        return response

    def poll_chat(self) -> tuple[str, str] | None:
        """Non-blocking check for pending chat requests."""
        try:
            return self._pending_chat.get_nowait()
        except queue.Empty:
            return None

    def respond_chat(self, role: str, response: str) -> None:
        self._chat_responses.put((role, response))

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
