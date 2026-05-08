"""
Super Agent Hub — Entry point.

Usage:
    python -m super_agent_hub.main
    uvicorn super_agent_hub.main:app --reload

Requires ANTHROPIC_API_KEY and PERPLEXITY_API_KEY in .env or environment.
"""
from dotenv import load_dotenv
load_dotenv()  # Load .env file BEFORE any imports that use env vars

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
