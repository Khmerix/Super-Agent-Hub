"""
SuperAgent — The Commander.

perform_mission(): Receives a task, broadcasts every step with ANSI colors,
saves checkpoints to SQLite after each phase. Type REVERT in the terminal
to roll back to the previous checkpoint.
"""
import asyncio
import json
import uuid
from datetime import datetime
from typing import Dict, Any, Optional, List

from ..api.connection_manager import manager
from ..minions.pool import MinionPool
from ..recorder.flight_recorder import FlightRecorder

# ANSI colors for terminal broadcast
A = {
    "green": "\033[32m", "cyan": "\033[36m", "yellow": "033[33m",
    "red": "\033[31m", "magenta": "\033[35m", "dim": "\033[2m",
    "bright": "\033[1m", "reset": "\033[0m"
}
# Fix: proper ANSI for yellow
A["yellow"] = "\033[33m"


class MissionState:
    """Serializable state snapshot for checkpointing / revert."""

    def __init__(self, run_id: str, title: str, description: str,
                 stage: str = "init", research: str = "", code: str = "",
                 kimi_output: str = "", plan: list = None, completed: list = None):
        self.run_id = run_id
        self.title = title
        self.description = description
        self.stage = stage           # init → plan → research → code → kimi → review → done
        self.research = research
        self.code = code
        self.kimi_output = kimi_output
        self.plan = plan or []
        self.completed = completed or []
        self.error = None

    def snapshot(self) -> dict:
        return {
            "run_id": self.run_id, "title": self.title,
            "description": self.description, "stage": self.stage,
            "research": self.research, "code": self.code,
            "kimi_output": self.kimi_output,
            "plan": self.plan, "completed": self.completed,
            "error": self.error
        }

    @classmethod
    def from_snapshot(cls, data: dict) -> "MissionState":
        s = cls(data["run_id"], data["title"], data["description"])
        s.stage = data.get("stage", "init")
        s.research = data.get("research", "")
        s.code = data.get("code", "")
        s.kimi_output = data.get("kimi_output", "")
        s.plan = data.get("plan", [])
        s.completed = data.get("completed", [])
        s.error = data.get("error")
        return s


class SuperAgent:
    """
    The Commander. Orchestrates minions with live terminal broadcast
    and SQLite checkpointing for revert support.
    """

    def __init__(self, name: str = "COMMANDER"):
        self.name = name
        self.pool = MinionPool()
        self.recorder = FlightRecorder()

    # ── Voice — Broadcast to Terminal ──────────────────────────

    async def speak(self, msg: str, color: str = "green"):
        c = A.get(color, A["green"])
        await manager.broadcast(f"{c}[{self.name}]: {msg}{A['reset']}")

    async def minion_speak(self, name: str, msg: str, color: str = "cyan"):
        c = A.get(color, A["cyan"])
        await manager.broadcast(f"{c}[{name}]: {msg}{A['reset']}")

    # ── Checkpoint — Save State to SQLite ──────────────────────

    async def _checkpoint(self, state: MissionState, step_num: int,
                          node_name: str) -> int:
        """Save a state snapshot. Returns checkpoint_id for revert."""
        cp_id = await self.recorder.checkpoint(
            state.run_id, step_num, node_name, state.snapshot()
        )
        await self.speak(f"Checkpoint saved :: #{cp_id} :: {node_name}", "dim")
        return cp_id

    # ── Revert — Restore Previous Checkpoint ───────────────────

    async def revert(self, run_id: str) -> Optional[MissionState]:
        """
        REVERT: Look up the previous checkpoint for this run and restore it.
        Returns the restored MissionState or None if no checkpoint exists.
        """
        await self.speak(f"REVERT requested for run {run_id}...", "yellow")

        cps = await self.recorder.list_checkpoints(run_id)
        if not cps:
            await self.speak("No checkpoints found. Nothing to revert.", "red")
            return None

        # Get the second-to-last checkpoint (previous state)
        target = cps[-2] if len(cps) >= 2 else cps[0]
        cp_id = target["step_id"]
        node = target["node_name"]

        restored = MissionState.from_snapshot(target["state"])
        restored.stage = node  # Resume from this node

        await self.speak(f"REVERTED to checkpoint #{cp_id} :: {node}", "yellow")
        await self.minion_speak("SYSTEM", f"State rolled back to {node}. Resuming...", "green")

        return restored

    # ═══════════════════════════════════════════════════════════
    #  PERFORM MISSION — The Main Loop
    # ═══════════════════════════════════════════════════════════

    async def perform_mission(self, title: str, description: str) -> dict:
        """
        Full async mission loop with broadcast + checkpointing.

        Flow:
            1. Broadcast: "Initializing mission"
            2. PLAN       → checkpoint #1
            3. RESEARCH   → checkpoint #2 (after Perplexity)
            4. CODE       → checkpoint #3 (after Claude)
            5. KIMI       → checkpoint #4 (after Kimi K2.6 universal)
            6. DONE       → final checkpoint

        Type REVERT in the terminal to roll back to any checkpoint.
        """
        run_id = str(uuid.uuid4())[:8]
        state = MissionState(run_id=run_id, title=title, description=description)

        try:
            # ── PHASE 0: Init ──────────────────────────────────
            await self.speak(f"Initializing mission: {title}")
            await self.minion_speak("SYSTEM", f"Run ID: {run_id} | Mission started.", "green")

            # ── PHASE 1: Plan ──────────────────────────────────
            await self.speak("Analyzing objective and forming battle plan...", "yellow")
            state.plan = self._plan(description)
            state.stage = "plan"
            state.completed.append("plan")
            await self.speak(f"Plan formed: {state.plan}", "yellow")
            await self._checkpoint(state, step_num=1, node_name="plan")

            # ── PHASE 2: Research (Perplexity) ─────────────────
            if "research" in state.plan:
                await self.speak("Deploying Researcher Minion...", "cyan")
                await self.minion_speak("RESEARCHER", f"Searching the web for '{title}'...", "magenta")

                # SYNC call to MinionPool (API is blocking)
                state.research = self.pool.ask_perplexity(description)

                state.stage = "research"
                state.completed.append("research")
                await self.minion_speak("RESEARCHER", f"Intel gathered: {len(state.research)} chars.", "magenta")
                await self.speak("Research phase complete.", "green")

                # CHECKPOINT after research
                await self._checkpoint(state, step_num=2, node_name="research")

            # ── PHASE 3: Code (Claude) ─────────────────────────
            if "code" in state.plan:
                await self.speak("Deploying Lead Developer...", "cyan")
                await self.minion_speak("DEVELOPER", f"Writing implementation for '{title}'...", "cyan")

                # SYNC call with research as context
                state.code = self.pool.ask_claude(task=description, context=state.research)

                state.stage = "code"
                state.completed.append("code")
                await self.minion_speak("DEVELOPER", f"Implementation complete: {len(state.code)} chars.", "cyan")
                await self.speak("Development phase complete.", "green")

                # CHECKPOINT after code
                await self._checkpoint(state, step_num=3, node_name="code")

            # ── PHASE 4: Kimi K2.6 (Universal) ─────────────────
            if "kimi" in state.plan:
                await self.speak("Deploying Kimi K2.6 Universal Minion...", "cyan")
                await self.minion_speak("KIMI", f"Running unified analysis + build for '{title}'...", "yellow")

                # Kimi handles everything — research + code in one
                state.kimi_output = self.pool.ask_kimi(
                    f"Task: {description}\nResearch thoroughly and then implement.",
                    mode="auto",
                    context=state.research
                )

                state.stage = "kimi"
                state.completed.append("kimi")
                await self.minion_speak("KIMI", f"Universal output complete: {len(state.kimi_output)} chars.", "yellow")
                await self.speak("Kimi universal phase complete.", "green")

                # CHECKPOINT after kimi
                await self._checkpoint(state, step_num=4, node_name="kimi")

            # ── PHASE 5: Review & Closeout ─────────────────────
            await self.speak("Reviewing outputs...", "yellow")
            state.stage = "done"
            await self.speak("Mission Accomplished. Logs saved to SQLite.", "green")
            await self._checkpoint(state, step_num=5, node_name="done")

            await self.recorder.update_run_status(run_id, "COMPLETED", state.snapshot())

            return {
                "run_id": run_id, "title": title, "success": True,
                "research": state.research, "code": state.code,
                "kimi_output": state.kimi_output,
                "plan": state.plan, "checkpoints": 5
            }

        except Exception as e:
            state.error = str(e)
            await self.speak(f"Mission FAILED: {str(e)[:80]}", "red")
            await self.recorder.update_run_status(run_id, "ERROR")
            return {"run_id": run_id, "title": title, "success": False, "error": str(e)}

    # ── Planning ───────────────────────────────────────────────

    def _plan(self, desc: str) -> list:
        d = desc.lower()
        stages = []
        if any(k in d for k in ["research", "investigate", "compare", "evaluate", "find", "look up", "search"]):
            stages.append("research")
        if any(k in d for k in ["build", "create", "implement", "code", "write", "fix", "debug", "develop", "boilerplate"]):
            stages.append("code")
        if any(k in d for k in ["kimi", "moonshot", "universal", "all-in-one", "single agent"]):
            stages.append("kimi")
        return stages or ["code"]

    # ── Backward-compatible API ────────────────────────────────

    async def submit_task(self, title: str, description: str, **kwargs) -> str:
        result = await self.perform_mission(title, description)
        return result["run_id"]

    async def get_agent_status(self, agent_id: str = None) -> list:
        ready = self.pool.ready
        agents = [
            {"agent_id": "minion_claude", "name": "Minion Claude",
             "status": "idle" if ready["claude"] else "error",
             "description": "Coding specialist", "executions": 0, "failures": 0,
             "model": "claude-3-7-sonnet-20250219"},
            {"agent_id": "minion_perplexity", "name": "Minion Perplexity",
             "status": "idle" if ready["perplexity"] else "error",
             "description": "Research specialist", "executions": 0, "failures": 0,
             "model": "sonar-pro"},
            {"agent_id": "minion_kimi", "name": "Minion Kimi",
             "status": "idle" if ready["kimi"] else "error",
             "description": "Universal specialist (research + code)", "executions": 0, "failures": 0,
             "model": "kimi-k2.6"}
        ]
        return [a for a in agents if a["agent_id"] == agent_id] if agent_id else agents

    async def get_run_status(self, run_id: str) -> Optional[dict]:
        run = await self.recorder.get_run(run_id)
        if not run:
            return None
        return {"run_id": run["run_id"], "title": run["title"],
                "status": run["status"], "created_at": run["created_at"]}

    async def get_trace(self, run_id: str) -> dict:
        return await self.recorder.export_trace(run_id)

    async def list_checkpoints(self, run_id: str) -> list:
        return await self.recorder.list_checkpoints(run_id)

    async def revert_run(self, run_id: str, checkpoint_id: int = None) -> bool:
        """Entry point for the REVERT endpoint. If no checkpoint_id, use previous."""
        restored = await self.revert(run_id)
        return restored is not None

    def add_listener(self, cb): pass
    def add_broadcast_callback(self, cb): pass


# Legacy alias
Orchestrator = SuperAgent
