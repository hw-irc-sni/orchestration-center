# Copyright (c) 2026 Huawei Technologies Co., Ltd.
# All Rights Reserved.
#
# SPDX-License-Identifier: Apache-2.0
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

"""Access authentication for internal API.

Internal API (``/rest/v1/orchestrate/*``):
    Database-backed user authentication.  Users are stored in PostgreSQL
    ``users`` table with SHA-256 + salt password hashing.  Session tokens
    are in-memory with configurable TTL.

External API (``/api/v1/*``):
    Protected by mTLS at the TLS layer when enable_https=true and
    verify_client=true.
"""

import secrets
import threading
import time

from fastapi import Request
from loguru import logger
from starlette import status
from starlette.responses import JSONResponse

from common.util.config_util import get_conf
from orchestrate.server.response_utils import ok, error

# Endpoints that must remain public even when auth is enabled.
_PUBLIC_AUTH_PATHS = {
    "/rest/v1/orchestrate/auth/login",
    "/rest/v1/orchestrate/auth/check",
    "/rest/v1/orchestrate/auth/register",
}

# Default token lifetime: 12 hours.
_DEFAULT_TTL = 12 * 60 * 60


def is_auth_enabled() -> bool:
    """Return True when authentication is enabled.

    In PostgreSQL mode: checks if the users table has any user.
    In file mode: checks ``access_password`` in server.conf.
    """
    conf = get_conf()
    if conf.get("persistence_mode", "file").lower() == "postgresql":
        from database.utils.user_store import has_any_user
        return has_any_user()
    # File mode: config-based auth
    return bool(conf.get("access_password", "").strip())



def _get_ttl() -> int:
    conf = get_conf()
    try:
        return int(conf.get("access_token_ttl", _DEFAULT_TTL))
    except (ValueError, TypeError):
        return _DEFAULT_TTL


class _SessionStore:
    """Thread-safe in-memory token store with TTL."""

    def __init__(self):
        # token -> (username, expiry epoch)
        self._tokens: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()

    def create(self, username: str) -> tuple[str, int]:
        """Create a new session token for the given user."""
        token = secrets.token_urlsafe(32)
        ttl = _get_ttl()
        expiry = time.time() + ttl
        with self._lock:
            self._cleanup_locked()
            self._tokens[token] = (username, expiry)
        logger.info(f"Session token created for user '{username}', expires in {ttl}s")
        return token, ttl

    def validate(self, token: str) -> bool:
        """Return True if the token exists and has not expired."""
        if not token:
            return False
        now = time.time()
        with self._lock:
            entry = self._tokens.get(token)
            if entry is None:
                return False
            if now >= entry[1]:
                del self._tokens[token]
                return False
            return True

    def get_username(self, token: str) -> str | None:
        """Return the username associated with the token, or None."""
        if not token:
            return None
        now = time.time()
        with self._lock:
            entry = self._tokens.get(token)
            if entry is None or now >= entry[1]:
                self._tokens.pop(token, None)
                return None
            return entry[0]

    def revoke(self, token: str) -> None:
        with self._lock:
            self._tokens.pop(token, None)

    def _cleanup_locked(self) -> None:
        """Remove expired tokens (caller must hold the lock)."""
        now = time.time()
        expired = [t for t, (_, exp) in self._tokens.items() if now >= exp]
        for t in expired:
            del self._tokens[t]


# Singleton - survives across requests within the same process.
_session_store = _SessionStore()


def get_session_store() -> _SessionStore:
    return _session_store


def _extract_token(request: Request) -> str | None:
    """Extract the session token from the Authorization header or query param."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:].strip()
    return request.query_params.get("access_token")


async def auth_middleware(request: Request, call_next):
    """Token-based auth for internal API; mTLS handles external API at TLS layer.

    Internal API (``/rest/v1/orchestrate/*``):
        Token-based auth via database-backed users.  Public auth endpoints
        (login/check/register) are exempt.

    External API (``/api/v1/*``):
        Protected by mTLS at the TLS layer when enable_https=true and
        verify_client=true.  No application-layer check needed here.
    """
    path = request.url.path

    # CORS preflight always allowed.
    if request.method == "OPTIONS":
        return await call_next(request)

    # Only the internal API needs application-layer token auth.
    if not path.startswith("/rest/v1/orchestrate"):
        return await call_next(request)

    if not is_auth_enabled():
        return await call_next(request)

    if path in _PUBLIC_AUTH_PATHS:
        return await call_next(request)

    token = _extract_token(request)
    if not _session_store.validate(token):
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content=error(401, "Unauthorized: valid token required"),
        )

    return await call_next(request)
