"""App factory and lifespan.

One httpx.AsyncClient is created at startup and shared by every request, as
required by CLAUDE.md. It is closed on shutdown.
"""

from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from app.api.v1 import health
from app.api.v1.router import api_router
from app.clients.vendor_client import VendorClient
from app.core.config import Settings, get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.services.admin_session import AdminSession
from app.services.mailer import Mailer

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
    app.state.http_client = client
    app.state.vendor_client = VendorClient(client, settings)
    app.state.admin_session = AdminSession(app.state.vendor_client, settings)
    app.state.mailer = Mailer(settings)
    try:
        yield
    finally:
        await client.aclose()
        logger.info("shutdown complete")


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
    return app


app = create_app()
