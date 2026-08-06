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
from loguru import logger


SPN_DOMAIN_PROMPT = """
You are an SPN Domain Agent simulator for City1 (Yuedong (Eastern Guangdong)) OMC.
You receive a private-line fault diagnosis task for the Yuedong (Eastern Guangdong) area.
Based on the received task, simulate a diagnosis result.

IMPORTANT: Yuedong (Eastern Guangdong) side has a FAULT. Your response must include:

1. 诊断结果类型: 诊断成功
2. 诊断结果详细信息: 粤东地市OMC诊断结果 - 端口Down, 光功率-28dBm(低于阈值), 存在故障
3. 修复方案: 更换粤东侧OMC端口光模块, 恢复端口Down状态。此修复方案需要人工授权后执行。
   修复方案字段: needs_authorization=true, risk_level=medium
4. 故障根因列表:
   - 故障根因名称: 粤东侧OMC端口光模块故障
   - 详细描述: 客户A粤东-粤西间专线中断, 粤东OMC告警端口Down, 光功率-28dBm低于正常阈值
   - 修复建议: 更换粤东侧OMC端口光模块, 需要人工授权后执行
   - 资源对象标识: port-yuedong-omc-01
   - 资源对象类型: 端口
   - 资源对象名称: 粤东OMC端口01
   - 详细位置: 粤东地市OMC机房

Format your response in Chinese as a structured diagnosis report.

Task content: {task}
"""

RECOVERY_PROMPT = """
你是SPN领域粤东OMC抢通执行专家。用一句话报告抢通成功结果，提及粤东OMC端口恢复Up、专线业务恢复。中文。

诊断信息：
{diagnosis}
"""


class SpnDomainAgentExecutor(NegotiationBaseAgentExecutor):

    def __init__(self) -> None:
        super().__init__(agent_prompt_template=SPN_DOMAIN_PROMPT)

    def needs_negotiation(self, input_text: str) -> bool:
        """City1 always needs negotiation (mirrors Java needsNegotiation returning true)."""
        return True

    def _execute_task(self, user_input: str, task_id: str = None, context_id: str = None) -> str:
        """Run diagnosis then self-trigger recovery (mirrors Java executeBusiness)."""
        diagnosis = super()._execute_task(user_input, task_id, context_id)
        recovery = self._self_trigger_recovery(diagnosis)
        return diagnosis + "\n\n" + recovery

    def _self_trigger_recovery(self, diagnosis_result: str) -> str:
        """Check authorization whitelist and execute recovery if authorized.

        Mirrors Java's selfTriggerRecovery: checks the pre-positioned
        Authorization-T whitelist policy, executes recovery if the fault
        matches the whitelist, and pushes the result via Notification-T.
        """
        policy = self.get_authorization_policy()
        in_whitelist = (
            policy is not None
            and policy != ""
            and ("业务抢通" in policy or "光模块" in policy or "授权" in policy)
        )
        if in_whitelist:
            logger.info("[SPN-Domain-Agent] Fault in whitelist, self-triggering recovery")
            recovery_result = self._llm_recovery(diagnosis_result)
            logger.info(
                f"[SPN-Domain-Agent] Recovery result reported via Notification-T: {recovery_result}"
            )
            self.push_notification_result(recovery_result)
            return recovery_result
        logger.info("[SPN-Domain-Agent] Fault not in whitelist, refusing recovery")
        refusal = "操作不在白名单内，拒绝执行抢通。"
        self.push_notification_result(refusal)
        return refusal

    def _llm_recovery(self, diagnosis: str) -> str:
        """Generate recovery result via LLM."""
        prompt = RECOVERY_PROMPT.format(diagnosis=diagnosis)
        try:
            _, result = self.llm.ask_llm(prompt)
            if result:
                return result
        except Exception as e:
            logger.warning(f"[SPN-Domain-Agent] LLM recovery failed: {e}")
        return "粤东OMC端口光模块已更换，端口恢复Up，专线业务恢复正常。"

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
