"""Run-scoped WebSocket command dispatch for the interactive server."""

from __future__ import annotations

import logging

from fastapi import WebSocket
from pydantic import BaseModel

from autocontext.loop.controller import LoopController
from autocontext.server.protocol import (
    AckMsg,
    ChatAgentCmd,
    ChatResponseMsg,
    ErrorMsg,
    InjectHintCmd,
    OverrideGateCmd,
    PauseCmd,
    ResumeCmd,
    RunAcceptedMsg,
    StartRunCmd,
    StateMsg,
    StopCmd,
)
from autocontext.server.resource_limits import (
    InteractiveWorkLimiter,
    InteractiveWorkLimitExceeded,
)
from autocontext.server.run_manager import RunManager, StopOutcome

logger = logging.getLogger(__name__)


def _run_control_error(
    action: str,
    outcome: StopOutcome,
    client_run_id: str | None,
    command_id: str | None,
) -> ErrorMsg:
    if outcome == "scope_mismatch":
        message = f"{action} targets a different run than the active one"
    elif action == "stop":
        message = "no active run to stop"
    elif action == "chat":
        message = "No active run is available for chat."
    else:
        message = f"no active run available for {action}"
    return ErrorMsg(
        message=message,
        client_run_id=client_run_id,
        command_id=command_id,
    )


async def dispatch_run_command(
    command: object,
    *,
    websocket: WebSocket,
    controller: LoopController,
    run_manager: RunManager | None,
    interactive_work: InteractiveWorkLimiter,
) -> bool:
    """Dispatch one run command and report whether it was recognized."""
    message: BaseModel
    match command:
        case PauseCmd(client_run_id=client_run_id, command_id=command_id):
            if run_manager is None:
                controller.pause()
                outcome: StopOutcome = "accepted"
            else:
                outcome = run_manager.control_run(client_run_id, "pause")
            if outcome == "accepted":
                if command_id is not None:
                    await websocket.send_json(
                        AckMsg(
                            action="pause",
                            client_run_id=client_run_id,
                            command_id=command_id,
                        ).model_dump()
                    )
                message = StateMsg(paused=True, client_run_id=client_run_id)
            else:
                message = _run_control_error(
                    "pause", outcome, client_run_id, command_id
                )
            await websocket.send_json(message.model_dump())

        case ResumeCmd(client_run_id=client_run_id, command_id=command_id):
            if run_manager is None:
                controller.resume()
                outcome = "accepted"
            else:
                outcome = run_manager.control_run(client_run_id, "resume")
            if outcome == "accepted":
                if command_id is not None:
                    await websocket.send_json(
                        AckMsg(
                            action="resume",
                            client_run_id=client_run_id,
                            command_id=command_id,
                        ).model_dump()
                    )
                message = StateMsg(paused=False, client_run_id=client_run_id)
            else:
                message = _run_control_error(
                    "resume", outcome, client_run_id, command_id
                )
            await websocket.send_json(message.model_dump())

        case StopCmd(client_run_id=client_run_id, command_id=command_id):
            outcome = (
                run_manager.stop_run(client_run_id, command_id, None)
                if run_manager
                else "not_active"
            )
            if outcome in ("accepted", "duplicate"):
                message = AckMsg(
                    action="stop",
                    client_run_id=client_run_id,
                    command_id=command_id,
                )
            else:
                message = _run_control_error(
                    "stop", outcome, client_run_id, command_id
                )
            await websocket.send_json(message.model_dump())

        case InjectHintCmd(
            text=text,
            client_run_id=client_run_id,
            command_id=command_id,
        ):
            if run_manager is None:
                controller.inject_hint(text)
                outcome = "accepted"
            else:
                outcome = run_manager.control_run(
                    client_run_id,
                    "inject_hint",
                    text,
                )
            if outcome == "accepted":
                message = AckMsg(
                    action="inject_hint",
                    client_run_id=client_run_id,
                    command_id=command_id,
                )
            else:
                message = _run_control_error(
                    "hint injection", outcome, client_run_id, command_id
                )
            await websocket.send_json(message.model_dump())

        case OverrideGateCmd(
            decision=decision,
            client_run_id=client_run_id,
            command_id=command_id,
        ):
            if run_manager is None:
                controller.set_gate_override(decision)
                outcome = "accepted"
            else:
                outcome = run_manager.control_run(
                    client_run_id,
                    "override_gate",
                    decision,
                )
            if outcome == "accepted":
                message = AckMsg(
                    action="override_gate",
                    decision=decision,
                    client_run_id=client_run_id,
                    command_id=command_id,
                )
            else:
                message = _run_control_error(
                    "gate override", outcome, client_run_id, command_id
                )
            await websocket.send_json(message.model_dump())

        case ChatAgentCmd(
            role=role,
            message=chat_message,
            client_run_id=client_run_id,
            command_id=command_id,
        ):
            try:
                if run_manager is None:
                    response = await interactive_work.run(
                        controller.submit_chat,
                        role,
                        chat_message,
                    )
                else:
                    outcome, run_session = run_manager.prepare_chat_run(
                        client_run_id
                    )
                    if outcome != "accepted" or run_session is None:
                        await websocket.send_json(
                            _run_control_error(
                                "chat", outcome, client_run_id, command_id
                            ).model_dump()
                        )
                        return True
                    outcome, manager_response = await interactive_work.run(
                        run_manager.chat_run,
                        run_session,
                        role,
                        chat_message,
                    )
                    if outcome != "accepted" or manager_response is None:
                        await websocket.send_json(
                            _run_control_error(
                                "chat", outcome, client_run_id, command_id
                            ).model_dump()
                        )
                        return True
                    response = manager_response
                await websocket.send_json(
                    ChatResponseMsg(
                        role=role,
                        text=response,
                        client_run_id=client_run_id,
                        command_id=command_id,
                    ).model_dump()
                )
            except InteractiveWorkLimitExceeded:
                await websocket.send_json(
                    ErrorMsg(
                        message="Interactive server is busy; try again later.",
                        client_run_id=client_run_id,
                        command_id=command_id,
                    ).model_dump()
                )
            except Exception:
                logger.warning("interactive chat failed", exc_info=True)
                await websocket.send_json(
                    ErrorMsg(
                        message="Chat request failed.",
                        client_run_id=client_run_id,
                        command_id=command_id,
                    ).model_dump()
                )

        case StartRunCmd(scenario=scenario, generations=generations) as start_cmd:
            if run_manager is None:
                await websocket.send_json(
                    ErrorMsg(
                        message="Run manager not available.",
                        client_run_id=start_cmd.client_run_id,
                        command_id=start_cmd.command_id,
                    ).model_dump()
                )
            elif run_manager.is_active:
                await websocket.send_json(
                    ErrorMsg(
                        message="A run is already active.",
                        client_run_id=start_cmd.client_run_id,
                        command_id=start_cmd.command_id,
                    ).model_dump()
                )
            else:
                try:
                    run_id = await interactive_work.run(
                        run_manager.start_run,
                        scenario,
                        generations,
                        require_playbook_approval=start_cmd.require_playbook_approval,
                        client_run_id=start_cmd.client_run_id,
                    )
                    await websocket.send_json(
                        RunAcceptedMsg(
                            run_id=run_id,
                            scenario=scenario,
                            generations=generations,
                            client_run_id=start_cmd.client_run_id,
                            command_id=start_cmd.command_id,
                        ).model_dump()
                    )
                except InteractiveWorkLimitExceeded:
                    await websocket.send_json(
                        ErrorMsg(
                            message="Interactive server is busy; try again later.",
                            client_run_id=start_cmd.client_run_id,
                            command_id=start_cmd.command_id,
                        ).model_dump()
                    )
                except Exception:
                    logger.warning("interactive run start failed", exc_info=True)
                    await websocket.send_json(
                        ErrorMsg(
                            message="Unable to start run.",
                            client_run_id=start_cmd.client_run_id,
                            command_id=start_cmd.command_id,
                        ).model_dump()
                    )

        case _:
            return False
    return True
