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

"""Tests for the llm_config.json -> a2a-t-sdk env file bridge."""

from unittest.mock import patch

import pytest

a2at_config = pytest.importorskip(
    "common.a2at_config", reason="a2a-t-sdk is not installed"
)

from a2a_t.llm.factory import LLMClientFactory  # noqa: E402

from common.llm.config.llm_config import ModelConfig  # noqa: E402


@pytest.fixture(autouse=True)
def _restore_provider_registry():
    """The SDK's factory registry is process-global; keep test registrations local."""
    original = dict(LLMClientFactory._clients)
    yield
    LLMClientFactory._clients = original


def make_config(**overrides) -> ModelConfig:
    base = {
        "provider": "openai",
        "model": "some-model",
        "url": "https://api.example.com/v1/chat/completions",
        "base_url": "",
        "api_key": "sk-test",
    }
    base.update(overrides)
    return ModelConfig(**base)


def generate(tmp_path, config: ModelConfig):
    out = tmp_path / "a2at.env"
    with patch("common.llm.config.llm_config.get_model_config", return_value=config):
        a2at_config.generate_env_from_llm_config(out)
    return dict(
        line.split("=", 1)
        for line in out.read_text(encoding="utf-8").splitlines()
        if "=" in line and not line.startswith("#")
    )


class TestProvider:
    def test_provider_comes_from_config_not_the_url(self, tmp_path):
        """Regression: every URL used to yield A2AT_LLM_PROVIDER=deepseek."""
        values = generate(tmp_path, make_config(provider="qwen"))
        assert values["A2AT_LLM_PROVIDER"] == "qwen"

    def test_deepseek_still_works(self, tmp_path):
        values = generate(
            tmp_path,
            make_config(
                provider="deepseek",
                model="deepseek-chat",
                url="https://api.deepseek.com/v1/chat/completions",
            ),
        )
        assert values["A2AT_LLM_PROVIDER"] == "deepseek"
        assert values["A2AT_LLM_MODEL"] == "deepseek-chat"

    def test_empty_provider_defaults_to_openai(self, tmp_path):
        values = generate(tmp_path, make_config(provider=""))
        assert values["A2AT_LLM_PROVIDER"] == "openai"


class TestDeriveBaseUrl:
    @pytest.mark.parametrize("url,expected", [
        ("https://api.openai.com/v1/chat/completions", "https://api.openai.com/v1"),
        ("https://api.deepseek.com/v1/chat/completions", "https://api.deepseek.com/v1"),
        ("https://gw.internal/llm/api/v2/chat/completions", "https://gw.internal/llm/api/v2"),
        # A root path still derives a well-formed base URL, just an unhelpful one
        ("https://api.x.com/chat/completions", "https://api.x.com/"),
        ("https://api.x.com/", "https://api.x.com/"),
    ])
    def test_openai_shaped_paths(self, url, expected):
        assert a2at_config._derive_base_url(url) == expected

    @pytest.mark.parametrize("url", [
        "https://api.x.com",       # no path: Path("").parent.parent == "." -> corrupt host
        "not-a-url",
        "",
    ])
    def test_underivable_urls_return_empty_rather_than_a_corrupt_host(self, url):
        assert a2at_config._derive_base_url(url) == ""

    def test_underivable_url_is_rejected_with_actionable_message(self, tmp_path):
        with pytest.raises(ValueError, match="LLM_CHAT_BASE_URL"):
            generate(tmp_path, make_config(url="https://api.x.com", base_url=""))


class TestBaseUrl:
    def test_explicit_base_url_wins(self, tmp_path):
        values = generate(
            tmp_path,
            make_config(
                url="https://gateway.internal/llm/api/chat/completions",
                base_url="https://gateway.internal/llm/api",
            ),
        )
        assert values["A2AT_LLM_BASE_URL"] == "https://gateway.internal/llm/api"

    def test_derived_from_url_when_absent(self, tmp_path):
        values = generate(tmp_path, make_config(url="https://api.example.com/v1/chat/completions"))
        assert values["A2AT_LLM_BASE_URL"] == "https://api.example.com/v1"


class TestProviderRegistration:
    def test_openai_is_not_registered_twice(self):
        """The SDK pre-registers 'openai' and raises on a duplicate registration."""
        a2at_config._ensure_provider_registered("openai")
        a2at_config._ensure_provider_registered("openai")
        assert "openai" in LLMClientFactory.available_providers()

    def test_unknown_provider_is_registered_once(self):
        name = "test-provider-registration"
        a2at_config._ensure_provider_registered(name)
        a2at_config._ensure_provider_registered(name)
        assert LLMClientFactory.available_providers().count(name) == 1

    @pytest.mark.parametrize("raw,expected", [
        ("OpenAI", "openai"),
        ("  DeepSeek  ", "deepseek"),
        ("Qwen Plus", "qwenplus"),
        ("", ""),
        (None, ""),
    ])
    def test_normalize_provider(self, raw, expected):
        assert a2at_config.normalize_provider(raw) == expected

    def test_mixed_case_provider_is_registered_and_usable(self):
        """Regression: 'OpenAI' used to register nothing and fail mid-negotiation."""
        a2at_config._ensure_provider_registered("MyProvider")
        assert "myprovider" in LLMClientFactory.available_providers()

    def test_generated_provider_is_normalized(self, tmp_path):
        values = generate(tmp_path, make_config(provider="DeepSeek"))
        assert values["A2AT_LLM_PROVIDER"] == "deepseek"

    def test_normalized_provider_is_accepted_by_the_sdk_factory(self, tmp_path):
        """End-to-end: what we write must survive LLMConfigLoader + factory.create."""
        from a2a_t.llm.config_loader import LLMConfigLoader

        out = tmp_path / "a2at.env"
        with patch("common.llm.config.llm_config.get_model_config",
                   return_value=make_config(provider="OpenAI")):
            a2at_config.generate_env_from_llm_config(out)
        config = LLMConfigLoader.load(out)
        assert LLMClientFactory.create(config.provider, config) is not None

    def test_get_a2at_env_path_registers_the_provider(self, tmp_path):
        """Every A2ATClient/A2ATServer site calls this right before constructing."""
        with patch.object(a2at_config, "_ensure_provider_registered") as mock_register:
            a2at_config.get_a2at_env_path(tmp_path / "a2at.env")
        mock_register.assert_called_once()


class TestStaleDotenvWarning:
    def _warn_with(self, tmp_path, content):
        dotenv = tmp_path / ".env"
        dotenv.write_text(content, encoding="utf-8")
        from common.llm.config import env_overrides

        with patch.object(env_overrides, "get_dotenv_path", lambda: dotenv), \
             patch.object(a2at_config, "logger") as mock_logger:
            a2at_config._warn_on_stale_dotenv()
        return mock_logger

    def test_warns_when_a2at_keys_are_present(self, tmp_path):
        logger = self._warn_with(tmp_path, "LLM_CHAT_MODEL=x\nA2AT_LLM_MODEL=stale\n")
        assert logger.warning.called
        assert "no longer read" in str(logger.warning.call_args)

    def test_silent_for_a_clean_dotenv(self, tmp_path):
        logger = self._warn_with(tmp_path, "LLM_CHAT_MODEL=x\n")
        assert not logger.warning.called

    def test_silent_when_dotenv_is_absent(self, tmp_path):
        from common.llm.config import env_overrides

        with patch.object(env_overrides, "get_dotenv_path", lambda: tmp_path / "absent"), \
             patch.object(a2at_config, "logger") as mock_logger:
            a2at_config._warn_on_stale_dotenv()
        assert not mock_logger.warning.called


class TestConfiguredProvider:
    def test_falls_back_to_openai_when_unset(self):
        with patch("common.llm.config.llm_config.get_model_config",
                   return_value=make_config(provider="")):
            assert a2at_config._configured_provider() == "openai"

    def test_falls_back_to_openai_when_capability_is_missing(self):
        with patch("common.llm.config.llm_config.get_model_config", return_value=None):
            assert a2at_config._configured_provider() == "openai"

    def test_normalizes_the_configured_value(self):
        with patch("common.llm.config.llm_config.get_model_config",
                   return_value=make_config(provider="DeepSeek")):
            assert a2at_config._configured_provider() == "deepseek"


class TestEnvFileLocation:
    def test_default_path_is_etc_conf_a2at_env(self):
        path = a2at_config._default_env_path()
        assert path.name == "a2at.env"
        assert path.parent.name == "conf"
        assert path.parent.parent.name == "etc"

    def test_explicit_path_is_returned_unchanged(self, tmp_path):
        custom = tmp_path / "custom.env"
        assert a2at_config.get_a2at_env_path(custom) == custom


class TestRegeneration:
    def test_existing_file_is_overwritten(self, tmp_path):
        out = tmp_path / "a2at.env"
        out.write_text("A2AT_LLM_MODEL=stale-model\n", encoding="utf-8")
        values = generate(tmp_path, make_config(model="fresh-model"))
        assert values["A2AT_LLM_MODEL"] == "fresh-model"
        assert "stale-model" not in out.read_text(encoding="utf-8")

    def test_parent_directory_is_created(self, tmp_path):
        out = tmp_path / "nested" / "dir" / "a2at.env"
        with patch(
            "common.llm.config.llm_config.get_model_config", return_value=make_config()
        ):
            a2at_config.generate_env_from_llm_config(out)
        assert out.exists()

    def test_ensure_env_file_exists_always_regenerates(self):
        with patch.object(a2at_config, "generate_env_from_llm_config") as mock_generate:
            a2at_config.ensure_env_file_exists()
        mock_generate.assert_called_once_with()

    def test_missing_model_is_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="model"):
            generate(tmp_path, make_config(model=""))


class TestPlaceholderGuard:
    """The SDK loader only checks for non-empty, so an unedited <YOUR_MODEL> would
    otherwise be written out and fail much later, inside a negotiation."""

    @pytest.mark.parametrize("field,env_var", [
        ("model", "LLM_CHAT_MODEL"),
        ("url", "LLM_CHAT_URL"),
        ("api_key", "LLM_CHAT_API_KEY"),
    ])
    def test_placeholders_are_rejected(self, tmp_path, field, env_var):
        with pytest.raises(ValueError) as exc:
            generate(tmp_path, make_config(**{field: f"<YOUR_{field.upper()}>"}))
        assert env_var in str(exc.value)

    def test_update_language_degrades_when_config_is_incomplete(self, tmp_path):
        """update_a2at_language runs per engine construction (exec_engine.py) outside
        the try/except guarding A2ATClient — it must never raise."""
        missing_file = tmp_path / "absent" / "a2at.env"
        with patch("common.llm.config.llm_config.get_model_config",
                   return_value=make_config(model="<YOUR_MODEL>")):
            a2at_config.update_a2at_language("en", missing_file)
        assert not missing_file.exists()

    def test_update_language_generates_a_missing_file(self, tmp_path):
        target = tmp_path / "a2at.env"
        with patch("common.llm.config.llm_config.get_model_config", return_value=make_config()):
            a2at_config.update_a2at_language("en", target)
        assert target.exists()
