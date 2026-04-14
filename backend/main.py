"""
Wuryn Platform — Main Application Entry Point
===============================================
FastAPI application factory. Mounts all routers and configures
middleware, CORS, logging, and lifecycle events.

ARCHITECTURE:
    This file is intentionally thin — it only wires together
    the routers defined in backend/routers/. All business logic
    lives in the routers and modules directories.

ROUTERS (Phase 1):
    /webhook  — WhatsApp Cloud API integration (live)

ROUTERS (Phase 2+):
    /auth        — Business owner authentication
    /dashboard   — Store management API
    /storefront  — Customer-facing store API
    /social      — Posts, reviews, follows
    /analytics   — Revenue and engagement metrics

DEPLOYMENT:
    Local:  uvicorn backend.main:app --reload --port 8000
    Render: uvicorn backend.main:app --host 0.0.0.0 --port $PORT
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.config import (
    APP_NAME, APP_VERSION, APP_ENV,
    CORS_ORIGINS, DEBUG,
    validate_config, get_config_summary,
)
from backend.routers import webhook


# ─── Logging Configuration ────────────────────────────────────────────────────
# Structured log format for Render.com log viewer compatibility.
# All module loggers inherit this configuration.
logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format="%(asctime)s [%(levelname)-8s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# APPLICATION LIFECYCLE
# ═══════════════════════════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application startup and shutdown lifecycle manager.

    Startup:
        1. Validate all required environment variables (fail fast if missing)
        2. Log non-sensitive configuration summary
        3. Signal readiness

    Shutdown:
        - Logs shutdown event
        - FastAPI + Uvicorn handle connection draining automatically
    """
    # ── Startup ───────────────────────────────────────────────────────────────
    logger.info(f"{'='*60}")
    logger.info(f"  {APP_NAME} v{APP_VERSION} — Starting up")
    logger.info(f"  Environment: {APP_ENV}")
    logger.info(f"{'='*60}")

    # Validate config — raises EnvironmentError with clear message if anything missing
    validate_config()

    config_summary = get_config_summary()
    for key, value in config_summary.items():
        logger.info(f"  {key:<20} = {value}")

    logger.info(f"{'='*60}")
    logger.info(f"  ✅ {APP_NAME} is ready to receive messages")
    logger.info(f"{'='*60}")

    yield

    # ── Shutdown ──────────────────────────────────────────────────────────────
    logger.info(f"[{APP_NAME}] Shutting down gracefully.")


# ═══════════════════════════════════════════════════════════════════════════════
# FASTAPI APPLICATION
# ═══════════════════════════════════════════════════════════════════════════════

app = FastAPI(
    title=APP_NAME,
    description=(
        "Wuryn Platform API — AI-powered social commerce for SMEs. "
        "Serves the WhatsApp bot, customer storefront, and business dashboard."
    ),
    version=APP_VERSION,
    docs_url="/docs" if DEBUG else None,      # Disable Swagger UI in production
    redoc_url="/redoc" if DEBUG else None,    # Disable ReDoc in production
    lifespan=lifespan,
)


# ─── CORS Middleware ──────────────────────────────────────────────────────────
# Allows the React frontends (storefront + dashboard) to call this API.
# In development: localhost:3000 and localhost:5173 (Vite default port).
# In production: replace with actual Render static site URLs via CORS_ORIGINS env var.
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTER REGISTRATION
# ═══════════════════════════════════════════════════════════════════════════════

# Phase 1: WhatsApp webhook (live)
app.include_router(webhook.router)

# Phase 2+ routers — uncomment as each phase is built:
# from backend.routers import auth, dashboard, storefront, social, analytics
# app.include_router(auth.router)
# app.include_router(dashboard.router)
# app.include_router(storefront.router)
# app.include_router(social.router)
# app.include_router(analytics.router)


# ═══════════════════════════════════════════════════════════════════════════════
# PLATFORM ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/health", tags=["Platform"])
async def health_check():
    """
    Health check endpoint. Serves two purposes:

    1. UptimeRobot keep-alive: Pings this URL every 5 minutes to prevent
       Render's free tier from sleeping the service.
       Configure: https://your-service.onrender.com/health
       Monitor type: HTTP(s) | Interval: 5 minutes

    2. Deployment verification: Confirms the service started correctly
       and all environment variables were validated.
    """
    return {
        "status":      "ok",
        "platform":    APP_NAME,
        "version":     APP_VERSION,
        "environment": APP_ENV,
    }


@app.get("/", tags=["Platform"])
async def root():
    """
    Root endpoint — basic platform info.
    Useful for verifying deployment without loading the full docs.
    """
    return {
        "platform": APP_NAME,
        "version":  APP_VERSION,
        "docs":     "/docs" if DEBUG else "disabled in production",
        "health":   "/health",
    }
