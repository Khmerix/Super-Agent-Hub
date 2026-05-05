"""
ConnectionManager — WebSocket broadcast hub.
Import this anywhere (orchestrator, agents, etc.) to broadcast to all terminals.
"""
from fastapi import WebSocket
from typing import List


class ConnectionManager:
    """Manages active WebSocket connections for live terminal broadcasts."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.active_connections: List[WebSocket] = []
        return cls._instance

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        """Broadcast a raw string to ALL connected terminal clients."""
        dead = []
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except Exception:
                dead.append(connection)
        for d in dead:
            self.disconnect(d)

    async def broadcast_json(self, data: dict):
        import json
        await self.broadcast(json.dumps(data))


# Global singleton — import this from anywhere
manager = ConnectionManager()
