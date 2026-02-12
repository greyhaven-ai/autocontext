from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from mts.config import load_settings
from mts.storage import SQLiteStore

app = FastAPI(title="MTS Dashboard API", version="0.1.0")
settings = load_settings()
store = SQLiteStore(settings.db_path)


def _dashboard_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "dashboard"


def _read_replay_file(run_id: str, generation: int) -> Path:
    replay_dir = settings.runs_root / run_id / "generations" / f"gen_{generation}" / "replays"
    replay_files = sorted(replay_dir.glob("*.json"))
    if not replay_files:
        raise HTTPException(status_code=404, detail=f"No replay files found under {replay_dir}")
    return replay_files[0]


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/runs")
def list_runs() -> list[dict[str, object]]:
    with store.connect() as conn:
        rows = conn.execute(
            "SELECT run_id, scenario, target_generations, executor_mode, status, created_at "
            "FROM runs ORDER BY created_at DESC LIMIT 50"
        ).fetchall()
    return [dict(row) for row in rows]


@app.get("/api/runs/{run_id}/status")
def run_status(run_id: str) -> list[dict[str, object]]:
    with store.connect() as conn:
        rows = conn.execute(
            "SELECT generation_index, mean_score, best_score, elo, wins, losses, gate_decision, status "
            "FROM generations WHERE run_id = ? ORDER BY generation_index ASC",
            (run_id,),
        ).fetchall()
    return [dict(row) for row in rows]


@app.get("/api/runs/{run_id}/replay/{generation}")
def replay(run_id: str, generation: int) -> dict[str, object]:
    replay_path = _read_replay_file(run_id, generation)
    payload = json.loads(replay_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise HTTPException(status_code=500, detail="replay payload is not a JSON object")
    return payload


@app.websocket("/ws/events")
async def ws_events(websocket: WebSocket) -> None:
    await websocket.accept()
    cursor = 0
    try:
        while True:
            if settings.event_stream_path.exists():
                content = settings.event_stream_path.read_text(encoding="utf-8")
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


dashboard_dir = _dashboard_dir()
if dashboard_dir.exists():
    app.mount("/dashboard", StaticFiles(directory=dashboard_dir, html=True), name="dashboard")


@app.get("/")
def root() -> FileResponse:
    index = dashboard_dir / "index.html"
    if not index.exists():
        raise HTTPException(status_code=404, detail="dashboard/index.html not found")
    return FileResponse(index)
