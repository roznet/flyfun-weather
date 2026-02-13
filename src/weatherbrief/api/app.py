"""FastAPI app factory."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from weatherbrief.api.flights import router as flights_router
from weatherbrief.api.packs import router as packs_router
from weatherbrief.api.routes import router as routes_router
from weatherbrief.db.engine import (
    SessionLocal,
    ensure_dev_user,
    get_engine,
    init_db,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle."""
    env = os.environ.get("ENVIRONMENT", "development")
    engine = get_engine()

    if env == "development":
        init_db(engine)
        logger.info("Dev mode: tables created via init_db")

    # Ensure the dev user exists until auth (Phase 2) is implemented.
    # In production current_user_id() still returns DEV_USER_ID, so the
    # row must exist or FK constraints on flights/packs will fail.
    with SessionLocal() as session:
        ensure_dev_user(session)
    logger.info("Dev user ensured")

    yield


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    load_dotenv()

    app = FastAPI(
        title="WeatherBrief API",
        description="Aviation weather briefing API",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.state.db_path = os.environ.get("AIRPORTS_DB", "")
    app.state.data_dir = Path(os.environ.get("DATA_DIR", "data"))

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
