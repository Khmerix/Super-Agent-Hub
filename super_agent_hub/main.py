"""
main.py - Entry point for the Super Agent Hub.
Starts the FastAPI server with uvicorn.

Usage:
    python super_agent_hub/main.py
    uvicorn super_agent_hub.main:app --reload
"""
import uvicorn
from super_agent_hub.api.server import create_app

app = create_app()

if __name__ == "__main__":
    uvicorn.run(
        "super_agent_hub.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )
