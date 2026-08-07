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

import hashlib
from unittest.mock import MagicMock, patch

import pytest

from database.utils import user_store


@pytest.fixture(autouse=True)
def _reset_any_user_exists_cache():
    user_store._any_user_exists_cache = False
    yield
    user_store._any_user_exists_cache = False


def _mock_conn():
    return MagicMock()


class TestCreateUser:
    def test_defaults_role_and_must_change_password(self):
        with patch.object(user_store, "create_connection", return_value=_mock_conn()), \
             patch.object(user_store, "execute_query", return_value=(None, None)) as mock_exec:
            assert user_store.create_user("alice", "pw") is True
            params = mock_exec.call_args[0][2]
            assert params[3] == "user"
            assert params[4] is False
            assert params[5] == "v2"

    def test_passes_through_role_and_must_change_password(self):
        with patch.object(user_store, "create_connection", return_value=_mock_conn()), \
             patch.object(user_store, "execute_query", return_value=(None, None)) as mock_exec:
            assert user_store.create_user("admin", "pw", role="admin", must_change_password=True) is True
            params = mock_exec.call_args[0][2]
            assert params[3] == "admin"
            assert params[4] is True

    def test_hashes_the_plaintext_directly(self):
        """create_user's hash must match _hash_password(plaintext, salt) --
        i.e. the real password, not some pre-hashed digest of it."""
        with patch.object(user_store, "create_connection", return_value=_mock_conn()), \
             patch.object(user_store, "_generate_salt", return_value="fixedsalt"), \
             patch.object(user_store, "execute_query", return_value=(None, None)) as mock_exec:
            user_store.create_user("alice", "S3cure!pw")
            params = mock_exec.call_args[0][2]
            expected = hashlib.sha256(b"fixedsalt:S3cure!pw").hexdigest()
            assert params[1] == expected

    def test_returns_false_on_db_error(self):
        with patch.object(user_store, "create_connection", return_value=_mock_conn()), \
             patch.object(user_store, "execute_query", return_value=(None, RuntimeError("boom"))):
            assert user_store.create_user("alice", "pw") is False

    def test_returns_false_when_no_connection(self):
        with patch.object(user_store, "create_connection", return_value=None):
            assert user_store.create_user("alice", "pw") is False

    def test_successful_creation_marks_a_user_as_existing(self):
        with patch.object(user_store, "create_connection", return_value=_mock_conn()), \
             patch.object(user_store, "execute_query", return_value=(None, None)):
            user_store.create_user("alice", "pw")
            assert user_store._any_user_exists_cache is True

    def test_failed_creation_does_not_mark_a_user_as_existing(self):
        with patch.object(user_store, "create_connection", return_value=_mock_conn()), \
             patch.object(user_store, "execute_query", return_value=(None, RuntimeError("boom"))):
            user_store.create_user("alice", "pw")
            assert user_store._any_user_exists_cache is False


class TestAuthenticateUserV2Scheme:
    def _row_for(self, plaintext, salt="salt123", role="user", must_change_password=False):
        password_hash = user_store._hash_password(plaintext, salt)
        return ("alice", password_hash, salt, role, must_change_password, "v2")

    def test_correct_plaintext_authenticates(self):
        row = self._row_for("S3cure!pw")
        with patch.object(user_store, "create_connection", return_value=_mock_conn()), \
             patch.object(user_store, "execute_query", return_value=([row], None)):
            result = user_store.authenticate_user("alice", "S3cure!pw")
            assert result == {"username": "alice", "role": "user", "must_change_password": False}

    def test_surfaces_must_change_password_true(self):
        row = self._row_for("OpenAN@2026", role="admin", must_change_password=True)
        with patch.object(user_store, "create_connection", return_value=_mock_conn()), \
             patch.object(user_store, "execute_query", return_value=([row], None)):
            result = user_store.authenticate_user("alice", "OpenAN@2026")
            assert result["must_change_password"] is True

    def test_wrong_plaintext_rejected(self):
        row = self._row_for("S3cure!pw")
        with patch.object(user_store, "create_connection", return_value=_mock_conn()), \
             patch.object(user_store, "execute_query", return_value=([row], None)):
            assert user_store.authenticate_user("alice", "wrong") is None

    def test_does_not_trigger_scheme_upgrade(self):
        row = self._row_for("S3cure!pw")
        with patch.object(user_store, "create_connection", return_value=_mock_conn()), \
             patch.object(user_store, "execute_query", return_value=([row], None)), \
             patch.object(user_store, "_upgrade_password_scheme") as mock_upgrade:
            user_store.authenticate_user("alice", "S3cure!pw")
            mock_upgrade.assert_not_called()

    def test_returns_none_when_user_not_found(self):
        with patch.object(user_store, "create_connection", return_value=_mock_conn()), \
             patch.object(user_store, "execute_query", return_value=([], None)):
            assert user_store.authenticate_user("nobody", "pw") is None


class TestAuthenticateUserLegacyScheme:
    def _legacy_row_for(self, plaintext, salt="salt123", role="user", must_change_password=False):
        # This is exactly what the old client-side sha256() + old backend
        # _hash_password(client_hash, salt) pipeline used to produce.
        client_hash = hashlib.sha256(plaintext.encode()).hexdigest()
        password_hash = user_store._hash_password(client_hash, salt)
        return ("alice", password_hash, salt, role, must_change_password, "legacy")

    def test_real_password_still_authenticates_without_reset(self):
        row = self._legacy_row_for("MyRealPassword1!")
        with patch.object(user_store, "create_connection", return_value=_mock_conn()), \
             patch.object(user_store, "execute_query", return_value=([row], None)), \
             patch.object(user_store, "_upgrade_password_scheme"):
            result = user_store.authenticate_user("alice", "MyRealPassword1!")
            assert result == {"username": "alice", "role": "user", "must_change_password": False}

    def test_wrong_password_rejected(self):
        row = self._legacy_row_for("MyRealPassword1!")
        with patch.object(user_store, "create_connection", return_value=_mock_conn()), \
             patch.object(user_store, "execute_query", return_value=([row], None)):
            assert user_store.authenticate_user("alice", "wrong") is None

    def test_successful_login_upgrades_scheme(self):
        row = self._legacy_row_for("MyRealPassword1!")
        with patch.object(user_store, "create_connection", return_value=_mock_conn()), \
             patch.object(user_store, "execute_query", return_value=([row], None)), \
             patch.object(user_store, "_upgrade_password_scheme") as mock_upgrade:
            user_store.authenticate_user("alice", "MyRealPassword1!")
            mock_upgrade.assert_called_once_with("alice", "MyRealPassword1!", "salt123")

    def test_failed_login_does_not_upgrade_scheme(self):
        row = self._legacy_row_for("MyRealPassword1!")
        with patch.object(user_store, "create_connection", return_value=_mock_conn()), \
             patch.object(user_store, "execute_query", return_value=([row], None)), \
             patch.object(user_store, "_upgrade_password_scheme") as mock_upgrade:
            user_store.authenticate_user("alice", "wrong")
            mock_upgrade.assert_not_called()

    def test_null_scheme_treated_as_legacy(self):
        """Rows that existed before the password_scheme column was added
        have NULL there -- must fall back to legacy verification."""
        client_hash = hashlib.sha256("MyRealPassword1!".encode()).hexdigest()
        password_hash = user_store._hash_password(client_hash, "salt123")
        row = ("alice", password_hash, "salt123", "user", False, None)
        with patch.object(user_store, "create_connection", return_value=_mock_conn()), \
             patch.object(user_store, "execute_query", return_value=([row], None)), \
             patch.object(user_store, "_upgrade_password_scheme"):
            result = user_store.authenticate_user("alice", "MyRealPassword1!")
            assert result == {"username": "alice", "role": "user", "must_change_password": False}

    def test_preserves_must_change_password_through_upgrade(self):
        row = self._legacy_row_for("OpenAN@2026", role="admin", must_change_password=True)
        with patch.object(user_store, "create_connection", return_value=_mock_conn()), \
             patch.object(user_store, "execute_query", return_value=([row], None)), \
             patch.object(user_store, "_upgrade_password_scheme"):
            result = user_store.authenticate_user("alice", "OpenAN@2026")
            assert result["must_change_password"] is True


class TestUpgradePasswordScheme:
    def test_rehashes_under_current_scheme(self):
        with patch.object(user_store, "create_connection", return_value=_mock_conn()), \
             patch.object(user_store, "execute_query", return_value=(None, None)) as mock_exec:
            user_store._upgrade_password_scheme("alice", "MyRealPassword1!", "salt123")
            query = mock_exec.call_args[0][1]
            params = mock_exec.call_args[0][2]
            expected_hash = user_store._hash_password("MyRealPassword1!", "salt123")
            assert "password_scheme" in query
            assert "must_change_password" not in query
            assert params[0] == expected_hash
            assert params[1] == "v2"


class TestUpdatePassword:
    def test_stamps_current_scheme_and_clears_must_change_password(self):
        with patch.object(user_store, "create_connection", return_value=_mock_conn()), \
             patch.object(user_store, "execute_query", return_value=(None, None)) as mock_exec:
            assert user_store.update_password("alice", "NewPassw0rd!") is True
            query = mock_exec.call_args[0][1]
            params = mock_exec.call_args[0][2]
            assert "password_scheme" in query
            assert "must_change_password = FALSE" in query
            assert "v2" in params

    def test_returns_false_on_db_error(self):
        with patch.object(user_store, "create_connection", return_value=_mock_conn()), \
             patch.object(user_store, "execute_query", return_value=(None, RuntimeError("boom"))):
            assert user_store.update_password("alice", "NewPassw0rd!") is False


class TestSeedAdminIfEmpty:
    def test_passes_plaintext_through_and_flags_must_change_password(self):
        with patch.object(user_store, "has_any_user", return_value=False), \
             patch.object(user_store, "create_user", return_value=True) as mock_create:
            assert user_store.seed_admin_if_empty("OpenAN@2026") is True
            mock_create.assert_called_once_with("admin", "OpenAN@2026", "admin", must_change_password=True)

    def test_no_op_when_users_already_exist(self):
        with patch.object(user_store, "has_any_user", return_value=True), \
             patch.object(user_store, "create_user") as mock_create:
            assert user_store.seed_admin_if_empty("OpenAN@2026") is False
            mock_create.assert_not_called()


class TestListUsers:
    def test_includes_must_change_password(self):
        rows = [("alice", "admin", True, "2026-01-01 00:00:00")]
        with patch.object(user_store, "create_connection", return_value=_mock_conn()), \
             patch.object(user_store, "execute_query", return_value=(rows, None)):
            result = user_store.list_users()
            assert result == [{
                "username": "alice", "role": "admin",
                "must_change_password": True, "created_at": "2026-01-01 00:00:00",
            }]


class TestDeleteUser:
    def test_returns_true_on_success(self):
        with patch.object(user_store, "create_connection", return_value=_mock_conn()), \
             patch.object(user_store, "execute_query", return_value=(None, None)) as mock_exec:
            assert user_store.delete_user("alice") is True
            query = mock_exec.call_args[0][1]
            params = mock_exec.call_args[0][2]
            assert "DELETE FROM users" in query
            assert params == ("alice",)

    def test_returns_false_on_db_error(self):
        with patch.object(user_store, "create_connection", return_value=_mock_conn()), \
             patch.object(user_store, "execute_query", return_value=(None, RuntimeError("boom"))):
            assert user_store.delete_user("alice") is False

    def test_returns_false_when_no_connection(self):
        with patch.object(user_store, "create_connection", return_value=None):
            assert user_store.delete_user("alice") is False


class TestHasAnyUser:
    def test_true_when_a_row_exists(self):
        with patch.object(user_store, "create_connection", return_value=_mock_conn()), \
             patch.object(user_store, "execute_query", return_value=([(1,)], None)):
            assert user_store.has_any_user() is True

    def test_false_when_table_is_empty(self):
        with patch.object(user_store, "create_connection", return_value=_mock_conn()), \
             patch.object(user_store, "execute_query", return_value=([], None)):
            assert user_store.has_any_user() is False

    def test_false_on_db_error(self):
        with patch.object(user_store, "create_connection", return_value=_mock_conn()), \
             patch.object(user_store, "execute_query", return_value=(None, RuntimeError("boom"))):
            assert user_store.has_any_user() is False

    def test_false_when_no_connection(self):
        with patch.object(user_store, "create_connection", return_value=None):
            assert user_store.has_any_user() is False

    def test_true_result_is_cached_and_skips_the_db_on_next_call(self):
        with patch.object(user_store, "create_connection", return_value=_mock_conn()) as mock_create_conn, \
             patch.object(user_store, "execute_query", return_value=([(1,)], None)):
            assert user_store.has_any_user() is True
            assert user_store.has_any_user() is True
            mock_create_conn.assert_called_once()

    def test_false_result_is_not_cached_and_rechecks_the_db(self):
        with patch.object(user_store, "create_connection", return_value=_mock_conn()) as mock_create_conn, \
             patch.object(user_store, "execute_query", return_value=([], None)):
            assert user_store.has_any_user() is False
            assert user_store.has_any_user() is False
            assert mock_create_conn.call_count == 2


class TestSaltUniqueness:
    def test_generate_salt_does_not_repeat(self):
        salts = {user_store._generate_salt() for _ in range(1000)}
        assert len(salts) == 1000

    def test_same_password_hashes_differently_per_user(self):
        """Two users who happen to pick the same password must not end up
        with the same password_hash -- that would let a stolen hash for one
        account fingerprint every other account sharing that password."""
        captured_hashes = []

        def _capture(conn, query, params):
            captured_hashes.append(params[1])  # password_hash
            return None, None

        with patch.object(user_store, "create_connection", return_value=_mock_conn()), \
             patch.object(user_store, "execute_query", side_effect=_capture):
            user_store.create_user("alice", "SamePassw0rd!")
            user_store.create_user("bob", "SamePassw0rd!")

        assert len(captured_hashes) == 2
        assert captured_hashes[0] != captured_hashes[1]
