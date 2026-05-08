"""
Minion_Perplexity - Research specialist with real-time web grounding.
Wraps Perplexity Sonar API (OpenAI-compatible endpoint). Set api_key via configure().
"""
import os
from typing import Dict, Any, Optional, List
from rich.console import Console

from .base import MinionAgent, MinionResult
from ..core.state_graph import AgentStatus

console = Console()


class MinionPerplexity(MinionAgent):
    """
    Perplexity Sonar-powered research specialist.
    
    Configuration (call before use):
        minion.configure(
            api_key="pplx-...",
            model="sonar-pro",
            citations_format="numbered"  # or "url"
        )
    
    Status transitions:
        THINKING -> (search + inference) -> IDLE
                 \\-> ERROR
    """

    def __init__(self):
        super().__init__(
            agent_id="minion_perplexity",
            name="Minion Perplexity",
            description="Research specialist: real-time web search, citations, synthesis"
        )
        self._model = "sonar-pro"
        self._citations_format = "numbered"
        self._client: Any = None

    def configure(self, api_key: Optional[str] = None, **kwargs) -> None:
        super().configure(api_key or os.getenv("PERPLEXITY_API_KEY", ""), **kwargs)
        if "model" in kwargs:
            self._model = kwargs["model"]
        if "citations_format" in kwargs:
            self._citations_format = kwargs["citations_format"]

    async def warmup(self) -> bool:
        """Validate Perplexity API key with a small call."""
        if not self._api_key:
            console.print("[yellow]MinionPerplexity: no API key configured[/yellow]")
            return False

        try:
            import openai
            self._client = openai.AsyncOpenAI(
                api_key=self._api_key,
                base_url="https://api.perplexity.ai"
            )
            await self._client.chat.completions.create(
                model=self._model, max_tokens=10,
                messages=[{"role": "user", "content": "ping"}]
            )
            console.print(f"[green]MinionPerplexity ready ({self._model})[/green]")
            return True
        except ImportError:
            console.print("[red]Install openai SDK: pip install openai[/red]")
            return False
        except Exception as e:
            console.print(f"[red]MinionPerplexity warmup failed: {e}[/red]")
            return False

    async def execute(self, prompt: str, context: Optional[Dict] = None,
                      task_id: Optional[str] = None) -> MinionResult:
        """
        Execute research query with web grounding.
        Mock mode returns realistic placeholder when no API key.
        """
        if not self._api_key or not self._client:
            return self._mock_execute(prompt, context, task_id)

        await self.set_status(AgentStatus.THINKING)

        messages: List[Dict[str, str]] = [
            {"role": "system", "content": self._system_prompt()},
            {"role": "user", "content": prompt}
        ]

        if context and context.get("previous_results"):
            ctx = self._format_context(context["previous_results"])
            messages.insert(1, {"role": "user", "content": ctx})

        try:
            import time
            start = time.time()

            response = await self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                temperature=0.2,
                max_tokens=4096
            )

            elapsed = (time.time() - start) * 1000
            msg = response.choices[0].message
            content = msg.content or ""

            citations: List[Dict[str, str]] = []
            if hasattr(msg, "citations") and msg.citations:
                for i, url in enumerate(msg.citations, 1):
                    citations.append({"index": str(i), "url": url})

            tokens = {}
            if hasattr(response, "usage") and response.usage:
                tokens = {
                    "input": getattr(response.usage, "prompt_tokens", 0),
                    "output": getattr(response.usage, "completion_tokens", 0)
                }

            await self.set_status(AgentStatus.IDLE)

            return MinionResult(
                success=True,
                output=content,
                citations=citations,
                thoughts=f"Web-grounded response using {self._model}",
                tokens_used=sum(tokens.values()),
                execution_time_ms=elapsed
            )

        except Exception as e:
            await self.set_status(AgentStatus.ERROR)
            return MinionResult(
                success=False,
                error=str(e),
                thoughts=f"Perplexity error: {e}"
            )

    def _system_prompt(self) -> str:
        return (
            "You are a research analyst with real-time web access.\n"
            "Rules:\n"
            "- Cite sources with [1], [2], etc.\n"
            "- Prioritize authoritative, recent sources\n"
            "- Flag uncertainties\n"
            "- Be concise but thorough"
        )

    def _format_context(self, previous_results: List[Dict]) -> str:
        parts = ["Background from earlier work:"]
        for r in previous_results:
            parts.append(f"\n--- {r.get('title', 'Task')} ---")
            parts.append(r.get("output", "")[:3000])
        return "\n".join(parts)

    def _mock_execute(self, prompt: str, context: Optional[Dict],
                      task_id: Optional[str]) -> MinionResult:
        """Mock result for integration without API key."""
        mock_output = (
            f"[MOCK Perplexity Output]\n"
            f"Task ID: {task_id}\n"
            f"Query: {prompt[:120]}...\n\n"
            "This is a MOCK response. To enable real Perplexity search:\n"
            "  1. Set PERPLEXITY_API_KEY environment variable, or\n"
            "  2. Call minion.configure(api_key='pplx-...')\n\n"
            "When live, Perplexity will:\n"
            "  - Search the live web\n"
            "  - Return citations [1], [2]...\n"
            "  - Cite authoritative sources\n"
        )
        return MinionResult(
            success=True,
            output=mock_output,
            citations=[
                {"index": "1", "url": "https://example.com/mock-source"}
            ],
            thoughts="Mock execution (no API key)",
            tool_calls=[{"tool": "web_search", "status": "no_api_key"}]
        )

    async def shutdown(self) -> None:
        if self._client:
            await self._client.close()
        await super().shutdown()
