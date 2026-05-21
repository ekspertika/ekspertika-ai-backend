"""Lazy singleton Supabase async client.

Server-to-server use only — built with `SUPABASE_SERVICE_KEY` (service role bypasses RLS).
The client is created on first call so the FastAPI app can boot without Supabase env vars
configured (the stateless `POST /api/v1/check` endpoint stays usable in dev).
"""

import logging
from typing import Optional

from supabase import AsyncClient, create_async_client

from config.config import Config

logger = logging.getLogger(__name__)

_client: Optional[AsyncClient] = None


class SupabaseNotConfiguredError(RuntimeError):
    """Raised when Supabase is required but env vars are not set."""


async def get_supabase() -> AsyncClient:
    """Return a process-wide AsyncClient, creating it on first call."""
    global _client
    if _client is not None:
        return _client

    if not Config.SUPABASE_URL or not Config.SUPABASE_SERVICE_ROLE_KEY:
        raise SupabaseNotConfiguredError(
            "SUPABASE_URL (or NEXT_PUBLIC_SUPABASE_URL) and SUPABASE_SERVICE_ROLE_KEY "
            "must be set to use the project-aware flow."
        )

    logger.info("Initializing Supabase async client")
    _client = await create_async_client(Config.SUPABASE_URL, Config.SUPABASE_SERVICE_ROLE_KEY)
    return _client


def is_configured() -> bool:
    return bool(Config.SUPABASE_URL and Config.SUPABASE_SERVICE_ROLE_KEY)
