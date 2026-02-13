from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from mts.config import load_settings
from mts.loop.controller import LoopController
from mts.loop.events import EventStreamEmitter
from mts.server.knowledge_api import router as knowledge_router
from mts.server.run_manager import RunManager
from mts.storage import SQLiteStore

LOGGER = logging.getLogger(__name__)


def _dashboard_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "dashboard"


def _build_scenario_creator(app_settings: object) -> object | None:
    try:
        from mts.agents.llm_client import AnthropicClient, DeterministicDevClient, LanguageModelClient
        from mts.agents.subagent_runtime import SubagentRuntime
        from mts.scenarios.custom.creator import ScenarioCreator

        provider = getattr(app_settings, "agent_provider", "deterministic")
        client: LanguageModelClient
        if provider == "deterministic":
            client = DeterministicDevClient()
        elif provider == "anthropic":
            api_key = getattr(app_settings, "anthropic_api_key", None)
            if not api_key:
                return None
            client = AnthropicClient(api_key)
        else:
            return None

        runtime = SubagentRuntime(client)
        model = getattr(app_settings, "model_architect", "claude-sonnet-4-5-20250929")
        knowledge_root = getattr(app_settings, "knowledge_root", Path("knowledge"))
        return ScenarioCreator(runtime=runtime, model=model, knowledge_root=knowledge_root)
    except Exception:
        LOGGER.warning("failed to initialize ScenarioCreator", exc_info=True)
        return None


def create_app(
    controller: LoopController | None = None,
    events: EventStreamEmitter | None = None,
    run_manager: RunManager | None = None,
) -> FastAPI:
    """Factory that creates the FastAPI app, optionally wired to a LoopController."""
    application = FastAPI(title="MTS Dashboard API", version="0.1.0")
    application.include_router(knowledge_router)
    app_settings = load_settings()
    store = SQLiteStore(app_settings.db_path)
    scenario_creator = _build_scenario_creator(app_settings)

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
    def list_runs() -> list[dict[str, object]]:
        with store.connect() as conn:
            rows = conn.execute(
                "SELECT run_id, scenario, target_generations, executor_mode, status, created_at "
                "FROM runs ORDER BY created_at DESC LIMIT 50"
            ).fetchall()
        return [dict(row) for row in rows]

    @application.get("/api/runs/{run_id}/status")
    def run_status(run_id: str) -> list[dict[str, object]]:
        with store.connect() as conn:
            rows = conn.execute(
                "SELECT generation_index, mean_score, best_score, elo, wins, losses, gate_decision, status "
                "FROM generations WHERE run_id = ? ORDER BY generation_index ASC",
                (run_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    @application.get("/api/runs/{run_id}/replay/{generation}")
    def replay(run_id: str, generation: int) -> dict[str, object]:
        replay_path = _read_replay_file(run_id, generation)
        payload = json.loads(replay_path.read_text(encoding="utf-8"))
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

        if controller is None or events is None:
            await websocket.send_json({
                "type": "error",
                "message": "Interactive mode not available. Start with 'mts tui'.",
            })
            await websocket.close()
            return

        # Send environment info on connect (scenarios, executors, provider)
        if run_manager:
            env_info = run_manager.get_environment_info()
            await websocket.send_json({
                "type": "environments",
                **env_info,
            })

        send_queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
        event_loop = asyncio.get_event_loop()

        def _on_event(event: str, payload: dict[str, object]) -> None:
            msg: dict[str, object] = {"type": "event", "event": event, "payload": payload}
            event_loop.call_soon_threadsafe(send_queue.put_nowait, msg)

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
                    msg_type = data.get("type", "")

                    if msg_type == "pause":
                        controller.pause()
                        await websocket.send_json({"type": "state", "paused": True})
                    elif msg_type == "resume":
                        controller.resume()
                        await websocket.send_json({"type": "state", "paused": False})
                    elif msg_type == "inject_hint":
                        text = data.get("text", "")
                        if text:
                            controller.inject_hint(text)
                            await websocket.send_json({"type": "ack", "action": "inject_hint"})
                    elif msg_type == "override_gate":
                        decision = data.get("decision", "")
                        if decision in ("advance", "retry", "rollback"):
                            controller.set_gate_override(decision)
                            await websocket.send_json({"type": "ack", "action": "override_gate", "decision": decision})
                    elif msg_type == "chat_agent":
                        role = data.get("role", "")
                        message = data.get("message", "")
                        if role and message:
                            response = await asyncio.to_thread(controller.submit_chat, role, message)
                            await websocket.send_json({"type": "chat_response", "role": role, "text": response})
                    elif msg_type == "start_run":
                        if run_manager is None:
                            await websocket.send_json({
                                "type": "error", "message": "Run manager not available.",
                            })
                        elif run_manager.is_active:
                            await websocket.send_json({
                                "type": "error", "message": "A run is already active.",
                            })
                        else:
                            scenario = data.get("scenario", "grid_ctf")
                            generations = int(data.get("generations", 5))
                            try:
                                rid = run_manager.start_run(scenario, generations)
                                await websocket.send_json({
                                    "type": "run_accepted",
                                    "run_id": rid,
                                    "scenario": scenario,
                                    "generations": generations,
                                })
                            except (ValueError, RuntimeError) as exc:
                                await websocket.send_json({"type": "error", "message": str(exc)})
                    elif msg_type == "list_scenarios":
                        if run_manager:
                            env_info = run_manager.get_environment_info()
                            await websocket.send_json({"type": "environments", **env_info})
                        else:
                            await websocket.send_json({"type": "environments", "scenarios": [], "executors": []})

                    # --- Custom scenario creation handlers ---
                    elif msg_type == "create_scenario":
                        if scenario_creator is None:
                            await websocket.send_json({
                                "type": "scenario_error", "message": "Scenario creator not available.", "stage": "generation",
                            })
                            continue
                        description = data.get("description", "")
                        if not description:
                            await websocket.send_json({
                                "type": "scenario_error", "message": "Description is required.", "stage": "generation",
                            })
                            continue

                        from mts.scenarios.custom.creator import ScenarioCreator
                        creator: ScenarioCreator = scenario_creator  # type: ignore[assignment]
                        name = creator.derive_name(description)
                        await websocket.send_json({"type": "scenario_generating", "name": name})

                        try:
                            spec = await asyncio.to_thread(creator.generate_spec, description)
                            pending_spec["current"] = spec
                            params = [{"name": p.name, "description": p.description} for p in spec.strategy_params]
                            scoring = [
                                {
                                    "name": s.name, "description": s.description,
                                    "weight": spec.final_score_weights.get(s.name, 0.0),
                                }
                                for s in spec.scoring_components
                            ]
                            constraints = [f"{c.expression} {c.operator} {c.threshold}" for c in spec.constraints]
                            await websocket.send_json({
                                "type": "scenario_preview",
                                "name": spec.name,
                                "display_name": spec.display_name,
                                "description": spec.description,
                                "strategy_params": params,
                                "scoring_components": scoring,
                                "constraints": constraints,
                                "win_threshold": spec.win_threshold,
                            })
                        except Exception as exc:
                            LOGGER.warning("scenario generation failed", exc_info=True)
                            await websocket.send_json({
                                "type": "scenario_error", "message": str(exc), "stage": "generation",
                            })

                    elif msg_type == "confirm_scenario":
                        current_spec = pending_spec.get("current")
                        if current_spec is None:
                            await websocket.send_json({
                                "type": "scenario_error", "message": "No pending scenario to confirm.", "stage": "validation",
                            })
                            continue

                        from mts.scenarios import SCENARIO_REGISTRY
                        from mts.scenarios.custom.creator import ScenarioCreator
                        creator = scenario_creator  # type: ignore[assignment]

                        try:
                            build_result = await asyncio.to_thread(creator.build_and_validate, current_spec)
                            SCENARIO_REGISTRY[current_spec.name] = build_result.scenario_class
                            pending_spec.clear()

                            await websocket.send_json({
                                "type": "scenario_ready",
                                "name": current_spec.name,
                                "test_scores": build_result.test_scores,
                            })

                            if run_manager:
                                env_info = run_manager.get_environment_info()
                                await websocket.send_json({"type": "environments", **env_info})
                        except Exception as exc:
                            LOGGER.warning("scenario build/validate failed", exc_info=True)
                            await websocket.send_json({
                                "type": "scenario_error", "message": str(exc), "stage": "validation",
                            })

                    elif msg_type == "revise_scenario":
                        current_spec = pending_spec.get("current")
                        if current_spec is None:
                            await websocket.send_json({
                                "type": "scenario_error", "message": "No pending scenario to revise.", "stage": "generation",
                            })
                            continue

                        feedback = data.get("feedback", "")
                        if not feedback:
                            continue

                        from mts.scenarios.custom.creator import ScenarioCreator
                        creator = scenario_creator  # type: ignore[assignment]

                        try:
                            revised = await asyncio.to_thread(creator.revise_spec, current_spec, feedback)
                            pending_spec["current"] = revised
                            params = [{"name": p.name, "description": p.description} for p in revised.strategy_params]
                            scoring = [
                                {
                                    "name": s.name, "description": s.description,
                                    "weight": revised.final_score_weights.get(s.name, 0.0),
                                }
                                for s in revised.scoring_components
                            ]
                            constraints = [f"{c.expression} {c.operator} {c.threshold}" for c in revised.constraints]
                            await websocket.send_json({
                                "type": "scenario_preview",
                                "name": revised.name,
                                "display_name": revised.display_name,
                                "description": revised.description,
                                "strategy_params": params,
                                "scoring_components": scoring,
                                "constraints": constraints,
                                "win_threshold": revised.win_threshold,
                            })
                        except Exception as exc:
                            LOGGER.warning("scenario revision failed", exc_info=True)
                            await websocket.send_json({
                                "type": "scenario_error", "message": str(exc), "stage": "generation",
                            })

                    elif msg_type == "cancel_scenario":
                        pending_spec.clear()

            except WebSocketDisconnect:
                pass
            finally:
                push_task.cancel()
        finally:
            events.unsubscribe(_on_event)

    dashboard = _dashboard_dir()
    if dashboard.exists():
        application.mount("/dashboard", StaticFiles(directory=dashboard, html=True), name="dashboard")

        @application.get("/")
        def root() -> FileResponse:
            index = dashboard / "index.html"
            if not index.exists():
                raise HTTPException(status_code=404, detail="dashboard/index.html not found")
            return FileResponse(index)

    return application


# Module-level app for backward compatibility (mts serve)
app = create_app()
