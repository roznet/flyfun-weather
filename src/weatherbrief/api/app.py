"""FastAPI app factory."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import RedirectResponse

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
from weatherbrief.api.debriefs import router as debriefs_router
from weatherbrief.api.pireps import router as pireps_router
from weatherbrief.api.flights import router as flights_router
from weatherbrief.api.packs import refresh_router, router as packs_router
from weatherbrief.api.preferences import router as preferences_router
from weatherbrief.api.profiles import admin_router as profiles_admin_router, router as profiles_router
from weatherbrief.api.admin import router as admin_router, require_admin
from weatherbrief.api.credits import (
    admin_router as cost_config_router,
    report_router as cost_report_router,
    router as credits_router,
    transparency_router,
)
from weatherbrief.api.donations import router as donations_router
from weatherbrief.api.feedback import router as feedback_router
from weatherbrief.analytics.api import router as analytics_router
from weatherbrief.analytics.admin_api import router as analytics_admin_router
from weatherbrief.api.messages import admin_router as messages_admin_router, router as messages_router
from weatherbrief.api.maps import router as maps_router
from weatherbrief.api.climatology import router as climatology_router
from weatherbrief.api.airport_profile import router as airport_profile_router
from weatherbrief.api.hewson_map import router as hewson_map_router
from weatherbrief.api.synoptic_charts import router as synoptic_charts_router
from weatherbrief.api.data_sources import router as data_sources_router
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

    metar_ingest_task = None
    if os.environ.get("DISABLE_METAR_INGEST", "").strip() not in ("1", "true"):
        from weatherbrief.scheduler import run_metar_ingest_loop

        metar_ingest_task = asyncio.create_task(
            run_metar_ingest_loop(app.state)
        )

    forecast_fetch_task = None
    if os.environ.get("DISABLE_FORECAST_FETCH") not in ("1", "true"):
        from weatherbrief.scheduler import run_forecast_fetch_loop

        forecast_fetch_task = asyncio.create_task(
            run_forecast_fetch_loop(app.state)
        )

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

    freshness_task = None
    if os.environ.get("DISABLE_FRESHNESS_LOOP", "").strip() not in ("1", "true"):
        from weatherbrief.scheduler import run_freshness_loop

        freshness_task = asyncio.create_task(run_freshness_loop(app.state))

    analytics_rollup_task = None
    analytics_digest_task = None
    if os.environ.get("DISABLE_ANALYTICS_ROLLUP", "").strip() not in ("1", "true"):
        from weatherbrief.scheduler import (
            run_analytics_digest_loop,
            run_analytics_rollup_loop,
        )

        analytics_rollup_task = asyncio.create_task(
            run_analytics_rollup_loop(app.state)
        )
        analytics_digest_task = asyncio.create_task(
            run_analytics_digest_loop(app.state)
        )

    grib_precache_task = None
    # Default off in dev so local devs don't pull ~38 GB on startup; on in prod.
    _precache_default = "false" if is_dev_mode() else "true"
    _precache_enabled = os.environ.get(
        "WB_GRIB_PRECACHE_ENABLED", _precache_default,
    ).strip().lower() in ("1", "true", "yes")
    if _precache_enabled:
        from weatherbrief.scheduler import run_grib_precache_loop

        grib_precache_task = asyncio.create_task(
            run_grib_precache_loop(app.state)
        )

    yield

    for task in (scheduler_task, retention_task, verification_task,
                 digest_task, metar_ingest_task, forecast_fetch_task,
                 standalone_task, ecmwf_watcher_task,
                 hewson_precompute_task, freshness_task,
                 analytics_rollup_task, analytics_digest_task,
                 grib_precache_task):
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    # Tear down the GRIB decode worker pool (if it was started). Worker
    # processes hold ECCODES file handles and ~270 MB RSS each — leaking
    # them on uvicorn reload would accumulate fast. Run via to_thread so
    # the synchronous pool.shutdown(wait=True) doesn't block the event loop.
    # drain_dispatcher=True also releases any caller blocked on a pending /
    # in-flight decode so they fail fast rather than hang on a vanishing pool.
    from weatherbrief.fetch.grib import shutdown_decode_pool
    await asyncio.to_thread(lambda: shutdown_decode_pool(drain_dispatcher=True))


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

    # Log Pydantic request-validation failures so 422s are diagnosable.
    # FastAPI's default returns the detail to the client but logs nothing;
    # that left iOS create-flight failures (and other 422s) opaque server-side.
    from fastapi.encoders import jsonable_encoder
    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse
    from starlette.exceptions import HTTPException as StarletteHTTPException

    _validation_logger = logging.getLogger("weatherbrief.api.validation")

    # Request bodies can carry credentials (e.g. autorouter_password on
    # PUT /api/user/preferences) — never write those to the log.
    _sensitive_key_re = re.compile(
        r"password|secret|token|api_key|apikey|authorization", re.IGNORECASE
    )

    def _redact_sensitive(value):
        if isinstance(value, dict):
            return {
                k: ("[REDACTED]" if _sensitive_key_re.search(str(k)) else _redact_sensitive(v))
                for k, v in value.items()
            }
        if isinstance(value, list):
            return [_redact_sensitive(v) for v in value]
        if isinstance(value, (str, bytes)):
            # Unparsed bodies arrive as raw str/bytes; we can't redact
            # per-field, so drop the whole thing if it looks sensitive.
            text = value.decode("utf-8", "replace") if isinstance(value, bytes) else value
            if _sensitive_key_re.search(text):
                return "[REDACTED: body contains sensitive field]"
        return value

    def _redact_errors(errors):
        # Pydantic v2 errors embed the offending value under "input" (and
        # sometimes "ctx"); when the failing field is itself sensitive
        # (loc ends in e.g. "autorouter_password"), drop the value.
        out = []
        for err in errors:
            err = dict(err)
            loc = "/".join(str(p) for p in err.get("loc", ()))
            for key in ("input", "ctx"):
                if key in err:
                    err[key] = (
                        "[REDACTED]" if _sensitive_key_re.search(loc)
                        else _redact_sensitive(err[key])
                    )
            out.append(err)
        return out

    @app.exception_handler(RequestValidationError)
    async def _log_validation_error(request: Request, exc: RequestValidationError):
        try:
            body = _redact_sensitive(exc.body)
        except Exception:
            body = None
        try:
            logged_errors = _redact_errors(exc.errors())
        except Exception:
            logged_errors = None
        _validation_logger.warning(
            "422 validation error path=%s method=%s errors=%s body=%r",
            request.url.path,
            request.method,
            logged_errors,
            body,
        )
        # Use jsonable_encoder — pydantic v2 puts the raw exception object
        # in ctx.error for custom validators (``raise ValueError(...)``),
        # which the default json encoder can't serialise. Without this
        # the response serialisation throws and Starlette turns it into
        # a 500, which is exactly what this handler exists to prevent.
        return JSONResponse(
            status_code=422,
            content={"detail": jsonable_encoder(exc.errors())},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _log_http_exception(request: Request, exc: StarletteHTTPException):
        # Surface 422s raised inside endpoint bodies (e.g. waypoint resolver
        # rejections in flights.create_flight) so they show up alongside the
        # Pydantic validation log line above. Other status codes pass through
        # silently to keep the noise floor down.
        if exc.status_code == 422:
            _validation_logger.warning(
                "422 endpoint error path=%s method=%s detail=%r",
                request.url.path,
                request.method,
                exc.detail,
            )
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=getattr(exc, "headers", None),
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
        from weatherbrief.notify.admin_email import is_admin_email

        import json as _json

        user = db.get(_UserRow, user_id)
        if not user:
            raise _HTTPException(status_code=401, detail="User not found")
        prefs = db.get(UserPreferencesRow, user_id)
        # Surface a few display prefs so pages can configure themselves without
        # a second round-trip to /user/preferences: the synoptic-map opt-in
        # (maps tab visibility) and the display-units region (visibility m vs SM).
        prefs_data: dict = {}
        if prefs and prefs.app_prefs_json:
            try:
                prefs_data = _json.loads(prefs.app_prefs_json)
            except _json.JSONDecodeError:
                prefs_data = {}
        synoptic_enabled = bool(prefs_data.get("synoptic_forecast_map_enabled", False))
        _ur = prefs_data.get("units_region")
        units_region = _ur if _ur in ("auto", "europe", "us") else "auto"
        return {
            "id": user.id,
            "email": user.email,
            "name": user.display_name,
            "approved": user.approved,
            "is_admin": is_dev_mode() or is_admin_email(user.email),
            "setup_completed": prefs.setup_completed if prefs else False,
            "synoptic_forecast_map_enabled": synoptic_enabled,
            "units_region": units_region,
        }

    # Auth router from flyfun-common (with weather-specific on_new_user callback)
    from weatherbrief.notify.magic_link_email import send_magic_link_email
    auth_router = create_auth_router(
        on_new_user=_on_new_user,
        on_delete_user=_on_delete_user,
        send_magic_link_email=send_magic_link_email,
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
    app.include_router(debriefs_router, prefix="/api")
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
    app.include_router(cost_report_router, prefix="/api")
    app.include_router(feedback_router, prefix="/api")
    app.include_router(analytics_router, prefix="/api")
    app.include_router(analytics_admin_router, prefix="/api")
    app.include_router(messages_router, prefix="/api")
    app.include_router(messages_admin_router, prefix="/api")
    app.include_router(maps_router, prefix="/api")
    app.include_router(climatology_router, prefix="/api")
    app.include_router(airport_profile_router, prefix="/api")
    app.include_router(hewson_map_router, prefix="/api")
    app.include_router(synoptic_charts_router, prefix="/api")
    app.include_router(tokens_router, prefix="/api")
    app.include_router(models_router, prefix="/api")
    app.include_router(data_sources_router, prefix="/api")
    app.include_router(refresh_router, prefix="/api")
    app.include_router(transparency_router, prefix="/api")
    app.include_router(donations_router, prefix="/api")

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

    # Short share-link redirect: /s/{code} → /briefing.html?flight=<id>.
    # Always lands on the briefing page (that's the artifact pilots
    # actually want to see); the briefing page auto-loads the latest
    # pack when no ``pack`` query is supplied. Registered before the
    # static-file mount below so it takes precedence over the catch-all.
    # Unauthenticated — auth is enforced on the destination page (the
    # redirect itself only resolves the code to an ID, which is no more
    # sensitive than the long ID URL).
    from urllib.parse import urlencode

    from weatherbrief.storage.flights import lookup_flight_id_by_share_code
    from flyfun_common.db import get_db as _get_db_for_share

    _SHARE_CODE_RE = re.compile(r"^[0-9A-Za-z]{4,16}$")

    @app.get("/s/{code}")
    def share_redirect(code: str, request: Request, db=Depends(_get_db_for_share)):
        # Reject obviously-bogus codes before hitting the DB so log spam
        # from random scanners doesn't blow up the index.
        if not _SHARE_CODE_RE.match(code):
            raise _HTTPException(status_code=404, detail="Unknown share link")
        flight_id = lookup_flight_id_by_share_code(db, code)
        if flight_id is None:
            raise _HTTPException(status_code=404, detail="Unknown share link")
        # Forward the ``pack`` query param (and only that one) so a
        # share link to a specific briefing pack still lands on the
        # right pack after the redirect. Other params are dropped to
        # keep the redirect surface tight.
        params: dict[str, str] = {"flight": flight_id}
        pack = request.query_params.get("pack")
        if pack:
            params["pack"] = pack
        return RedirectResponse(
            f"/briefing.html?{urlencode(params)}", status_code=302,
        )

    # Clean, shareable deep link to the "What's New" tab of the help page.
    # The help page is a single client-rendered document with tabs selected
    # via ``?tab=``; copying the address bar from the tab yields a bare
    # ``/help.html``. This redirect gives a stable URL to hand out instead.
    @app.get("/whats-new")
    def whats_new_redirect():
        return RedirectResponse("/help.html?tab=whats-new", status_code=302)

    # Mount static files for web UI (if directory exists)
    web_dir = Path(__file__).resolve().parent.parent.parent.parent / "web"
    if web_dir.exists():
        app.mount("/", StaticFiles(directory=str(web_dir), html=True), name="web")

    return app


app = create_app()
