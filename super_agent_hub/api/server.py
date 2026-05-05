"""
FastAPI backend for the Super Agent Hub.
Handles REST, SSE, and WebSocket — including REVERT command from terminal.
"""
import asyncio
import json
from typing import AsyncGenerator, Dict, Any, Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import os

from ..core.orchestrator import SuperAgent
from ..api.connection_manager import manager

# ── Pydantic models ──────────────────────────────────────────

class TaskSubmit(BaseModel):
    title: str
    description: str
    priority: str = "NORMAL"


class TaskResponse(BaseModel):
    run_id: str
    status: str
    message: str


class RevertRequest(BaseModel):
    checkpoint_id: int = 0  # 0 = auto (previous checkpoint)


# ── App factory ──────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: build SuperAgent Commander."""
    agent = SuperAgent(name="COMMANDER")
    app.state.agent = agent
    app.state.sse_queues: Dict[str, asyncio.Queue] = {}

    yield

    print("[Server] Shutdown complete.")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Super Agent Hub",
        description="3D Holographic Multi-Agent Orchestration",
        version="0.3.0",
        lifespan=lifespan
    )

    # Static frontend files
    _static_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend", "static")
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")

    # ── Routes ───────────────────────────────────────────────

    @app.get("/")
    async def root():
        return FileResponse(os.path.join(_static_dir, "index.html"))

    @app.get("/terminal")
    async def terminal():
        return FileResponse(os.path.join(_static_dir, "terminal.html"))

    # ── Task Management ────────────────────────────────────────

    @app.post("/api/tasks", response_model=TaskResponse)
    async def submit_task(payload: TaskSubmit):
        """Submit a new mission to the Commander."""
        agent: SuperAgent = app.state.agent
        run_id = await agent.submit_task(title=payload.title, description=payload.description)
        return TaskResponse(run_id=run_id, status="PENDING", message="Mission queued")

    @app.get("/api/tasks/{run_id}")
    async def get_task(run_id: str):
        agent: SuperAgent = app.state.agent
        status = await agent.get_run_status(run_id)
        if not status:
            return {"error": "Run not found"}
        return status

    @app.get("/api/tasks/{run_id}/stream")
    async def stream_task(run_id: str):
        """SSE stream of state changes."""
        queue: asyncio.Queue = asyncio.Queue()
        app.state.sse_queues[run_id] = queue

        async def event_generator() -> AsyncGenerator[str, None]:
            try:
                while True:
                    event = await asyncio.wait_for(queue.get(), timeout=300)
                    yield f"data: {json.dumps(event)}\n\n"
                    if event.get("event") == "complete":
                        break
            except asyncio.TimeoutError:
                yield f"data: {json.dumps({'event': 'timeout'})}\n\n"
            finally:
                app.state.sse_queues.pop(run_id, None)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"}
        )

    # ── Agent Status ─────────────────────────────────────────

    @app.get("/api/agents/status")
    async def all_agent_status():
        agent: SuperAgent = app.state.agent
        return {"agents": await agent.get_agent_status()}

    # ── Tracing & Revert ─────────────────────────────────────

    @app.get("/api/runs/{run_id}/trace")
    async def get_trace(run_id: str):
        agent: SuperAgent = app.state.agent
        return await agent.get_trace(run_id)

    @app.get("/api/runs/{run_id}/checkpoints")
    async def get_checkpoints(run_id: str):
        agent: SuperAgent = app.state.agent
        return {"checkpoints": await agent.list_checkpoints(run_id)}

    @app.post("/api/runs/{run_id}/revert")
    async def revert_run(run_id: str, req: RevertRequest):
        """HTTP endpoint for revert."""
        agent: SuperAgent = app.state.agent
        restored = await agent.revert(run_id)
        if restored:
            return {
                "success": True,
                "run_id": run_id,
                "restored_stage": restored.stage,
                "message": f"Reverted to checkpoint :: stage={restored.stage}"
            }
        return {"success": False, "run_id": run_id, "message": "No checkpoints found"}

    # ── WebSocket (handles REVERT + all terminal commands) ─────

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        """
        Terminal WebSocket. Handles:
        - JSON commands: {"action": "status"}
        - Text agent chat: "[COMMANDER]: Hello"
        - REVERT command: "REVERT <run_id>" or {"action": "revert", "run_id": "abc123"}
        """
        await manager.connect(websocket)
        agent: SuperAgent = app.state.agent

        try:
            while True:
                raw = await websocket.receive_text()

                # ── Try JSON first ─────────────────────────────
                try:
                    msg = json.loads(raw)
                    action = msg.get("action", "")

                    if action == "status":
                        agents = await agent.get_agent_status()
                        await websocket.send_json({"type": "agents", "data": agents})

                    elif action == "submit":
                        run_id = await agent.submit_task(msg["title"], msg["description"])
                        await websocket.send_json({"type": "submitted", "run_id": run_id})

                    elif action == "revert":
                        rid = msg.get("run_id", "")
                        restored = await agent.revert(rid)
                        if restored:
                            await manager.broadcast(f"\033[33m[BOSS]: REVERTED run={rid} to stage={restored.stage}\033[0m")
                            await websocket.send_json({"type": "reverted", "run_id": rid, "stage": restored.stage})
                        else:
                            await manager.broadcast(f"\033[31m[SYSTEM]: REVERT failed for {rid} — no checkpoints\033[0m")
                            await websocket.send_json({"type": "error", "message": "No checkpoints found"})

                    continue  # Handled as JSON
                except json.JSONDecodeError:
                    pass  # Not JSON, treat as plain text

                # ── Plain text: check for REVERT command ───────
                import re
                revert_match = re.match(r'^REVERT\s+(\S+)$', raw, re.IGNORECASE)
                if revert_match:
                    rid = revert_match.group(1)
                    restored = await agent.revert(rid)
                    if restored:
                        await manager.broadcast(f"\033[33m[BOSS]: REVERTED run={rid} to stage={restored.stage}\033[0m")
                    else:
                        await manager.broadcast(f"\033[31m[SYSTEM]: REVERT failed — no checkpoints for {rid}\033[0m")
                    continue

                # ── Plain text: agent chat broadcast ────────────
                # Forward raw text (already ANSI-formatted from orchestrator)
                await manager.broadcast(raw)

        except WebSocketDisconnect:
            manager.disconnect(websocket)
        except Exception as e:
            print(f"[WS] Error: {e}")
            manager.disconnect(websocket)

    return app
