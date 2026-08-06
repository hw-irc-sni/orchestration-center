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

from typing import Dict, Any, Optional
from loguru import logger

from a2a_t.negotiation.common.constants import (
    NEGOTIATION_TEXT_KEY,
    NEGOTIATION_CONTEXT_KEY,
    TASK_PROMPT_KEY,
)
from a2a_t.negotiation.common.models import NegotiationContext

NEGOTIATION_RESOLUTION_MARKER = "[NEGOTIATION_RESOLUTION]"
NEGOTIATION_REQUEST_MARKER = "[NEGOTIATION_REQUEST]"
NEGOTIATION_CONTEXT_MARKER = "[NEGOTIATION_CONTEXT]"
NEGOTIATION_CONCERN_KEY = "negotiationConcern"


def build_negotiation_response_metadata(
    negotiation_context_data: Optional[Dict[str, Any]],
    negotiation_text: Optional[str],
    negotiation_concern: Optional[str] = None,
) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {}
    if negotiation_context_data:
        metadata[NEGOTIATION_CONTEXT_KEY] = negotiation_context_data
    if negotiation_text:
        metadata[NEGOTIATION_TEXT_KEY] = negotiation_text
    if negotiation_concern:
        metadata[NEGOTIATION_CONCERN_KEY] = negotiation_concern
    return metadata


def log_negotiation_context(context: NegotiationContext, prefix: str = "") -> None:
    logger.info(
        f"{prefix} Negotiation context: "
        f"type={context.negotiation_type.value}, "
        f"id={context.negotiation_id}, "
        f"role={context.role.value}, "
        f"round={context.round}, "
        f"status={context.status.value}"
    )


def is_follow_up_task(task_text: str) -> bool:
    return NEGOTIATION_RESOLUTION_MARKER in task_text


def cleanup_negotiation_resolution_marker(task_text: str) -> str:
    for marker in (NEGOTIATION_CONTEXT_MARKER, NEGOTIATION_RESOLUTION_MARKER):
        if marker not in task_text:
            continue
        parts = task_text.split(marker, 1)
        body = parts[0] if len(parts) > 1 else ""
        if len(parts) > 1:
            rest = parts[1]
            if "\nPlease re-execute the task based on the clarification above.\n" in rest:
                rest = rest.split("\nPlease re-execute the task based on the clarification above.\n", 1)[0]
            if "\nOriginal Task:\n" in rest:
                rest = rest.split("\nOriginal Task:\n", 1)[0]
        task_text = body.strip() + "\n\nPlease re-execute the task based on the clarification above."
        break
    return task_text.strip()
