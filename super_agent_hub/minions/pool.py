"""
MinionPool — Direct API layer for Claude, Perplexity, AND Kimi K2.6.
Every call is automatically logged to SQLite.

Usage:
    from minions.pool import MinionPool
    pool = MinionPool()

    # Choose your agent:
    research = pool.ask_perplexity("Compare vector databases for RAG")
    code = pool.ask_claude("Build a FastAPI auth module", context=research)
    unified = pool.ask_kimi("Do both research and code", context=research)
"""
import os
import sqlite3
import json
from datetime import datetime
from typing import Optional

try:
    from anthropic import Anthropic
except ImportError:
    Anthropic = None

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None


class MinionPool:
    """
    Three-minion API pool:
      - Perplexity (sonar-pro) → research, facts, comparisons
      - Claude (claude-3-7-sonnet-20250219) → code, architecture, implementation
      - Kimi K2.6 (kimi-k2.6) → universal: code + research + reasoning

    Every call is auto-logged to super_agent.db via _log_action().
    """

    def __init__(self, db_path: str = "super_agent.db"):
        self.db_path = db_path
        self._claude_ready = False
        self._perplexity_ready = False
        self._kimi_ready = False
        self._claude = None
        self._perplexity = None
        self._kimi = None
        self._init_db()
        self._connect()

    # ── Database ───────────────────────────────────────────────

    def _init_db(self):
        """Create tables if they don't exist."""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()

        # Per-action logs (your original table, enhanced)
        c.execute('''CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            agent TEXT,
            task TEXT,
            output TEXT,
            status TEXT,
            tokens_used INTEGER DEFAULT 0,
            model TEXT,
            metadata TEXT
        )''')

        # Pipeline runs — tracks multi-step missions
        c.execute('''CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY,
            timestamp TEXT,
            title TEXT,
            description TEXT,
            status TEXT DEFAULT 'pending',
            research_output TEXT,
            code_output TEXT,
            completed_at TEXT
        )''')

        conn.commit()
        conn.close()

    def _log_action(self, agent_name: str, task: str, result: str,
                    status: str = "success", tokens_used: int = 0,
                    model: str = "", metadata: Optional[dict] = None):
        """
        Your original logger — enhanced with tokens, model, metadata.
        Every ask_* call auto-logs here.
        """
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute(
            "INSERT INTO logs (timestamp, agent, task, output, status, tokens_used, model, metadata) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                datetime.now().isoformat(),
                agent_name,
                task[:500],           # Truncate long tasks
                result[:2000],        # Truncate long outputs
                status,
                tokens_used,
                model,
                json.dumps(metadata) if metadata else None
            )
        )
        conn.commit()
        conn.close()

    def _connect(self):
        """Initialize all API clients from environment keys."""
        # Claude
        claude_key = os.getenv("ANTHROPIC_API_KEY")
        if Anthropic and claude_key:
            try:
                self._claude = Anthropic(api_key=claude_key)
                self._claude_ready = True
                print("[MinionPool] Claude connected")
            except Exception as e:
                print(f"[MinionPool] Claude init failed: {e}")
        else:
            print("[MinionPool] Claude: set ANTHROPIC_API_KEY + pip install anthropic")

        # Perplexity
        pplx_key = os.getenv("PERPLEXITY_API_KEY")
        if OpenAI and pplx_key:
            try:
                self._perplexity = OpenAI(api_key=pplx_key, base_url="https://api.perplexity.ai")
                self._perplexity_ready = True
                print("[MinionPool] Perplexity connected")
            except Exception as e:
                print(f"[MinionPool] Perplexity init failed: {e}")
        else:
            print("[MinionPool] Perplexity: set PERPLEXITY_API_KEY + pip install openai")

        # Kimi
        kimi_key = os.getenv("MOONSHOT_API_KEY")
        if OpenAI and kimi_key:
            try:
                self._kimi = OpenAI(api_key=kimi_key, base_url="https://api.moonshot.ai/v1")
                self._kimi_ready = True
                print("[MinionPool] Kimi K2.6 connected")
            except Exception as e:
                print(f"[MinionPool] Kimi init failed: {e}")
        else:
            print("[MinionPool] Kimi: set MOONSHOT_API_KEY + pip install openai")

    @property
    def ready(self) -> dict:
        return {
            "claude": self._claude_ready,
            "perplexity": self._perplexity_ready,
            "kimi": self._kimi_ready
        }

    # ── Claude API ─────────────────────────────────────────────

    def ask_claude(self, task: str, context: str = "") -> str:
        """Claude: The minion that writes the code. Auto-logged."""
        if not self._claude_ready:
            output = (
                "[MOCK CODE OUTPUT]\n\n"
                "No Claude API key configured.\n"
                "Set ANTHROPIC_API_KEY to enable live code generation.\n\n"
                f"# Mock: {task[:50]}...\n"
                "# Provide ANTHROPIC_API_KEY for real Claude inference."
            )
            self._log_action("claude", task, output, status="mock")
            return output

        try:
            full_prompt = f"Context: {context}\n\nTask: {task}" if context else task
            response = self._claude.messages.create(
                model="claude-3-7-sonnet-20250219",
                max_tokens=4096,
                messages=[{"role": "user", "content": full_prompt}]
            )
            output = response.content[0].text
            tokens = response.usage.input_tokens + response.usage.output_tokens if response.usage else 0

            self._log_action("claude", task, output,
                           status="success", tokens_used=tokens,
                           model="claude-3-7-sonnet-20250219",
                           metadata={"had_context": bool(context)})
            return output

        except Exception as e:
            self._log_action("claude", task, str(e), status="error")
            return f"[Claude Error: {e}]"

    # ── Perplexity API ───────────────────────────────────────

    def ask_perplexity(self, prompt: str) -> str:
        """Perplexity: The minion that finds facts. Auto-logged."""
        if not self._perplexity_ready:
            output = (
                "[MOCK RESEARCH OUTPUT]\n\n"
                "No Perplexity API key configured.\n"
                "Set PERPLEXITY_API_KEY to enable live research.\n\n"
                "Mock result: Based on current industry trends, the recommended approach "
                "involves microservices architecture with containerized deployments."
            )
            self._log_action("perplexity", prompt, output, status="mock")
            return output

        try:
            response = self._perplexity.chat.completions.create(
                model="sonar-pro",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=4096
            )
            output = response.choices[0].message.content
            tokens = response.usage.total_tokens if response.usage else 0

            self._log_action("perplexity", prompt, output,
                           status="success", tokens_used=tokens, model="sonar-pro")
            return output

        except Exception as e:
            self._log_action("perplexity", prompt, str(e), status="error")
            return f"[Perplexity Error: {e}]"

    # ── Kimi API ─────────────────────────────────────────────

    def ask_kimi(self, prompt: str, mode: str = "auto", context: str = "") -> str:
        """
        Kimi K2.6: Universal minion — research, code, or both.

        Args:
            prompt: The task or question
            mode: "auto", "code", or "research"
            context: Optional context from previous agents
        """
        if not self._kimi_ready:
            output = (
                "[MOCK KIMI OUTPUT]\n\n"
                "No Kimi API key configured.\n"
                "Set MOONSHOT_API_KEY to enable live Kimi inference.\n\n"
                f"# Mock: {prompt[:50]}...\n"
                "# Provide MOONSHOT_API_KEY for real Kimi K2.6."
            )
            self._log_action("kimi", prompt, output, status="mock")
            return output

        try:
            # Build system prompt based on mode
            if mode == "code":
                system = "You are an expert software engineer. Write clean, tested, documented code."
            elif mode == "research":
                system = "You are a research analyst. Cite sources, be thorough, flag uncertainties."
            else:
                system = "You are a universal AI assistant. You can code, research, analyze, and build."

            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt}
            ]

            if context:
                messages.insert(1, {"role": "user", "content": f"Context: {context}"})

            response = self._kimi.chat.completions.create(
                model="kimi-k2.6",
                messages=messages,
                temperature=0.2 if mode == "research" else 0.1,
                max_tokens=4096
            )
            output = response.choices[0].message.content
            tokens = response.usage.total_tokens if response.usage else 0

            self._log_action("kimi", prompt, output,
                           status="success", tokens_used=tokens,
                           model="kimi-k2.6",
                           metadata={"mode": mode, "had_context": bool(context)})
            return output

        except Exception as e:
            self._log_action("kimi", prompt, str(e), status="error")
            return f"[Kimi Error: {e}]"

    # ── Hybrid Pipelines ─────────────────────────────────────

    def run_hybrid_claude_perplexity(self, task: str, prompt: str) -> dict:
        """
        Original pipeline: Perplexity researches → Claude implements.
        """
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute(
            "INSERT INTO runs (run_id, timestamp, title, description, status) VALUES (?, ?, ?, ?, ?)",
            (run_id, datetime.now().isoformat(), task, prompt, "running")
        )
        conn.commit()
        conn.close()

        research = self.ask_perplexity(f"Research for: {prompt}")
        code = self.ask_claude(task=task, context=research)

        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute(
            "UPDATE runs SET status=?, research_output=?, code_output=?, completed_at=? WHERE run_id=?",
            ("completed", research[:2000], code[:2000], datetime.now().isoformat(), run_id)
        )
        conn.commit()
        conn.close()

        return {
            "run_id": run_id,
            "task": task,
            "research": research,
            "code": code,
            "success": bool(code and not code.startswith("["))
        }

    def run_hybrid_kimi(self, task: str, prompt: str) -> dict:
        """
        New pipeline: Kimi handles everything — research + code in one.
        """
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute(
            "INSERT INTO runs (run_id, timestamp, title, description, status) VALUES (?, ?, ?, ?, ?)",
            (run_id, datetime.now().isoformat(), task, prompt, "running")
        )
        conn.commit()
        conn.close()

        # Kimi does research first
        research = self.ask_kimi(f"Research this thoroughly: {prompt}", mode="research")
        # Then Kimi codes with that context
        code = self.ask_kimi(f"Implement: {task}", mode="code", context=research)

        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute(
            "UPDATE runs SET status=?, research_output=?, code_output=?, completed_at=? WHERE run_id=?",
            ("completed", research[:2000], code[:2000], datetime.now().isoformat(), run_id)
        )
        conn.commit()
        conn.close()

        return {
            "run_id": run_id,
            "task": task,
            "research": research,
            "code": code,
            "success": bool(code and not code.startswith("["))
        }

    def run_all_agents(self, task: str, prompt: str) -> dict:
        """
        Run ALL three agents and compare results.
        Best for validation and ensemble reasoning.
        """
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Run all in parallel (simulated — in async you'd use asyncio.gather)
        perplexity_result = self.ask_perplexity(prompt)
        claude_result = self.ask_claude(task, context=perplexity_result)
        kimi_result = self.ask_kimi(prompt, mode="auto", context=perplexity_result)

        return {
            "run_id": run_id,
            "task": task,
            "perplexity": perplexity_result,
            "claude": claude_result,
            "kimi": kimi_result,
            "ensemble": f"Perplexity research + Claude code + Kimi unified analysis"
        }

    # ── Query helpers ──────────────────────────────────────────

    def get_recent_logs(self, limit: int = 20) -> list:
        """Pull recent logs for display / debugging."""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute(
            "SELECT timestamp, agent, task, status, tokens_used FROM logs ORDER BY id DESC LIMIT ?",
            (limit,)
        )
        rows = c.fetchall()
        conn.close()
        return rows

    def get_runs(self, limit: int = 10) -> list:
        """Pull recent pipeline runs."""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute(
            "SELECT run_id, timestamp, title, status FROM runs ORDER BY timestamp DESC LIMIT ?",
            (limit,)
        )
        rows = c.fetchall()
        conn.close()
        return rows
