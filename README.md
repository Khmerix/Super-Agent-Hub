# 🤖 Super Agent Hub v2

**LangGraph-style multi-agent orchestration** with checkpoint/rollback, SQLite Flight Recorder, and FastAPI backend ready for Three.js / HTML / CSS frontend integration.

---

## Architecture

```
super_agent_hub/
├── core/
│   ├── state_graph.py          # LangGraph-inspired StateGraph + CompiledGraph
│   ├── orchestrator.py          # Central brain: nodes, edges, routing
│   └── task.py                  # Priority enum
├── minions/
│   ├── base.py                  # Abstract MinionAgent with AgentStatus
│   ├── claude.py                # Minion_Claude (coding specialist)
│   └── perplexity.py            # Minion_Perplexity (research specialist)
├── recorder/
│   └── flight_recorder.py       # SQLite audit log (runs, steps, messages, thoughts)
├── api/
│   └── server.py                # FastAPI: REST + SSE + WebSocket
├── frontend/static/
│   ├── index.html               # Frontend bridge (HTML/CSS/JS)
│   ├── css/style.css
│   └── js/main.js
└── main.py                      # Uvicorn entry point
```

---

## Core Features

### 1. State Machine (LangGraph-Style)

- **Nodes**: plan → route → claude / perplexity → review → end
- **Edges**: Conditional routing based on task analysis
- **Checkpoints**: SQLite-saved after every step
- **Revert**: `POST /api/runs/{id}/revert` restores previous checkpoint
- **Human-in-the-loop**: Interrupt and resume capability

### 2. Flight Recorder (SQLite)

| Table | Purpose |
|-------|---------|
| `runs` | Task executions with status |
| `steps` | State snapshots (checkpoints) |
| `messages` | All agent I/O |
| `thoughts` | Reasoning traces + tool chains |

Export trace: `GET /api/runs/{run_id}/trace`

### 3. Agent Status (for UI Bridge)

Each minion reports:
- `idle` — waiting
- `thinking` — LLM inference
- `working` — tool execution
- `error` — faulted

Endpoint: `GET /api/agents/status`

### 4. Mock Minions (Plug-in Ready)

Both minions run in **mock mode** when no API key is set, returning realistic placeholder responses so you can build the UI and orchestration logic immediately.

**Configure Claude:**
```python
minion = MinionClaude()
minion.configure(api_key="sk-ant-...", model="claude-3-5-sonnet-20241022")
await minion.warmup()
```

**Configure Perplexity:**
```python
minion = MinionPerplexity()
minion.configure(api_key="pplx-...", model="sonar-pro")
await minion.warmup()
```

---

## Quick Start

```bash
pip install -r requirements.txt

# Set keys (optional — mock mode works without them)
export ANTHROPIC_API_KEY="sk-ant-..."
export PERPLEXITY_API_KEY="pplx-..."

# Start server
python -m super_agent_hub.main
# or
uvicorn super_agent_hub.main:app --reload --port 8000
```

Open `http://localhost:8000/` for the frontend dashboard.

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/tasks` | Submit a new task |
| GET | `/api/tasks/{run_id}` | Task status & state |
| GET | `/api/tasks/{run_id}/stream` | SSE live events |
| GET | `/api/agents/status` | All agent statuses |
| GET | `/api/agents/{id}/status` | Single agent status |
| GET | `/api/runs/{run_id}/trace` | Full execution trace |
| GET | `/api/runs/{run_id}/checkpoints` | List checkpoints |
| POST | `/api/runs/{run_id}/revert` | Revert to checkpoint |
| WS | `/ws` | WebSocket live feed |

---

## Programmatic Usage

```python
import asyncio
from super_agent_hub.core import Orchestrator, TaskPriority
from super_agent_hub.minions import MinionClaude, MinionPerplexity

async def main():
    orch = Orchestrator()
    
    claude = MinionClaude()
    claude.configure(api_key="sk-ant-...")
    await orch.register_minion(claude)
    
    perplexity = MinionPerplexity()
    perplexity.configure(api_key="pplx-...")
    await orch.register_minion(perplexity)
    
    await orch.warmup_all()
    
    run_id = await orch.submit_task(
        title="Build auth API",
        description="Research JWT best practices, then implement FastAPI auth",
        priority=TaskPriority.HIGH
    )
    
    # Poll or use SSE/WebSocket for updates
    import time
    time.sleep(10)
    
    status = await orch.get_run_status(run_id)
    print(status)

asyncio.run(main())
```

---

## Reverting State

```bash
# Get checkpoints
curl http://localhost:8000/api/runs/a1b2c3d4/checkpoints

# Revert to checkpoint #5
curl -X POST http://localhost:8000/api/runs/a1b2c3d4/revert \
  -H "Content-Type: application/json" \
  -d '{"checkpoint_id": 5}'
```

---

## Frontend Bridge

The static files in `frontend/static/` provide a ready-to-use dashboard that:
- Polls agent status every 3s
- Accepts task submissions via REST
- Receives live events via WebSocket
- Displays runs, checkpoints, and logs

Swap in Three.js by replacing `index.html` — all API endpoints remain the same.

---

## License

MIT
