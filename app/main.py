"""App factory and lifespan.

One httpx.AsyncClient is created at startup and shared by every request, as
required by CLAUDE.md. It is closed on shutdown.
"""

from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from app.api.v1 import dpr_analysis, health, intake
from app.api.v1.router import api_router
from app.clients.otp_client import OtpClient, build_otp_http_client
from app.clients.vendor_client import VendorClient
from app.core.config import Settings, get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.services.admin_session import AdminSession
from app.services.dpr_agentic.jobs import engine_for
from app.services.dpr_agentic.migrate import apply_migrations
from app.services.dpr_agentic.worker_process import (
    start_if_enabled as start_dpr_worker_if_enabled,
)
from app.services.intake.processor import IntakeProcessor
from app.services.intake.worker import IntakeWorker
from app.services.mailer import Mailer
from app.services.otp import OtpGuard

logger = get_logger(__name__)


def build_http_client(settings: Settings) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=settings.vendor_base_url,
        timeout=httpx.Timeout(
            settings.vendor_timeout_seconds,
            connect=settings.vendor_connect_timeout_seconds,
        ),
        limits=httpx.Limits(max_connections=settings.vendor_max_connections),
        verify=settings.vendor_verify_ssl,
    )


def build_gemini_http_client() -> httpx.AsyncClient:
    """Separate client for the public Gemini API.

    Must never inherit `vendor_verify_ssl`: that flag is a workaround for the
    vendor's own broken cert chain (see CLAUDE.md) and is routinely False in
    local/sandbox runs. Reusing the vendor client here would silently disable
    TLS verification for calls that carry the Gemini API key and uploaded DPR
    document text to a public third-party host.
    """
    return httpx.AsyncClient(timeout=60.0, verify=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: Settings = app.state.settings
    configure_logging(settings.log_level)
    logger.info(
        "startup app=%s environment=%s context_path=%s vendor_base_url=%s "
        "verify_ssl=%s",
        settings.app_name,
        settings.environment,
        settings.context_path or "/",
        settings.vendor_base_url,
        settings.vendor_verify_ssl,
    )

    client = build_http_client(settings)
    gemini_client = build_gemini_http_client()
    app.state.http_client = client
    app.state.gemini_http_client = gemini_client
    app.state.vendor_client = VendorClient(client, settings)
    app.state.admin_session = AdminSession(app.state.vendor_client, settings)
    app.state.mailer = Mailer(settings)
    app.state.dpr_engine = build_dpr_engine(settings)
    app.state.intake_engine = build_intake_engine(settings, app.state.dpr_engine)
    app.state.intake_worker = start_intake_worker(app, settings)
    # The OTP service is a separate host with its own key: its own client.
    otp_http = build_otp_http_client(settings)
    app.state.otp_client = OtpClient(otp_http, settings)
    app.state.otp_guard = OtpGuard.from_settings(settings)
    app.state.dpr_worker = await start_dpr_worker_if_enabled(settings)
    try:
        yield
    finally:
        if app.state.intake_worker is not None:
            await app.state.intake_worker.stop()
        if app.state.dpr_worker is not None:
            await app.state.dpr_worker.stop()
        await client.aclose()
        await gemini_client.aclose()
        if app.state.dpr_engine is not None:
            app.state.dpr_engine.dispose()
        if (
            app.state.intake_engine is not None
            and app.state.intake_engine is not app.state.dpr_engine
        ):
            app.state.intake_engine.dispose()
        await otp_http.aclose()
        logger.info("shutdown complete")


def build_dpr_engine(settings: Settings):
    """The DPR job-registry/report-artifact Postgres engine, or None.

    Unset dpr_database_url (or the feature flag off) leaves durable job
    tracking and report storage disabled for this run: /document-with-analysis
    still works ephemerally as before, but /services/{code}/report always
    503s. Migrations are additive and idempotent, so applying them on every
    startup is safe with several replicas starting concurrently; a failure
    here must never crash the whole app, since the rest of the dashboard
    doesn't depend on this database.
    """
    if not (settings.dpr_agentic_enabled and settings.dpr_database_url):
        return None
    try:
        engine = engine_for(settings.dpr_database_url.get_secret_value())
        apply_migrations(engine)
    except Exception:
        logger.exception(
            "dpr_agentic_db_unavailable -- durable job tracking and report "
            "storage are disabled for this run"
        )
        return None
    logger.info("dpr_agentic_db_ready")
    return engine


def build_intake_engine(settings: Settings, dpr_engine):
    """The Core LAS Postgres engine for email-intake events, or None.

    Same database as the DPR jobs (dpr_database_url); reuses the DPR engine
    when that feature is on, otherwise opens its own. None (intake disabled,
    no database, or unreachable) makes the intake route answer 503, which
    LAS_Email retries -- it never crashes the app.
    """
    if not (settings.intake_enabled and settings.dpr_database_url):
        return None
    if dpr_engine is not None:
        return dpr_engine
    try:
        engine = engine_for(settings.dpr_database_url.get_secret_value())
        apply_migrations(engine)
    except Exception:
        logger.exception(
            "intake_db_unavailable -- email intake is disabled for this run"
        )
        return None
    logger.info("intake_db_ready")
    return engine


def start_intake_worker(app: FastAPI, settings: Settings):
    """The background loop acting on intake events, or None.

    Needs the intake database and its encryption key, and
    intake_worker_enabled (tests turn it off and drive the processor
    directly).
    """
    engine = app.state.intake_engine
    if engine is None or settings.intake_encryption_key is None:
        return None
    if not settings.intake_worker_enabled:
        return None
    processor = IntakeProcessor(
        engine,
        settings,
        app.state.vendor_client,
        app.state.admin_session,
        app.state.mailer,
    )
    worker = IntakeWorker(processor, settings.intake_poll_seconds)
    worker.start()
    return worker


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    prefix = settings.context_path
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description=(
            "Backend-for-frontend between the LAS dashboard and the iWallet "
            "vendor API. See api_docs/ENDPOINTS.md for the vendor contract."
        ),
        lifespan=lifespan,
        docs_url=f"{prefix}/docs",
        redoc_url=f"{prefix}/redoc",
        openapi_url=f"{prefix}/openapi.json",
    )
    app.state.settings = settings
    register_exception_handlers(app)
    app.include_router(health.router, prefix=prefix)
    app.include_router(api_router, prefix=prefix)
    # Off the versioned /api/v1 surface on purpose -- see router.py's note.
    app.include_router(dpr_analysis.router, prefix=prefix)
    # Service-to-service (LAS_Email), also off the dashboard's /api/v1.
    app.include_router(intake.router, prefix=prefix)
    return app


app = create_app()
