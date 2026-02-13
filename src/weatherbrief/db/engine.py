"""Database engine configuration and initialization."""

from __future__ import annotations

import logging
import os

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from weatherbrief.db.models import Base, UserPreferencesRow, UserRow

logger = logging.getLogger(__name__)

_engine: Engine | None = None
SessionLocal: sessionmaker[Session] = sessionmaker()

DEV_USER_ID = "dev-user-001"


def get_engine(db_url: str | None = None) -> Engine:
    """Return a singleton SQLAlchemy engine.

    Defaults:
      - ENVIRONMENT=development → sqlite:///data/weatherbrief.db
      - ENVIRONMENT=production  → DATABASE_URL env var (MySQL)
    """
    global _engine
    if _engine is not None:
        return _engine

    if db_url is None:
        env = os.environ.get("ENVIRONMENT", "development")
        if env == "production":
            db_url = os.environ.get("DATABASE_URL")
            if not db_url:
                raise ValueError(
                    "DATABASE_URL environment variable must be set in production"
                )
        else:
            data_dir = os.environ.get("DATA_DIR", "data")
            os.makedirs(data_dir, exist_ok=True)
            db_url = f"sqlite:///{data_dir}/weatherbrief.db"

    connect_args = {}
    if db_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        connect_args["timeout"] = 30

    _engine = create_engine(db_url, connect_args=connect_args)
    SessionLocal.configure(bind=_engine)

    # Enable WAL mode for SQLite concurrency
    if db_url.startswith("sqlite"):
        @event.listens_for(_engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, _connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    logger.info("Database engine created: %s", db_url.split("@")[-1])
    return _engine


def reset_engine() -> None:
    """Reset the singleton engine (for testing)."""
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = None
    SessionLocal.configure(bind=None)


def init_db(engine: Engine | None = None) -> None:
    """Create all tables. Use in dev mode; prod uses Alembic."""
    engine = engine or get_engine()
    Base.metadata.create_all(engine)
    logger.info("Database tables created")


def ensure_dev_user(session: Session) -> None:
    """Upsert a dev user for local development."""
    user = session.get(UserRow, DEV_USER_ID)
    if user is None:
        user = UserRow(
            id=DEV_USER_ID,
            provider="local",
            provider_sub="dev",
            email="dev@localhost",
            display_name="Dev User",
            approved=True,
        )
        session.add(user)
        prefs = UserPreferencesRow(user_id=DEV_USER_ID)
        session.add(prefs)
        session.commit()
        logger.info("Dev user created: %s", DEV_USER_ID)
