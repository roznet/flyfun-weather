"""FastAPI app factory."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from flyfun_common.auth import (
    SlidingSessionMiddleware,
    create_auth_router,
    get_jwt_secret,
    is_dev_mode,
)
from flyfun_common.autorouter import create_autorouter_router
from flyfun_common.oauth import create_oauth_router
from flyfun_common.db import (
    SessionLocal,
    ensure_dev_user,
    get_engine,
    init_shared_db,
)
from flyfun_common.db.models import UserPreferencesRow

from weatherbrief.api.aircraft import router as aircraft_router
from weatherbrief.api.pireps import router as pireps_router
from weatherbrief.api.flights import router as flights_router
from weatherbrief.api.packs import refresh_router, router as packs_router
from weatherbrief.api.preferences import router as preferences_router
from weatherbrief.api.profiles import admin_router as profiles_admin_router, router as profiles_router
from weatherbrief.api.admin import router as admin_router, require_admin
from weatherbrief.api.credits import (
    admin_router as cost_config_router,
    router as credits_router,
    transparency_router,
)
from weatherbrief.api.feedback import router as feedback_router
from weatherbrief.api.messages import admin_router as messages_admin_router, router as messages_router
from weatherbrief.api.maps import router as maps_router
from weatherbrief.api.models import router as models_router
from weatherbrief.api.tokens import router as tokens_router
from weatherbrief.api.usage import router as usage_router
from flyfun_common.admin_hub import create_hub_router

logger = logging.getLogger(__name__)


def _on_delete_user(user_id: str, db):
    """Callback for account deletion: clean up all weatherbrief-specific data."""
    from pathlib import Path
    from weatherbrief.db.models import (
        BriefingUsageRow, DeviceTokenRow, FeedbackRow, FlightProfileRow,
        FlightRow, PirepRow, UserAircraftRow,
    )
    from weatherbrief.storage.flights import _data_dir, _rmtree, safe_path_component

    # Delete artifact files for all user's flights
    for flight in db.query(FlightRow).filter(FlightRow.user_id == user_id).all():
        for pack in flight.packs:
            if pack.artifact_path:
                _rmtree(Path(pack.artifact_path))

    # Remove the entire user packs directory (catches any orphaned files)
    user_pack_dir = _data_dir() / "packs" / safe_path_component(user_id)
    _rmtree(user_pack_dir)

    # Delete DB rows — FlightRow cascade-deletes BriefingPackRow
    db.query(FlightRow).filter(FlightRow.user_id == user_id).delete(
        synchronize_session=False
    )
    db.query(FlightProfileRow).filter(
        FlightProfileRow.user_id == user_id
    ).delete(synchronize_session=False)
    db.query(BriefingUsageRow).filter(
        BriefingUsageRow.user_id == user_id
    ).delete(synchronize_session=False)
    db.query(FeedbackRow).filter(
        FeedbackRow.user_id == user_id
    ).delete(synchronize_session=False)
    db.query(UserAircraftRow).filter(
        UserAircraftRow.user_id == user_id
    ).delete(synchronize_session=False)
    # Anonymize PIREPs — preserve observation data, remove identity
    db.query(PirepRow).filter(PirepRow.user_id == user_id).update(
        {"user_id": None, "aircraft_id": None}, synchronize_session=False
    )
    # Delete device tokens entirely
    db.query(DeviceTokenRow).filter(
        DeviceTokenRow.user_id == user_id
    ).delete(synchronize_session=False)
    db.flush()

    logger.info("Deleted weatherbrief data for user %s", user_id)


def _on_new_user(user, request, db):
    """Callback for new user registration: create prefs row + send emails."""
    db.add(UserPreferencesRow(user_id=user.id))
    db.flush()

    try:
        from weatherbrief.notify.admin_email import send_welcome_email

        base_url = str(request.base_url).rstrip("/")
        if not is_dev_mode():
            base_url = base_url.replace("http://", "https://")
        send_welcome_email(user.email, user.display_name, base_url)
    except Exception:
        logger.warning("Failed to send welcome email for new user %s", user.email, exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle."""
    env = os.environ.get("ENVIRONMENT", "development")
    engine = get_engine()

    if env == "development":
        # Import app models to register them on Base before create_all
        import weatherbrief.db.models  # noqa: F401
        init_shared_db(engine)
        logger.info("Dev mode: tables created")

    if is_dev_mode():
        with SessionLocal() as session:
            ensure_dev_user(session)
            # Ensure a default cost config exists
            from weatherbrief.api.credits import get_active_cost_config
            from weatherbrief.costs import DEFAULT_CONFIG
            from weatherbrief.db.models import CostConfigRow

            if not get_active_cost_config(session):
                session.add(CostConfigRow(config_json=DEFAULT_CONFIG.to_json()))
                session.commit()
                logger.info("Seeded default cost config")
        logger.info("Dev user ensured")

    scheduler_task = None
    if os.environ.get("DISABLE_SCHEDULER") != "1":
        from weatherbrief.scheduler import run_scheduler_loop

        scheduler_task = asyncio.create_task(run_scheduler_loop(app.state))

    retention_task = None
    if os.environ.get("DISABLE_RETENTION") != "1":
        from weatherbrief.scheduler import run_retention_loop

        retention_task = asyncio.create_task(run_retention_loop(app.state))

    verification_task = None
    if os.environ.get("DISABLE_VERIFICATION") != "1":
        from weatherbrief.scheduler import run_verification_loop

        verification_task = asyncio.create_task(run_verification_loop(app.state))

    digest_task = None
    if os.environ.get("DISABLE_DIGEST") != "1":
        from weatherbrief.scheduler import run_digest_loop

        digest_task = asyncio.create_task(run_digest_loop(app.state))

    standalone_task = None
    if os.environ.get("DISABLE_STANDALONE_VERIFICATION") not in ("1", "true"):
        from weatherbrief.scheduler import run_standalone_verification_loop

        standalone_task = asyncio.create_task(
            run_standalone_verification_loop(app.state)
        )

    ecmwf_watcher_task = None
    if os.environ.get("DISABLE_ECMWF_WATCHER") != "1":
        from weatherbrief.fetch.grib.ecmwf_fetch import ecmwf_grib_dir

        ecmwf_dir = ecmwf_grib_dir()
        if ecmwf_dir.exists():
            from weatherbrief.scheduler import run_ecmwf_watcher_loop

            ecmwf_watcher_task = asyncio.create_task(
                run_ecmwf_watcher_loop(app.state)
            )
        else:
            logger.info("ECMWF watcher skipped — %s does not exist", ecmwf_dir)

    hewson_precompute_task = None
    if os.environ.get("DISABLE_HEWSON_PRECOMPUTE", "").strip() not in ("1", "true"):
        from weatherbrief.scheduler import run_hewson_precompute_loop

        hewson_precompute_task = asyncio.create_task(
            run_hewson_precompute_loop(app.state)
        )

    yield

    for task in (scheduler_task, retention_task, verification_task,
                 digest_task, standalone_task, ecmwf_watcher_task,
                 hewson_precompute_task):
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    load_dotenv()

    # Ensure app-level loggers (scheduler, pipeline, etc.) are visible
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")

    # Disable interactive API docs in production to reduce attack surface
    docs_kwargs = {}
    if not is_dev_mode():
        docs_kwargs = {"docs_url": None, "redoc_url": None, "openapi_url": None}

    app = FastAPI(
        title="WeatherBrief API",
        description="Aviation weather briefing API",
        version="0.1.0",
        lifespan=lifespan,
        **docs_kwargs,
    )

    app.state.db_path = os.environ.get("AIRPORTS_DB", "")
    app.state.data_dir = Path(os.environ.get("DATA_DIR", "data"))

    # SessionMiddleware required by authlib for OAuth CSRF state
    # Apple OAuth uses response_mode=form_post (cross-origin POST),
    # so SameSite must be "none" + https_only for the cookie to survive.
    app.add_middleware(
        SessionMiddleware,
        secret_key=get_jwt_secret(),
        same_site="none",
        https_only=not is_dev_mode(),
    )

    # Rolling JWT cookie — refreshes flyfun_auth when it drops below the
    # refresh threshold so active users stay logged in up to the hard cap.
    app.add_middleware(SlidingSessionMiddleware)

    if is_dev_mode():
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # Weather-specific /auth/me (adds is_admin, setup_completed) — must be
    # registered BEFORE common's auth router so it takes priority.
    from fastapi import Depends
    from flyfun_common.db import current_user_id, get_db as _get_db
    from flyfun_common.db.models import UserRow as _UserRow
    from fastapi import HTTPException as _HTTPException

    @app.get("/auth/me", tags=["auth"])
    async def get_me(
        user_id: str = Depends(current_user_id),
        db=Depends(_get_db),
    ):
        from weatherbrief.notify.admin_email import get_admin_emails

        user = db.get(_UserRow, user_id)
        if not user:
            raise _HTTPException(status_code=401, detail="User not found")
        prefs = db.get(UserPreferencesRow, user_id)
        return {
            "id": user.id,
            "email": user.email,
            "name": user.display_name,
            "approved": user.approved,
            "is_admin": is_dev_mode() or user.email in get_admin_emails(),
            "setup_completed": prefs.setup_completed if prefs else False,
        }

    # Auth router from flyfun-common (with weather-specific on_new_user callback)
    auth_router = create_auth_router(
        on_new_user=_on_new_user,
        on_delete_user=_on_delete_user,
    )
    app.include_router(create_autorouter_router())
    app.include_router(create_oauth_router())
    app.include_router(auth_router)

    # Dev-only endpoint: issue a JWT for the dev user without OAuth
    if is_dev_mode():
        from flyfun_common.auth import create_token
        from flyfun_common.db import DEV_USER_ID

        @app.get("/auth/dev-token")
        def dev_token():
            token = create_token(DEV_USER_ID, "dev@localhost", "Dev User", get_jwt_secret())
            return {"token": token}

    app.include_router(aircraft_router, prefix="/api")
    app.include_router(pireps_router, prefix="/api")
    app.include_router(flights_router, prefix="/api")
    app.include_router(packs_router, prefix="/api")
    app.include_router(preferences_router, prefix="/api")
    app.include_router(profiles_router, prefix="/api")
    app.include_router(profiles_admin_router, prefix="/api")
    app.include_router(usage_router, prefix="/api")
    app.include_router(credits_router, prefix="/api")
    app.include_router(admin_router, prefix="/api")
    app.include_router(cost_config_router, prefix="/api")
    app.include_router(feedback_router, prefix="/api")
    app.include_router(messages_router, prefix="/api")
    app.include_router(messages_admin_router, prefix="/api")
    app.include_router(maps_router, prefix="/api")
    app.include_router(tokens_router, prefix="/api")
    app.include_router(models_router, prefix="/api")
    app.include_router(refresh_router, prefix="/api")
    app.include_router(transparency_router, prefix="/api")

    hub_router = create_hub_router(
        require_admin=require_admin,
        app_registry={
            "flyfun-weather": "/user-costs.html?user={user_id}",
            "flyfun-maps": None,
            "flyfun-forms": None,
        },
    )
    app.include_router(hub_router, prefix="/api/admin")

    @app.get("/health")
    def health():
        return {"status": "ok"}

    # Mount static files for web UI (if directory exists)
    web_dir = Path(__file__).resolve().parent.parent.parent.parent / "web"
    if web_dir.exists():
        app.mount("/", StaticFiles(directory=str(web_dir), html=True), name="web")

    return app


app = create_app()
