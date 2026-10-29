"""
Single shared slowapi Limiter instance, keyed by client IP (X-Forwarded-For aware).

Two things this module gets right that are easy to get wrong:

1. Trusted-proxy-aware IP extraction. X-Forwarded-For is a client-supplied
   header: anyone can send `X-Forwarded-For: 1.2.3.4` directly to the
   origin and have it believed if you naively take the first entry. A
   real proxy (Render's edge, in front of this app) *appends* the real
   client IP as the last hop it adds; only the last `TRUSTED_PROXY_HOPS`
   entries were added by proxies we trust. We read from the right, not
   the left. With TRUSTED_PROXY_HOPS=0 (no proxy in front, e.g. local
   dev) the header is ignored entirely and the socket peer is used.

2. A shared backend across instances/workers. slowapi/limits' in-memory
   storage is per-process: with >1 uvicorn worker or >1 Render instance,
   each process has its own counters, so the effective rate limit is
   (configured limit) x (process count) — silently much looser than
   configured. Setting REDIS_URL (e.g. a free Upstash Redis instance)
   makes every process share one counter. Without it we fall back to
   in-memory and log a loud warning if we can tell more than one worker
   is likely in play in production.
"""
import logging

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import get_settings

logger = logging.getLogger("syncsupply.rate_limit")
settings = get_settings()


def _key_func(request):
    hops = settings.TRUSTED_PROXY_HOPS
    fwd = request.headers.get("x-forwarded-for")
    if hops > 0 and fwd:
        parts = [p.strip() for p in fwd.split(",") if p.strip()]
        if parts:
            # The (hops)-th entry from the right is the IP our outermost
            # trusted proxy observed as the client — anything further left
            # may have been forged by the client itself.
            idx = max(len(parts) - hops, 0)
            return parts[idx]
    return get_remote_address(request)


_storage_uri = settings.REDIS_URL or None
if not _storage_uri and settings.ENV.lower() == "production":
    logger.warning(
        "REDIS_URL is not set in production: rate limits are enforced per-process "
        "(in-memory). If this service ever runs with more than one worker/instance, "
        "effective limits are looser than configured. Set REDIS_URL (e.g. a free "
        "Upstash Redis database) to share limits across processes."
    )

limiter = Limiter(key_func=_key_func, storage_uri=_storage_uri)
