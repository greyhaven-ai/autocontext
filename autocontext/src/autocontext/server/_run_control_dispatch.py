"""Transactional parent-side dispatch for spawned-run controller calls."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from multiprocessing.connection import Connection
from typing import Any

from autocontext.loop.controller import LoopController
from autocontext.server._run_process_ipc import _RunProcessProtocolError


def dispatch_control_request(
    controller: LoopController,
    connection: Connection,
    request: dict[str, Any],
    *,
    next_token: str,
    send_response: Callable[..., None],
    response_timeout_seconds: float,
) -> None:
    """Validate, respond, and atomically commit one controller transaction."""
    response: dict[str, Any] = {
        "type": "control_result",
        "ok": False,
        "next_token": next_token,
    }
    commit_action: Callable[[], None] | None = None
    rollback_action: Callable[[], None] | None = None
    try:
        if request.get("type") != "control":
            raise ValueError("unknown controller message type")
        operation = request.get("operation")
        args = request.get("args")
        if not isinstance(operation, str) or not isinstance(args, list):
            raise ValueError("invalid controller request")

        value: Any
        if operation == "is_paused" and not args:
            value = controller.is_paused()
        elif operation == "stop_requested" and not args:
            value = controller.stop_requested()
        elif operation == "stop_details" and not args:
            value = list(controller.stop_details())
        elif operation == "take_hint" and not args:
            hint_reservation, value = controller.reserve_hint()
            commit_action = partial(controller.commit_hint, hint_reservation)
            rollback_action = partial(controller.rollback_hint, hint_reservation)
        elif operation == "take_gate_override" and not args:
            gate_reservation, value = controller.reserve_gate_override()
            commit_action = partial(controller.commit_gate_override, gate_reservation)
            rollback_action = partial(
                controller.rollback_gate_override,
                gate_reservation,
            )
        elif operation == "poll_chat" and not args:
            chat_reservation = controller.reserve_chat()
            if chat_reservation is None:
                value = None
            else:
                request_id, role, message = chat_reservation
                value = [role, message]
                commit_action = partial(controller.commit_chat, request_id)
                rollback_action = partial(controller.rollback_chat, request_id)
        elif (
            operation == "respond_chat"
            and len(args) == 2
            and all(isinstance(item, str) for item in args)
        ):
            response_reservation = controller.reserve_chat_response(args[0], args[1])
            commit_action = partial(
                controller.commit_chat_response,
                response_reservation,
            )
            rollback_action = partial(
                controller.rollback_chat_response,
                response_reservation,
            )
            value = None
        else:
            raise ValueError("unsupported controller operation")
        response.update({"ok": True, "value": value})
    except Exception as exc:
        if rollback_action is not None:
            rollback_action()
            rollback_action = None
        commit_action = None
        response.update(
            {
                "error_type": type(exc).__name__[:128],
                "message": str(exc)[:2_000],
            }
        )

    try:
        send_response(
            connection,
            response,
            timeout_seconds=response_timeout_seconds,
        )
    except (BrokenPipeError, OSError, TimeoutError, ValueError) as exc:
        if rollback_action is not None:
            rollback_action()
        raise _RunProcessProtocolError(
            "interactive run controller response could not be sent"
        ) from exc
    if commit_action is not None:
        try:
            commit_action()
        except Exception as exc:
            if rollback_action is not None:
                rollback_action()
            raise _RunProcessProtocolError(
                "interactive run controller transaction could not commit"
            ) from exc
