"""
Request-level security checks applied to every /api/* request:
API-key auth (config.Config.API_KEY) and a per-IP rate limit
(config.Config.RATE_LIMIT_PER_MINUTE). Both are wired in from
app.py's before_request so no individual route file has to
remember to call them.

Both are no-ops when their corresponding config value is falsy
(API_KEY unset / RATE_LIMIT_PER_MINUTE == 0), see config.py for
why that's the default and what to set before a public deploy.
"""

from __future__ import annotations

import hmac
import time
import threading
from collections import defaultdict, deque

from flask import request, jsonify

from config import Config

# EXEMPT_PATHS are never gated by auth or rate limiting, a health
# check needs to work even if a client's key is wrong/missing, so
# uptime monitors and load balancers aren't tied to a secret.
EXEMPT_PATHS = {"/api/health"}


def _is_api_path(path: str) -> bool:
    return path.startswith("/api/") and path not in EXEMPT_PATHS


def check_api_key():
    """Returns a (response, status) tuple to short-circuit the
    request with, or None to let it through."""
    if request.method == "OPTIONS":
        return None  # never block a CORS preflight

    if not _is_api_path(request.path):
        return None

    if not Config.API_KEY:
        return None  # auth disabled, no key configured (see config.py)

    supplied = request.headers.get("X-API-Key", "")
    # constant-time comparison: a naive `==` leaks how many leading
    # characters matched via response-timing, letting an attacker
    # guess the key one byte at a time.
    if not supplied or not hmac.compare_digest(supplied, Config.API_KEY):
        return jsonify({"error": "Unauthorized, missing or invalid X-API-Key header"}), 401

    return None


# --- Rate limiting -----------------------------------------------
#
# A plain in-memory sliding window, per client IP. See config.py's
# RATE_LIMIT_PER_MINUTE docstring for the multi-worker/multi-instance
# caveat, this is a V1 baseline, not a distributed rate limiter.

_lock = threading.Lock()
_hits: dict[str, deque] = defaultdict(deque)
_WINDOW_SECONDS = 60


def _client_key() -> str:
    # Platforms like Railway/Render put the real client IP in
    # X-Forwarded-For (the connection Flask sees is the platform's
    # own proxy), prefer that when present, falling back to
    # request.remote_addr for local/direct connections.
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def check_rate_limit():
    """Returns a (response, status) tuple to short-circuit the
    request with, or None to let it through."""
    if request.method == "OPTIONS":
        return None

    if not _is_api_path(request.path):
        return None

    limit = Config.RATE_LIMIT_PER_MINUTE
    if not limit:
        return None  # disabled

    now = time.time()
    key = _client_key()

    with _lock:
        window = _hits[key]
        while window and now - window[0] > _WINDOW_SECONDS:
            window.popleft()

        if len(window) >= limit:
            retry_after = max(1, int(_WINDOW_SECONDS - (now - window[0])))
            response = jsonify({"error": "Too many requests. Please slow down and try again shortly."})
            response.headers["Retry-After"] = str(retry_after)
            return response, 429

        window.append(now)

    return None
