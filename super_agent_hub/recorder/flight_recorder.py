"""
Flight Recorder - Persistent audit logging for the Super Agent Hub.
SQLite-backed storage for every input, output, state change, and agent thought.
"""
import json
import sqlite3
import aiosqlite
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, asdict
from contextlib import asynccontextmanager

DB_PATH = Path("./data/flight_recorder.db")


class FlightRecorder:
    """
    Async SQLite recorder for complete execution observability.
    
    Schema:
        runs        - Top-level task executions
        steps       - State machine checkpoints (snapshots)
        messages    - Agent I/O (user input, agent responses)
        thoughts    - Agent reasoning traces and tool call chains
    """

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self._ensure_db()

    def _ensure_db(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(_SCHEMA)
            conn.commit()

    @asynccontextmanager
    async def _connect(self):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            yield db

    # ── Runs ───────────────────────────────────────────────

    async def start_run(self, run_id: str, task_id: str, task_title: str,
                        initial_state: Dict[str, Any]) -> None:
        async with self._connect() as db:
            await db.execute(
                """INSERT INTO runs (run_id, task_id, title, status, state_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (run_id, task_id, task_title, "PENDING",
                 json.dumps(initial_state), datetime.utcnow().isoformat())
            )
            await db.commit()

    async def update_run_status(self, run_id: str, status: str,
                                final_state: Optional[Dict[str, Any]] = None) -> None:
        async with self._connect() as db:
            if final_state:
                await db.execute(
                    """UPDATE runs SET status=?, state_json=?, completed_at=?
                       WHERE run_id=?""",
                    (status, json.dumps(final_state), datetime.utcnow().isoformat(), run_id)
                )
            else:
                await db.execute(
                    "UPDATE runs SET status=? WHERE run_id=?",
                    (status, run_id)
                )
            await db.commit()

    async def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        async with self._connect() as db:
            cursor = await db.execute(
                "SELECT * FROM runs WHERE run_id=?", (run_id,)
            )
            row = await cursor.fetchone()
            if row:
                return dict(row)
            return None

    async def list_runs(self, limit: int = 50) -> List[Dict[str, Any]]:
        async with self._connect() as db:
            cursor = await db.execute(
                "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?",
                (limit,)
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    # ── Steps (Checkpoints) ───────────────────────────────

    async def checkpoint(self, run_id: str, step_number: int, node_name: str,
                         state_snapshot: Dict[str, Any],
                         parent_checkpoint_id: Optional[int] = None) -> int:
        """Save a state snapshot. Returns checkpoint_id for revert targeting."""
        async with self._connect() as db:
            cursor = await db.execute(
                """INSERT INTO steps (run_id, step_number, node_name, state_json,
                                     parent_checkpoint_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (run_id, step_number, node_name, json.dumps(state_snapshot),
                 parent_checkpoint_id, datetime.utcnow().isoformat())
            )
            await db.commit()
            return cursor.lastrowid

    async def get_checkpoint(self, checkpoint_id: int) -> Optional[Dict[str, Any]]:
        async with self._connect() as db:
            cursor = await db.execute(
                "SELECT * FROM steps WHERE step_id=?", (checkpoint_id,)
            )
            row = await cursor.fetchone()
            if row:
                d = dict(row)
                d["state"] = json.loads(d["state_json"])
                return d
            return None

    async def get_latest_checkpoint(self, run_id: str) -> Optional[Dict[str, Any]]:
        async with self._connect() as db:
            cursor = await db.execute(
                """SELECT * FROM steps WHERE run_id=?
                   ORDER BY step_number DESC, step_id DESC LIMIT 1""",
                (run_id,)
            )
            row = await cursor.fetchone()
            if row:
                d = dict(row)
                d["state"] = json.loads(d["state_json"])
                return d
            return None

    async def list_checkpoints(self, run_id: str) -> List[Dict[str, Any]]:
        async with self._connect() as db:
            cursor = await db.execute(
                "SELECT * FROM steps WHERE run_id=? ORDER BY step_number",
                (run_id,)
            )
            rows = await cursor.fetchall()
            result = []
            for r in rows:
                d = dict(r)
                d["state"] = json.loads(d["state_json"])
                result.append(d)
            return result

    # ── Messages ────────────────────────────────────────────

    async def log_message(self, run_id: str, step_id: Optional[int],
                          role: str, content: str, agent_id: Optional[str] = None,
                          metadata: Optional[Dict] = None) -> int:
        async with self._connect() as db:
            cursor = await db.execute(
                """INSERT INTO messages (run_id, step_id, role, content,
                                         agent_id, metadata_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (run_id, step_id, role, content, agent_id,
                 json.dumps(metadata or {}), datetime.utcnow().isoformat())
            )
            await db.commit()
            return cursor.lastrowid

    async def get_messages(self, run_id: str) -> List[Dict[str, Any]]:
        async with self._connect() as db:
            cursor = await db.execute(
                "SELECT * FROM messages WHERE run_id=? ORDER BY msg_id",
                (run_id,)
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    # ── Thoughts ───────────────────────────────────────────

    async def log_thought(self, run_id: str, step_id: Optional[int],
                          agent_id: str, thought: str,
                          tool_calls: Optional[List[Dict]] = None,
                          tokens_used: int = 0) -> int:
        async with self._connect() as db:
            cursor = await db.execute(
                """INSERT INTO thoughts (run_id, step_id, agent_id, thought,
                                        tool_calls_json, tokens_used, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (run_id, step_id, agent_id, thought,
                 json.dumps(tool_calls or []), tokens_used,
                 datetime.utcnow().isoformat())
            )
            await db.commit()
            return cursor.lastrowid

    async def get_thoughts(self, run_id: str, agent_id: Optional[str] = None) -> List[Dict]:
        async with self._connect() as db:
            if agent_id:
                cursor = await db.execute(
                    "SELECT * FROM thoughts WHERE run_id=? AND agent_id=? ORDER BY thought_id",
                    (run_id, agent_id)
                )
            else:
                cursor = await db.execute(
                    "SELECT * FROM thoughts WHERE run_id=? ORDER BY thought_id",
                    (run_id,)
                )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    # ── Full Trace Export ───────────────────────────────────

    async def export_trace(self, run_id: str) -> Dict[str, Any]:
        """Export a complete execution trace for a run."""
        run = await self.get_run(run_id)
        if not run:
            return {}

        checkpoints = await self.list_checkpoints(run_id)
        messages = await self.get_messages(run_id)
        thoughts = await self.get_thoughts(run_id)

        return {
            "run": run,
            "checkpoints": checkpoints,
            "messages": messages,
            "thoughts": thoughts,
            "export_time": datetime.utcnow().isoformat()
        }


# ── Schema ──────────────────────────────────────────────────
_SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS runs (
    run_id          TEXT PRIMARY KEY,
    task_id         TEXT NOT NULL,
    title           TEXT,
    status          TEXT DEFAULT 'PENDING',
    state_json      TEXT,
    created_at      TEXT,
    completed_at    TEXT
);

CREATE TABLE IF NOT EXISTS steps (
    step_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id              TEXT NOT NULL REFERENCES runs(run_id),
    step_number         INTEGER NOT NULL,
    node_name           TEXT,
    state_json          TEXT NOT NULL,
    parent_checkpoint_id INTEGER,
    created_at          TEXT
);

CREATE INDEX IF NOT EXISTS idx_steps_run ON steps(run_id, step_number);

CREATE TABLE IF NOT EXISTS messages (
    msg_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT NOT NULL REFERENCES runs(run_id),
    step_id         INTEGER REFERENCES steps(step_id),
    role            TEXT,           -- 'user', 'agent', 'system', 'tool'
    content         TEXT,
    agent_id        TEXT,
    metadata_json   TEXT,
    created_at      TEXT
);

CREATE INDEX IF NOT EXISTS idx_messages_run ON messages(run_id);

CREATE TABLE IF NOT EXISTS thoughts (
    thought_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT NOT NULL REFERENCES runs(run_id),
    step_id         INTEGER REFERENCES steps(step_id),
    agent_id        TEXT,
    thought         TEXT,
    tool_calls_json TEXT,
    tokens_used     INTEGER DEFAULT 0,
    created_at      TEXT
);

CREATE INDEX IF NOT EXISTS idx_thoughts_run ON thoughts(run_id);
"""
