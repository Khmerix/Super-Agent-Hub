"""
Minion_Claude - Coding specialist with tool-augmented reasoning.
Wraps Anthropic Claude API. Set api_key via configure() before use.
"""
import os
import json
from typing import Dict, Any, Optional, List
from rich.console import Console

from .base import MinionAgent, MinionResult
from ..core.state_graph import AgentStatus

console = Console()

# Tool definitions for Claude — Anthropic native format (not OpenAI function format)
CLAUDE_TOOLS = [
    {
        "name": "read_file",
        "description": "Read contents of a file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "offset": {"type": "integer"},
                "limit": {"type": "integer"}
            },
            "required": ["path"]
        }
    },
    {
        "name": "write_file",
        "description": "Write or append to a file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
                "append": {"type": "boolean"}
            },
            "required": ["path", "content"]
        }
    },
    {
        "name": "list_directory",
        "description": "List files in a directory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "recursive": {"type": "boolean"}
            }
        }
    },
    {
        "name": "execute_command",
        "description": "Run a shell command.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout": {"type": "integer"}
            },
            "required": ["command"]
        }
    },
    {
        "name": "search_code",
        "description": "Search code with grep/ripgrep.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "path": {"type": "string"},
                "glob": {"type": "string"}
            },
            "required": ["pattern"]
        }
    }
]


class MinionClaude(MinionAgent):
    """
    Claude-3.5/4 coding specialist.
    
    Configuration (call before use):
        minion.configure(
            api_key="sk-ant-...",
            model="claude-3-5-sonnet-20241022",
            max_iterations=25,
            working_dir="./workspace"
        )
    
    Status transitions during execution:
        THINKING -> (reasoning) -> WORKING -> (tool calls) -> THINKING -> ... -> IDLE
    """

    def __init__(self, working_dir: str = "./workspace"):
        super().__init__(
            agent_id="minion_claude",
            name="Minion Claude",
            description="Coding specialist: generation, debugging, file ops, architecture"
        )
        self._model = "claude-3-5-sonnet-20241022"
        self._max_iterations = 25
        self._working_dir = working_dir
        self._client: Any = None

    def configure(self, api_key: Optional[str] = None, **kwargs) -> None:
        super().configure(api_key or os.getenv("ANTHROPIC_API_KEY", ""), **kwargs)
        if "model" in kwargs:
            self._model = kwargs["model"]
        if "max_iterations" in kwargs:
            self._max_iterations = kwargs["max_iterations"]
        if "working_dir" in kwargs:
            self._working_dir = kwargs["working_dir"]

    async def warmup(self) -> bool:
        """Validate API key by making a tiny test request."""
        if not self._api_key:
            console.print("[yellow]MinionClaude: no API key configured[/yellow]")
            return False

        try:
            import anthropic
            self._client = anthropic.AsyncAnthropic(api_key=self._api_key)
            # Minimal validation call
            await self._client.messages.create(
                model=self._model, max_tokens=10,
                messages=[{"role": "user", "content": "ping"}]
            )
            console.print(f"[green]MinionClaude ready ({self._model})[/green]")
            return True
        except ImportError:
            console.print("[red]Install anthropic SDK: pip install anthropic[/red]")
            return False
        except Exception as e:
            console.print(f"[red]MinionClaude warmup failed: {e}[/red]")
            return False

    async def execute(self, prompt: str, context: Optional[Dict] = None,
                      task_id: Optional[str] = None) -> MinionResult:
        """
        Execute with tool-augmented loop.
        When api_key is missing, returns a mock structure showing what would happen.
        """
        if not self._api_key or not self._client:
            # ── MOCK MODE ── Returns a realistic mock for integration testing
            return self._mock_execute(prompt, context, task_id)

        # ── REAL MODE ── Full tool loop against Anthropic API
        messages: List[Dict[str, Any]] = [
            {"role": "user", "content": prompt}
        ]
        if context and context.get("previous_results"):
            ctx_text = self._format_context(context["previous_results"])
            messages.insert(0, {"role": "user", "content": ctx_text})

        artifacts: Dict[str, Any] = {}
        all_output = []
        tool_call_log: List[Dict] = []
        total_tokens = {"input": 0, "output": 0}

        for iteration in range(self._max_iterations):
            await self.set_status(AgentStatus.THINKING)

            response = await self._client.messages.create(
                model=self._model,
                max_tokens=4096,
                system=self._system_prompt(),
                messages=messages,
                tools=CLAUDE_TOOLS,
                tool_choice={"type": "auto"}
            )

            total_tokens["input"] += response.usage.input_tokens
            total_tokens["output"] += response.usage.output_tokens

            has_tool_use = False
            tool_results = []

            for block in response.content:
                if block.type == "text":
                    all_output.append(block.text)
                elif block.type == "tool_use":
                    has_tool_use = True
                    await self.set_status(AgentStatus.WORKING)
                    result = await self._run_tool(block.name, block.input)
                    tool_call_log.append({
                        "tool": block.name,
                        "input": block.input,
                        "output": result
                    })
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result)
                    })
                    if block.name in ("read_file", "write_file"):
                        artifacts[block.input.get("path", "artifact")] = result

            if not has_tool_use:
                break

            messages.append({
                "role": "assistant",
                "content": [b.model_dump() if hasattr(b, "model_dump") else b for b in response.content]
            })
            messages.append({"role": "user", "content": tool_results})

        return MinionResult(
            success=True,
            output="\n".join(all_output),
            artifacts=artifacts,
            thoughts=f"Completed in {iteration + 1} iterations",
            tool_calls=tool_call_log,
            tokens_used=sum(total_tokens.values()),
        )

    # ── Internal helpers ─────────────────────────────────────

    def _system_prompt(self) -> str:
        return (
            "You are an expert software engineer with file system and shell access.\n"
            "Rules:\n"
            "- Prefer small, focused functions\n"
            "- Add error handling\n"
            "- Write tests where appropriate\n"
            f"Working directory: {self._working_dir}"
        )

    def _format_context(self, previous_results: List[Dict]) -> str:
        parts = ["Context from earlier work:"]
        for r in previous_results:
            parts.append(f"\n--- {r.get('title', 'Task')} ---")
            parts.append(r.get("output", "")[:2000])
        return "\n".join(parts)

    async def _run_tool(self, name: str, params: Dict) -> Dict:
        # Tool implementations (same as previous build)
        from pathlib import Path
        import asyncio, subprocess

        try:
            if name == "read_file":
                p = Path(self._working_dir) / params["path"]
                return {"content": p.read_text()[:5000], "exists": p.exists()}
            if name == "write_file":
                p = Path(self._working_dir) / params["path"]
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(params["content"], encoding="utf-8")
                return {"success": True, "path": str(p)}
            if name == "list_directory":
                p = Path(self._working_dir) / params.get("path", ".")
                return {"items": [x.name for x in p.iterdir()]}
            if name == "execute_command":
                proc = await asyncio.create_subprocess_shell(
                    params["command"],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    cwd=self._working_dir
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=params.get("timeout", 30))
                return {
                    "returncode": proc.returncode,
                    "stdout": stdout.decode(errors="replace"),
                    "stderr": stderr.decode(errors="replace")
                }
            if name == "search_code":
                return {"matches": ["mock: pattern not available without ripgrep"]}
        except Exception as e:
            return {"error": str(e)}
        return {"error": f"Unknown tool: {name}"}

    # ── Mock execution for keyless integration ──────────────

    def _mock_execute(self, prompt: str, context: Optional[Dict],
                      task_id: Optional[str]) -> MinionResult:
        """Return a realistic mock result when API key is unavailable."""
        mock_output = (
            f"[MOCK Claude Output]\n"
            f"Task ID: {task_id}\n"
            f"Received prompt ({len(prompt)} chars): {prompt[:120]}...\n\n"
            "This is a MOCK response. To enable real Claude inference:\n"
            "  1. Set ANTHROPIC_API_KEY environment variable, or\n"
            "  2. Call minion.configure(api_key='sk-ant-...')\n\n"
            "When live, Claude will use these tools:\n"
            "  - read_file / write_file / list_directory\n"
            "  - execute_command / search_code\n"
        )
        return MinionResult(
            success=True,
            output=mock_output,
            artifacts={"mock_mode": True, "prompt_preview": prompt[:200]},
            thoughts="Mock execution (no API key)",
            tool_calls=[{"tool": "mock", "status": "no_api_key"}]
        )

    async def shutdown(self) -> None:
        if self._client:
            await self._client.close()
        await super().shutdown()
