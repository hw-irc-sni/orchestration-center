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

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from loguru import logger

@dataclass
class ModelConfig:
    description: str = ""
    model: str = ""
    url: str = ""
    api_key: str = ""
    enable_thinking: bool = False
    verify_ssl: bool = True
    auth: Any = None
    headers: Dict[str, str] = field(default_factory=dict)
    body: Dict[str, Any] = field(default_factory=dict)
    response: Dict[str, str] = field(default_factory=dict)

    @staticmethod
    def from_dict(key: str, raw: dict) -> "ModelConfig":
        return ModelConfig(
            description=raw.get("description", key),
            model=raw.get("model", ""),
            url=raw.get("url", ""),
            api_key=raw.get("api_key", ""),
            enable_thinking=raw.get("enable_thinking", False),
            verify_ssl=raw.get("verify_ssl", True),
            auth=raw.get("auth"),
            headers=raw.get("headers", {}),
            body=raw.get("body", {}),
            response=raw.get("response", {}),
        )

class _ModelConfigHolder:
    """Lazy holder for model configurations (avoids mutable global state)."""
    _instance: Optional[Dict[str, ModelConfig]] = None

    @classmethod
    def get(cls) -> Dict[str, ModelConfig]:
        if cls._instance is None:
            cls._instance = cls._load()
        return cls._instance

    @classmethod
    def _load(cls) -> Dict[str, ModelConfig]:
        try:
            from common.llm.config.config_reader import read_config_as_json
            raw_config = read_config_as_json("../../config/llm_config.json")
        except Exception as e:
            logger.error(f"Failed to load LLM config: {e}")
            raw_config = {}
        # Environment overrides are applied here, not at the call sites, so every
        # caller of get_model_config() sees the same resolved values.
        from common.llm.config.env_overrides import apply_env_overrides
        return {
            key: ModelConfig.from_dict(key, apply_env_overrides(key, val))
            for key, val in raw_config.items()
        }

    @classmethod
    def reset(cls) -> None:
        """Drop the cached configuration. Intended for tests."""
        cls._instance = None

def get_model_config(capability: str) -> Optional[ModelConfig]:
    return _ModelConfigHolder.get().get(capability)

# Fields that must be filled in before any call can succeed. llm_config.json ships with
# <YOUR_...> placeholders so it stays provider-neutral; leaving one in place must fail
# with a clear message rather than reaching the provider.
REQUIRED_FIELDS = ("url", "model", "api_key")

def is_placeholder(value: str) -> bool:
    value = (value or "").strip()
    return not value or (value.startswith("<") and value.endswith(">"))

def missing_required_fields(config: ModelConfig) -> list:
    return [f for f in REQUIRED_FIELDS if is_placeholder(getattr(config, f, ""))]

def describe_missing_fields(capability: str, missing: list) -> str:
    from common.llm.config.env_overrides import env_var_name

    env_vars = ", ".join(env_var_name(capability, f) for f in missing)
    return (
        f"LLM capability '{capability}' is not configured: {', '.join(missing)}. "
        f"Set {env_vars} (environment or .env), or edit common/config/llm_config.json."
    )
