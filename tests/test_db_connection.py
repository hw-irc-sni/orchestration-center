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

"""Regression coverage for #40: create_database_if_not_exists() opened a
throwaway connection to the `postgres` maintenance database on every call --
and it was called on every /rest/v1/orchestrate/* request via
create_connection() -> has_any_user() -> is_auth_enabled(). It's now cached
for the process lifetime after a successful check.
"""

from unittest.mock import MagicMock, patch

import pytest

from database.utils import db_connection


@pytest.fixture(autouse=True)
def _reset_database_verified_cache():
    db_connection._database_verified = False
    yield
    db_connection._database_verified = False


def _mock_pg_connection(existing_db=True):
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.fetchone.return_value = (1,) if existing_db else None
    return conn


class TestCreateDatabaseIfNotExistsCaching:
    def test_first_call_connects_and_caches_on_success(self):
        with patch.object(db_connection, "_ConnInfoHolder") as mock_holder, \
             patch.object(db_connection.psycopg2, "connect", return_value=_mock_pg_connection()) as mock_connect:
            mock_holder.get.return_value = {"host": "127.0.0.1", "database": "orchestration_center"}
            assert db_connection.create_database_if_not_exists() is True
            assert mock_connect.call_count == 1
            assert db_connection._database_verified is True

    def test_second_call_does_not_reconnect(self):
        with patch.object(db_connection, "_ConnInfoHolder") as mock_holder, \
             patch.object(db_connection.psycopg2, "connect", return_value=_mock_pg_connection()) as mock_connect:
            mock_holder.get.return_value = {"host": "127.0.0.1", "database": "orchestration_center"}
            assert db_connection.create_database_if_not_exists() is True
            assert db_connection.create_database_if_not_exists() is True
            assert mock_connect.call_count == 1

    def test_failed_attempt_is_not_cached_and_retries(self):
        with patch.object(db_connection, "_ConnInfoHolder") as mock_holder, \
             patch.object(db_connection.psycopg2, "connect", side_effect=Exception("connection refused")) as mock_connect:
            mock_holder.get.return_value = {"host": "127.0.0.1", "database": "orchestration_center"}
            assert db_connection.create_database_if_not_exists() is False
            assert db_connection._database_verified is False
            assert db_connection.create_database_if_not_exists() is False
            assert mock_connect.call_count == 2

    def test_creates_database_when_missing(self):
        conn = _mock_pg_connection(existing_db=False)
        with patch.object(db_connection, "_ConnInfoHolder") as mock_holder, \
             patch.object(db_connection.psycopg2, "connect", return_value=conn):
            mock_holder.get.return_value = {"host": "127.0.0.1", "database": "orchestration_center"}
            assert db_connection.create_database_if_not_exists() is True
            # SELECT (existence check) + CREATE DATABASE, since fetchone()
            # was mocked to report the database missing.
            assert conn.cursor().execute.call_count == 2


class TestCreateConnectionUsesCachedCheck:
    def test_create_connection_skips_recheck_once_cached(self):
        db_connection._database_verified = True
        with patch.object(db_connection, "_ConnInfoHolder") as mock_holder, \
             patch.object(db_connection.psycopg2, "connect", return_value=MagicMock()) as mock_connect:
            mock_holder.get.return_value = {"host": "127.0.0.1", "database": "orchestration_center"}
            conn = db_connection.create_connection()
            assert conn is not None
            # Only the application-DB connection, not a second one for the
            # `postgres` maintenance database.
            assert mock_connect.call_count == 1
