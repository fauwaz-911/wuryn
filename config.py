"""
Wuryn Platform — Configuration Module
======================================
Central configuration for the entire platform.
All environment variables are loaded here and exposed as typed constants.

USAGE:
    from backend.config import SUPABASE_URL, GROQ_API_KEY, ...

ENVIRONMENT SOURCES:
    Local development : .env file (loaded by python-dotenv)
    Render.com        : Environment tab in service settings
    Never             : Hardcoded in source code

ADDING A NEW CONFIG VALUE:
    1. Add to .env.example with a description
    2. Add os.getenv() call here with a safe default if optional
    3. Add to REQUIRED_VARS if the application cannot start without it
    4. Document it in the README environment variables section
"""

import os
from dotenv import load_dotenv

# Load .env file for local development.
# On Render.com, environment variables are injected directly by the platform.
# load_dotenv() is safe to call in production — it simply does nothing if
# no .env file is present.
load_dotenv()


# ─── Supabase ─────────────────────────────────────────────────────────────────
SUPABASE_URL         = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY         = os.getenv("SUPABASE_KEY", "")
# Use the anon/public key for MVP.
# For production with Row Level Security: switch to service_role key for server-side ops.
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")


# ─── AI Providers ─────────────────────────────────────────────────────────────
GROQ_API_KEY   = os.getenv("GROQ_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Model identifiers — update these when better free-tier models are released
GROQ_MODEL   = "llama3-8b-8192"       # Primary: fast, high quality, generous free tier
GEMINI_MODEL = "gemini-1.5-flash"     # Fallback: lightweight, reliable


# ─── WhatsApp / Meta Cloud API ────────────────────────────────────────────────
# NOTE: In the multi-tenant architecture, each store has its own
# wa_phone_number_id and wa_access_token stored in the `stores` database table.
# These env vars serve as the PLATFORM DEFAULT — used only for single-store
# deployments or for the initial platform setup.
# The application code looks up per-store tokens from the database at runtime.

WHATSAPP_API_VERSION = "v19.0"        # Meta Graph API version — update when Meta deprecates

# Base URL template — phone_number_id is substituted per-store at runtime
WHATSAPP_API_BASE = f"https://graph.facebook.com/{WHATSAPP_API_VERSION}"


# ─── Authentication (JWT) ─────────────────────────────────────────────────────
# Used to sign and verify JWT tokens for business owner dashboard authentication.
# Must be a long, random, secret string — minimum 32 characters.
JWT_SECRET      = os.getenv("JWT_SECRET", "")
JWT_ALGORITHM   = "HS256"
JWT_EXPIRY_DAYS = int(os.getenv("JWT_EXPIRY_DAYS", "7"))


# ─── Application ──────────────────────────────────────────────────────────────
APP_ENV         = os.getenv("APP_ENV", "development")   # development | production
APP_NAME        = "Wuryn Platform"
APP_VERSION     = "1.0.0"
DEBUG           = APP_ENV == "development"

# CORS: allowed origins for the React frontends.
# In production, replace with your actual Render static site URLs.
CORS_ORIGINS = os.getenv(
    "CORS_ORIGINS",
    "http://localhost:3000,http://localhost:5173"
).split(",")


# ─── Startup Validation ───────────────────────────────────────────────────────
# These are the minimum variables required for the application to start.
# Missing any of these means the bot cannot function — fail fast at startup.
REQUIRED_VARS = [
    "SUPABASE_URL",
    "SUPABASE_KEY",
    "GROQ_API_KEY",
]


def validate_config() -> None:
    """
    Validate that all required environment variables are present.

    Called once at application startup in main.py lifespan context.
    Raises EnvironmentError immediately if any required variable is missing,
    providing a clear, actionable error message with the specific missing keys.

    This is intentional fail-fast behaviour — a misconfigured deployment
    should fail loudly at startup, not silently mid-conversation.
    """
    missing = [var for var in REQUIRED_VARS if not os.getenv(var)]

    if missing:
        raise EnvironmentError(
            f"\n{'='*60}\n"
            f"[WURYN] STARTUP FAILED — Missing environment variables:\n"
            f"  {', '.join(missing)}\n\n"
            f"Set these in:\n"
            f"  Local dev   → .env file (copy from .env.example)\n"
            f"  Render.com  → Service → Environment → Add variable\n"
            f"{'='*60}"
        )


def get_config_summary() -> dict:
    """
    Return a non-sensitive summary of the current configuration.
    Safe to log at startup — excludes all secrets and tokens.

    Returns:
        Dict of configuration keys and their safe display values.
    """
    return {
        "app_name":      APP_NAME,
        "app_version":   APP_VERSION,
        "environment":   APP_ENV,
        "debug":         DEBUG,
        "supabase_url":  SUPABASE_URL[:40] + "..." if SUPABASE_URL else "NOT SET",
        "groq_model":    GROQ_MODEL,
        "gemini_model":  GEMINI_MODEL,
        "cors_origins":  CORS_ORIGINS,
        "jwt_expiry":    f"{JWT_EXPIRY_DAYS} days",
    }
