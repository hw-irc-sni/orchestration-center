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
"""

import hashlib
import secrets as _secrets
from typing import Optional

from loguru import logger

from database.utils.db_connection import create_connection
from database.utils.query_execution import execute_query


def _hash_password(password: str, salt: str) -> str:
    """Hash password with salt using SHA-256."""
    return hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()


def _generate_salt() -> str:
    return _secrets.token_hex(16)


def create_user(username: str, password: str, role: str = "user") -> bool:
    """Create a new user. Returns True on success."""
    conn = create_connection()
    if conn is None:
        return False
    try:
        salt = _generate_salt()
        password_hash = _hash_password(password, salt)
        _, err = execute_query(
            conn,
            "INSERT INTO users (username, password_hash, salt, role) VALUES (%s, %s, %s, %s)",
            (username, password_hash, salt, role),
        )
        if err:
            logger.warning(f"Failed to create user '{username}': {err}")
            return False
        logger.info(f"User '{username}' created with role '{role}'")
        return True
    finally:
        conn.close()


def authenticate_user(username: str, password: str) -> Optional[dict]:
    """Verify username/password. Returns user dict or None."""
    conn = create_connection()
    if conn is None:
        return None
    try:
        result, err = execute_query(
            conn,
            "SELECT username, password_hash, salt, role FROM users WHERE username = %s",
            (username,),
        )
        if err or not result:
            return None
        row = result[0]
        stored_hash = row[1]
        salt = row[2]
        role = row[3]
        input_hash = _hash_password(password, salt)
        if _secrets.compare_digest(input_hash, stored_hash):
            return {"username": row[0], "role": role}
        return None
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
            "SELECT username, role, created_at FROM users ORDER BY created_at",
            None,
        )
        if err or not result:
            return []
        return [{"username": r[0], "role": r[1], "created_at": str(r[2])} for r in result]
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
    """Update a user's password. Returns True on success."""
    conn = create_connection()
    if conn is None:
        return False
    try:
        salt = _generate_salt()
        password_hash = _hash_password(new_password, salt)
        _, err = execute_query(
            conn,
            "UPDATE users SET password_hash = %s, salt = %s WHERE username = %s",
            (password_hash, salt, username),
        )
        if err:
            logger.warning(f"Failed to update password for '{username}': {err}")
            return False
        logger.info(f"Password updated for user '{username}'")
        return True
    finally:
        conn.close()

def seed_admin_if_empty(default_password: str = "OpenAN@2026") -> bool:
    """Create default admin user if no users exist."""
    if has_any_user():
        return False
    return create_user("admin", default_password, "admin")
