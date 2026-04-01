from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from autocontext.config import load_settings
from autocontext.loop.controller import LoopController
from autocontext.loop.events import EventStreamEmitter
from autocontext.server.cockpit_api import cockpit_router
from autocontext.server.hub_api import hub_router
from autocontext.server.knowledge_api import router as knowledge_router
from autocontext.server.monitor_api import monitor_router
from autocontext.server.notebook_api import notebook_router
from autocontext.server.openclaw_api import router as openclaw_router
from autocontext.server.protocol import (
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
    ScenarioErrorMsg,
    ScenarioGeneratingMsg,
    ScenarioPreviewMsg,
    ScenarioReadyMsg,
    ScoringComponent,
    StartRunCmd,
    StateMsg,
    StrategyParam,
    parse_client_message,
)
from autocontext.server.run_manager import RunManager
from autocontext.storage import SQLiteStore
from autocontext.util.json_io import read_json

logger = logging.getLogger(__name__)
def _build_scenario_creator(app_settings: object) -> object | None:
    try:
        from autocontext.agents.llm_client import build_client_from_settings
        from autocontext.agents.subagent_runtime import SubagentRuntime
        from autocontext.scenarios.custom.creator import ScenarioCreator

        client = build_client_from_settings(app_settings)  # type: ignore[arg-type]
        runtime = SubagentRuntime(client)
        model = getattr(app_settings, "model_architect", "claude-sonnet-4-5-20250929")
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
    if migrations_dir.exists():
        store.migrate(migrations_dir)
    application.state.store = store
    application.state.migrations_dir = migrations_dir
    scenario_creator = _build_scenario_creator(app_settings)

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
        replay_dir = app_settings.runs_root / run_id / "generations" / f"gen_{generation}" / "replays"
        replay_files = sorted(replay_dir.glob("*.json"))
        if not replay_files:
            raise HTTPException(status_code=404, detail=f"No replay files found under {replay_dir}")
        return replay_files[0]

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/api/runs")
    def list_runs() -> list[dict[str, Any]]:
        return store.list_runs(limit=50)  # type: ignore[return-value]

    @application.get("/api/runs/{run_id}/status")
    def run_status(run_id: str) -> list[dict[str, Any]]:
        return store.run_status(run_id)

    @application.get("/api/runs/{run_id}/replay/{generation}")
    def replay(run_id: str, generation: int) -> dict[str, Any]:
        replay_path = _read_replay_file(run_id, generation)
        payload = read_json(replay_path)
        if not isinstance(payload, dict):
            raise HTTPException(status_code=500, detail="replay payload is not a JSON object")
        return payload

    @application.websocket("/ws/events")
    async def ws_events(websocket: WebSocket) -> None:
        await websocket.accept()
        cursor = 0
        try:
            while True:
                if app_settings.event_stream_path.exists():
                    content = app_settings.event_stream_path.read_text(encoding="utf-8")
                    lines = content.splitlines()
                    while cursor < len(lines):
                        line = lines[cursor].strip()
                        cursor += 1
                        if not line:
                            continue
                        await websocket.send_text(line)
                await asyncio.sleep(0.5)
        except WebSocketDisconnect:
            return

    @application.websocket("/ws/interactive")
    async def ws_interactive(websocket: WebSocket) -> None:
        await websocket.accept()

        # Protocol version handshake -- always first message
        await websocket.send_json(HelloMsg().model_dump())

        if controller is None or events is None:
            await websocket.send_json(ErrorMsg(message="Interactive mode not available. Start with 'autoctx tui'.").model_dump())
            await websocket.close()
            return

        # Send environment info on connect (scenarios, executors, provider)
        if run_manager:
            env_info = run_manager.get_environment_info()
            await websocket.send_json(_build_environments_msg(env_info).model_dump())

        send_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        event_loop = asyncio.get_event_loop()

        def _on_event(event: str, payload: dict[str, Any]) -> None:
            msg = EventMsg(event=event, payload=payload)
            event_loop.call_soon_threadsafe(send_queue.put_nowait, msg.model_dump())

        events.subscribe(_on_event)

        # Per-websocket pending scenario state
        pending_spec: dict[str, Any] = {}

        try:
            # Task to push events to client
            async def push_events() -> None:
                while True:
                    msg = await send_queue.get()
                    await websocket.send_json(msg)

            push_task = asyncio.create_task(push_events())

            # Listen for commands from client
            try:
                while True:
                    data = await websocket.receive_json()

                    try:
                        cmd = parse_client_message(data)
                    except ValidationError:
                        await websocket.send_json(
                            ErrorMsg(message=f"Unknown or invalid message type: {data.get('type', '?')}").model_dump()
                        )
                        continue

                    match cmd:
                        case PauseCmd():
                            controller.pause()
                            await websocket.send_json(StateMsg(paused=True).model_dump())

                        case ResumeCmd():
                            controller.resume()
                            await websocket.send_json(StateMsg(paused=False).model_dump())

                        case InjectHintCmd(text=text):
                            if text:
                                controller.inject_hint(text)
                                await websocket.send_json(AckMsg(action="inject_hint").model_dump())

                        case OverrideGateCmd(decision=decision):
                            controller.set_gate_override(decision)
                            await websocket.send_json(AckMsg(action="override_gate", decision=decision).model_dump())

                        case ChatAgentCmd(role=role, message=message):
                            if role and message:
                                response = await asyncio.to_thread(controller.submit_chat, role, message)
                                await websocket.send_json(ChatResponseMsg(role=role, text=response).model_dump())

                        case StartRunCmd(scenario=scenario, generations=generations):
                            if run_manager is None:
                                await websocket.send_json(ErrorMsg(message="Run manager not available.").model_dump())
                            elif run_manager.is_active:
                                await websocket.send_json(ErrorMsg(message="A run is already active.").model_dump())
                            else:
                                try:
                                    rid = run_manager.start_run(scenario, generations)
                                    await websocket.send_json(
                                        RunAcceptedMsg(run_id=rid, scenario=scenario, generations=generations).model_dump()
                                    )
                                except (ValueError, RuntimeError) as exc:
                                    await websocket.send_json(ErrorMsg(message=str(exc)).model_dump())

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
                                spec = await asyncio.to_thread(creator.generate_spec, description)
                                pending_spec["current"] = spec
                                await websocket.send_json(_build_scenario_preview_msg(spec).model_dump())
                            except Exception as exc:
                                logger.warning("scenario generation failed", exc_info=True)
                                await websocket.send_json(
                                    ScenarioErrorMsg(message=str(exc), stage="generation").model_dump()
                                )

                        case ConfirmScenarioCmd():
                            current_spec = pending_spec.get("current")
                            if current_spec is None:
                                await websocket.send_json(
                                    ScenarioErrorMsg(
                                        message="No pending scenario to confirm.", stage="validation"
                                    ).model_dump()
                                )
                                continue

                            from autocontext.scenarios import SCENARIO_REGISTRY
                            from autocontext.scenarios.custom.creator import ScenarioCreator
                            creator = scenario_creator  # type: ignore[assignment]

                            try:
                                build_result = await asyncio.to_thread(creator.build_and_validate, current_spec)
                                SCENARIO_REGISTRY[current_spec.name] = build_result.scenario_class
                                pending_spec.clear()

                                await websocket.send_json(
                                    ScenarioReadyMsg(name=current_spec.name, test_scores=build_result.test_scores).model_dump()
                                )

                                if run_manager:
                                    env_info = run_manager.get_environment_info()
                                    await websocket.send_json(_build_environments_msg(env_info).model_dump())
                            except Exception as exc:
                                logger.warning("scenario build/validate failed", exc_info=True)
                                await websocket.send_json(
                                    ScenarioErrorMsg(message=str(exc), stage="validation").model_dump()
                                )

                        case ReviseScenarioCmd(feedback=feedback):
                            current_spec = pending_spec.get("current")
                            if current_spec is None:
                                await websocket.send_json(
                                    ScenarioErrorMsg(
                                        message="No pending scenario to revise.", stage="generation"
                                    ).model_dump()
                                )
                                continue

                            if not feedback:
                                continue

                            from autocontext.scenarios.custom.creator import ScenarioCreator
                            creator = scenario_creator  # type: ignore[assignment]

                            try:
                                revised = await asyncio.to_thread(creator.revise_spec, current_spec, feedback)
                                pending_spec["current"] = revised
                                await websocket.send_json(_build_scenario_preview_msg(revised).model_dump())
                            except Exception as exc:
                                logger.warning("scenario revision failed", exc_info=True)
                                await websocket.send_json(
                                    ScenarioErrorMsg(message=str(exc), stage="generation").model_dump()
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
