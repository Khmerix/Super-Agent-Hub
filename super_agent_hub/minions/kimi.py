"""
Minion_Kimi — Universal agent powered by Moonshot Kimi K2.6.
Replaces both Claude (coding) and Perplexity (research) with a single minion.
Uses OpenAI-compatible API: base_url="https://api.moonshot.ai/v1"
"""
import os
import json
from typing import Dict, Any, Optional, List
from rich.console import Console

from .base import MinionAgent, MinionResult
from ..core.state_graph import AgentStatus

console = Console()

# Tool definitions for Kimi (OpenAI function-calling format)
KIMI_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read contents of a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "offset": {"type": "integer", "default": 1},
                    "limit": {"type": "integer", "default": 100}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write or append to a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "append": {"type": "boolean", "default": False}
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "List files in a directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "default": "."},
                    "recursive": {"type": "boolean", "default": False}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "execute_command",
            "description": "Run a shell command.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout": {"type": "integer", "default": 30}
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_code",
            "description": "Search code with grep/ripgrep.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string", "default": "."},
                    "glob": {"type": "string"}
                },
                "required": ["pattern"]
            }
        }
    }
]


class MinionKimi(MinionAgent):
    """
    Kimi K2.6 universal specialist — coding + research in one minion.

    Configuration (call before use):
        minion.configure(
            api_key="sk-...",
            model="kimi-k2.6",           # or "kimi-k2.5", "kimi-k2-thinking"
            max_iterations=25,
            working_dir="./workspace",
            mode="auto"                  # "code", "research", or "auto"
        )

    Status transitions:
        THINKING -> (reasoning) -> WORKING -> (tool calls) -> THINKING -> ... -> IDLE
    """

    def __init__(self, working_dir: str = "./workspace"):
        super().__init__(
            agent_id="minion_kimi",
            name="Minion Kimi",
            description="Universal specialist: coding, research, file ops, architecture, web search"
        )
        self._model = "kimi-k2.6"
        self._max_iterations = 25
        self._working_dir = working_dir
        self._mode = "auto"  # "code", "research", "auto"
        self._client: Any = None

    def configure(self, api_key: Optional[str] = None, **kwargs) -> None:
        super().configure(api_key or os.getenv("MOONSHOT_API_KEY", ""), **kwargs)
        if "model" in kwargs:
            self._model = kwargs["model"]
        if "max_iterations" in kwargs:
            self._max_iterations = kwargs["max_iterations"]
        if "working_dir" in kwargs:
            self._working_dir = kwargs["working_dir"]
        if "mode" in kwargs:
            self._mode = kwargs["mode"]

    async def warmup(self) -> bool:
        """Validate API key by making a tiny test request."""
        if not self._api_key:
            console.print("[yellow]MinionKimi: no API key configured[/yellow]")
            return False

        try:
            from openai import AsyncOpenAI
            self._client = AsyncOpenAI(
                api_key=self._api_key,
                base_url="https://api.moonshot.ai/v1"
            )
            # Minimal validation call
            await self._client.chat.completions.create(
                model=self._model,
                max_tokens=10,
                messages=[{"role": "user", "content": "ping"}]
            )
            console.print(f"[green]MinionKimi ready ({self._model})[/green]")
            return True
        except ImportError:
            console.print("[red]Install openai SDK: pip install openai[/red]")
            return False
        except Exception as e:
            console.print(f"[red]MinionKimi warmup failed: {e}[/red]")
            return False

    async def execute(self, prompt: str, context: Optional[Dict] = None,
                      task_id: Optional[str] = None) -> MinionResult:
        """
        Execute with tool-augmented loop.
        When api_key is missing, returns a mock structure for integration testing.
        """
        if not self._api_key or not self._client:
            return self._mock_execute(prompt, context, task_id)

        # Determine system prompt based on mode
        system_prompt = self._system_prompt()

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

            response = await self._client.chat.completions.create(
                model=self._model,
                max_tokens=4096,
                messages=messages,
                tools=KIMI_TOOLS,
                tool_choice="auto"
            )

            if response.usage:
                total_tokens["input"] += response.usage.prompt_tokens or 0
                total_tokens["output"] += response.usage.completion_tokens or 0

            has_tool_use = False
            tool_results = []
            assistant_content = []

            msg = response.choices[0].message

            # Handle text content
            if msg.content:
                all_output.append(msg.content)
                assistant_content.append({"type": "text", "text": msg.content})

            # Handle tool calls
            if msg.tool_calls:
                has_tool_use = True
                for tool_call in msg.tool_calls:
                    await self.set_status(AgentStatus.WORKING)
                    tool_name = tool_call.function.name
                    tool_input = json.loads(tool_call.function.arguments)
                    result = await self._run_tool(tool_name, tool_input)
                    tool_call_log.append({
                        "tool": tool_name,
                        "input": tool_input,
                        "output": result
                    })
                    tool_results.append({
                        "tool_call_id": tool_call.id,
                        "role": "tool",
                        "name": tool_name,
                        "content": json.dumps(result)
                    })
                    if tool_name in ("read_file", "write_file"):
                        artifacts[tool_input.get("path", "artifact")] = result

            if not has_tool_use:
                break

            # Add assistant message with tool calls
            messages.append({
                "role": "assistant",
                "content": assistant_content if assistant_content else None,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": tc.type,
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments
                        }
                    } for tc in msg.tool_calls
                ] if msg.tool_calls else None
            })

            # Add tool results
            for tr in tool_results:
                messages.append({
                    "role": "tool",
                    "tool_call_id": tr["tool_call_id"],
                    "content": tr["content"]
                })

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
        mode = self._mode
        if mode == "code":
            return (
                "You are an expert software engineer with file system and shell access.\n"
                "Rules:\n"
                "- Prefer small, focused functions\n"
                "- Add error handling\n"
                "- Write tests where appropriate\n"
                f"Working directory: {self._working_dir}"
            )
        elif mode == "research":
            return (
                "You are a research analyst with real-time knowledge access.\n"
                "Rules:\n"
                "- Cite sources with [1], [2], etc.\n"
                "- Prioritize authoritative, recent sources\n"
                "- Flag uncertainties\n"
                "- Be concise but thorough"
            )
        else:  # auto
            return (
                "You are a universal AI assistant with file system and shell access.\n"
                "You can write code, research topics, analyze data, and build projects.\n"
                "Rules:\n"
                "- For coding: prefer small functions, add error handling, write tests\n"
                "- For research: cite sources, flag uncertainties, be thorough\n"
                f"Working directory: {self._working_dir}"
            )

    def _format_context(self, previous_results: List[Dict]) -> str:
        parts = ["Context from earlier work:"]
        for r in previous_results:
            parts.append(f"\n--- {r.get('title', 'Task')} ---")
            parts.append(r.get("output", "")[:2000])
        return "\n".join(parts)

    async def _run_tool(self, name: str, params: Dict) -> Dict:
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
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=params.get("timeout", 30)
                )
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
            f"[MOCK Kimi K2.6 Output]\n"
            f"Task ID: {task_id}\n"
            f"Received prompt ({len(prompt)} chars): {prompt[:120]}...\n\n"
            "This is a MOCK response. To enable real Kimi inference:\n"
            "  1. Set MOONSHOT_API_KEY environment variable, or\n"
            "  2. Call minion.configure(api_key='sk-...')\n\n"
            "When live, Kimi K2.6 will use these tools:\n"
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
