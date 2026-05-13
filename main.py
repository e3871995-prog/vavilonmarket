"""Entry point for `uvicorn main:app` (Fly.io / deploy backend tool).

The actual application lives in :mod:`app.web`. This module just re-exports
the FastAPI instance so the auto-generated Dockerfile finds it.
"""
from app.web import app

__all__ = ["app"]
