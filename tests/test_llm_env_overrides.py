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

"""Tests for LLM_<CAPABILITY>_<FIELD> environment overrides."""

from unittest.mock import MagicMock, patch

import pytest

from common.llm.config import env_overrides
from common.llm.config.env_overrides import (
    OVERRIDABLE_FIELDS,
    apply_env_overrides,
    env_var_name,
)

CHAT_RAW = {
    "description": "Chat model",
    "model": "json-model",
    "url": "https://json.example/v1/chat/completions",
    "api_key": "<YOUR_API_KEY>",
    "enable_thinking": True,
    "verify_ssl": True,
    "auth": {"type": "aoc_signed", "app_key": "k"},
    "headers": {"X-Static": "1"},
    "body": {"model": "$MODEL", "messages": [{"role": "user", "content": "$PROMPT"}]},
    "response": {"answer": "choices[0].message.content"},
}


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    """Detach every test from the developer's real environment and repo-root .env."""
    for capability in ("chat", "embed", "rerank"):
        for field in OVERRIDABLE_FIELDS:
            monkeypatch.delenv(env_var_name(capability, field), raising=False)
    monkeypatch.setattr(env_overrides, "get_dotenv_path", lambda: tmp_path / ".env")
    env_overrides.reset_dotenv_cache()
    yield
    env_overrides.reset_dotenv_cache()


def write_dotenv(tmp_path, **values):
    lines = [f"{k}={v}" for k, v in values.items()]
    (tmp_path / ".env").write_text("\n".join(lines) + "\n", encoding="utf-8")
    env_overrides.reset_dotenv_cache()


class TestPrecedence:
    def test_json_is_used_when_nothing_is_set(self):
        merged = apply_env_overrides("chat", CHAT_RAW)
        assert merged["model"] == "json-model"
        assert merged["url"] == "https://json.example/v1/chat/completions"

    def test_dotenv_overrides_json(self, tmp_path):
        write_dotenv(tmp_path, LLM_CHAT_MODEL="dotenv-model")
        assert apply_env_overrides("chat", CHAT_RAW)["model"] == "dotenv-model"

    def test_environment_overrides_dotenv(self, tmp_path, monkeypatch):
        write_dotenv(tmp_path, LLM_CHAT_MODEL="dotenv-model")
        monkeypatch.setenv("LLM_CHAT_MODEL", "env-model")
        assert apply_env_overrides("chat", CHAT_RAW)["model"] == "env-model"

    def test_quoted_dotenv_value_is_unquoted(self, tmp_path):
        write_dotenv(tmp_path, LLM_CHAT_API_KEY='"sk-quoted"')
        assert apply_env_overrides("chat", CHAT_RAW)["api_key"] == "sk-quoted"

    def test_missing_dotenv_is_not_an_error(self, tmp_path):
        assert not (tmp_path / ".env").exists()
        assert apply_env_overrides("chat", CHAT_RAW)["model"] == "json-model"


class TestUnsetHandling:
    @pytest.mark.parametrize("value", ["", "   "])
    def test_empty_value_does_not_clobber_json(self, monkeypatch, value):
        # Compose forwards unset variables as "" via ${LLM_CHAT_MODEL:-}
        monkeypatch.setenv("LLM_CHAT_MODEL", value)
        assert apply_env_overrides("chat", CHAT_RAW)["model"] == "json-model"

    def test_value_is_stripped(self, monkeypatch):
        monkeypatch.setenv("LLM_CHAT_MODEL", "  spaced-model  ")
        assert apply_env_overrides("chat", CHAT_RAW)["model"] == "spaced-model"


class TestBooleanCoercion:
    @pytest.mark.parametrize("value", ["false", "FALSE", "0", "no", "off"])
    def test_falsey_values(self, monkeypatch, value):
        monkeypatch.setenv("LLM_CHAT_VERIFY_SSL", value)
        assert apply_env_overrides("chat", CHAT_RAW)["verify_ssl"] is False

    @pytest.mark.parametrize("value", ["true", "True", "1", "yes", "on"])
    def test_truthy_values(self, monkeypatch, value):
        raw = {**CHAT_RAW, "verify_ssl": False}
        monkeypatch.setenv("LLM_CHAT_VERIFY_SSL", value)
        assert apply_env_overrides("chat", raw)["verify_ssl"] is True

    def test_invalid_boolean_falls_back_to_json(self, monkeypatch):
        monkeypatch.setenv("LLM_CHAT_VERIFY_SSL", "maybe")
        assert apply_env_overrides("chat", CHAT_RAW)["verify_ssl"] is True


class TestScope:
    def test_structured_fields_are_never_overridden(self, monkeypatch):
        monkeypatch.setenv("LLM_CHAT_BODY", '{"model": "hacked"}')
        monkeypatch.setenv("LLM_CHAT_AUTH", "none")
        monkeypatch.setenv("LLM_CHAT_HEADERS", "{}")
        merged = apply_env_overrides("chat", CHAT_RAW)
        assert merged["body"] == CHAT_RAW["body"]
        assert merged["auth"] == CHAT_RAW["auth"]
        assert merged["headers"] == CHAT_RAW["headers"]

    def test_capabilities_are_isolated(self, monkeypatch):
        monkeypatch.setenv("LLM_EMBED_URL", "https://embed.example/v1/embeddings")
        assert apply_env_overrides("chat", CHAT_RAW)["url"] == CHAT_RAW["url"]
        embed = apply_env_overrides("embed", {"url": "https://json.example/embed"})
        assert embed["url"] == "https://embed.example/v1/embeddings"

    def test_input_dict_is_not_mutated(self, monkeypatch):
        monkeypatch.setenv("LLM_CHAT_MODEL", "env-model")
        apply_env_overrides("chat", CHAT_RAW)
        assert CHAT_RAW["model"] == "json-model"

    def test_all_documented_fields_are_overridable(self, monkeypatch):
        monkeypatch.setenv("LLM_CHAT_MODEL", "qwen-max")
        monkeypatch.setenv("LLM_CHAT_ENABLE_THINKING", "false")
        merged = apply_env_overrides("chat", CHAT_RAW)
        assert merged["model"] == "qwen-max"
        assert merged["enable_thinking"] is False


class TestSecretLogging:
    def test_api_key_value_is_never_logged(self, monkeypatch):
        monkeypatch.setenv("LLM_CHAT_API_KEY", "sk-super-secret")
        mock_logger = MagicMock()
        with patch.object(env_overrides, "logger", mock_logger):
            apply_env_overrides("chat", CHAT_RAW)
        logged = " ".join(str(c) for c in mock_logger.info.call_args_list)
        assert "sk-super-secret" not in logged
        assert "api_key=***" in logged


class TestModelConfigIntegration:
    """The override must be applied where both LLM stacks read the config."""

    def test_get_model_config_applies_overrides(self, monkeypatch):
        from common.llm.config.llm_config import get_model_config
        from common.llm.llm import reset_instances

        monkeypatch.setenv("LLM_CHAT_MODEL", "deepseek-chat")
        monkeypatch.setenv("LLM_CHAT_VERIFY_SSL", "false")
        reset_instances()

        config = get_model_config("chat")
        assert config.model == "deepseek-chat"
        assert config.verify_ssl is False
        reset_instances()

    def test_verify_ssl_reaches_the_http_client(self, monkeypatch):
        """Regression: verify_ssl used to be dropped between ModelConfig and GenericLLM."""
        from common.llm.llm import get_llm_instance, reset_instances

        monkeypatch.setenv("LLM_CHAT_MODEL", "some-model")
        monkeypatch.setenv("LLM_CHAT_URL", "https://example.invalid/v1/chat/completions")
        monkeypatch.setenv("LLM_CHAT_API_KEY", "sk-test")
        monkeypatch.setenv("LLM_CHAT_VERIFY_SSL", "false")
        reset_instances()

        assert get_llm_instance()._verify_ssl is False
        reset_instances()
