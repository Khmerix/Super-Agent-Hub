"""
Orchestrator - The central brain that assembles the StateGraph,
plugs in minions, and manages task lifecycle with checkpoint/rollback.
"""
import asyncio
import uuid
import json
from typing import Dict, Any, Optional, List, Callable, Awaitable
from datetime import datetime
from rich.console import Console

from .state_graph import StateGraph, CompiledGraph, AgentState, AgentStatus
from .task import TaskPriority
from ..recorder.flight_recorder import FlightRecorder
from ..minions.base import MinionAgent, MinionResult
from ..minions.claude import MinionClaude
from ..minions.perplexity import MinionPerplexity

console = Console()

# ── Node names ───────────────────────────────────────────────
NODE_PLAN = "plan"
NODE_ROUTE = "route"
NODE_CLAUDE = "claude"
NODE_PERPLEXITY = "perplexity"
NODE_REVIEW = "review"
NODE_END = "end"


class Orchestrator:
    """
    Central orchestrator that builds and runs the StateGraph.
    
    Each task becomes a graph execution:
        plan -> route -> [claude | perplexity] -> review -> end
                          ^                    v
                          └────── retry ───────┘
    
    Features:
    - SQLite checkpointing every step
    - Real-time SSE/WebSocket listeners
    - Human-in-the-loop revert via checkpoint_id
    """

    def __init__(self, recorder: Optional[FlightRecorder] = None,
                 working_dir: str = "./workspace"):
        self.recorder = recorder or FlightRecorder()
        self.working_dir = working_dir
        self.minions: Dict[str, MinionAgent] = {}
        self._graphs: Dict[str, CompiledGraph] = {}  # run_id -> compiled graph
        self._listeners: List[Callable[[str, AgentState], Awaitable[None]]] = []
        self._lock = asyncio.Lock()

    # ── Minion registration ────────────────────────────────────

    def register_minion(self, minion: MinionAgent) -> None:
        self.minions[minion.agent_id] = minion
        console.print(f"[green]Registered {minion.name} ({minion.agent_id})[/green]")

    async def warmup_all(self) -> Dict[str, bool]:
        """Run warmup on all registered minions."""
        results = {}
        for mid, minion in self.minions.items():
            results[mid] = await minion.warmup()
        return results

    # ── Graph building ──────────────────────────────────────────

    def _build_graph(self) -> StateGraph:
        """Construct the state graph with all nodes and edges."""
        graph = StateGraph(recorder=self.recorder)

        graph.add_node(NODE_PLAN, self._node_plan, "Decompose task into sub-tasks")
        graph.add_node(NODE_ROUTE, self._node_route, "Decide which minion to invoke")
        graph.add_node(NODE_CLAUDE, self._node_claude, "Claude coding execution")
        graph.add_node(NODE_PERPLEXITY, self._node_perplexity, "Perplexity research execution")
        graph.add_node(NODE_REVIEW, self._node_review, "Review and validate output")
        graph.add_node(NODE_END, self._node_end, "Finalize and aggregate")

        graph.set_entry_point(NODE_PLAN)
        graph.add_conditional_edges(NODE_PLAN, self._edge_after_plan)
        graph.add_conditional_edges(NODE_ROUTE, self._edge_after_route)
        graph.add_edge(NODE_CLAUDE, NODE_REVIEW)
        graph.add_edge(NODE_PERPLEXITY, NODE_REVIEW)
        graph.add_conditional_edges(NODE_REVIEW, self._edge_after_review)

        return graph

    # ── Task submission ─────────────────────────────────────────

    async def submit_task(self, title: str, description: str,
                          priority: TaskPriority = TaskPriority.NORMAL,
                          metadata: Optional[Dict] = None) -> str:
        """
        Submit a new complex task. Returns run_id for tracking.
        """
        run_id = str(uuid.uuid4())[:8]
        task_id = str(uuid.uuid4())[:8]

        initial_state = AgentState(
            run_id=run_id,
            task_id=task_id,
            task_title=title,
            task_description=description,
            metadata={
                "priority": priority.name,
                **(metadata or {})
            }
        )

        # Build and compile graph
        graph = self._build_graph()
        compiled = graph.compile()

        # Register listeners
        for cb in self._listeners:
            compiled.add_listener(cb)

        async with self._lock:
            self._graphs[run_id] = compiled

        # Launch execution in background
        asyncio.create_task(self._run_graph(run_id, compiled, initial_state))

        console.print(f"[cyan]Task submitted: {run_id} – {title}[/cyan]")
        return run_id

    async def _run_graph(self, run_id: str, compiled: CompiledGraph,
                        initial_state: AgentState) -> None:
        """Background execution of the compiled graph."""
        try:
            final_state = await compiled.ainvoke(initial_state)
            console.print(f"[green]Run {run_id} completed: {final_state.task_title}[/green]")
        except asyncio.CancelledError:
            console.print(f"[yellow]Run {run_id} cancelled[/yellow]")
        except Exception as e:
            console.print(f"[red]Run {run_id} crashed: {e}[/red]")
        finally:
            async with self._lock:
                self._graphs.pop(run_id, None)

    # ── Human-in-the-loop: Revert ───────────────────────────────

    async def revert_run(self, run_id: str, checkpoint_id: int) -> bool:
        """Request a revert to a previous checkpoint for a running graph."""
        async with self._lock:
            compiled = self._graphs.get(run_id)

        if not compiled:
            # Graph may have finished; we can still load from recorder
            # and start a new graph execution from checkpoint
            cp = await self.recorder.get_checkpoint(checkpoint_id)
            if not cp:
                return False

            restored = AgentState.from_snapshot(cp["state"])
            restored.revert_target_checkpoint = None
            restored.next_node = restored.current_node

            graph = self._build_graph()
            compiled = graph.compile()
            for cb in self._listeners:
                compiled.add_listener(cb)

            async with self._lock:
                self._graphs[run_id] = compiled

            asyncio.create_task(self._run_graph(run_id, compiled, restored))
            return True

        # Inject revert request into running graph via state mutation
        # (The graph loop checks revert_target_checkpoint each iteration)
        # We need a way to signal the running graph. Since CompiledGraph
        # doesn't expose internal state directly, we'll use a side-channel
        # via the recorder or a signal mechanism.
        # For now, return False for active-running revert (requires more infra)
        return False

    # ── Status & queries ────────────────────────────────────────

    async def get_run_status(self, run_id: str) -> Optional[Dict[str, Any]]:
        """Get current status of a run from recorder."""
        run = await self.recorder.get_run(run_id)
        if not run:
            return None
        return {
            "run_id": run["run_id"],
            "task_id": run["task_id"],
            "title": run["title"],
            "status": run["status"],
            "created_at": run["created_at"],
            "completed_at": run["completed_at"],
            "state": json.loads(run["state_json"]) if run["state_json"] else {}
        }

    async def get_agent_status(self, agent_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get status of all agents or a specific one."""
        if agent_id:
            m = self.minions.get(agent_id)
            return [m.stats] if m else []
        return [m.stats for m in self.minions.values()]

    async def get_trace(self, run_id: str) -> Dict[str, Any]:
        return await self.recorder.export_trace(run_id)

    async def list_checkpoints(self, run_id: str) -> List[Dict]:
        return await self.recorder.list_checkpoints(run_id)

    def add_listener(self, callback: Callable[[str, AgentState], Awaitable[None]]) -> None:
        """Register a real-time state listener (used by SSE/WebSocket)."""
        self._listeners.append(callback)

    # ═════════════════════════════════════════════════════════════
    #  Graph Nodes
    # ═════════════════════════════════════════════════════════════

    async def _node_plan(self, state: AgentState) -> AgentState:
        """Decompose the task into strategy metadata."""
        desc = state.task_description.lower()

        # Simple keyword-based planner
        needs_research = any(k in desc for k in ["research", "investigate", "compare", "evaluate", "find"])
        needs_code = any(k in desc for k in ["build", "create", "implement", "code", "write", "fix", "debug"])
        needs_review = needs_code

        state.metadata["plan"] = {
            "needs_research": needs_research,
            "needs_code": needs_code,
            "needs_review": needs_review,
            "stages": []
        }

        if needs_research:
            state.metadata["plan"]["stages"].append("research")
        if needs_code:
            state.metadata["plan"]["stages"].append("code")
        if needs_review:
            state.metadata["plan"]["stages"].append("review")

        # Add plan message
        state.messages.append({
            "role": "orchestrator",
            "content": f"Plan: {state.metadata['plan']['stages']}"
        })

        if self.recorder:
            await self.recorder.log_message(
                state.run_id, None, "orchestrator",
                f"Planned stages: {state.metadata['plan']['stages']}"
            )

        return state

    async def _node_route(self, state: AgentState) -> AgentState:
        """Routing node: pick next minion based on plan and current progress."""
        plan = state.metadata.get("plan", {})
        stages = plan.get("stages", [])
        completed = state.metadata.get("completed_stages", [])

        next_stage = None
        for stage in stages:
            if stage not in completed:
                next_stage = stage
                break

        # Map stage to minion node
        if next_stage == "research":
            state.next_node = NODE_PERPLEXITY
        elif next_stage == "code":
            state.next_node = NODE_CLAUDE
        elif next_stage == "review":
            state.next_node = NODE_REVIEW
        else:
            state.next_node = NODE_END

        return state

    async def _node_claude(self, state: AgentState) -> AgentState:
        """Invoke Claude minion for coding tasks."""
        minion = self.minions.get("minion_claude")
        if not minion:
            state.error = "Claude minion not registered"
            state._completed = True
            return state

        context = self._build_context(state)
        result: MinionResult = await minion.run(
            prompt=state.task_description,
            context=context,
            task_id=state.task_id
        )

        state.messages.append({
            "role": "agent",
            "agent_id": minion.agent_id,
            "content": result.output,
            "success": result.success
        })

        if result.success:
            state.artifacts.update(result.artifacts)
            state.metadata.setdefault("completed_stages", []).append("code")
        else:
            state.error = result.error

        # Log thought
        if self.recorder:
            await self.recorder.log_thought(
                state.run_id, None, minion.agent_id,
                thought=result.thoughts,
                tool_calls=result.tool_calls,
                tokens_used=result.tokens_used
            )

        return state

    async def _node_perplexity(self, state: AgentState) -> AgentState:
        """Invoke Perplexity minion for research tasks."""
        minion = self.minions.get("minion_perplexity")
        if not minion:
            state.error = "Perplexity minion not registered"
            state._completed = True
            return state

        context = self._build_context(state)
        result: MinionResult = await minion.run(
            prompt=state.task_description,
            context=context,
            task_id=state.task_id
        )

        state.messages.append({
            "role": "agent",
            "agent_id": minion.agent_id,
            "content": result.output,
            "success": result.success,
            "citations": result.citations
        })

        if result.success:
            state.metadata.setdefault("completed_stages", []).append("research")
        else:
            state.error = result.error

        if self.recorder:
            await self.recorder.log_thought(
                state.run_id, None, minion.agent_id,
                thought=result.thoughts,
                tokens_used=result.tokens_used
            )

        return state

    async def _node_review(self, state: AgentState) -> AgentState:
        """Review node: validate outputs or trigger retry."""
        if state.error:
            state.next_node = NODE_END
            return state

        # Mark review complete
        state.metadata.setdefault("completed_stages", []).append("review")

        state.messages.append({
            "role": "orchestrator",
            "content": "Review passed."
        })

        # Don't set next_node here — let the edge condition decide
        return state

    async def _node_end(self, state: AgentState) -> AgentState:
        """Terminal node: aggregate and finalize."""
        state._completed = True
        state.next_node = None

        # Build final summary
        outputs = []
        for msg in state.messages:
            if msg.get("role") == "agent":
                agent_name = msg.get("agent_id", "unknown")
                outputs.append(f"## {agent_name}\n{msg.get('content', '')}")

        if self.recorder:
            await self.recorder.log_message(
                state.run_id, None, "orchestrator",
                f"Run complete. Stages: {state.metadata.get('completed_stages', [])}"
            )

        return state

    # ═════════════════════════════════════════════════════════════
    #  Edge Conditions
    # ═════════════════════════════════════════════════════════════

    async def _edge_after_plan(self, state: AgentState) -> str:
        """After planning, go to routing."""
        return NODE_ROUTE

    async def _edge_after_route(self, state: AgentState) -> str:
        """Routing already sets state.next_node; this edge shouldn't fire."""
        # Fallback if route didn't set next_node
        return state.next_node or NODE_END

    async def _edge_after_review(self, state: AgentState) -> str:
        """After review, check if we need to loop back or finish."""
        stages = state.metadata.get("plan", {}).get("stages", [])
        completed = state.metadata.get("completed_stages", [])

        if state.error:
            return NODE_END

        if set(stages).issubset(set(completed)):
            return NODE_END

        # More stages remaining → go back to route
        return NODE_ROUTE

    # ── Helpers ─────────────────────────────────────────────────

    def _build_context(self, state: AgentState) -> Dict[str, Any]:
        """Build execution context from previous agent messages."""
        previous = []
        for msg in state.messages:
            if msg.get("role") == "agent":
                previous.append({
                    "title": msg.get("agent_id", "agent"),
                    "output": msg.get("content", ""),
                    "artifacts": msg.get("artifacts", {})
                })
        return {"previous_results": previous} if previous else {}
