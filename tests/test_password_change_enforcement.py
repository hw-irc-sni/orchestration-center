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

import pytest
from fastapi import Request
from fastapi.responses import PlainTextResponse

from orchestrate.server import auth as auth_module
from orchestrate.server.auth import (
    mark_must_change_password,
    clear_must_change_password,
    username_must_change_password,
    auth_middleware,
    get_session_store,
)


def _make_request(path, token=None, method="GET"):
    headers = []
    if token:
        headers.append((b"authorization", f"Bearer {token}".encode()))
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": headers,
        "query_string": b"",
    }
    return Request(scope)


async def _call_next(request):
    return PlainTextResponse("ok")


class TestPendingPasswordChangeTracking:
    """Regression coverage for #12: forced password change on first login."""

    def test_username_must_change_password_false_by_default(self):
        assert username_must_change_password("nobody-marked") is False

    def test_mark_and_check(self):
        clear_must_change_password("temp-user")
        mark_must_change_password("temp-user")
        try:
            assert username_must_change_password("temp-user") is True
        finally:
            clear_must_change_password("temp-user")

    def test_clear_removes_flag(self):
        mark_must_change_password("temp-user-2")
        clear_must_change_password("temp-user-2")
        assert username_must_change_password("temp-user-2") is False

    def test_none_username_is_never_flagged(self):
        assert username_must_change_password(None) is False


@pytest.mark.anyio
class TestAuthMiddlewareEnforcement:
    async def test_blocks_non_exempt_path_when_flagged(self, monkeypatch):
        monkeypatch.setattr(auth_module, "is_auth_enabled", lambda: True)
        store = get_session_store()
        token, _ = store.create("must-change-user")
        mark_must_change_password("must-change-user")
        try:
            request = _make_request("/rest/v1/orchestrate/workflows", token=token)
            response = await auth_middleware(request, _call_next)
            assert response.status_code == 403
        finally:
            store.revoke(token)
            clear_must_change_password("must-change-user")

    async def test_allows_change_password_path_when_flagged(self, monkeypatch):
        monkeypatch.setattr(auth_module, "is_auth_enabled", lambda: True)
        store = get_session_store()
        token, _ = store.create("must-change-user-2")
        mark_must_change_password("must-change-user-2")
        try:
            request = _make_request("/rest/v1/orchestrate/auth/change-password", token=token, method="POST")
            response = await auth_middleware(request, _call_next)
            assert response.status_code == 200
        finally:
            store.revoke(token)
            clear_must_change_password("must-change-user-2")

    async def test_allows_logout_path_when_flagged(self, monkeypatch):
        monkeypatch.setattr(auth_module, "is_auth_enabled", lambda: True)
        store = get_session_store()
        token, _ = store.create("must-change-user-3")
        mark_must_change_password("must-change-user-3")
        try:
            request = _make_request("/rest/v1/orchestrate/auth/logout", token=token, method="POST")
            response = await auth_middleware(request, _call_next)
            assert response.status_code == 200
        finally:
            store.revoke(token)
            clear_must_change_password("must-change-user-3")

    async def test_allows_normal_path_once_cleared(self, monkeypatch):
        monkeypatch.setattr(auth_module, "is_auth_enabled", lambda: True)
        store = get_session_store()
        token, _ = store.create("cleared-user")
        try:
            request = _make_request("/rest/v1/orchestrate/workflows", token=token)
            response = await auth_middleware(request, _call_next)
            assert response.status_code == 200
        finally:
            store.revoke(token)
