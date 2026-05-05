"""
MinionPool — Direct API layer for Claude and Perplexity.
Every call is automatically logged to SQLite.

Usage:
    from minions.pool import MinionPool
    pool = MinionPool()
    research = pool.ask_researcher("Compare vector databases for RAG")
    code = pool.ask_coder("Build a FastAPI auth module", context=research)
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
    Two-minion API pool:
      - Perplexity (sonar-pro) → research, facts, comparisons
      - Claude (claude-3-7-sonnet-20250219) → code, architecture, implementation
    
    Every call is auto-logged to super_agent.db via _log_action().
    """

    def __init__(self, db_path: str = "super_agent.db"):
        self.db_path = db_path
        self._claude_ready = False
        self._perplexity_ready = False
        self._claude = None
        self._perplexity = None
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
        Every ask_researcher / ask_coder call auto-logs here.
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
        """Initialize both API clients from environment keys."""
        claude_key = os.getenv("ANTHROPIC_API_KEY")
        if Anthropic and claude_key:
            try:
                self._claude = Anthropic(api_key=claude_key)
                self._claude_ready = True
            except Exception as e:
                print(f"[MinionPool] Claude init failed: {e}")
        else:
            print("[MinionPool] Claude: set ANTHROPIC_API_KEY + pip install anthropic")

        pplx_key = os.getenv("PERPLEXITY_API_KEY")
        if OpenAI and pplx_key:
            try:
                self._perplexity = OpenAI(api_key=pplx_key, base_url="https://api.perplexity.ai")
                self._perplexity_ready = True
            except Exception as e:
                print(f"[MinionPool] Perplexity init failed: {e}")
        else:
            print("[MinionPool] Perplexity: set PERPLEXITY_API_KEY + pip install openai")

    @property
    def ready(self) -> dict:
        return {"claude": self._claude_ready, "perplexity": self._perplexity_ready}

    # ── Public API ─────────────────────────────────────────────

    def ask_researcher(self, prompt: str) -> str:
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

    def ask_coder(self, task: str, context: str = "") -> str:
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

    def run_hybrid(self, task: str, prompt: str) -> dict:
        """
        Full pipeline: Perplexity researches → Claude implements.
        Logs each step + the combined run.
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

        # Step 1: Research (always calls — mock mode handles missing keys)
        research = self.ask_researcher(f"Research for: {prompt}")

        # Step 2: Code with research context (always calls)
        code = self.ask_coder(task=task, context=research)

        # Mark complete
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
