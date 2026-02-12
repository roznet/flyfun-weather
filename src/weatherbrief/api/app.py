"""FastAPI app factory."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from weatherbrief.api.flights import router as flights_router
from weatherbrief.api.packs import router as packs_router
from weatherbrief.api.routes import router as routes_router


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    load_dotenv()

    app = FastAPI(
        title="WeatherBrief API",
        description="Aviation weather briefing API",
        version="0.1.0",
    )

    app.state.db_path = os.environ.get("AIRPORTS_DB", "")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(routes_router, prefix="/api")
    app.include_router(flights_router, prefix="/api")
    app.include_router(packs_router, prefix="/api")

    @app.get("/health")
    def health():
        return {"status": "ok"}

    # Mount static files for web UI (if directory exists)
    web_dir = Path(__file__).resolve().parent.parent.parent.parent / "web"
    if web_dir.exists():
        app.mount("/", StaticFiles(directory=str(web_dir), html=True), name="web")

    return app


app = create_app()
