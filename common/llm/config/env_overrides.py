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

"""Environment-variable overrides for llm_config.json.

Any scalar field of any capability can be overridden without editing JSON, using
``LLM_<CAPABILITY>_<FIELD>`` — e.g. ``LLM_CHAT_MODEL``, ``LLM_CHAT_API_KEY``,
``LLM_EMBED_URL``. Values resolve in this order:

    os.environ  >  repo-root .env  >  common/config/llm_config.json

Structured fields (``auth``, ``headers``, ``body``, ``response``) are templates and
stay JSON-only.
"""

import os
from pathlib import Path
from typing import Any, Dict, Optional

from loguru import logger

# Scalar fields that may be overridden. Structured template fields are excluded on
# purpose — they cannot be expressed sensibly as a single environment value.
OVERRIDABLE_FIELDS = (
    "description",
    "model",
    "url",
    "api_key",
    "enable_thinking",
    "verify_ssl",
)

_BOOLEAN_FIELDS = frozenset({"enable_thinking", "verify_ssl"})
_SECRET_FIELDS = frozenset({"api_key"})

_TRUE_VALUES = frozenset({"true", "1", "yes", "on"})
_FALSE_VALUES = frozenset({"false", "0", "no", "off"})

_dotenv_cache: Optional[Dict[str, str]] = None


def _repo_root() -> Path:
    # common/llm/config/env_overrides.py -> common/llm/config -> common/llm -> common -> <root>
    return Path(os.path.abspath(__file__)).parent.parent.parent.parent


def get_dotenv_path() -> Path:
    """Path of the user-owned .env file (repo root)."""
    return _repo_root() / ".env"


def _load_dotenv() -> Dict[str, str]:
    """Read the user-owned .env once per process. A missing file is normal."""
    global _dotenv_cache
    if _dotenv_cache is not None:
        return _dotenv_cache

    path = get_dotenv_path()
    values: Dict[str, str] = {}
    if path.exists():
        try:
            from dotenv import dotenv_values
            values = {k: v for k, v in dotenv_values(path).items() if k and v is not None}
        except Exception as e:
            logger.warning(f"Failed to read {path}, ignoring it: {e}")
    _dotenv_cache = values
    return values


def reset_dotenv_cache() -> None:
    """Drop the cached .env contents. Intended for tests."""
    global _dotenv_cache
    _dotenv_cache = None


def env_var_name(capability: str, field: str) -> str:
    return f"LLM_{capability.upper()}_{field.upper()}"


def _lookup(capability: str, field: str) -> Optional[tuple]:
    """Return (value, source) for a field, or None when unset.

    An empty value counts as unset — Docker Compose passes unset variables through
    as ``""`` (e.g. ``LLM_CHAT_MODEL=${LLM_CHAT_MODEL:-}``), which must not clobber
    the JSON default.
    """
    name = env_var_name(capability, field)

    value = os.environ.get(name)
    if value is not None and value.strip():
        return value.strip(), "environment"

    value = _load_dotenv().get(name)
    if value is not None and value.strip():
        return value.strip(), ".env"

    return None


def _coerce(capability: str, field: str, value: str, fallback: Any) -> Any:
    if field not in _BOOLEAN_FIELDS:
        return value
    lowered = value.lower()
    if lowered in _TRUE_VALUES:
        return True
    if lowered in _FALSE_VALUES:
        return False
    logger.warning(
        f"Ignoring {env_var_name(capability, field)}='{value}': expected a boolean "
        f"(true/false/1/0/yes/no)"
    )
    return fallback


def apply_env_overrides(capability: str, raw: dict) -> dict:
    """Return a copy of one capability's raw config with env overrides applied."""
    merged = dict(raw)
    applied = []

    for field in OVERRIDABLE_FIELDS:
        found = _lookup(capability, field)
        if found is None:
            continue
        value, source = found
        coerced = _coerce(capability, field, value, merged.get(field))
        if coerced == merged.get(field):
            continue
        merged[field] = coerced
        shown = "***" if field in _SECRET_FIELDS else coerced
        applied.append(f"{field}={shown} (from {source})")

    if applied:
        logger.info(f"LLM config '{capability}' overridden by environment: {', '.join(applied)}")
    return merged
