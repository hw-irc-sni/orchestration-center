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

"""Regression coverage for #24: etc/conf/db_config.json was committed with a
live-looking PostgreSQL user/password and was not gitignored, so every local
edit kept flowing into history. It's now gitignored, untracked, and replaced
in git by a placeholder etc/conf/db_config.json.template that
database/utils/db_connection.py's read_db_config()/_ConnInfoHolder consumers
expect a real db_config.json to structurally match.
"""

import json
import os
import subprocess

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TEMPLATE_PATH = os.path.join(_REPO_ROOT, "etc", "conf", "db_config.json.template")


class TestDbConfigTemplate:
    def test_template_exists_and_is_valid_json(self):
        with open(_TEMPLATE_PATH, "r", encoding="utf-8") as f:
            config = json.load(f)
        assert isinstance(config, dict)

    def test_template_has_the_keys_db_connection_expects(self):
        with open(_TEMPLATE_PATH, "r", encoding="utf-8") as f:
            config = json.load(f)
        assert set(config.keys()) == {"host", "port", "database", "user", "password"}

    def test_template_does_not_ship_a_real_looking_credential(self):
        with open(_TEMPLATE_PATH, "r", encoding="utf-8") as f:
            config = json.load(f)
        assert config["user"].startswith("<") and config["user"].endswith(">")
        assert config["password"].startswith("<") and config["password"].endswith(">")


class TestDbConfigJsonIsNotTracked:
    def test_db_config_json_is_gitignored(self):
        gitignore_path = os.path.join(_REPO_ROOT, ".gitignore")
        with open(gitignore_path, "r", encoding="utf-8") as f:
            lines = {line.strip() for line in f}
        assert "etc/conf/db_config.json" in lines

    def test_db_config_json_is_not_tracked_by_git(self):
        result = subprocess.run(
            ["git", "ls-files", "etc/conf/db_config.json"],
            cwd=_REPO_ROOT, capture_output=True, text=True, check=True,
        )
        assert result.stdout.strip() == "", (
            "etc/conf/db_config.json is still tracked by git -- it should "
            "have been removed with `git rm --cached` (see #24)."
        )
