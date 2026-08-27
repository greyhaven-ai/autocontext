from __future__ import annotations

import asyncio
import logging
import os
from contextlib import suppress
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from autocontext.config import load_settings
from autocontext.loop.controller import LoopController
from autocontext.loop.events import EventStreamEmitter
from autocontext.security.confined_files import (
    ConfinedFileTooLarge,
    ConfinedPathError,
    atomic_write_confined_text,
    read_confined_text,
    unlink_confined_file,
)
from autocontext.server.auth import (
    request_is_authorized,
    resolve_server_auth_token,
    tokenless_client_is_local,
    websocket_auth_subprotocol,
    websocket_rejection_code,
)
from autocontext.server.cockpit_api import cockpit_router
from autocontext.server.hub_api import hub_router
from autocontext.server.knowledge_api import router as knowledge_router
from autocontext.server.monitor_api import monitor_router
from autocontext.server.notebook_api import notebook_router
from autocontext.server.openclaw_api import router as openclaw_router
from autocontext.server.protocol import (
    SERVER_CAPABILITIES,
    AckMsg,
    CancelScenarioCmd,
    ChatAgentCmd,
    ChatResponseMsg,
    ConfirmScenarioCmd,
    CreateScenarioCmd,
    EnvironmentsMsg,
    ErrorMsg,
    EventMsg,
    HelloMsg,
    InjectHintCmd,
    ListScenariosCmd,
    OverrideGateCmd,
    PauseCmd,
    ResumeCmd,
    ReviseScenarioCmd,
    RunAcceptedMsg,
    RunStoppedPayload,
    ScenarioErrorMsg,
    ScenarioGeneratingMsg,
    ScenarioPreviewMsg,
    ScenarioReadyMsg,
    ScoringComponent,
    StartRunCmd,
    StateMsg,
    StopCmd,
    StrategyParam,
    parse_client_message,
)
from autocontext.server.resource_limits import (
    MAX_CONCURRENT_INTERACTIVE_WORK,
    MAX_HTTP_REQUEST_BODY_BYTES,
    MAX_KNOWLEDGE_FILE_BYTES,
    MAX_PENDING_EVENT_MESSAGES,
    MAX_PENDING_INTERACTIVE_WORK,
    MAX_REPLAY_FILE_BYTES,
    MAX_WEBSOCKET_CONNECTIONS,
    EventStreamTailState,
    HttpRequestBodyLimitMiddleware,
    InteractivePayloadTooLarge,
    InteractiveWorkLimiter,
    InteractiveWorkLimitExceeded,
    InvalidInteractivePayload,
    ReplayFileTooLarge,
    WebSocketConnectionLimitMiddleware,
    read_event_stream_lines,
    read_limited_json_object,
    receive_limited_json_object,
)
from autocontext.server.run_manager import RunManager
from autocontext.storage import SQLiteStore

logger = logging.getLogger(__name__)


def _build_scenario_creator(app_settings: object) -> object | None:
    try:
        from autocontext.agents.llm_client import build_client_from_settings
        from autocontext.agents.subagent_runtime import SubagentRuntime
        from autocontext.scenarios.custom.creator import ScenarioCreator

        client = build_client_from_settings(app_settings)  # type: ignore[arg-type]
        runtime = SubagentRuntime(client)
        model = getattr(app_settings, "model_architect", "claude-sonnet-5")
        knowledge_root = getattr(app_settings, "knowledge_root", Path("knowledge"))
        return ScenarioCreator(runtime=runtime, model=model, knowledge_root=knowledge_root)
    except Exception:
        logger.warning("failed to initialize ScenarioCreator", exc_info=True)
        return None


def _build_environments_msg(env_info: dict[str, Any]) -> EnvironmentsMsg:
    """Convert the raw dict from RunManager.get_environment_info() into a typed model."""
    return EnvironmentsMsg(**env_info)  # type: ignore[arg-type]


def _build_scenario_preview_msg(spec: Any) -> ScenarioPreviewMsg:
    """Build a ScenarioPreviewMsg from a ScenarioSpec object."""
    params = [StrategyParam(name=p.name, description=p.description) for p in spec.strategy_params]
    scoring = [
        ScoringComponent(
            name=s.name,
            description=s.description,
            weight=spec.final_score_weights.get(s.name, 0.0),
        )
        for s in spec.scoring_components
    ]
    constraints = [f"{c.expression} {c.operator} {c.threshold}" for c in spec.constraints]
    return ScenarioPreviewMsg(
        name=spec.name,
        display_name=spec.display_name,
        description=spec.description,
        strategy_params=params,
        scoring_components=scoring,
        constraints=constraints,
        win_threshold=spec.win_threshold,
    )


def create_app(
    controller: LoopController | None = None,
    events: EventStreamEmitter | None = None,
    run_manager: RunManager | None = None,
) -> FastAPI:
    """Factory that creates the FastAPI app, optionally wired to a LoopController."""
    application = FastAPI(title="autocontext API", version="0.1.0")
    # These pure-ASGI guards sit inside authentication for HTTP requests, so
    # rejected callers are not allowed to make the process buffer a body.
    application.add_middleware(
        HttpRequestBodyLimitMiddleware,
        max_bytes=MAX_HTTP_REQUEST_BODY_BYTES,
    )
    application.add_middleware(
        WebSocketConnectionLimitMiddleware,
        max_connections=MAX_WEBSOCKET_CONNECTIONS,
    )
    # Local GUI clients (cowork desktop webview, browser dev servers) call the
    # HTTP API cross-origin. The engine binds to localhost, so allowing
    # explicit local-app origins is safe; override via AUTOCONTEXT_CORS_ORIGINS.
    cors_origins = [
        origin.strip()
        for origin in os.environ.get(
            "AUTOCONTEXT_CORS_ORIGINS",
            "http://localhost:1420,http://localhost:4173,http://localhost:3000,tauri://localhost",
        ).split(",")
        if origin.strip()
    ]
    server_auth_token = resolve_server_auth_token()

    @application.middleware("http")
    async def authenticate_control_plane(request: Request, call_next: Any) -> Any:
        origin = request.headers.get("origin")
        client_host = request.client.host if request.client is not None else None
        if (
            request.url.path != "/health"
            and server_auth_token is None
            and not tokenless_client_is_local(client_host)
        ):
            return JSONResponse(status_code=403, content={"detail": "Token required for non-loopback clients"})
        if (
            request.method not in {"GET", "HEAD", "OPTIONS"}
            and origin is not None
            and origin not in cors_origins
        ):
            return JSONResponse(status_code=403, content={"detail": "Forbidden origin"})
        if request.url.path != "/health" and not request_is_authorized(
            server_auth_token,
            request.headers.get("authorization"),
        ):
            return JSONResponse(
                status_code=401,
                content={"detail": "Unauthorized"},
                headers={"WWW-Authenticate": 'Bearer realm="autocontext"'},
            )
        return await call_next(request)

    application.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_methods=["GET", "PUT", "POST"],
        allow_headers=["authorization", "content-type"],
    )
    application.include_router(cockpit_router)
    application.include_router(hub_router)
    application.include_router(knowledge_router)
    application.include_router(notebook_router)
    application.include_router(openclaw_router)
    application.include_router(monitor_router)
    app_settings = load_settings()
    application.state.app_settings = app_settings
    store = SQLiteStore(app_settings.db_path)
    migrations_dir = Path(__file__).resolve().parents[3] / "migrations"
    store.migrate(migrations_dir)
    application.state.store = store
    application.state.migrations_dir = migrations_dir
    scenario_creator = _build_scenario_creator(app_settings)
    interactive_work = InteractiveWorkLimiter(
        max_concurrent=MAX_CONCURRENT_INTERACTIVE_WORK,
        max_pending=MAX_PENDING_INTERACTIVE_WORK,
    )

    # Monitor engine (AC-209)
    monitor_engine = None
    if app_settings.monitor_enabled:
        try:
            from autocontext.monitor.engine import MonitorEngine, set_engine

            monitor_engine = MonitorEngine(
                sqlite=store,
                emitter=events,
                default_heartbeat_timeout=app_settings.monitor_heartbeat_timeout,
                max_conditions=app_settings.monitor_max_conditions,
            )
            monitor_engine.start()
            set_engine(monitor_engine)
            logger.info("Monitor engine started")
        except Exception:
            logger.warning("failed to initialize MonitorEngine", exc_info=True)
    application.state.monitor_engine = monitor_engine

    def _read_replay_file(run_id: str, generation: int) -> Path:
        if not run_id or len(run_id) > 128 or not run_id.replace("_", "").replace("-", "").isalnum():
            raise HTTPException(status_code=400, detail="invalid run id")
        if generation < 1:
            raise HTTPException(status_code=400, detail="invalid generation")

        runs_root = app_settings.runs_root.resolve()
        replay_dir = app_settings.runs_root / run_id / "generations" / f"gen_{generation}" / "replays"
        try:
            resolved_dir = replay_dir.resolve(strict=True)
        except OSError:
            raise HTTPException(status_code=404, detail="replay not found") from None
        if resolved_dir != runs_root and runs_root not in resolved_dir.parents:
            raise HTTPException(status_code=400, detail="invalid replay path")

        replay_file = min(resolved_dir.glob("*.json"), default=None)
        if replay_file is None:
            raise HTTPException(status_code=404, detail="replay not found")
        return replay_file

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/api/runs")
    def list_runs() -> list[dict[str, Any]]:
        return store.list_runs(limit=50)  # type: ignore[return-value]

    @application.get("/api/runs/{run_id}/status")
    def run_status(run_id: str) -> list[dict[str, Any]]:
        return store.run_status(run_id)

    @application.get("/api/runs/{run_id}/candidate")
    def candidate(run_id: str) -> dict[str, Any]:
        """The latest generation's agent outputs — the live candidate the loop is producing."""
        return store.get_latest_agent_outputs(run_id)

    @application.get("/api/knowledge/{scenario}")
    def knowledge(scenario: str) -> dict[str, Any]:
        """Knowledge the loop has accumulated for a scenario: playbook, hints, dead ends."""
        if not scenario or len(scenario) > 128 or not scenario.replace("_", "").replace("-", "").isalnum():
            raise HTTPException(status_code=400, detail="invalid scenario id")

        def _read(name: str) -> str:
            try:
                return read_confined_text(
                    app_settings.knowledge_root,
                    (scenario,),
                    name,
                    max_bytes=MAX_KNOWLEDGE_FILE_BYTES,
                ) or ""
            except FileNotFoundError:
                return ""
            except ConfinedFileTooLarge:
                raise HTTPException(status_code=413, detail="knowledge file exceeds the size limit") from None
            except ConfinedPathError:
                raise HTTPException(status_code=400, detail="invalid knowledge path") from None
            except OSError:
                return ""

        playbook = _read("playbook.md").strip()
        if playbook.startswith("No playbook yet"):
            playbook = ""
        return {
            "scenario": scenario,
            "playbook": playbook,
            "hints": _read("hints.md"),
            "deadEnds": _read("dead_ends.md"),
        }

    @application.put("/api/knowledge/{scenario}")
    def update_knowledge(scenario: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Overwrite a scenario's knowledge files (operator curation of what persists)."""
        if not scenario or len(scenario) > 128 or not scenario.replace("_", "").replace("-", "").isalnum():
            raise HTTPException(status_code=400, detail="invalid scenario id")
        files = {"playbook": "playbook.md", "hints": "hints.md", "deadEnds": "dead_ends.md"}
        written: list[str] = []
        try:
            for key, fname in files.items():
                value = payload.get(key)
                if isinstance(value, str):
                    atomic_write_confined_text(
                        app_settings.knowledge_root,
                        (scenario,),
                        fname,
                        value,
                        max_bytes=MAX_KNOWLEDGE_FILE_BYTES,
                    )
                    written.append(fname)
            if "hints.md" in written:
                # Structured state would otherwise shadow the operator's edit.
                unlink_confined_file(app_settings.knowledge_root, (scenario,), "hint_state.json")
        except ConfinedFileTooLarge:
            raise HTTPException(status_code=413, detail="knowledge file exceeds the size limit") from None
        except ConfinedPathError:
            raise HTTPException(status_code=400, detail="invalid knowledge path") from None
        except OSError:
            logger.warning("failed to update knowledge files", exc_info=True)
            raise HTTPException(status_code=500, detail="knowledge update failed") from None
        return {"scenario": scenario, "written": written}

    @application.get("/api/runs/{run_id}/replay/{generation}")
    def replay(run_id: str, generation: int) -> dict[str, Any]:
        replay_path = _read_replay_file(run_id, generation)
        try:
            return read_limited_json_object(replay_path, max_bytes=MAX_REPLAY_FILE_BYTES)
        except ReplayFileTooLarge:
            raise HTTPException(status_code=413, detail="replay exceeds the file size limit") from None
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="replay not found") from None
        except (OSError, ValueError):
            logger.warning("failed to read replay", exc_info=True)
            raise HTTPException(status_code=500, detail="replay is unavailable") from None

    @application.websocket("/ws/events")
    async def ws_events(websocket: WebSocket) -> None:
        rejection_code = websocket_rejection_code(
            websocket,
            auth_token=server_auth_token,
            allowed_origins=cors_origins,
        )
        if rejection_code is not None:
            await websocket.close(code=rejection_code)
            return
        await websocket.accept(
            subprotocol=websocket_auth_subprotocol(websocket, auth_token=server_auth_token)
        )
        tail_state = EventStreamTailState()

        async def _wait_for_disconnect() -> None:
            while True:
                message = await websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    return

        disconnect_task = asyncio.create_task(_wait_for_disconnect())
        try:
            while True:
                for line in read_event_stream_lines(app_settings.event_stream_path, tail_state):
                    await websocket.send_text(line)
                done, _pending = await asyncio.wait({disconnect_task}, timeout=0.5)
                if done:
                    await disconnect_task
                    return
        except (WebSocketDisconnect, RuntimeError):
            return
        except OSError:
            logger.warning("event stream became unavailable", exc_info=True)
            with suppress(RuntimeError):
                await websocket.close(code=1011, reason="Event stream unavailable")
        finally:
            disconnect_task.cancel()
            with suppress(asyncio.CancelledError, WebSocketDisconnect):
                await disconnect_task

    @application.websocket("/ws/interactive")
    async def ws_interactive(websocket: WebSocket) -> None:
        rejection_code = websocket_rejection_code(
            websocket,
            auth_token=server_auth_token,
            allowed_origins=cors_origins,
        )
        if rejection_code is not None:
            await websocket.close(code=rejection_code)
            return
        await websocket.accept(
            subprotocol=websocket_auth_subprotocol(websocket, auth_token=server_auth_token)
        )

        # Protocol version handshake -- always first message. Only advertise
        # safe_run_stop_v1 when a RunManager is wired: it is the component that
        # honors stop, so paths without one (e.g. `autoctx run --serve`) must not
        # claim a capability they cannot service.
        hello_capabilities = list(SERVER_CAPABILITIES) if run_manager is not None else None
        await websocket.send_json(HelloMsg(capabilities=hello_capabilities).model_dump())

        if controller is None or events is None:
            await websocket.send_json(ErrorMsg(message="Interactive mode not available. Start with 'autoctx tui'.").model_dump())
            await websocket.close()
            return

        # Send environment info on connect (scenarios, executors, provider)
        if run_manager:
            env_info = run_manager.get_environment_info()
            await websocket.send_json(_build_environments_msg(env_info).model_dump())

        send_queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=MAX_PENDING_EVENT_MESSAGES)
        event_loop = asyncio.get_running_loop()
        event_backlog_overflowed = False

        def _enqueue_event(message: dict[str, Any]) -> None:
            nonlocal event_backlog_overflowed
            if event_backlog_overflowed:
                return
            try:
                send_queue.put_nowait(message)
            except asyncio.QueueFull:
                event_backlog_overflowed = True
                while not send_queue.empty():
                    send_queue.get_nowait()
                send_queue.put_nowait(None)

        def _on_event(event: str, payload: dict[str, Any]) -> None:
            if event == "run_stopped":
                receipt = RunStoppedPayload(
                    run_id=payload["run_id"],
                    reason="operator",
                    command_id=payload["command_id"],
                    completed_generations=payload["completed_generations"],
                    best_score=payload.get("best_score"),
                )
                msg = EventMsg(event=event, payload=receipt.model_dump())
            else:
                msg = EventMsg(event=event, payload=payload)
            event_loop.call_soon_threadsafe(_enqueue_event, msg.model_dump())

        events.subscribe(_on_event)

        # Per-websocket pending scenario state
        pending_spec: dict[str, Any] = {}

        try:
            # Task to push events to client
            async def push_events() -> None:
                while True:
                    msg = await send_queue.get()
                    if msg is None:
                        await websocket.send_json(
                            ErrorMsg(message="Event backlog limit exceeded; reconnect to continue.").model_dump()
                        )
                        await websocket.close(code=1013, reason="Event backlog limit exceeded")
                        return
                    await websocket.send_json(msg)

            push_task = asyncio.create_task(push_events())

            # Listen for commands from client
            try:
                while True:
                    try:
                        data = await receive_limited_json_object(websocket)
                    except InteractivePayloadTooLarge:
                        break
                    except InvalidInteractivePayload:
                        await websocket.send_json(ErrorMsg(message="Invalid interactive message.").model_dump())
                        continue

                    try:
                        cmd = parse_client_message(data)
                    except ValidationError:
                        await websocket.send_json(ErrorMsg(message="Unknown or invalid interactive command.").model_dump())
                        continue

                    match cmd:
                        case PauseCmd():
                            controller.pause()
                            await websocket.send_json(StateMsg(paused=True).model_dump())

                        case ResumeCmd():
                            controller.resume()
                            await websocket.send_json(StateMsg(paused=False).model_dump())

                        case StopCmd(client_run_id=client_run_id, command_id=command_id):
                            outcome = run_manager.stop_run(client_run_id, command_id, None) if run_manager else "not_active"
                            if outcome in ("accepted", "duplicate"):
                                await websocket.send_json(
                                    AckMsg(action="stop", client_run_id=client_run_id, command_id=command_id).model_dump()
                                )
                            elif outcome == "scope_mismatch":
                                await websocket.send_json(
                                    ErrorMsg(
                                        message="stop targets a different run than the active one",
                                        client_run_id=client_run_id,
                                        command_id=command_id,
                                    ).model_dump()
                                )
                            else:  # not_active
                                await websocket.send_json(
                                    ErrorMsg(
                                        message="no active run to stop",
                                        client_run_id=client_run_id,
                                        command_id=command_id,
                                    ).model_dump()
                                )

                        case InjectHintCmd(text=text):
                            if text:
                                controller.inject_hint(text)
                                await websocket.send_json(AckMsg(action="inject_hint").model_dump())

                        case OverrideGateCmd(decision=decision):
                            controller.set_gate_override(decision)
                            await websocket.send_json(AckMsg(action="override_gate", decision=decision).model_dump())

                        case ChatAgentCmd(role=role, message=message):
                            if role and message:
                                try:
                                    response = await interactive_work.run(controller.submit_chat, role, message)
                                    await websocket.send_json(ChatResponseMsg(role=role, text=response).model_dump())
                                except InteractiveWorkLimitExceeded:
                                    await websocket.send_json(
                                        ErrorMsg(message="Interactive server is busy; try again later.").model_dump()
                                    )
                                except Exception:
                                    logger.warning("interactive chat failed", exc_info=True)
                                    await websocket.send_json(ErrorMsg(message="Chat request failed.").model_dump())

                        case StartRunCmd(scenario=scenario, generations=generations) as start_cmd:
                            if run_manager is None:
                                await websocket.send_json(ErrorMsg(message="Run manager not available.").model_dump())
                            elif run_manager.is_active:
                                await websocket.send_json(ErrorMsg(message="A run is already active.").model_dump())
                            else:
                                try:
                                    rid = await interactive_work.run(
                                        run_manager.start_run,
                                        scenario,
                                        generations,
                                        require_playbook_approval=start_cmd.require_playbook_approval,
                                        client_run_id=start_cmd.client_run_id,
                                    )
                                    await websocket.send_json(
                                        RunAcceptedMsg(run_id=rid, scenario=scenario, generations=generations).model_dump()
                                    )
                                except InteractiveWorkLimitExceeded:
                                    await websocket.send_json(
                                        ErrorMsg(message="Interactive server is busy; try again later.").model_dump()
                                    )
                                except (ValueError, RuntimeError):
                                    logger.warning("interactive run start failed", exc_info=True)
                                    await websocket.send_json(ErrorMsg(message="Unable to start run.").model_dump())

                        case ListScenariosCmd():
                            if run_manager:
                                env_info = run_manager.get_environment_info()
                                await websocket.send_json(_build_environments_msg(env_info).model_dump())
                            else:
                                await websocket.send_json(
                                    EnvironmentsMsg(
                                        scenarios=[], executors=[], current_executor="", agent_provider=""
                                    ).model_dump()
                                )

                        # --- Custom scenario creation handlers ---

                        case CreateScenarioCmd(description=description):
                            if scenario_creator is None:
                                await websocket.send_json(
                                    ScenarioErrorMsg(message="Scenario creator not available.", stage="generation").model_dump()
                                )
                                continue
                            if not description:
                                await websocket.send_json(
                                    ScenarioErrorMsg(message="Description is required.", stage="generation").model_dump()
                                )
                                continue

                            from autocontext.scenarios.custom.creator import ScenarioCreator

                            creator: ScenarioCreator = scenario_creator  # type: ignore[assignment]
                            name = creator.derive_name(description)
                            await websocket.send_json(ScenarioGeneratingMsg(name=name).model_dump())

                            try:
                                spec = await interactive_work.run(creator.generate_spec, description)
                                pending_spec["current"] = spec
                                await websocket.send_json(_build_scenario_preview_msg(spec).model_dump())
                            except InteractiveWorkLimitExceeded:
                                await websocket.send_json(
                                    ScenarioErrorMsg(
                                        message="Interactive server is busy; try again later.",
                                        stage="generation",
                                    ).model_dump()
                                )
                            except Exception:
                                logger.warning("scenario generation failed", exc_info=True)
                                await websocket.send_json(
                                    ScenarioErrorMsg(message="Scenario generation failed.", stage="generation").model_dump()
                                )

                        case ConfirmScenarioCmd():
                            current_spec = pending_spec.get("current")
                            if current_spec is None:
                                await websocket.send_json(
                                    ScenarioErrorMsg(message="No pending scenario to confirm.", stage="validation").model_dump()
                                )
                                continue

                            from autocontext.scenarios import SCENARIO_REGISTRY
                            from autocontext.scenarios.custom.creator import ScenarioCreator

                            creator = scenario_creator  # type: ignore[assignment]

                            try:
                                build_result = await interactive_work.run(creator.build_and_validate, current_spec)
                                SCENARIO_REGISTRY[current_spec.name] = build_result.scenario_class
                                pending_spec.clear()

                                await websocket.send_json(
                                    ScenarioReadyMsg(name=current_spec.name, test_scores=build_result.test_scores).model_dump()
                                )

                                if run_manager:
                                    env_info = run_manager.get_environment_info()
                                    await websocket.send_json(_build_environments_msg(env_info).model_dump())
                            except InteractiveWorkLimitExceeded:
                                await websocket.send_json(
                                    ScenarioErrorMsg(
                                        message="Interactive server is busy; try again later.",
                                        stage="validation",
                                    ).model_dump()
                                )
                            except Exception:
                                logger.warning("scenario build/validate failed", exc_info=True)
                                await websocket.send_json(
                                    ScenarioErrorMsg(message="Scenario validation failed.", stage="validation").model_dump()
                                )

                        case ReviseScenarioCmd(feedback=feedback):
                            current_spec = pending_spec.get("current")
                            if current_spec is None:
                                await websocket.send_json(
                                    ScenarioErrorMsg(message="No pending scenario to revise.", stage="generation").model_dump()
                                )
                                continue

                            if not feedback:
                                continue

                            from autocontext.scenarios.custom.creator import ScenarioCreator

                            creator = scenario_creator  # type: ignore[assignment]

                            try:
                                revised = await interactive_work.run(creator.revise_spec, current_spec, feedback)
                                pending_spec["current"] = revised
                                await websocket.send_json(_build_scenario_preview_msg(revised).model_dump())
                            except InteractiveWorkLimitExceeded:
                                await websocket.send_json(
                                    ScenarioErrorMsg(
                                        message="Interactive server is busy; try again later.",
                                        stage="generation",
                                    ).model_dump()
                                )
                            except Exception:
                                logger.warning("scenario revision failed", exc_info=True)
                                await websocket.send_json(
                                    ScenarioErrorMsg(message="Scenario revision failed.", stage="generation").model_dump()
                                )

                        case CancelScenarioCmd():
                            pending_spec.clear()

            except WebSocketDisconnect:
                pass
            finally:
                push_task.cancel()
        finally:
            events.unsubscribe(_on_event)

    @application.on_event("shutdown")
    def _shutdown_monitor() -> None:
        if monitor_engine is not None:
            from autocontext.monitor.engine import clear_engine

            monitor_engine.stop()
            clear_engine()
            logger.info("Monitor engine stopped")

    def _api_info() -> dict[str, Any]:
        return {
            "service": "autocontext",
            "version": "0.2.4",
            "endpoints": {
                "health": "/health",
                "runs": "/api/runs",
                "scenarios": "/api/scenarios",
                "knowledge": "/api/knowledge/playbook/{scenario}",
                "websocket": "/ws/interactive",
                "events": "/ws/events",
            },
        }

    @application.get("/")
    def root() -> dict[str, Any]:
        return _api_info()

    @application.get("/dashboard")
    @application.get("/dashboard/{path:path}")
    def dashboard_placeholder(path: str = "") -> dict[str, Any]:
        return _api_info()

    return application


# Module-level app for backward compatibility (autoctx serve)
app = create_app()
