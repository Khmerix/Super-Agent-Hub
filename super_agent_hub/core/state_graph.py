"""
LangGraph-inspired state machine with checkpoint/rollback support.
Nodes are async functions; edges are routing conditions.
State is Pydantic-based and serializable for persistence.
"""
from __future__ import annotations
from typing import Dict, Any, List, Optional, Callable, Awaitable, Set
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import asyncio
import copy
import json
from rich.console import Console

from ..recorder.flight_recorder import FlightRecorder

console = Console()


class AgentStatus(str, Enum):
    """Canonical agent/minion status for UI bridge."""
    IDLE = "idle"
    THINKING = "thinking"
    WORKING = "working"
    ERROR = "error"


@dataclass
class AgentState:
    """
    The shared state object that flows through the graph.
    Immutable snapshots allow safe checkpointing and rollback.
    """
    run_id: str
    task_id: str
    task_title: str = ""
    task_description: str = ""
    messages: List[Dict[str, Any]] = field(default_factory=list)
    artifacts: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    current_node: Optional[str] = None
    next_node: Optional[str] = None
    revert_target_checkpoint: Optional[int] = None
    _completed: bool = False

    def snapshot(self) -> Dict[str, Any]:
        """Deep-copy serializable snapshot for checkpointing."""
        return {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "task_title": self.task_title,
            "task_description": self.task_description,
            "messages": copy.deepcopy(self.messages),
            "artifacts": copy.deepcopy(self.artifacts),
            "metadata": copy.deepcopy(self.metadata),
            "error": self.error,
            "current_node": self.current_node,
            "next_node": self.next_node,
            "completed": self._completed,
        }

    @classmethod
    def from_snapshot(cls, data: Dict[str, Any]) -> AgentState:
        """Restore state from a checkpoint snapshot."""
        s = cls(
            run_id=data["run_id"],
            task_id=data["task_id"],
            task_title=data.get("task_title", ""),
            task_description=data.get("task_description", ""),
            messages=copy.deepcopy(data.get("messages", [])),
            artifacts=copy.deepcopy(data.get("artifacts", {})),
            metadata=copy.deepcopy(data.get("metadata", {})),
            error=data.get("error"),
            current_node=data.get("current_node"),
            next_node=data.get("next_node"),
            _completed=data.get("completed", False),
        )
        return s


# Node function type: receives AgentState, returns modified AgentState
NodeFunc = Callable[[AgentState], Awaitable[AgentState]]
# Edge condition: receives AgentState, returns next node name
EdgeCondition = Callable[[AgentState], Awaitable[str]]


@dataclass
class GraphNode:
    """A node in the state graph."""
    name: str
    func: NodeFunc
    description: str = ""


@dataclass
class GraphEdge:
    """A conditional edge between nodes."""
    source: str
    condition: EdgeCondition


class StateGraph:
    """
    LangGraph-style state graph with checkpoint persistence.
    
    Usage:
        graph = StateGraph(recorder)
        graph.add_node("plan", plan_node)
        graph.add_node("claude", claude_node)
        graph.add_node("perplexity", perplexity_node)
        graph.add_node("review", review_node)
        graph.add_node("end", end_node)
        
        graph.set_entry_point("plan")
        graph.add_conditional_edges("plan", route_by_task_type)
        graph.add_edge("claude", "review")
        graph.add_edge("perplexity", "review")
        graph.add_conditional_edges("review", route_after_review)
        
        compiled = graph.compile()
        result_state = await compiled.ainvoke(initial_state)
    """

    def __init__(self, recorder: Optional[FlightRecorder] = None):
        self.nodes: Dict[str, GraphNode] = {}
        self.edges: Dict[str, List[str]] = {}           # unconditional: source -> [targets]
        self.conditional_edges: Dict[str, GraphEdge] = {}  # conditional
        self.entry_point: Optional[str] = None
        self.recorder = recorder
        self._step_counter = 0
        self._lock = asyncio.Lock()

    def add_node(self, name: str, func: NodeFunc, description: str = "") -> StateGraph:
        self.nodes[name] = GraphNode(name, func, description)
        if name not in self.edges:
            self.edges[name] = []
        return self

    def set_entry_point(self, name: str) -> StateGraph:
        if name not in self.nodes:
            raise ValueError(f"Node '{name}' not defined")
        self.entry_point = name
        return self

    def add_edge(self, source: str, target: str) -> StateGraph:
        if source not in self.edges:
            self.edges[source] = []
        self.edges[source].append(target)
        return self

    def add_conditional_edges(self, source: str, condition: EdgeCondition) -> StateGraph:
        self.conditional_edges[source] = GraphEdge(source, condition)
        return self

    def compile(self) -> CompiledGraph:
        if not self.entry_point:
            raise ValueError("Entry point not set")
        return CompiledGraph(self)


class CompiledGraph:
    """Executable, compiled state graph."""

    def __init__(self, graph: StateGraph):
        self.graph = graph
        self._running = False
        self._listeners: List[Callable[[str, AgentState], Awaitable[None]]] = []

    def add_listener(self, callback: Callable[[str, AgentState], Awaitable[None]]) -> None:
        """Register a callback for real-time state updates."""
        self._listeners.append(callback)

    async def _notify(self, event: str, state: AgentState) -> None:
        for cb in self._listeners:
            try:
                await cb(event, state)
            except Exception:
                pass

    async def ainvoke(self, state: AgentState,
                      checkpoint_every: int = 1) -> AgentState:
        """
        Execute the graph asynchronously with checkpointing.
        
        Args:
            state: Initial AgentState
            checkpoint_every: Save checkpoint every N steps (1 = every step)
            
        Returns:
            Final AgentState after graph completes
        """
        self._running = True
        current = state
        current.current_node = self.graph.entry_point
        current.next_node = self.graph.entry_point
        step_number = 0

        # Start run in recorder
        if self.graph.recorder:
            await self.graph.recorder.start_run(
                state.run_id, state.task_id, state.task_title,
                current.snapshot()
            )

        try:
            while current.next_node and not current._completed and self._running:
                node_name = current.next_node
                current.current_node = node_name
                current.next_node = None
                step_number += 1

                # ── CHECKPOINT BEFORE EXECUTION ──
                checkpoint_id: Optional[int] = None
                if self.graph.recorder and step_number % checkpoint_every == 0:
                    parent = getattr(current, "_last_checkpoint_id", None)
                    checkpoint_id = await self.graph.recorder.checkpoint(
                        current.run_id, step_number, node_name,
                        current.snapshot(), parent_checkpoint_id=parent
                    )
                    current._last_checkpoint_id = checkpoint_id

                await self._notify("node_start", current)

                # ── HUMAN-IN-THE-LOOP: REVERT REQUESTED ──
                if current.revert_target_checkpoint is not None:
                    revert_id = current.revert_target_checkpoint
                    current.revert_target_checkpoint = None
                    restored = await self._revert_to_checkpoint(current, revert_id)
                    if restored:
                        current = restored
                        await self._notify("revert", current)
                        continue

                # ── EXECUTE NODE ──
                node = self.graph.nodes.get(node_name)
                if not node:
                    current.error = f"Node '{node_name}' not found"
                    current._completed = True
                    break

                try:
                    current = await node.func(current)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    current.error = f"Node '{node_name}' failed: {e}"
                    current._completed = True
                    if self.graph.recorder:
                        await self.graph.recorder.update_run_status(
                            current.run_id, "ERROR", current.snapshot()
                        )
                    break

                # Log agent thought if available
                if self.graph.recorder:
                    await self.graph.recorder.log_message(
                        current.run_id, checkpoint_id,
                        role="agent", content=f"Completed node: {node_name}",
                        metadata={"node": node_name}
                    )

                await self._notify("node_end", current)

                # ── RESOLVE NEXT NODE ──
                if current.next_node:
                    continue  # Node explicitly set next

                # Conditional edge?
                cond_edge = self.graph.conditional_edges.get(node_name)
                if cond_edge:
                    try:
                        target = await cond_edge.condition(current)
                        current.next_node = target
                        continue
                    except Exception as e:
                        current.error = f"Edge condition from '{node_name}' failed: {e}"
                        current._completed = True
                        break

                # Unconditional edge?
                targets = self.graph.edges.get(node_name, [])
                if targets:
                    current.next_node = targets[0]  # Default to first target
                    continue

                # No outgoing edges: we're done
                current._completed = True

            # Final checkpoint
            if self.graph.recorder:
                await self.graph.recorder.update_run_status(
                    current.run_id,
                    "COMPLETED" if not current.error else "ERROR",
                    current.snapshot()
                )

            return current

        except asyncio.CancelledError:
            if self.graph.recorder:
                await self.graph.recorder.update_run_status(
                    current.run_id, "CANCELLED"
                )
            raise
        finally:
            self._running = False

    async def _revert_to_checkpoint(self, state: AgentState, checkpoint_id: int) -> Optional[AgentState]:
        """Rollback state to a previous checkpoint."""
        if not self.graph.recorder:
            return None

        cp = await self.graph.recorder.get_checkpoint(checkpoint_id)
        if not cp:
            console.print(f"[red]Checkpoint {checkpoint_id} not found[/red]")
            return None

        restored = AgentState.from_snapshot(cp["state"])
        restored.next_node = restored.current_node  # Re-run from that node
        restored.revert_target_checkpoint = None

        if self.graph.recorder:
            await self.graph.recorder.log_message(
                state.run_id, None,
                role="system",
                content=f"REVERT to checkpoint {checkpoint_id} (step {cp.get('step_number')})",
                metadata={"checkpoint_id": checkpoint_id, "reverted_node": restored.current_node}
            )

        console.print(f"[yellow]Reverted run {state.run_id} to checkpoint {checkpoint_id}[/yellow]")
        return restored

    def stop(self) -> None:
        """Request graceful shutdown."""
        self._running = False
