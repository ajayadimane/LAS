"""App factory and lifespan.

One httpx.AsyncClient is created at startup and shared by every request, as
required by CLAUDE.md. It is closed on shutdown.
"""

from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from app.api.v1 import dpr_analysis, health
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
    # The OTP service is a separate host with its own key: its own client.
    otp_http = build_otp_http_client(settings)
    app.state.otp_client = OtpClient(otp_http, settings)
    app.state.otp_guard = OtpGuard.from_settings(settings)
    app.state.dpr_worker = await start_dpr_worker_if_enabled(settings)
    try:
        yield
    finally:
        if app.state.dpr_worker is not None:
            await app.state.dpr_worker.stop()
        await client.aclose()
        await gemini_client.aclose()
        if app.state.dpr_engine is not None:
            app.state.dpr_engine.dispose()
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
    return app


app = create_app()
