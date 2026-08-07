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

import asyncio
import uuid
import queue as _queue
from pathlib import Path
from typing import Dict, Any, Optional
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import Task, TaskStatus, TaskState, Artifact, Part
from loguru import logger

from a2a_t.server import A2ATServer
from a2a_t.negotiation.common.enums import NegotiationType
from a2a_t.negotiation.common.models import StartNegotiationInput, NegotiationContext

from common.llm import get_llm_instance
from samples.agents.util.negotiation_utils import (
    NEGOTIATION_CONTEXT_KEY,
    NEGOTIATION_TEXT_KEY,
    TASK_PROMPT_KEY,
    NEGOTIATION_REQUEST_MARKER,
    build_negotiation_response_metadata,
    log_negotiation_context,
    is_follow_up_task,
    cleanup_negotiation_resolution_marker,
)

# A2A-T extension URIs for pre-positioned extension detection.
# Mirrors Java SDK's A2ATExtension enum values.
AUTHORIZATION_T_URI = "https://projects.tmforum.org/a2aproject/telecommunication/extensions/Authorization-T/v1"
NOTIFICATION_T_URI = "https://projects.tmforum.org/a2aproject/telecommunication/extensions/Notification-T/v1"


class NegotiationBaseAgentExecutor(AgentExecutor):
    """Server-side negotiation base, mirroring Java's NegotiationBaseAgentExecutor.

    Every agent that declares the Negotiation-T extension can receive and reply
    to negotiation messages. This base implements: on a new task it starts a
    fulfillment negotiation and replies INPUT_REQUIRED carrying the negotiation
    context; on a follow-up ([NEGOTIATION_RESOLUTION]) it re-executes the
    business and completes.

    Pre-positioned extensions (Authorization-T / Notification-T) are detected
    and handled before the negotiation flow: Authorization-T is stored as a
    whitelist policy; Notification-T opens a long-lived stream that stays open
    for later recovery results pushed via push_notification_result().
    """

    def __init__(self, agent_prompt_template: str) -> None:
        self.llm = get_llm_instance()
        env_path = Path(__file__).resolve().parents[2] / ".env"
        self.a2at_server = A2ATServer(env_path=env_path)
        self.prompt_template = agent_prompt_template
        self._authorization_policy: Optional[str] = None
        self._notification_queue: "_queue.Queue[str]" = _queue.Queue()
        self._shutdown = False
        logger.info(f"[{self.__class__.__name__}] Initialized with A2ATServer, env_path={env_path}")

    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        user_input = context.get_user_input()
        task_id = context.task_id or "N/A"
        ctx_id = context.context_id or "N/A"
        logger.info(f"[{self.__class__.__name__}] execute: task_id={task_id}, context_id={ctx_id}")

        # Read Task-T prompt from message metadata if present (mirrors Java SDK).
        task_t_uri = TASK_PROMPT_KEY
        try:
            if hasattr(context, 'message') and context.message:
                msg = context.message
                if msg.metadata and task_t_uri in msg.metadata:
                    task_t_text = msg.metadata[task_t_uri]
                    if isinstance(task_t_text, str) and len(task_t_text) > len(user_input or ''):
                        logger.info(f"[{self.__class__.__name__}] Using TASK-T prompt from message metadata")
                        user_input = task_t_text
        except Exception as e:
            logger.debug(f"[{self.__class__.__name__}] Could not read TASK-T metadata: {e}")

        # Detect pre-positioned extensions (Authorization-T / Notification-T).
        # These are sent before the workflow starts via ExtensionSender.
        pre_positioned_ext = self._detect_pre_positioned_extension(context)
        if pre_positioned_ext:
            if "Notification" in pre_positioned_ext:
                await self._handle_notification_subscription(context, event_queue)
                return
            elif "Authorization" in pre_positioned_ext:
                await self._handle_authorization(context, event_queue)
                return

        if is_follow_up_task(user_input):
            task = await self._handle_follow_up_task(context, user_input)
        else:
            task = await self._handle_new_task(context, user_input)

        await event_queue.enqueue_event(task)

    # ------------------------------------------------------------------
    # Pre-positioned extension handling (GAP 8 alignment with Java SDK)
    # ------------------------------------------------------------------

    def _detect_pre_positioned_extension(self, context: RequestContext) -> Optional[str]:
        """Detect if the incoming message is a pre-positioned extension."""
        try:
            if hasattr(context, 'message') and context.message:
                msg = context.message
                if msg.metadata:
                    if NOTIFICATION_T_URI in msg.metadata:
                        return "Notification-T"
                    if AUTHORIZATION_T_URI in msg.metadata:
                        return "Authorization-T"
        except Exception:
            pass
        return None

    async def _handle_authorization(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Accept and store the Authorization-T whitelist policy."""
        try:
            if hasattr(context, 'message') and context.message:
                msg = context.message
                if msg.metadata and AUTHORIZATION_T_URI in msg.metadata:
                    self._authorization_policy = str(msg.metadata[AUTHORIZATION_T_URI])
                    logger.info(
                        f"[{self.__class__.__name__}] Accepted Authorization-T policy: "
                        f"{len(self._authorization_policy)} chars"
                    )
        except Exception as e:
            logger.warning(f"[{self.__class__.__name__}] Failed to read Authorization-T: {e}")

        task = Task(
            id=context.task_id,
            context_id=context.context_id,
            status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
            artifacts=[],
            metadata={}
        )
        await event_queue.enqueue_event(task)

    async def _handle_notification_subscription(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Handle Notification-T subscription: send ack, keep stream open for results.

        Mirrors Java's handleNotificationSubscription: sends a "subscribed" ack
        artifact, then blocks on a queue to keep the SSE stream open. Recovery
        results pushed via push_notification_result() are forwarded as artifacts
        with the Notification-T URI in metadata.
        """
        agent_tag = self.__class__.__name__
        logger.info(f"[{agent_tag}] Notification-T subscription received, keeping stream open")

        ack_task = Task(
            id=context.task_id,
            context_id=context.context_id,
            status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
            artifacts=[
                Artifact(
                    artifact_id=str(uuid.uuid4()),
                    name=f"{agent_tag} subscription",
                    parts=[Part(text="Subscribed to recovery results")]
                )
            ],
            metadata={}
        )
        await event_queue.enqueue_event(ack_task)

        while not self._shutdown:
            try:
                result = await asyncio.to_thread(self._notification_queue.get, True, 2)
                logger.info(f"[{agent_tag}] Pushing recovery result via Notification-T stream")
                result_task = Task(
                    id=context.task_id,
                    context_id=context.context_id,
                    status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
                    artifacts=[
                        Artifact(
                            artifact_id=str(uuid.uuid4()),
                            name="recovery-result",
                            parts=[Part(text=result)],
                            metadata={NOTIFICATION_T_URI: result}
                        )
                    ],
                    metadata={NOTIFICATION_T_URI: result}
                )
                await event_queue.enqueue_event(result_task)
            except _queue.Empty:
                continue
            except Exception as e:
                if self._shutdown:
                    break
                logger.debug(f"[{agent_tag}] Notification queue error: {e}")
                continue
        
        logger.info(f"[{agent_tag}] Notification-T subscription closed")

    def push_notification_result(self, result: str) -> None:
        """Push a recovery result via the Notification-T stream."""
        self._notification_queue.put_nowait(result)

    def shutdown(self) -> None:
        """Signal this executor to shut down gracefully."""
        self._shutdown = True
        logger.info(f"[{self.__class__.__name__}] Shutdown signal received")

    def get_authorization_policy(self) -> Optional[str]:
        """Get the pre-positioned Authorization-T whitelist policy."""
        return self._authorization_policy

    # ------------------------------------------------------------------
    # Task handling (negotiation flow)
    # ------------------------------------------------------------------

    async def _handle_new_task(self, context: RequestContext, user_input: str) -> Task:
        negotiation_result = self._start_negotiation(user_input, context.task_id, context.context_id)

        negotiation_context_data = negotiation_result.get(NEGOTIATION_CONTEXT_KEY, {})
        negotiation_text = negotiation_result.get(NEGOTIATION_TEXT_KEY, "")
        if negotiation_context_data:
            try:
                negotiation_ctx = NegotiationContext.from_context(negotiation_context_data)
                log_negotiation_context(negotiation_ctx, f"[{self.__class__.__name__}]")
            except Exception as e:
                logger.warning(f"Failed to parse negotiation context: {e}")

        # Mirrors Java handleNewTask: if negotiation is needed, request it
        # WITHOUT executing the business. Business runs on the follow-up.
        if self.needs_negotiation(user_input):
            concern = self.generate_negotiation_concern(user_input)
            logger.info(f"[{self.__class__.__name__}] needs_negotiation=True, concern={concern}")
            metadata = build_negotiation_response_metadata(
                negotiation_context_data=negotiation_context_data,
                negotiation_text=negotiation_text,
                negotiation_concern=concern,
            )
            request_text = f"收到诊断任务，但当前信息不足以完成诊断。具体分析如下：\n\n{concern}\n\n请补充以上缺少的信息后，我将重新执行诊断。"
            return Task(
                id=context.task_id,
                context_id=context.context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_INPUT_REQUIRED),
                artifacts=[
                    Artifact(
                        artifact_id=str(uuid.uuid4()),
                        parts=[Part(text=request_text)]
                    )
                ],
                metadata=metadata
            )

        # No negotiation needed: execute business and complete
        response = await asyncio.to_thread(self._execute_task, user_input, context.task_id, context.context_id)
        return self._build_task_response(
            context=context,
            response=response,
            negotiation_context=negotiation_context_data,
        )

    async def _handle_follow_up_task(self, context: RequestContext, user_input: str) -> Task:
        logger.info(f"[{self.__class__.__name__}] Detected follow-up task with negotiation resolution")
        cleaned_input = cleanup_negotiation_resolution_marker(user_input)
        # Mirrors Java handleFollowUp: clean input, run business, complete.
        # No new negotiation is started -- the negotiation is already resolved.
        negotiation_context_data = {}

        execute_input = cleaned_input if cleaned_input else user_input
        response = await asyncio.to_thread(self._execute_task, execute_input, context.task_id, context.context_id)

        return self._build_task_response(
            context=context,
            response=response,
            negotiation_context=negotiation_context_data,
        )

    def _start_negotiation(self, user_input: str, task_id: str = None, context_id: str = None) -> Dict[str, Any]:
        try:
            facts = {"agent": self.__class__.__name__}
            if task_id:
                facts["task_id"] = task_id
            if context_id:
                facts["context_id"] = context_id
            negotiation_result = self.a2at_server.start_negotiation(
                StartNegotiationInput(
                    type=NegotiationType.FULFILLMENT,
                    content_text=user_input,
                    facts=facts
                )
            )
            negotiation_text = negotiation_result.get(NEGOTIATION_TEXT_KEY)
            if negotiation_text:
                logger.info(f"[{self.__class__.__name__}] Started fulfillment negotiation: {negotiation_text}")
            else:
                logger.info(f"[{self.__class__.__name__}] Started fulfillment negotiation (no text in result)")
            return negotiation_result
        except Exception as e:
            logger.error(f"[{self.__class__.__name__}] Failed to start negotiation: {e}")
            return {}

    def needs_negotiation(self, input_text: str) -> bool:
        """Whether the incoming task parameters require a Negotiation-T round.

        Mirrors Java SDK's needsNegotiation(input). Default: False (parameters
        sufficient, skip negotiation and run business directly). Override to
        return True when the agent needs clarification.
        """
        return False

    def generate_negotiation_concern(self, user_input: str) -> str:
        """Generate a specific negotiation concern listing exactly which fields are missing.

        Uses LLM to analyze the task input, identify what data was provided vs
        what is still needed, and return a structured list of missing fields.
        """
        prompt = (
            f"你是一个SPN专线故障诊断智能体。你收到了以下诊断任务请求：\n\n"
            f"---\n{user_input[:800]}\n---\n\n"
            f"请分析这个任务请求：\n"
            f"1. **已提供的信息**：任务中明确给出的所有内容，包括场景名称、任务类型、业务背景等\n"
            f"2. **缺少的关键数据**：要完成故障诊断还需要的具体数据字段\n\n"
            f"故障诊断通常需要以下具体数据（仅列出任务中**没有给出具体值**的字段）：\n"
            f"- 客户名称/专线电路编号\n"
            f"- 故障端口编号和端口状态\n"
            f"- 光功率值（收光/发光）和正常阈值\n"
            f"- 告警类型和告警时间\n"
            f"- 故障现象描述（如中断、丢包、时延等）\n"
            f"- 对端设备和端口信息\n\n"
            f"请按以下格式回复：\n"
            f"已提供：逐条列出任务中已有的信息（如场景名称、任务类型等，至少有一条）\n"
            f"缺少：逐条列出缺少的具体数据字段，每条一行\n\n"
            f"只输出分析结果，不要有其他内容。"
        )
        try:
            _, concern = self.llm.ask_llm(prompt)
            if concern and len(concern.strip()) > 10:
                return concern.strip()
        except Exception as e:
            logger.warning(f"[{self.__class__.__name__}] LLM concern generation failed: {e}")
        return (
            "已提供：SPN跨城专线故障诊断场景\n"
            "缺少：\n"
            "- 客户名称/专线电路编号\n"
            "- 故障端口编号\n"
            "- 端口状态（Up/Down）\n"
            "- 光功率值及正常阈值\n"
            "- 告警类型\n"
            "- 故障现象描述"
        )

    def _execute_task(self, user_input: str, task_id: str = None, context_id: str = None) -> str:
        prompt = self.prompt_template.format(task=user_input)
        ctx_lines = []
        if task_id:
            ctx_lines.append(f"Task ID: {task_id}")
        if context_id:
            ctx_lines.append(f"Context ID: {context_id}")
        if ctx_lines:
            prompt = "## Execution Context\n" + "\n".join(ctx_lines) + "\n\n" + prompt
        _, res = self.llm.ask_llm(prompt)
        logger.info(f"[{self.__class__.__name__}] Task: {user_input}, Result: {res}")
        return res

    def _build_task_response(
        self,
        context: RequestContext,
        response: str,
        negotiation_context: Dict[str, Any]
    ) -> Task:
        metadata = build_negotiation_response_metadata(
            negotiation_context_data=negotiation_context if negotiation_context else None,
            negotiation_text=None,
        )
        # Put the agent response in Task-T metadata, mirroring the Java SDK demo's
        # buildResponseMetadata which sets TASK_PROMPT_KEY -> response.
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

    async def cancel(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        logger.info(f"[{self.__class__.__name__}] Task cancelled: task_id={context.task_id}, context_id={context.context_id}")
