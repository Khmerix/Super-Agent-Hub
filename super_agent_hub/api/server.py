"""
FastAPI backend for the Super Agent Hub.
Exposes: task submission, status polling, SSE streaming, agent status, WebSocket.
Ready to bridge with a 3.js / HTML / CSS frontend.
"""
import asyncio
import json
from typing import AsyncGenerator, Dict, Any, Optional, List
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, BackgroundTasks
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import os

from ..core.orchestrator import Orchestrator
from ..core.task import TaskPriority
from ..core.state_graph import AgentState
from ..recorder.flight_recorder import FlightRecorder
from ..minions.claude import MinionClaude
from ..minions.perplexity import MinionPerplexity

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
    checkpoint_id: int


# ── App factory ──────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: build orchestrator, register minions, warm up."""
    recorder = FlightRecorder()
    orch = Orchestrator(recorder=recorder)

    # Register minions (will run in mock mode if no API keys)
    orch.register_minion(MinionClaude(working_dir="./workspace"))
    orch.register_minion(MinionPerplexity())

    # Async warmup
    await orch.warmup_all()

    app.state.orch = orch
    app.state.recorder = recorder
    app.state.sse_queues: Dict[str, asyncio.Queue] = {}
    app.state.ws_clients: List[WebSocket] = []

    # Add SSE listener to orchestrator
    orch.add_listener(_make_sse_listener(app))

    yield

    # Shutdown
    for m in orch.minions.values():
        await m.shutdown()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Super Agent Hub",
        description="Multi-agent orchestration with LangGraph-style state machine",
        version="0.1.0",
        lifespan=lifespan
    )

    # Static frontend files
    _static_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend", "static")
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")

    # ── Routes ───────────────────────────────────────────────

    @app.get("/")
    async def root():
        return FileResponse(os.path.join(_static_dir, "index.html"))

    # ── Task Management ────────────────────────────────────────

    @app.post("/api/tasks", response_model=TaskResponse)
    async def submit_task(payload: TaskSubmit):
        """Submit a new complex task to the hub."""
        orch: Orchestrator = app.state.orch
        priority = TaskPriority[payload.priority.upper()]
        run_id = await orch.submit_task(
            title=payload.title,
            description=payload.description,
            priority=priority
        )
        return TaskResponse(run_id=run_id, status="PENDING", message="Task queued")

    @app.get("/api/tasks/{run_id}")
    async def get_task(run_id: str):
        """Get current status and state of a run."""
        orch: Orchestrator = app.state.orch
        status = await orch.get_run_status(run_id)
        if not status:
            return {"error": "Run not found"}
        return status

    @app.get("/api/tasks/{run_id}/stream")
    async def stream_task(run_id: str):
        """Server-Sent Events (SSE) stream of state changes."""
        orch: Orchestrator = app.state.orch
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
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no"
            }
        )

    # ── Agent Status ─────────────────────────────────────────

    @app.get("/api/agents/status")
    async def all_agent_status():
        """Status of all registered agents."""
        orch: Orchestrator = app.state.orch
        return {"agents": await orch.get_agent_status()}

    @app.get("/api/agents/{agent_id}/status")
    async def single_agent_status(agent_id: str):
        """Status of a specific agent."""
        orch: Orchestrator = app.state.orch
        agents = await orch.get_agent_status(agent_id)
        if not agents:
            return {"error": "Agent not found"}
        return agents[0]

    # ── Tracing & Revert ─────────────────────────────────────

    @app.get("/api/runs/{run_id}/trace")
    async def get_trace(run_id: str):
        """Full execution trace (checkpoints, messages, thoughts)."""
        orch: Orchestrator = app.state.orch
        trace = await orch.get_trace(run_id)
        return trace

    @app.get("/api/runs/{run_id}/checkpoints")
    async def get_checkpoints(run_id: str):
        """List all checkpoints for a run."""
        orch: Orchestrator = app.state.orch
        return {"checkpoints": await orch.list_checkpoints(run_id)}

    @app.post("/api/runs/{run_id}/revert")
    async def revert_run(run_id: str, req: RevertRequest):
        """Revert a run to a specific checkpoint."""
        orch: Orchestrator = app.state.orch
        ok = await orch.revert_run(run_id, req.checkpoint_id)
        return {"success": ok, "run_id": run_id, "checkpoint_id": req.checkpoint_id}

    # ── WebSocket ────────────────────────────────────────────

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        await ws.accept()
        app.state.ws_clients.append(ws)
        try:
            while True:
                data = await ws.receive_text()
                # Echo or handle commands from frontend
                msg = json.loads(data)
                if msg.get("action") == "status":
                    orch: Orchestrator = app.state.orch
                    agents = await orch.get_agent_status()
                    await ws.send_json({"type": "agents", "data": agents})
                elif msg.get("action") == "submit":
                    orch: Orchestrator = app.state.orch
                    run_id = await orch.submit_task(
                        msg["title"], msg["description"]
                    )
                    await ws.send_json({"type": "submitted", "run_id": run_id})
        except WebSocketDisconnect:
            app.state.ws_clients.remove(ws)
        except Exception as e:
            try:
                await ws.send_json({"type": "error", "message": str(e)})
            except:
                pass
            if ws in app.state.ws_clients:
                app.state.ws_clients.remove(ws)

    return app


# ── SSE Listener factory ─────────────────────────────────────

def _make_sse_listener(app: FastAPI):
    """Create an orchestrator listener that pushes to SSE queues."""
    async def listener(event: str, state: AgentState):
        payload = {
            "event": event,
            "run_id": state.run_id,
            "node": state.current_node,
            "completed": state._completed,
            "error": state.error,
            "timestamp": asyncio.get_event_loop().time(),
        }
        # Push to SSE queue for this run
        queue = app.state.sse_queues.get(state.run_id)
        if queue:
            await queue.put(payload)
        # Broadcast to all WebSocket clients
        dead = []
        for ws in app.state.ws_clients:
            try:
                await ws.send_json(payload)
            except:
                dead.append(ws)
        for ws in dead:
            if ws in app.state.ws_clients:
                app.state.ws_clients.remove(ws)

    return listener
