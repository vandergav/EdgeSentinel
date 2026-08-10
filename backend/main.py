"""
Uvicorn entrypoint for the EdgeOne agent platform backend.

    python main.py
    # or
    uvicorn main:app --reload --port 8000

For the old manual Runner/SessionService REPL (no HTTP server, just a
terminal chat loop against the same root_agent), see cli.py instead —
useful for quickly testing agent changes without the frontend/runtime
pieces running.
"""
import os

from dotenv import load_dotenv

load_dotenv()

from app.server import app  # noqa: E402  (must load .env before importing app.server)

__all__ = ["app"]

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), reload=True)
