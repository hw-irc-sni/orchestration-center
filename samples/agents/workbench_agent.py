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

"""Workbench Agent -- workflow execution host.

Leader role: receives the raw intent via A2A-T, searches/loads the PSOP
workflow from the orchestration center, pre-positions extensions, then
executes the workflow via the workflow-engine SDK (execute_psop), streaming
SDK events back to the caller as A2A-T TaskUpdate events.

Mirrors the Java demo's TransportWorkbenchAgentExecutor +
WorkbenchOrchestrator + WorkbenchControlPoint.
"""

import asyncio
import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, Optional

from loguru import logger

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import (
    Task,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    TaskArtifactUpdateEvent,
    Artifact,
    Part,
    Message,
)

from workflow_engine import (
    A2ATransport,
    ControlPoint,
    EventCallback,
    EventType,
    ExtensionSender,
    RouteDecision,
    TaskResponse,
    Workflow as SDKWorkflow,
    WorkflowEngineClient,
    execute_psop,
    load_psop,
    search_psop,
    RegistryClient,
)

try:
    from a2a_t.llm.factory import LLMClientFactory as _LLMFactory
    from a2a_t.llm.providers.openai import OpenAIClient as _OpenAIClient
    _LLMFactory.register("deepseek", _OpenAIClient)
except Exception:
    pass

from common.llm import get_llm_instance
from common.util.config_util import get_conf


class WorkbenchControlPoint(ControlPoint):
    """Workbench ControlPoint -- routing / negotiation / self-loop policy.

    Migrated from orchestrate/runtime/exec_engine.py's _EngineControlPoint.
    The workbench owns its own business policy (LLM routing, negotiation
    clarification, self-loop merge).
    """

    _llm_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="wb_llm_")
    _NEGOTIATION_MAX_ROUNDS = 3

    def __init__(
        self,
        orch_url: str,
        ssl_verify: bool = False,
        a2at_env_path: Optional[str] = None,
        lang: str = "zh",
    ):
        self.orch_url = orch_url.rstrip("/")
        self.ssl_verify = ssl_verify
        self.a2at_env_path = a2at_env_path
        self.lang = lang or "zh"
        self.llm_client = get_llm_instance()
        self._sdk_workflow: Optional[SDKWorkflow] = None
        self._step_outputs: Dict[str, Dict[str, Any]] = {}
        self._current_step: Optional[str] = None
        self._engine_client: Optional[WorkflowEngineClient] = None

    def set_workflow(self, workflow: SDKWorkflow):
        self._sdk_workflow = workflow

    def set_engine_client(self, engine_client: WorkflowEngineClient):
        self._engine_client = engine_client

    def update_step_outputs(self, step_outputs: Dict[str, Dict[str, Any]]):
        self._step_outputs = dict(step_outputs)

    def set_current_step(self, step_name: str):
        self._current_step = step_name

    # ------------------------------------------------------------------
    # ControlPoint interface
    # ------------------------------------------------------------------

    async def on_task(self, request, engine_client: WorkflowEngineClient) -> TaskResponse:
        """on_task: send a task to the agent via A2A-T and return the response.

        The SDK handles Task-T prompt generation, Negotiation-T auto-loop,
        auth, and extension header injection internally. Just send the message.
        """
        task_label = request.description or request.message
        try:
            result = await engine_client.send_message(request.agent_name, request.message)
            if result.task_state and "INPUT_REQUIRED" in result.task_state:
                raise RuntimeError(
                    f"Negotiation with agent '{request.agent_name}' did not converge "
                    f"after {self._NEGOTIATION_MAX_ROUNDS} round(s)."
                )
            return TaskResponse(success=bool(result.text), output=result.text or "")
        except Exception as e:
            err = f"Agent call failed : {str(e)}"
            logger.error(f"  >Task failed: {task_label} | Error: {err}")
            return TaskResponse(success=False, error=err)

    async def on_self_task(self, request) -> TaskResponse:
        """on_self_task: self-loop node -- the workbench handles it locally.

        Uses LLM to merge/aggregate prior step results. Does NOT send an
        A2A-T message (self-loop nodes are processed in-process).
        """
        try:
            result = await self._llm_merge(request.message)
            return TaskResponse(success=True, output=result)
        except Exception as e:
            err = f"Self-loop task failed : {str(e)}"
            logger.error(f"  >Self-loop failed: {request.message[:60]} | Error: {err}")
            return TaskResponse(success=False, error=err)

    async def on_route(self, step_name: str, results: Dict[str, Any],
                       conditions: list) -> RouteDecision:
        """on_route: LLM-driven branch decision at conditional forks."""
        sdk_step = self._find_step(step_name)
        next_name = await self._llm_route_decision(sdk_step, results)
        return RouteDecision(next_step=next_name)

    async def on_negotiation(self, agent_name: str, negotiation_text: str,
                             receive_result: Dict[str, Any]) -> str:
        """on_negotiation: supply clarification during Negotiation-T auto-loop.

        Strategy: forward to direct DAG predecessors first; if they return
        data, use that as the clarification. Otherwise, LLM-generate a
        clarification from the execution context.
        """
        predecessor_data = await self._forward_to_predecessors(agent_name, negotiation_text)
        if predecessor_data:
            return predecessor_data
        receive_msg = receive_result.get("message", "") if isinstance(receive_result, dict) else ""
        clarification = await self._generate_clarification(
            agent_name=agent_name,
            original_task="",
            negotiation_text=negotiation_text,
            receive_message=receive_msg,
        )
        return clarification or ""

    # LLM routing (preserved from exec_engine._llm_route_decision)
    # ------------------------------------------------------------------

    async def _llm_route_decision(self, current_step, task_result: Dict[str, Any]) -> str:
        results_context = []
        for skill, res in task_result.items():
            if isinstance(res, dict) and "error" in res:
                results_context.append(f"[{skill}]: Execution failed - {res['error']}")
            else:
                text_res = res if isinstance(res, str) else str(res)
                results_context.append(f"[{skill}]: Execution succeeded - Output summary: {text_res}")
        results_text = "\n".join(results_context)
        next_list = current_step.next if current_step is not None and current_step.next else []
        next_conditions = json.dumps(
            [{"step": c.step, "condition": c.condition} for c in next_list],
            ensure_ascii=False, indent=2,
        )
        if current_step is not None:
            step_name = current_step.name
            if hasattr(current_step, "step_type") and current_step.step_type is not None:
                step_type_val = current_step.step_type.value
            elif hasattr(current_step, "type") and current_step.type is not None:
                step_type_val = current_step.type.value
            else:
                step_type_val = "AllSuccess"
        else:
            step_name, step_type_val = "(unknown)", "AllSuccess"
        prompt_template = f"""
# Role
You are a workflow logic controller. Your task is to determine the next step of the
workflow based on the task execution results and predefined conditions.

# Current Context
Current step: {step_name}
Step type: {step_type_val}

# Execution Results (Previous Step Output)
{results_text}

# Next Conditions (Required for Transition)
{next_conditions}

# Decision Logic
1. Analyze the Execution Results above.
2. Check whether any of the Next Conditions' "condition" descriptions are satisfied.
   - If a condition says e.g. "xx succeeded", check the results for evidence that xx succeeded.
   - An empty condition ('""') typically means unconditional transition to the next step.
3. If a condition is met, output the corresponding target step name.
4. If no condition is met, or the task execution contains an error, output "end".
5. If the result is ambiguous but appears successful, output "retry" to request manual intervention.

# Output Format
- Output exactly one word or phrase: the target step name (e.g. "step2"), "end", or "retry".
- Do NOT output any explanation, punctuation, or other characters.
"""
        if not self.llm_client:
            raise ValueError("LLM Client not initialized.")
        try:
            t0 = time.time()
            logger.info(f"[ControlPoint] LLM route decision for step '{step_name}': calling LLM...")
            _, decision = await asyncio.get_event_loop().run_in_executor(
                self._llm_executor, self.llm_client.ask_llm, prompt_template
            )
            logger.info(f"[ControlPoint] LLM route decision for step '{step_name}': LLM call done ({time.time()-t0:.2f}s)")
            decision = decision.strip() if decision else ""
            if not decision:
                logger.error(f"LLM returned empty decision for step '{step_name}', defaulting to termination.")
                return "end"
            logger.info(f"LLM route decision for step '{step_name}': raw='{decision}', conditions={next_conditions}")
            if decision in ["end", "retry"]:
                return decision
            allowed_next = [jc.step for jc in next_list]
            allowed_lower = {n.lower(): n for n in allowed_next}
            if decision in allowed_next:
                return decision
            if decision.lower() in allowed_lower:
                logger.info(f"LLM step name '{decision}' case-normalized to '{allowed_lower[decision.lower()]}'")
                return allowed_lower[decision.lower()]
            else:
                logger.warning(f"LLM returned step '{decision}' not in declared next {allowed_next}, defaulting to termination.")
                return "end"
        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            return "end"

    # ------------------------------------------------------------------
    # LLM self-loop merge (preserved from exec_engine.on_self_task logic)
    # ------------------------------------------------------------------

    async def _llm_merge(self, message: str) -> str:
        """LLM merge/analyze for self-loop nodes (e.g. merge_analysis step)."""
        context = self._build_merge_context()
        lang_hint = "Respond in Chinese." if self.lang == "zh" else "Respond in English."
        prompt = f"""# Role
You are the workbench agent performing a self-loop (local processing) task.
Analyze the available execution context and produce a comprehensive result.

# Execution Context (completed steps and their outputs)
{context}

# Task
{message}

# Output
Produce a clear, structured analysis based on the context above. {lang_hint}"""
        try:
            t0 = time.time()
            logger.info(f"[Workbench] Self-loop merge: calling LLM...")
            _, result = await asyncio.get_event_loop().run_in_executor(
                self._llm_executor, self.llm_client.ask_llm, prompt
            )
            logger.info(f"[Workbench] Self-loop merge: LLM call done ({time.time()-t0:.2f}s)")
            if result:
                logger.info(f"[Workbench] Self-loop merge result: {result[:150]}...")
                return result
        except Exception as e:
            logger.error(f"[Workbench] LLM merge failed: {e}")
        return "Self-loop processing complete (LLM unavailable)."

    def _build_merge_context(self) -> str:
        if not self._step_outputs:
            return "(no completed steps yet)"
        steps = self._sdk_workflow.steps if self._sdk_workflow else []
        parts = []
        for step in steps:
            name = getattr(step, "name", None)
            if not name or name not in self._step_outputs:
                continue
            outputs = self._step_outputs[name]
            parts.append(f"### {name}")
            for task_desc, output in outputs.items():
                text = output if isinstance(output, str) else str(output)
                parts.append(f"- Task: {task_desc}")
                parts.append(f"  Output: {text}")
        return "\n".join(parts) if parts else "(no completed steps yet)"

    # ------------------------------------------------------------------
    # Negotiation: LLM clarification (DAG forwarding simplified)
    # ------------------------------------------------------------------

    async def _forward_to_predecessors(self, agent_name: str, negotiation_text: str) -> Optional[str]:
        """Forward negotiation request to direct DAG predecessors via A2A-T.

        Mirrors the original exec_engine._forward_to_predecessors: send a
        follow-up message to predecessor agents asking for supplemental data
        or clarification, then return their combined response.
        """
        current_name = self._current_step
        if not current_name or self._sdk_workflow is None:
            return None
        workflow = self._sdk_workflow
        predecessor_names = []
        for s in workflow.steps:
            if s.next:
                for jc in s.next:
                    if jc.step == current_name and s.name != current_name:
                        predecessor_names.append(s.name)
                        break
        if not predecessor_names:
            logger.info(f"Step '{current_name}' has no predecessor steps")
            return None
        logger.info(
            f"Forwarding negotiation from '{agent_name}' (step '{current_name}') "
            f"to predecessors: {predecessor_names}"
        )
        if not self._engine_client:
            logger.warning("No engine_client set, cannot forward to predecessors")
            return None
        collected = {}
        for pred_name in predecessor_names:
            prior_output = self._step_outputs.get(pred_name, {})
            prior_summary = json.dumps(prior_output, ensure_ascii=False, default=str)[:2000]
            pred_step = None
            for s in workflow.steps:
                if s.name == pred_name:
                    pred_step = s
                    break
            if not pred_step or not pred_step.subtasks:
                continue
            for task in pred_step.subtasks:
                pred_agent = task.agent
                if not pred_agent:
                    continue
                forward_msg = (
                    f"[Negotiation Request - Forwarded from Workbench]\n\n"
                    f"Agent '{agent_name}' is processing a follow-up task "
                    f"and indicates it needs additional data or clarification.\n\n"
                    f"The original output you provided for step '{pred_name}':\n"
                    f"---\n{prior_summary}\n---\n\n"
                    f"The request from '{agent_name}':\n"
                    f"---\n{negotiation_text}\n---\n\n"
                    f"As the predecessor agent, please provide supplemental data "
                    f"or clarification to help resolve this."
                )
                logger.info(f"Forwarding to predecessor '{pred_agent}' (step '{pred_name}')")
                try:
                    result = await self._engine_client.send_message(pred_agent, forward_msg)
                    if result and result.text:
                        collected[pred_agent] = result.text
                        logger.info(f"Got response from '{pred_agent}' ({len(result.text)} chars)")
                    else:
                        logger.warning(f"Empty response from '{pred_agent}'")
                except Exception as e:
                    logger.warning(f"Failed to contact predecessor '{pred_agent}': {e}")
        if not collected:
            logger.warning("No data collected from any predecessor agent")
            return None
        parts = [f"[Response from {a}]:\n{d}" for a, d in collected.items()]
        return "\n\n".join(parts)

    async def _generate_clarification(self, agent_name: str, original_task: str,
                                       negotiation_text: str, receive_message: str) -> str:
        if not self.llm_client:
            return (
                "Engine received your negotiation request and has reviewed the execution "
                "context. Please proceed with the original task using the clarification "
                "above. If you have specific questions, state them clearly."
            )
        workflow_context = self._build_merge_context()
        lang_hint = "Respond in Chinese." if self.lang == "zh" else "Respond in English."
        prompt = f"""# Role
You are the workbench agent's negotiation handler. An agent expressed uncertainty
or confusion about a task you assigned. Based on the completed workflow execution
context below, provide an accurate clarification or supplementary explanation.

# Important Constraints
- You may ONLY base your answer on the actual outputs in the "Executed Workflow Context" below.
  **Do NOT fabricate or speculate about facts that have not occurred.**
- If the context is insufficient to answer the agent's concern, tell the agent directly:
  "Insufficient information available. Please do your best with what you have."
- Be concise and focused on the specific concern the agent raised.

# Executed Workflow Context (completed steps and their outputs)
{workflow_context}

# Current Agent
{agent_name}

# Original Task
{original_task or "(see request message)"}

# Agent's Negotiation Request (the concern or question)
{negotiation_text}

# Supplementary Notes
{receive_message}

# Task
Based on the execution context above, provide a clear clarification to the agent.
Do NOT add any prefix markers like "Clarification:". {lang_hint}"""
        try:
            t0 = time.time()
            logger.info(f"[Workbench] Negotiation clarification for '{agent_name}': calling LLM...")
            _, clarification = await asyncio.get_event_loop().run_in_executor(
                self._llm_executor, self.llm_client.ask_llm, prompt,
            )
            logger.info(f"[Workbench] Negotiation clarification for '{agent_name}': LLM call done ({time.time()-t0:.2f}s)")
            clarification = clarification.strip() if clarification else ""
            if clarification:
                logger.info(f"Generated negotiation clarification for '{agent_name}': {clarification[:150]}...")
                return clarification
        except Exception as e:
            logger.error(f"LLM clarification failed: {e}")
        return (
            "Engine received your negotiation request. Please re-attempt the original "
            "task. If you have specific questions, state them clearly."
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_step(self, step_name: str):
        if self._sdk_workflow is None:
            return None
        for s in self._sdk_workflow.steps:
            if s.name == step_name:
                return s
        return None


class _WorkbenchEventCallback(EventCallback):
    """Tracks current step for negotiation forwarding + step outputs update."""

    def __init__(self, control_point: WorkbenchControlPoint):
        self._cp = control_point
        self._step_start_times: Dict[str, float] = {}
        self._task_start_times: Dict[str, float] = {}

    def on_event(self, event_type: str, data: Dict[str, Any]):
        now = time.time()
        if event_type == EventType.STEP_START:
            step_name = data.get("step")
            self._step_start_times[step_name] = now
            self._cp.set_current_step(step_name)
            logger.info(f"[Timing] Step '{step_name}' started")
        elif event_type == EventType.TASK_REQUEST:
            agent = data.get("agent", "?")
            step = data.get("step", "?")
            self._task_start_times[agent] = now
            logger.info(f"[Timing] Task dispatched to '{agent}' (step={step})")
        elif event_type == EventType.TASK_RESPONSE:
            agent = data.get("agent", "?")
            step = data.get("step", "?")
            elapsed = now - self._task_start_times.get(agent, now)
            logger.info(f"[Timing] Task response from '{agent}' (step={step}): {elapsed:.2f}s")
        elif event_type == EventType.TASK_STATUS_CHANGED:
            step_name = data.get("step")
            status = data.get("status", "")
            if step_name and status:
                outputs = self._cp._step_outputs.setdefault(step_name, {})
                outputs[data.get("subtask", "task")] = data.get("result", "")
        elif event_type == EventType.STEP_COMPLETE:
            step_name = data.get("step")
            elapsed = now - self._step_start_times.get(step_name, now)
            logger.info(f"[Timing] Step '{step_name}' completed: {elapsed:.2f}s")
        elif event_type == EventType.WORKFLOW_COMPLETE:
            d = data if isinstance(data, dict) else {}
            self._cp.update_step_outputs(d.get("step_outputs", {}))
        elif event_type == EventType.ERROR:
            d = data if isinstance(data, dict) else {}
            self._cp.update_step_outputs(d.get("step_outputs", {}))


class WorkbenchAgentExecutor(AgentExecutor):
    """Workbench Agent -- workflow execution host, integrated with workflow-engine SDK.

    Leader role: receives the raw intent via A2A-T, searches/loads the PSOP
    workflow from the orchestration center, pre-positions extensions, then
    executes the workflow via execute_psop, streaming SDK events back
    as A2A-T TaskUpdate events.
    """

    _AUTH_INPUT = (
        "任务类型新增授权，操作名称业务抢通，"
        "操作类型光模块更换，"
        "操作对象SPN专线业务，溯源策略OMC自判自执行，"
        "触发执行条件业务投诉诊断确认故障，"
        "预期输出返回是否成功。"
    )
    _NOTIF_INPUT = (
        "通知主题为service-recovery-execution-result，"
        "订阅条件业务抢通方案执行结果，"
        "上报通知数据格式为TextPart。"
    )

    def __init__(self) -> None:
        self.lang = "zh"
        self._a2at_env_path = str(Path(__file__).resolve().parents[2] / ".env")
        self._ssl_verify = str(get_conf().get("client_verify_server", "false")).lower() == "true"

        # Orchestration center URL (for PSOP search/load via external API)
        orch_port = get_conf().get("port", "5001")
        self._orch_url = f"https://127.0.0.1:{orch_port}"

        # Registry center URL (for agent card fetching)
        # Keep the configured protocol; ssl_verify controls cert validation
        registry_url = get_conf().get("agent_registry_url", None)
        self._registry_url = registry_url or "https://127.0.0.1:5000"

        self._cred_path = str(
            Path(__file__).resolve().parent.parent / "agent_credentials.json"
        )

        logger.info(
            f"[WorkbenchAgent] Initialized: orch_url={self._orch_url}, "
            f"registry_url={self._registry_url}, ssl_verify={self._ssl_verify}"
        )

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        t_execute = time.time()
        intent = context.get_user_input()
        task_id = context.task_id or "N/A"
        ctx_id = context.context_id or "N/A"
        logger.info(f"[WorkbenchAgent] execute: task_id={task_id}, context_id={ctx_id}, intent={intent[:100]}")

        collected_events = []
        await event_queue.enqueue_event(Task(
            id=context.task_id,
            context_id=context.context_id,
            status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
            metadata={},
        ))

        try:
            processed_intent = await self._process_intent(intent)

            request_metadata = context.metadata or {}
            psop_id = request_metadata.get("__orch_psop_id__")
            if psop_id:
                logger.info(f"[WorkbenchAgent] Using psop_id from orchestration center: {psop_id}")
            else:
                t0 = time.time()
                psop_id = await self._search_psop(processed_intent)
                logger.info(f"[WorkbenchAgent] PSOP search done ({time.time()-t0:.2f}s)")

            t0 = time.time()
            workflow = await self._load_psop(psop_id)
            logger.info(f"[WorkbenchAgent] PSOP load done ({time.time()-t0:.2f}s)")

            t0 = time.time()
            agent_cards = await self._load_agent_cards()
            logger.info(f"[WorkbenchAgent] Agent cards load done: {len(agent_cards)} cards ({time.time()-t0:.2f}s)")

            transport = A2ATransport(
                agent_cards=agent_cards,
                a2at_env_path=self._a2at_env_path,
                credentials_config=self._cred_path,
                ssl_verify=self._ssl_verify,
            )
            engine_client = WorkflowEngineClient(transport, max_negotiation_rounds=3)

            sender = await self._pre_position_extensions(transport, agent_cards, workflow=workflow, event_queue=event_queue, context=context)

            cp = WorkbenchControlPoint(
                orch_url=self._orch_url,
                ssl_verify=self._ssl_verify,
                a2at_env_path=self._a2at_env_path,
                lang=self.lang,
            )
            sdk_workflow = workflow
            cp.set_workflow(sdk_workflow)
            cp.set_engine_client(engine_client)
            engine_client.set_control_point(cp)
            engine_client.set_event_callback(_WorkbenchEventCallback(cp))

            t0 = time.time()
            logger.info(f"[WorkbenchAgent] Starting workflow execution")
            async for event in execute_psop(
                psop=workflow,
                agent_cards=agent_cards,
                control_point=cp,
                engine_client=engine_client,
                runtime_intent=processed_intent,
                lang=self.lang,
                ssl_verify=self._ssl_verify,
            ):
                collected_events.append(event)
                task_update = self._event_to_task_update(event, context)
                await event_queue.enqueue_event(task_update)
            logger.info(f"[WorkbenchAgent] Workflow execution done ({time.time()-t0:.2f}s), {len(collected_events)} events")

            await event_queue.enqueue_event(Task(
                id=context.task_id,
                context_id=context.context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
                metadata={"__sdk_events__": json.dumps(collected_events, ensure_ascii=False, default=str)},
            ))
            logger.info(f"[WorkbenchAgent] Total execute time: {time.time()-t_execute:.2f}s")
            try:
                if sender:
                    sender.cancel_notification_streams()
            except Exception:
                pass
            try:
                await engine_client.close()
            except Exception:
                pass

        except Exception as e:
            logger.error(f"[WorkbenchAgent] Failed: {e}", exc_info=True)
            await event_queue.enqueue_event(self._error_task(context, str(e)))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        logger.info(f"[WorkbenchAgent] Task cancelled: task_id={context.task_id}")

    async def _process_intent(self, intent: str) -> str:
        return intent

    async def _search_psop(self, intent: str) -> str:
        results = await search_psop(self._orch_url, intent, top_n=3, ssl_verify=self._ssl_verify)
        if results:
            logger.info(f"[WorkbenchAgent] Found PSOP: {results[0].workflow_id}")
            return results[0].workflow_id
        raise RuntimeError("No matching workflow found")

    async def _load_psop(self, psop_id: str):
        workflow = await load_psop(self._orch_url, psop_id, ssl_verify=self._ssl_verify)
        logger.info(f"[WorkbenchAgent] Loaded PSOP: {workflow.name} ({len(workflow.steps)} steps)")
        return workflow

    async def _load_agent_cards(self) -> list:
        registry = RegistryClient(self._registry_url, ssl_verify=self._ssl_verify)
        cards = await registry.fetch_agent_cards()
        logger.info(f"[WorkbenchAgent] Loaded {len(cards)} agent cards from registry")
        return cards

    async def _pre_position_extensions(self, transport: A2ATransport, agent_cards: list, workflow=None, event_queue=None, context=None):
        t_total = time.time()
        sender = ExtensionSender(transport)
        workflow_agents = set()
        if workflow:
            for step in workflow.steps:
                for subtask in (step.subtasks or []):
                    if subtask.agent:
                        workflow_agents.add(subtask.agent)
        logger.info(f"[WorkbenchAgent] Pre-positioning to {len(workflow_agents)} workflow agents: {workflow_agents}")
        for card in agent_cards:
            name = getattr(card, "name", "") or (card.get("name", "") if isinstance(card, dict) else "")
            if not name or name not in workflow_agents:
               continue
            if "Workbench" in name:
                continue
            try:
                t0 = time.time()
                logger.info(f"[WorkbenchAgent] Pre-position Authorization-T to {name}")
                await sender.send_authorization(name, "下发授权放行策略", self._AUTH_INPUT)
                logger.info(f"[WorkbenchAgent] Authorization-T to {name} done ({time.time()-t0:.2f}s)")
            except Exception as e:
                logger.warning(f"[WorkbenchAgent] Auth-T to {name} failed: {e}")
            logger.info(f"[WorkbenchAgent] Pre-position Notification-T to {name} (long-lived SSE)")
            try:
                await sender.send_notification(
                    name, "订阅业务抢通结果通知", self._NOTIF_INPUT,
                    event_callback=lambda evt, n=name: self._on_notification_event(evt, n, event_queue, context),
                )
                logger.info(f"[WorkbenchAgent] Notification-T subscription confirmed for {name}")
            except Exception as e:
                logger.warning(f"[WorkbenchAgent] Notification-T to {name} failed: {e}")
        logger.info(f"[WorkbenchAgent] Extension pre-positioning complete ({time.time()-t_total:.2f}s)")
        return sender

    def _on_notification_event(self, event: Dict[str, Any], agent_name: str, event_queue=None, context=None):
        """Handle Notification-T SSE events (recovery results pushed by agents)."""
        text = event.get("text", "")
        state = event.get("state", "")
        evt_type = event.get("type", "")
        logger.info(f"[WorkbenchAgent] Notification-T from {agent_name}: type={evt_type}, state={state}, text={len(text)} chars")
        if text:
            logger.info(f"[WorkbenchAgent] Recovery result from {agent_name}: {text[:200]}")
        if event_queue and context:
            artifact_text = f"[Notification-T] {agent_name}: {text}" if text else f"[Notification-T] {agent_name}: {evt_type}"
            artifact = Artifact(
                artifact_id=str(uuid.uuid4()),
                name=f"notification-{agent_name}",
                parts=[Part(text=artifact_text)],
                metadata={"notification_agent": agent_name, "notification_state": state, "notification_type": evt_type},
            )
            try:
                loop = asyncio.get_event_loop()
                loop.create_task(event_queue.enqueue_event(TaskArtifactUpdateEvent(
                    task_id=context.task_id,
                    context_id=context.context_id,
                    artifact=artifact,
                )))
            except Exception as e:
                logger.warning(f"[WorkbenchAgent] Failed to enqueue notification artifact: {e}")

    # Event types that carry artifact content (not just status)
    _ARTIFACT_EVENTS = frozenset({"agent_request", "agent_response", "step_complete"})

    def _event_to_task_update(self, event: dict, context: RequestContext):
        """Convert an SDK event dict to an A2A-T TaskUpdate.

        Per the plan encoding table:
        - Status events (step_start, route_decision, negotiation_*, etc.)
          -> TaskStatusUpdateEvent (WORKING)
        - Artifact events (agent_request, agent_response, step_complete)
          -> TaskArtifactUpdateEvent (text summary)
        - Terminal events (complete, error, close)
          -> TaskStatusUpdateEvent (COMPLETED/FAILED)

        The raw SDK event is preserved in metadata["__sdk_event__"] so the
        orchestration center can forward it verbatim to the frontend.
        """
        etype = event.get("type", "")
        data = event.get("data", {})
        summary = self._event_summary(etype, data)
        metadata = {"__sdk_event__": json.dumps(event, ensure_ascii=False, default=str)}

        if etype in self._ARTIFACT_EVENTS:
            return TaskArtifactUpdateEvent(
                task_id=context.task_id,
                context_id=context.context_id,
                artifact=Artifact(
                    artifact_id=str(uuid.uuid4()),
                    name=etype,
                    parts=[Part(text=summary)],
                ),
                metadata=metadata,
                last_chunk=False,
            )

        state = (
            TaskState.TASK_STATE_COMPLETED if etype in ("complete", "close")
            else TaskState.TASK_STATE_FAILED if etype == "error"
            else TaskState.TASK_STATE_WORKING
        )
        return TaskStatusUpdateEvent(
            task_id=context.task_id,
            context_id=context.context_id,
            status=TaskStatus(
                state=state,
                message=Message(role=2, parts=[Part(text=summary)]),
            ),
            metadata=metadata,
        )

    def _event_summary(self, etype: str, data: dict) -> str:
        if etype == "start":
            wf_name = data.get("workflow", "")
            return f"工作流开始: {wf_name}" if wf_name else "工作流开始"
        if etype == "step_start":
            return f"步骤开始: {data.get('step', '')}"
        if etype == "agent_request":
            return f"-> {data.get('agent', '')}"
        if etype == "agent_response":
            return f"<- {data.get('agent', '')}"
        if etype == "route_decision":
            return f"路由: {data.get('step', '')} -> {data.get('next', '')}"
        if etype == "step_complete":
            return f"步骤完成: {data.get('step', '')}"
        if etype == "task_status_changed":
            return f"状态: {data.get('status', '')}"
        if etype == "negotiation_request":
            return f"协商请求: {data.get('agent', '')}"
        if etype == "negotiation_resolved":
            return f"协商解决: {data.get('agent', '')}"
        if etype == "negotiation_failed":
            return f"协商失败: {data.get('agent', '')}"
        if etype == "authorization_request":
            return f"授权请求: {data.get('agent', '')}"
        if etype == "notification":
            return f"通知: {data.get('agent', '')}"
        if etype == "complete":
            return "工作流执行完成"
        if etype == "error":
            return f"错误: {data.get('error', '')}"
        if etype == "close":
            return "流结束"
        return etype

    def _error_task(self, context: RequestContext, error_msg: str) -> TaskStatusUpdateEvent:
        return TaskStatusUpdateEvent(
            task_id=context.task_id,
            context_id=context.context_id,
            status=TaskStatus(
                state=TaskState.TASK_STATE_FAILED,
                message=Message(role=2, parts=[Part(text=f"错误: {error_msg}")]),
            ),
            metadata={"__sdk_event__": json.dumps(
                {"type": "error", "data": {"error": error_msg}}, ensure_ascii=False
            )},
        )
