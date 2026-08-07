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

from unittest.mock import MagicMock, patch

import pytest

from database.utils import table_creation


class TestCreateTables:
    def test_runs_must_change_password_migration(self):
        with patch.object(table_creation, "create_connection", return_value=MagicMock()), \
             patch.object(table_creation, "execute_query", return_value=(None, None)) as mock_exec:
            table_creation.create_tables()
            queries = [call.args[1] for call in mock_exec.call_args_list]
            assert any("ADD COLUMN IF NOT EXISTS must_change_password" in q for q in queries)

    def test_runs_password_scheme_migration(self):
        with patch.object(table_creation, "create_connection", return_value=MagicMock()), \
             patch.object(table_creation, "execute_query", return_value=(None, None)) as mock_exec:
            table_creation.create_tables()
            queries = [call.args[1] for call in mock_exec.call_args_list]
            assert any("ADD COLUMN IF NOT EXISTS password_scheme" in q for q in queries)

    def test_raises_when_must_change_password_migration_fails(self):
        def _fake_execute(conn, query, *args, **kwargs):
            if "ALTER TABLE" in query and "must_change_password" in query:
                return None, RuntimeError("boom")
            return None, None

        with patch.object(table_creation, "create_connection", return_value=MagicMock()), \
             patch.object(table_creation, "execute_query", side_effect=_fake_execute):
            with pytest.raises(RuntimeError, match="Failed to migrate users table \\(must_change_password\\)"):
                table_creation.create_tables()

    def test_raises_when_password_scheme_migration_fails(self):
        def _fake_execute(conn, query, *args, **kwargs):
            if "ALTER TABLE" in query and "password_scheme" in query:
                return None, RuntimeError("boom")
            return None, None

        with patch.object(table_creation, "create_connection", return_value=MagicMock()), \
             patch.object(table_creation, "execute_query", side_effect=_fake_execute):
            with pytest.raises(RuntimeError, match="Failed to migrate users table \\(password_scheme\\)"):
                table_creation.create_tables()

    def test_raises_when_no_connection(self):
        with patch.object(table_creation, "create_connection", return_value=None):
            with pytest.raises(RuntimeError, match="Unable to create database connection"):
                table_creation.create_tables()
