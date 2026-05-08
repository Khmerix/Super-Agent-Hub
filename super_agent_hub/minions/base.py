"""
Base minion agent with status tracking (Idle, Thinking, Working, Error).
All concrete minions extend this and report status transitions for the UI bridge.
"""
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
import asyncio
import time
from dataclasses import dataclass

from ..core.state_graph import AgentStatus


@dataclass
class MinionResult:
    """Standard output from any minion execution."""
    success: bool
    output: str = ""
    artifacts: Dict[str, Any] = None
    citations: List[Dict[str, str]] = None
    thoughts: str = ""
    tool_calls: List[Dict] = None
    tokens_used: int = 0
    error: Optional[str] = None
    execution_time_ms: float = 0.0

    def __post_init__(self):
        if self.artifacts is None:
            self.artifacts = {}
        if self.citations is None:
            self.citations = []
        if self.tool_calls is None:
            self.tool_calls = []


class MinionAgent(ABC):
    """
    Abstract base for all minions (Claude, Perplexity, future additions).
    
    Status lifecycle:
        IDLE -> THINKING -> WORKING -> IDLE
                          \\-> ERROR
    """

    def __init__(self, agent_id: str, name: str, description: str = ""):
        self.agent_id = agent_id
        self.name = name
        self.description = description
        self.status = AgentStatus.IDLE
        self._status_lock = asyncio.Lock()
        self._total_executions = 0
        self._total_failures = 0
        self._api_key: Optional[str] = None
        self._model: str = ""

    @property
    def is_available(self) -> bool:
        return self.status in (AgentStatus.IDLE, AgentStatus.THINKING)

    @property
    def stats(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "status": self.status.value,
            "executions": self._total_executions,
            "failures": self._total_failures,
            "model": self._model,
        }

    async def set_status(self, status: AgentStatus) -> None:
        async with self._status_lock:
            self.status = status

    def configure(self, api_key: str, **kwargs) -> None:
        """Plug in API key and optional parameters after instantiation."""
        self._api_key = api_key
        for k, v in kwargs.items():
            setattr(self, f"_{k}", v)

    @abstractmethod
    async def warmup(self) -> bool:
        """Validate API key and connectivity. Return True if ready."""
        pass

    @abstractmethod
    async def execute(self, prompt: str, context: Optional[Dict] = None,
                      task_id: Optional[str] = None) -> MinionResult:
        """
        Execute a task. Implementations must:
        1. Set status THINKING before LLM call
        2. Set status WORKING during tool execution
        3. Set status IDLE on completion
        4. Set status ERROR on failure
        """
        pass

    async def run(self, prompt: str, context: Optional[Dict] = None,
                  task_id: Optional[str] = None) -> MinionResult:
        """Wrapped execute with metrics and status management."""
        await self.set_status(AgentStatus.THINKING)
        start = time.time()

        try:
            result = await self.execute(prompt, context, task_id)
            elapsed = (time.time() - start) * 1000
            result.execution_time_ms = elapsed
            self._total_executions += 1
            if not result.success:
                self._total_failures += 1
                await self.set_status(AgentStatus.ERROR)
            else:
                await self.set_status(AgentStatus.IDLE)
            return result
        except Exception as e:
            self._total_failures += 1
            await self.set_status(AgentStatus.ERROR)
            return MinionResult(
                success=False,
                error=str(e),
                execution_time_ms=(time.time() - start) * 1000
            )

    async def shutdown(self) -> None:
        """Cleanup. Override if needed."""
        await self.set_status(AgentStatus.IDLE)
