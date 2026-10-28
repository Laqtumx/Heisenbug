"""CleanSlate API.

The FastAPI application. Routers live in ``app.api.routers``; everything they do
is a thin transport over ``app.pipeline``.

Cross-cutting concerns are installed here: CORS, rate limiting, structured
logging with a request id, and a health check that reports whether Postgres,
Mongo and the encryption key are actually usable.
"""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from app.api.routers import audit as audit_router
from app.api.routers import auth as auth_router
from app.api.routers import batches as batches_router
from app.api.routers import findings as findings_router
from app.api.routers import inventory as inventory_router
from app.api.routers import reports as reports_router
from app.api.routers import review as review_router
from app.config import settings
from app.utils.logging import configure_logging, request_id_var

logger = logging.getLogger(__name__)

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[settings.rate_limit_default],
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    from app.db import mongo

    try:
        mongo.ensure_indexes()
    except Exception:
        logger.warning("could not ensure Mongo indexes at startup", exc_info=True)

    from app.security.crypto import encryption_ready

    if not encryption_ready():
        logger.warning(
            "FIELD_ENCRYPTION_KEY is not set; sensitive columns use a derived "
            "development key. Set it before storing real data."
        )

    logger.info("%s API starting in %s mode", settings.app_name, settings.environment)
    yield
    mongo.close()
    logger.info("%s API stopping", settings.app_name)


app = FastAPI(
    title=f"{settings.app_name} API",
    version="1.0.0",
    description=(
        "AI-assisted ERP data migration. Move the data, not the mistakes.\n\n"
        "Every route requires a bearer token. Every state change writes an "
        "immutable audit entry."
    ),
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["x-request-id"],
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    """Give every request an id and log how it went.

    The id is echoed in the response header and put on every log line, so a
    support question about one request can be traced through the logs.
    """
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
    token = request_id_var.set(request_id)
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("unhandled error on %s %s", request.method, request.url.path)
        request_id_var.reset(token)
        raise
    duration_ms = int((time.perf_counter() - started) * 1000)
    response.headers["x-request-id"] = request_id
    logger.info(
        "%s %s -> %s in %sms",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
    )
    request_id_var.reset(token)
    return response


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content={"detail": "Too many requests. Slow down and try again shortly."},
    )


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Return field errors in a shape the frontend can show next to the input."""
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "detail": "The request was not valid.",
            "errors": [
                {"field": ".".join(str(p) for p in err["loc"][1:]), "message": err["msg"]}
                for err in exc.errors()
            ],
        },
    )


@app.get("/health", tags=["system"])
def health() -> dict:
    """Report whether each dependency is usable.

    Returns 200 with a per-dependency status rather than failing, because a
    partial outage is worth seeing in full rather than as one error.
    """
    from app.db import mongo
    from app.db.engine import healthcheck as pg_healthcheck
    from app.security.crypto import encryption_ready

    checks: dict = {"app": settings.app_name, "environment": settings.environment}
    try:
        checks["postgres"] = pg_healthcheck()
    except Exception as exc:
        checks["postgres"] = {"status": "error", "detail": str(exc)}
    checks["mongo"] = mongo.healthcheck()
    checks["encryption"] = "configured" if encryption_ready() else "derived-dev-key"
    checks["llm"] = settings.llm_provider if settings.llm_enabled else "disabled"
    checks["ok"] = (
        checks["postgres"].get("postgres") == "ok"
        and checks["mongo"].get("mongo") == "ok"
    )
    return checks


@app.get("/", include_in_schema=False)
def root() -> dict:
    return {
        "name": settings.app_name,
        "tagline": "Move the data, not the mistakes.",
        "docs": "/docs",
        "health": "/health",
        "api": settings.api_prefix,
    }


API = settings.api_prefix
app.include_router(auth_router.router, prefix=API)
app.include_router(batches_router.router, prefix=API)
app.include_router(findings_router.router, prefix=API)
app.include_router(review_router.router, prefix=API)
app.include_router(reports_router.router, prefix=API)
app.include_router(audit_router.router, prefix=API)
app.include_router(inventory_router.router, prefix=API)
