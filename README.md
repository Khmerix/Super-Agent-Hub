# 🤖 Super Agent Hub v2

**LangGraph-style multi-agent orchestration** with checkpoint/rollback, SQLite Flight Recorder, and FastAPI backend.
Supports Claude, Perplexity, and Kimi K2.6 minions.

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
│   ├── perplexity.py            # Minion_Perplexity (research specialist)
│   ├── kimi.py                  # Minion_Kimi K2.6 (universal specialist)
│   └── pool.py                  # Direct API pool for all minions
├── recorder/
│   └── flight_recorder.py       # SQLite audit log (runs, steps, messages, thoughts)
├── api/
│   ├── connection_manager.py    # WebSocket broadcast hub
│   └── server.py                # FastAPI: REST + SSE + WebSocket
├── frontend/static/
│   ├── index.html               # Dashboard (HTML/CSS/JS)
│   ├── terminal.html            # xterm.js terminal
│   ├── css/style.css
│   └── js/main.js
├── main.py                      # Uvicorn entry point
└── __init__.py                  # Package init
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
# On Windows PowerShell:
.\venv\Scripts\Activate.ps1

# On Windows CMD:
# .\venv\Scripts\activate.bat

pip install -r requirements.txt

# Set keys (optional — mock mode works without them)
$env:ANTHROPIC_API_KEY="sk-ant-..."
$env:PERPLEXITY_API_KEY="pplx-..."
$env:MOONSHOT_API_KEY="sk-..."

# Start server
python -m super_agent_hub.main
# or
uvicorn super_agent_hub.main:app --reload --port 8000
```

Open `http://localhost:8000/` for the frontend dashboard.
Open `http://localhost:8000/terminal` for the terminal view.

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
from super_agent_hub.core import SuperAgent, TaskPriority

async def main():
    agent = SuperAgent(name="COMMANDER")
    
    run_id = await agent.submit_task(
        title="Build auth API",
        description="Research JWT best practices, then implement FastAPI auth",
        priority=TaskPriority.HIGH
    )
    
    status = await agent.get_run_status(run_id)
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
- Receives live events via WebSocket + SSE
- Displays runs, checkpoints, and logs

Two views available:
- `/` — Main dashboard with agent cards, mission dispatch, and telemetry
- `/terminal` — xterm.js-based terminal with command-line interface (REVERT, status, trace, etc.)

---

## License

MIT
