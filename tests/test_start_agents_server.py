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

from a2a.types import AgentCard, AgentInterface

from samples.start_agents_server import _override_advertised_host, _warn_if_chat_llm_unconfigured


def _card(*urls):
    return AgentCard(
        name="test-agent",
        supported_interfaces=[AgentInterface(url=url) for url in urls],
    )


class TestOverrideAdvertisedHost:
    def test_rewrites_host_keeps_port(self):
        card = _card("http://127.0.0.1:8899/a2a/v1")
        _override_advertised_host(card, "sample-agents")
        assert card.supported_interfaces[0].url == "http://sample-agents:8899/a2a/v1"

    def test_rewrites_host_without_port(self):
        card = _card("http://127.0.0.1/a2a/v1")
        _override_advertised_host(card, "sample-agents")
        assert card.supported_interfaces[0].url == "http://sample-agents/a2a/v1"

    def test_preserves_scheme_path_and_query(self):
        card = _card("https://127.0.0.1:26335/rest/plat/smapp/v1/oauth/token?x=1")
        _override_advertised_host(card, "sample-agents")
        iface = card.supported_interfaces[0]
        assert iface.url == "https://sample-agents:26335/rest/plat/smapp/v1/oauth/token?x=1"

    def test_rewrites_every_interface_on_the_card(self):
        card = _card(
            "http://127.0.0.1:8899/a2a/v1",
            "http://127.0.0.1:8899/a2a/v2",
        )
        _override_advertised_host(card, "sample-agents")
        assert [i.url for i in card.supported_interfaces] == [
            "http://sample-agents:8899/a2a/v1",
            "http://sample-agents:8899/a2a/v2",
        ]

    def test_skips_interfaces_with_no_url(self):
        card = _card("")
        _override_advertised_host(card, "sample-agents")
        assert card.supported_interfaces[0].url == ""

    def test_no_interfaces_is_a_noop(self):
        card = AgentCard(name="test-agent", supported_interfaces=[])
        _override_advertised_host(card, "sample-agents")
        assert list(card.supported_interfaces) == []

    def test_host_is_expected_to_be_bare_not_host_colon_port(self):
        # SAMPLE_AGENTS_HOST is documented/used as a bare hostname (Docker service
        # name). A host that already carries its own port gets the original URL's
        # port appended after it too, producing a malformed netloc -- this is
        # current behavior, not a supported input.
        card = _card("http://127.0.0.1:8899/a2a/v1")
        _override_advertised_host(card, "sample-agents:9000")
        assert card.supported_interfaces[0].url == "http://sample-agents:9000:8899/a2a/v1"


class TestWarnIfChatLlmUnconfigured:
    def test_warns_when_required_fields_are_placeholders(self):
        config = MagicMock(url="<YOUR_URL>", model="<YOUR_MODEL>", api_key="<YOUR_API_KEY>")
        with patch("common.llm.config.llm_config.get_model_config", return_value=config), \
             patch("samples.start_agents_server.logger") as mock_logger:
            _warn_if_chat_llm_unconfigured()
        assert mock_logger.warning.called
        message = mock_logger.warning.call_args[0][0]
        assert "url" in message and "model" in message and "api_key" in message

    def test_no_warning_when_fully_configured(self):
        config = MagicMock(url="https://api.openai.com/v1/chat/completions", model="gpt-4o", api_key="sk-real")
        with patch("common.llm.config.llm_config.get_model_config", return_value=config), \
             patch("samples.start_agents_server.logger") as mock_logger:
            _warn_if_chat_llm_unconfigured()
        mock_logger.warning.assert_not_called()

    def test_warns_when_chat_capability_missing_entirely(self):
        with patch("common.llm.config.llm_config.get_model_config", return_value=None), \
             patch("samples.start_agents_server.logger") as mock_logger:
            _warn_if_chat_llm_unconfigured()
        assert mock_logger.warning.called

    def test_does_not_raise_if_config_lookup_itself_fails(self):
        with patch("common.llm.config.llm_config.get_model_config", side_effect=RuntimeError("boom")), \
             patch("samples.start_agents_server.logger") as mock_logger:
            _warn_if_chat_llm_unconfigured()
        assert mock_logger.warning.called
