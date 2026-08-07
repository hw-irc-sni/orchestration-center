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

"""User management with PostgreSQL persistence.

Stores usernames and password hashes in the ``users`` table.
Passwords are hashed with SHA-256 + per-user salt.

``password`` parameters below take the real plaintext password (sent by
the frontend over the wire, ideally under TLS). Rows created before #9
(the ``password_scheme='legacy'`` ones) instead stored a hash of the
*client's own* SHA-256 pre-hash of the password -- see
``authenticate_user()`` for how those keep working without a forced
reset.
"""

import hashlib
import secrets as _secrets
from typing import Optional

from loguru import logger

from database.utils.db_connection import create_connection
from database.utils.query_execution import execute_query

# Current on-disk password scheme: password_hash = _hash_password(plaintext, salt).
# 'legacy' rows instead hold _hash_password(sha256(plaintext), salt) -- the
# client used to pre-hash the password with SHA-256 before it ever reached
# the backend, so the "password" that scheme's _hash_password() was ever
# given is that digest, not the real plaintext.
_CURRENT_PASSWORD_SCHEME = "v2"


def _hash_password(password: str, salt: str) -> str:
    """Hash password with salt using SHA-256."""
    return hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()


def _generate_salt() -> str:
    return _secrets.token_hex(16)


def create_user(username: str, password: str, role: str = "user", must_change_password: bool = False) -> bool:
    """Create a new user with the current password scheme. Returns True on success."""
    conn = create_connection()
    if conn is None:
        return False
    try:
        salt = _generate_salt()
        password_hash = _hash_password(password, salt)
        _, err = execute_query(
            conn,
            "INSERT INTO users (username, password_hash, salt, role, must_change_password, password_scheme) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (username, password_hash, salt, role, must_change_password, _CURRENT_PASSWORD_SCHEME),
        )
        if err:
            logger.warning(f"Failed to create user '{username}': {err}")
            return False
        logger.info(f"User '{username}' created with role '{role}'")
        return True
    finally:
        conn.close()


def _upgrade_password_scheme(username: str, password: str, salt: str) -> None:
    """Re-hash a successfully-authenticated legacy row under the current scheme.

    Only called after the legacy verification in authenticate_user() has
    already confirmed ``password`` is correct, so this is a same-password
    re-hash, not a credential change -- must_change_password is untouched.
    """
    conn = create_connection()
    if conn is None:
        return
    try:
        password_hash = _hash_password(password, salt)
        _, err = execute_query(
            conn,
            "UPDATE users SET password_hash = %s, password_scheme = %s WHERE username = %s",
            (password_hash, _CURRENT_PASSWORD_SCHEME, username),
        )
        if err:
            logger.warning(f"Failed to upgrade password scheme for '{username}': {err}")
        else:
            logger.info(f"Upgraded '{username}' from legacy to '{_CURRENT_PASSWORD_SCHEME}' password scheme")
    finally:
        conn.close()


def authenticate_user(username: str, password: str) -> Optional[dict]:
    """Verify username/plaintext password. Returns user dict or None.

    Rows stored under the legacy scheme are verified by reconstructing the
    client-side SHA-256 pre-hash the old frontend used to send, so existing
    accounts keep working with the password their owner already knows --
    no forced reset. A successful legacy login is opportunistically
    upgraded to the current scheme.
    """
    conn = create_connection()
    if conn is None:
        return None
    try:
        result, err = execute_query(
            conn,
            "SELECT username, password_hash, salt, role, must_change_password, password_scheme "
            "FROM users WHERE username = %s",
            (username,),
        )
        if err or not result:
            return None
        row = result[0]
        stored_hash = row[1]
        salt = row[2]
        role = row[3]
        must_change_password = bool(row[4])
        scheme = row[5] or "legacy"

        if scheme == "legacy":
            legacy_input = hashlib.sha256(password.encode()).hexdigest()
            computed_hash = _hash_password(legacy_input, salt)
        else:
            computed_hash = _hash_password(password, salt)

        if not _secrets.compare_digest(computed_hash, stored_hash):
            return None

        if scheme == "legacy":
            _upgrade_password_scheme(username, password, salt)

        return {"username": row[0], "role": role, "must_change_password": must_change_password}
    finally:
        conn.close()


def user_exists(username: str) -> bool:
    conn = create_connection()
    if conn is None:
        return False
    try:
        result, err = execute_query(
            conn,
            "SELECT 1 FROM users WHERE username = %s",
            (username,),
        )
        return bool(result and not err)
    finally:
        conn.close()


def list_users() -> list[dict]:
    """List all users (without password hashes)."""
    conn = create_connection()
    if conn is None:
        return []
    try:
        result, err = execute_query(
            conn,
            "SELECT username, role, must_change_password, created_at FROM users ORDER BY created_at",
            None,
        )
        if err or not result:
            return []
        return [
            {"username": r[0], "role": r[1], "must_change_password": bool(r[2]), "created_at": str(r[3])}
            for r in result
        ]
    finally:
        conn.close()


def delete_user(username: str) -> bool:
    """Delete a user. Returns True on success."""
    conn = create_connection()
    if conn is None:
        return False
    try:
        _, err = execute_query(
            conn,
            "DELETE FROM users WHERE username = %s",
            (username,),
        )
        if err:
            return False
        return True
    finally:
        conn.close()


def has_any_user() -> bool:
    """Check if any user exists in the database."""
    conn = create_connection()
    if conn is None:
        return False
    try:
        result, err = execute_query(conn, "SELECT 1 FROM users LIMIT 1", None)
        return bool(result and not err)
    finally:
        conn.close()


def update_password(username: str, new_password: str) -> bool:
    """Update a user's password (plaintext) under the current scheme and
    clear any pending forced-change flag. Returns True on success.
    """
    conn = create_connection()
    if conn is None:
        return False
    try:
        salt = _generate_salt()
        password_hash = _hash_password(new_password, salt)
        _, err = execute_query(
            conn,
            "UPDATE users SET password_hash = %s, salt = %s, must_change_password = FALSE, "
            "password_scheme = %s WHERE username = %s",
            (password_hash, salt, _CURRENT_PASSWORD_SCHEME, username),
        )
        if err:
            logger.warning(f"Failed to update password for '{username}': {err}")
            return False
        logger.info(f"Password updated for user '{username}'")
        return True
    finally:
        conn.close()

def seed_admin_if_empty(default_password: str = "OpenAN@2026") -> bool:
    """Create default admin user if no users exist.

    The seeded admin is flagged must_change_password so the well-known
    default credential can't be used indefinitely (see #12).
    """
    if has_any_user():
        return False
    return create_user("admin", default_password, "admin", must_change_password=True)
