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

from fastapi import HTTPException, Request
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
    
    Returns False when TESTING environment variable is set.
    """
    import os
    if os.environ.get('TESTING', '').lower() in ('true', '1', 'yes'):
        return False
    
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
    """Thread-safe in-memory token store with TTL.

    Single-process only (see #14): a token minted by one worker/replica
    isn't visible to another, and every restart drops all sessions. Fine
    for the single-process launch paths this project ships today; revisit
    with a shared backend (DB/Redis) before ever running multiple workers
    or replicas. See the "Session storage is single-process only" note in
    README.md for the operator-facing version of this.
    """

    def __init__(self):
        # token -> (username, role, expiry epoch)
        self._tokens: dict[str, tuple[str, str, float]] = {}
        self._lock = threading.Lock()

    def create(self, username: str, role: str = "user") -> tuple[str, int]:
        """Create a new session token for the given user and role.

        ``role`` is stamped once at login rather than re-resolved per
        request, so admin-only checks don't add a DB lookup to every
        authenticated request.
        """
        token = secrets.token_urlsafe(32)
        ttl = _get_ttl()
        expiry = time.time() + ttl
        with self._lock:
            self._cleanup_locked()
            self._tokens[token] = (username, role, expiry)
        logger.info(f"Session token created for user '{username}' (role={role}), expires in {ttl}s")
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
            if now >= entry[2]:
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
            if entry is None or now >= entry[2]:
                self._tokens.pop(token, None)
                return None
            return entry[0]

    def get_role(self, token: str) -> str | None:
        """Return the role associated with the token, or None."""
        if not token:
            return None
        now = time.time()
        with self._lock:
            entry = self._tokens.get(token)
            if entry is None or now >= entry[2]:
                self._tokens.pop(token, None)
                return None
            return entry[1]

    def revoke(self, token: str) -> None:
        with self._lock:
            self._tokens.pop(token, None)

    def _cleanup_locked(self) -> None:
        """Remove expired tokens (caller must hold the lock)."""
        now = time.time()
        expired = [t for t, (_, _, exp) in self._tokens.items() if now >= exp]
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


def require_admin(request: Request) -> None:
    """FastAPI dependency: reject the request unless the session role is 'admin'.

    No-op when auth is disabled: ``auth_middleware`` already lets every
    request through in that mode, so re-checking here would just lock
    everyone out of a deployment that has authentication turned off.
    """
    if not is_auth_enabled():
        return
    token = _extract_token(request)
    role = _session_store.get_role(token) if token else None
    if role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")


# Usernames that must change their password before anything else is allowed.
# Populated at login from the DB (see #12) rather than re-queried per
# request, matching the session-role pattern -- avoids a DB round trip on
# every authenticated request. Self-healing across restarts: the set is
# empty on startup, but the next login for a still-pending user repopulates
# it from the DB's own must_change_password column.
_pending_password_change: set[str] = set()
_pending_password_change_lock = threading.Lock()

# Authenticated paths that must stay reachable for a user stuck in the
# forced-change state, or they'd have no way to actually clear it.
_PASSWORD_CHANGE_EXEMPT_PATHS = {
    "/rest/v1/orchestrate/auth/change-password",
    "/rest/v1/orchestrate/auth/logout",
}


def mark_must_change_password(username: str) -> None:
    with _pending_password_change_lock:
        _pending_password_change.add(username)


def clear_must_change_password(username: str) -> None:
    with _pending_password_change_lock:
        _pending_password_change.discard(username)


def username_must_change_password(username: str | None) -> bool:
    if not username:
        return False
    with _pending_password_change_lock:
        return username in _pending_password_change


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

    username = _session_store.get_username(token)
    if username_must_change_password(username) and path not in _PASSWORD_CHANGE_EXEMPT_PATHS:
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content=error(403, "Password change required", data={"must_change_password": True}),
        )

    return await call_next(request)
