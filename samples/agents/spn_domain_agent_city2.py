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

from samples.agents.negotiation_base_agent import NegotiationBaseAgentExecutor


SPN_DOMAIN_CITY2_PROMPT = """
You are an SPN Domain Agent simulator for City2 (Yuexi / Western Guangdong) OMC.
You receive a private-line fault diagnosis task for the City2 area.
Based on the received task, simulate a diagnosis result.

IMPORTANT: City2 (Yuexi / Western Guangdong) side is NORMAL in this scenario. The fault is on City1 (Yuedong / Eastern Guangdong) side.
Your diagnosis should show City2 is healthy:

1. 诊断结果类型: 诊断成功
2. 诊断结果详细信息: 广州地市OMC诊断结果 - 端口状态正常, 光功率-17dBm(正常范围), 无异常告警
3. 修复建议: 广州地市无需修复, 故障不在此地市
4. 故障根因列表: 无根因 (此地市正常)

Format your response in Chinese as a structured diagnosis report.

Task content: {task}
"""


class SpnDomainAgentCity2Executor(NegotiationBaseAgentExecutor):

    def __init__(self) -> None:
        super().__init__(agent_prompt_template=SPN_DOMAIN_CITY2_PROMPT)

    def _build_task_response(self, context, response, negotiation_context):
        """Build task response with Task-T metadata only.

        Mirrors Java demo buildResponseMetadata: only TASK_PROMPT_KEY is set.
        Authorization-T is pre-positioned, Notification-T is pushed via SSE.
        """
        from samples.agents.util.negotiation_utils import build_negotiation_response_metadata, TASK_PROMPT_KEY
        from a2a.types import Task, TaskStatus, TaskState, Artifact, Part
        import uuid

        metadata = build_negotiation_response_metadata(
            negotiation_context_data=negotiation_context if negotiation_context else None,
            negotiation_text=None,
        )
        metadata[TASK_PROMPT_KEY] = response

        return Task(
            id=context.task_id,
            context_id=context.context_id,
            status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
            artifacts=[
                Artifact(
                    artifact_id=str(uuid.uuid4()),
                    parts=[Part(text=response)]
                )
            ],
            metadata=metadata
        )
