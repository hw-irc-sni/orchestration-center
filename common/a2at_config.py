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

import os
import re
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from loguru import logger

from a2a_t.llm.factory import LLMClientFactory as _LLMFactory
from a2a_t.llm.providers.openai import OpenAIClient as _OpenAIClient

LANG_MAP = {"en": "en-US", "zh": "zh-CN"}

# The user-owned .env is read for LLM_* overrides (see common/llm/config/env_overrides.py);
# this file, derived from the resolved chat config, is what the a2a-t-sdk consumes.
A2AT_ENV_FILENAME = "a2at.env"

def _default_env_path() -> Path:
    base = Path(os.path.abspath(__file__)).parent.parent
    return base / "etc" / "conf" / A2AT_ENV_FILENAME

def _configured_provider() -> str:
    from common.llm.config.llm_config import get_model_config

    model_cfg = get_model_config("chat")
    return (model_cfg.provider if model_cfg else "") or "openai"

def _ensure_provider_registered(provider: str = None) -> None:
    """Register the configured provider with the SDK's client factory.

    The SDK ships only "openai" and raises if a name is registered twice, so this is
    guarded. Any OpenAI-compatible endpoint works under any name — the name merely
    selects the client class, and base_url does the real work.
    """
    try:
        provider = provider or _configured_provider()
        if provider not in _LLMFactory.available_providers():
            _LLMFactory.register(provider, _OpenAIClient)
    except Exception as e:
        logger.warning(f"Could not register LLM provider with the a2a-t-sdk factory: {e}")

def get_a2at_env_path(env_path: Path = None) -> Path:
    # Every A2ATClient/A2ATServer construction site calls this immediately before
    # building the client, which makes it the one reliable point to register the
    # configured provider (this replaces an import-time register("deepseek", ...)).
    _ensure_provider_registered()
    if env_path is not None:
        return env_path
    return _default_env_path()

def _get_available_languages() -> set[str]:
    from a2a_t.config.models import _default_prompt_resource_root_dir
    root = Path(_default_prompt_resource_root_dir())
    scenarios_dir = root / "scenarios"
    if not scenarios_dir.is_dir():
        return set()
    return {d.name for d in scenarios_dir.iterdir() if d.is_dir() and (d / "scenarios.json").exists()}

def update_a2at_language(language: str, env_path: Path = None) -> None:
    path = get_a2at_env_path(env_path)
    if not path.exists():
        # The engine can be constructed without going through server startup (tests,
        # demo scripts); generate the derived file rather than failing on the read.
        # Best-effort: this runs per engine construction, and an unconfigured install
        # must degrade to "no negotiation support", not break workflow execution.
        try:
            generate_env_from_llm_config(path)
        except Exception as e:
            logger.warning(f"Cannot generate {path} ({e}); skipping language update")
            return
    lang_code = LANG_MAP.get(language, language)
    available = _get_available_languages()
    if available and lang_code not in available:
        fallback = "zh-CN" if "zh-CN" in available else next(iter(available))
        logger.info(f"Language '{lang_code}' has no prompt resources, falling back to '{fallback}'")
        lang_code = fallback
    content = path.read_text(encoding='utf-8')
    updated = re.sub(r'^(A2AT_LANGUAGE=).*$', rf'\1{lang_code}', content, flags=re.MULTILINE)
    try:
        path.write_text(updated, encoding='utf-8')
        logger.info(f"Updated A2AT_LANGUAGE to {lang_code} in {path}")
    except PermissionError:
        logger.warning(f"Cannot write {path} (locked by another process); skipping language update")

def _derive_base_url(full_url: str) -> str:
    """Strip the '/chat/completions' suffix off a full endpoint URL.

    Only correct for OpenAI-shaped paths; set 'base_url' explicitly in llm_config.json
    (or LLM_CHAT_BASE_URL) for gateways and other endpoint layouts.
    """
    parsed = urlparse(full_url)
    base_path = Path(parsed.path).parent.parent.as_posix()
    return f"{parsed.scheme}://{parsed.netloc}{base_path}"

def _warn_on_stale_dotenv() -> None:
    """Point out A2AT_* keys left in the user-owned .env, where they no longer apply."""
    from common.llm.config.env_overrides import get_dotenv_path

    dotenv_path = get_dotenv_path()
    try:
        if not dotenv_path.exists():
            return
        content = dotenv_path.read_text(encoding='utf-8')
    except OSError:
        return
    if any(line.strip().startswith("A2AT_") for line in content.splitlines()):
        logger.warning(
            f"{dotenv_path} contains A2AT_* entries, which are no longer read. "
            f"They are generated into {_default_env_path()} from the resolved chat config; "
            f"use LLM_CHAT_* to change provider/model/api_key/base_url."
        )

def generate_env_from_llm_config(
    env_output_path: Optional[Path] = None,
    capability: str = "chat",
) -> Path:
    from common.llm.config.llm_config import (
        describe_missing_fields,
        get_model_config,
        missing_required_fields,
    )

    if env_output_path is None:
        env_output_path = _default_env_path()

    model_cfg = get_model_config(capability)
    if model_cfg is None:
        raise ValueError(f"LLM capability '{capability}' not found in llm_config.json")

    # Same guard the GenericLLM stack applies: writing an unedited <YOUR_MODEL>
    # placeholder here would be accepted by the SDK's loader (it only checks for
    # non-empty) and fail much later, inside a negotiation.
    missing = missing_required_fields(model_cfg)
    if missing:
        raise ValueError(describe_missing_fields(capability, missing))

    model = model_cfg.model
    api_key = model_cfg.api_key
    base_url = model_cfg.base_url or _derive_base_url(model_cfg.url)

    a2at_provider = model_cfg.provider or "openai"
    capability_upper = capability.upper()
    _ensure_provider_registered(a2at_provider)
    _warn_on_stale_dotenv()

    env_content = f"""# Copyright (c) 2026 Huawei Technologies Co., Ltd.
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

# A2A-T SDK Configuration
#
# GENERATED FILE - DO NOT EDIT. Rewritten on every startup from the resolved
# '{capability}' capability of common/config/llm_config.json, after LLM_{capability_upper}_*
# environment / .env overrides have been applied. Change the JSON or those variables.

# Prompt runtime
A2AT_LANGUAGE=zh-CN
A2AT_PROMPT_SOURCE_TYPE=local_file
A2AT_PROMPT_RESOURCE_LOCAL_ROOT_DIR=

# Prompt compliance
A2AT_PROMPT_COMPLIANCE_ENABLED=false
A2AT_PROMPT_COMPLIANCE_GUARDRAIL_PROVIDER=noop

# LLM runtime
A2AT_LLM_PROVIDER={a2at_provider}
A2AT_LLM_MODEL={model}
A2AT_LLM_API_KEY={api_key}
A2AT_LLM_BASE_URL={base_url}
A2AT_LLM_MAX_TOKENS=2000
A2AT_LLM_TEMPERATURE=0
A2AT_LLM_TIMEOUT_SECONDS=60
A2AT_LLM_HISTORY_WINDOW=10
A2AT_LLM_SESSION_MAX_TOTAL=300
A2AT_LLM_SESSION_MAX_PER_PROVIDER=100

# Negotiation
A2AT_NEGOTIATION_STATE_STORE_TYPE=in_memory
"""

    env_output_path.parent.mkdir(parents=True, exist_ok=True)
    env_output_path.write_text(env_content, encoding='utf-8')
    logger.info(f"Generated A2AT env file at: {env_output_path}")
    return env_output_path

def ensure_env_file_exists() -> Path:
    """(Re)generate the a2a-t-sdk env file from the currently resolved LLM config.

    The file is purely derived, so it is rewritten on every startup: there is no user
    content to preserve, and no window in which a stale copy silently overrides a
    freshly configured provider, model or key.
    """
    return generate_env_from_llm_config()
