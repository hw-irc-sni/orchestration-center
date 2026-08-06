# 工作台智能体架构重构方案

> 日期: 2026-07-31
> 状态: 已实施
>
> **注意：本方案已实施完成。** `DynamicWorkflowEngine` 已被 `OrchestrationEngine`（薄 A2A-T 分发通道）替代，工作流执行逻辑已迁移至工作台智能体（`samples/agents/workbench_agent.py`）和 `workflow-engine` SDK。

## 1. 背景与问题

### 当前架构

```
前端 UI -> HTTP/SSE -> DynamicWorkflowEngine (编排中心)
                         |-- PSOP 搜索/加载
                         |-- engine_client + transport (SDK)
                         |-- ExtensionPrePositioner
                         |-- _EngineControlPoint (on_task/on_self_task/on_route/on_negotiation)
                         +-- execute_psop -> WorkflowExecutor
                              | A2A-T
                              v
                            SPN Agents
```

### 问题

1. 编排中心直接执行工作流，没有经过 A2A-T 协议层 -- 编排中心自己扮演了工作台智能体，但没有走 A2A-T 的接收路径
2. 没有工作台智能体 -- 其他智能体或外部调用方无法通过 A2A-T 把任务发给"工作台"
3. 与 Java demo 不对齐 -- Java 的工作台是一个独立 A2A agent，接收和返回都走 A2A-T
4. 编排中心耦合了工作流执行逻辑（路由、协商、自旋），无法作为稳定框架复用
5. 前端执行过程不可观测 -- 事件平铺在时间线上，无步骤分组，协议细节展示不完整

---

## 2. 目标架构

### 三层分离

```
+---------------------------------------------------------------------------+
|  编排中心 (框架层，不再动)                                                 |
|  |-- 前端 UI + REST API                                                   |
|  |-- PSOP 工作流定义管理 (CRUD、搜索 API -- 纯数据服务，供工作台调用)      |
|  |-- Agent Card 注册表管理 (供工作台调用)                                  |
|  |-- 用户认证/鉴权                                                        |
|  +-- 任务下发 + SSE 事件转发                                              |
|         |                                          ^                      |
|         | A2A-T send_message (意图)                 | SSE 事件流           |
|         v                                          |                      |
+---------------------------------------------------------------------------+
|  工作台智能体 (载体层，集成 SDK，按业务域变化)                              |
|  |-- A2A-T server: 接收编排中心下发的任务意图                              |
|  |-- 1. 意图处理: LLM 理解/改写原始意图                                   |
|  |-- 2. PSOP 检索: 用处理后的意图调编排中心 API -> 拿到 Top1 工作流        |
|  |-- 3. 加载 agent cards (调注册中心)                                     |
|  |-- 4. 扩展预置 (Authorization-T / Notification-T)                       |
|  |-- 5. ControlPoint: on_task / on_self_task / on_route / on_negotiation  |
|  |-- 6. execute_psop -> WorkflowExecutor                                  |
|  |      |-- on_task -> A2A-T -> SPN Agent City1/City2                     |
|  |      |-- on_self_task -> 本地处理 (leader 自己干的活)                  |
|  |      +-- on_route -> LLM 路由决策                                      |
|  +-- 7. 事件回流: SDK 事件 -> A2A-T TaskUpdate -> 编排中心 -> 前端         |
+---------------------------------------------------------------------------+
         | A2A-T                                    ^ A2A-T SSE
         v                                          |
+---------------------------------------------------------------------------+
|  SPN 智能体 (worker agents)                                               |
|  |-- SPN Domain Agent City1 (粤东 -- 故障侧)                              |
|  |-- SPN Domain Agent City2 (粤西 -- 正常侧)                              |
|  +-- 可扩展: 其他业务域智能体                                              |
+---------------------------------------------------------------------------+
```

### 核心原则

- 编排中心是稳定框架，一套代码可扩展，新增工作流/智能体不改编排中心
- 工作台智能体是可变载体，集成 SDK，按业务域变化
- 前端 UI 输入的意图，等价于给工作台 Agent 发了个意图任务
- 工作台是 Leader 角色：统筹工作流里其他智能体，也可处理 self-loop 节点

---

## 3. 职责划分

| 职责 | 当前位置 | 调整后 | 为什么 |
|------|---------|--------|--------|
| PSOP 搜索/加载 | exec_engine.py | 工作台智能体 | 检索是工作流执行的逻辑，工作台自己做意图理解后检索 |
| 意图处理 (LLM 理解/改写) | 无 | 工作台智能体 | 工作台接收原始意图后做二次处理 |
| Agent Card 加载 | exec_engine.py | 工作台智能体 | 工作台需要知道有哪些 agent 可用 |
| 扩展预置 | exec_engine.py | 工作台智能体 | 预置是工作流执行的一部分 |
| ControlPoint (on_task 等) | exec_engine.py | 工作台智能体 | 路由/协商/自旋是业务逻辑 |
| WorkflowExecutor | exec_engine.py (通过 execute_psop) | 工作台智能体 | SDK 集成层 |
| 事件流转换 | exec_engine.py _shape_event | 工作台智能体 | SDK 事件 -> A2A-T TaskUpdate |
| UI + REST API | 编排中心 | 保留 | 框架层职责 |
| PSOP CRUD API | 编排中心 | 保留 (纯数据服务) | 工作台调用编排中心 API 检索 |
| Agent Card 注册表 | registry-center | 保留 | 基础设施 |
| 用户认证 | 编排中心 | 保留 | 框架层职责 |
| SSE 事件转发 | exec_engine.py | 保留 (瘦身) | 只做 A2A-T 响应 -> 前端 SSE |

### 编排中心瘦身后的 events()

```python
class OrchestrationEngine:
    """编排中心执行入口 -- 只做意图接收 + A2A-T 下发 + 事件转发。"""

    async def events(self, intent: str) -> AsyncIterator[dict]:
        engine_client = self._get_engine_client()  # 只有 A2A-T client

        # 流式接收工作台的 A2A-T 响应，转发给前端
        async for response in engine_client.send_message_stream(
            "Transport Workbench Agent", intent
        ):
            # 从 TaskUpdate metadata 中提取 SDK 事件
            sdk_event = response.metadata.get("__sdk_event__")
            if sdk_event:
                yield json.loads(sdk_event)
```

---

## 4. 工作台智能体设计

### 对标 Java

Java demo 中：
- TransportWorkbenchAgentExecutor (A2A server) -> WorkbenchOrchestrator -> ExecutePsop
- WorkbenchControlPoint: on_task (城市特定消息) / on_self_task (LLM 汇总) / on_route / on_negotiation
- ExtensionPrePositioner: 工作流启动前预置 Authorization-T + Notification-T

### Python 工作台智能体

```python
# samples/agents/workbench_agent.py

class WorkbenchAgentExecutor(AgentExecutor):
    """工作台智能体 -- 工作流执行宿主，集成 workflow_engine SDK。

    Leader 角色：统筹工作流中其他智能体，也可处理 self-loop 节点。
    对标 Java TransportWorkbenchAgentExecutor + WorkbenchOrchestrator。
    """

    def __init__(self, orch_url, ssl_verify, a2at_env_path):
        self.orch_url = orch_url
        self.ssl_verify = ssl_verify
        self.a2at_env_path = a2at_env_path

    async def execute(self, context, event_queue):
        intent = context.get_user_input()
        logger.info(f"[Workbench] Received intent: {intent[:100]}")

        try:
            # 1. 意图处理 (LLM 理解/改写，可选)
            processed_intent = await self._process_intent(intent)

            # 2. PSOP 检索 (调编排中心 API)
            psop_id = await self._search_psop(processed_intent)
            workflow = await self._load_psop(psop_id)

            # 3. 加载 agent cards (调注册中心)
            agent_cards = await self._load_agent_cards()

            # 4. 创建 SDK transport + engine_client
            transport = A2ATransport(
                agent_cards=agent_cards,
                a2at_env_path=self.a2at_env_path,
                credentials_config=self.cred_path,
                ssl_verify=self.ssl_verify,
            )
            engine_client = WorkflowEngineClient(transport)

            # 5. 预置扩展
            await self._pre_position_extensions(transport, agent_cards)

            # 6. 创建 ControlPoint
            cp = WorkbenchControlPoint(
                orch_url=self.orch_url,
                a2at_env_path=self.a2at_env_path,
                ssl_verify=self.ssl_verify,
            )

            # 7. 执行工作流，流式返回事件
            async for event in execute_psop(
                psop=workflow.model_dump(),
                agent_cards=agent_cards,
                control_point=cp,
                engine_client=engine_client,
                runtime_intent=processed_intent,
                ssl_verify=self.ssl_verify,
            ):
                # SDK 事件 -> A2A-T TaskUpdate，推到 event_queue
                task_update = self._event_to_task_update(event, context)
                await event_queue.enqueue_event(task_update)

        except Exception as e:
            logger.error(f"[Workbench] Failed: {e}", exc_info=True)
            await event_queue.enqueue_event(self._error_task(context, str(e)))

    async def _process_intent(self, intent: str) -> str:
        """意图处理：LLM 理解/改写。可按业务域定制。"""
        return intent

    async def _search_psop(self, intent: str) -> str:
        """PSOP 检索：调编排中心 API。"""
        results = await search_psop(self.orch_url, intent, top_n=3, ssl_verify=self.ssl_verify)
        if results:
            logger.info(f"[Workbench] Found PSOP: {results[0].workflow_id}")
            return results[0].workflow_id
        raise RuntimeError("No matching workflow found")

    async def _load_psop(self, psop_id: str) -> Workflow:
        """加载 PSOP：调编排中心 API。"""
        return await load_psop(self.orch_url, psop_id, ssl_verify=self.ssl_verify)

    async def _load_agent_cards(self) -> list:
        """加载 agent cards：调注册中心。"""
        registry = RegistryClient(self.registry_url, ssl_verify=self.ssl_verify)
        return await registry.fetch_agent_cards()

    async def _pre_position_extensions(self, transport, agent_cards):
        """预置 Authorization-T + Notification-T。"""
        sender = ExtensionSender(transport)
        for card in agent_cards:
            name = getattr(card, "name", "")
            if not name or "Workbench" in name:
                continue
            try:
                await sender.send_authorization(name, "下发授权放行策略", self._AUTH_INPUT)
            except Exception as e:
                logger.warning(f"[PrePosition] Auth-T to {name} failed: {e}")
            try:
                await asyncio.wait_for(
                    sender.send_notification(name, "订阅业务抢通结果通知", self._NOTIF_INPUT),
                    timeout=10.0,
                )
            except asyncio.TimeoutError:
                logger.info(f"[PrePosition] Notif-T to {name}: subscribed")
            except Exception as e:
                logger.warning(f"[PrePosition] Notif-T to {name} failed: {e}")

    def _event_to_task_update(self, event: dict, context) -> TaskStatusUpdateEvent:
        """SDK 事件 -> A2A-T TaskUpdate，携带原始事件数据在 metadata 中。"""
        etype = event.get("type", "")
        data = event.get("data", {})
        is_final = etype in ("complete", "error", "close")
        state = (TaskState.TASK_STATE_COMPLETED if etype == "complete"
                 else TaskState.TASK_STATE_FAILED if etype == "error"
                 else TaskState.TASK_STATE_WORKING)
        summary = self._event_summary(etype, data)
        metadata = {"__sdk_event__": json.dumps(event, ensure_ascii=False)}
        return TaskStatusUpdateEvent(
            taskId=context.task_id,
            contextId=context.context_id,
            status=TaskStatus(state=state, message=Message(parts=[Part(text=summary)])),
            metadata=metadata,
            isFinal=is_final,
        )

    @staticmethod
    def _event_summary(etype: str, data: dict) -> str:
        if etype == "step_start": return f"步骤开始: {data.get('step', '')}"
        if etype == "agent_request": return f"-> {data.get('agent', '')}"
        if etype == "agent_response": return f"<- {data.get('agent', '')}"
        if etype == "route_decision": return f"路由: {data.get('step','')} -> {data.get('next','')}"
        if etype == "step_complete": return f"步骤完成: {data.get('step', '')}"
        if etype == "complete": return "工作流执行完成"
        if etype == "error": return f"错误: {data.get('error', '')}"
        return etype
```

### WorkbenchControlPoint

从 exec_engine.py 的 _EngineControlPoint 迁移：

```python
class WorkbenchControlPoint(ControlPoint):
    """工作台 ControlPoint -- 路由/协商/自旋策略，按业务域定制。"""

    def __init__(self, orch_url, a2at_env_path, ssl_verify):
        self.orch_url = orch_url
        self.a2at_env_path = a2at_env_path
        self.ssl_verify = ssl_verify
        self.llm = get_llm_instance()

    async def on_task(self, request, engine_client):
        # 城市特定消息构建 + 发送
        result = await engine_client.send_message(request.agent_name, request.message)
        return TaskResponse(success=bool(result.text), output=result.text)

    async def on_self_task(self, request):
        # LLM 汇总分析 (当前 _llm_route_decision / analyzeFaultLocation 逻辑)
        result = await self._llm_merge(request.message)
        return TaskResponse(success=True, output=result)

    async def on_route(self, step_name, results, conditions):
        # LLM 路由决策 (当前 _llm_route_decision 逻辑)
        next_step = await self._llm_route(step_name, results, conditions)
        return RouteDecision(next_step=next_step)

    async def on_negotiation(self, agent_name, negotiation_text, receive_result):
        # 协商澄清 (当前 _negotiation_resolver 逻辑)
        return await self._resolve_negotiation(agent_name, negotiation_text, receive_result)
```

---

## 5. 事件流转换与可观测性

### 事件流路径

```
execute_psop 产出事件                A2A-T SSE                     前端
     |                                   |                          |
     v                                   v                          v
 {type: "step_start",                TaskStatusUpdate            {type: "step_start",
  data: {step: "diagnosis_city1"}}  -->  (WORKING,             -->  data: {step: ...}}
                                         metadata={__sdk_event__: ...})

 {type: "agent_request",             TaskArtifactUpdate          {type: "agent_request",
  data: {agent, request, metadata}} -->  (text="-> SPN City1",  -->  data: {agent, ...}}
                                         metadata={__sdk_event__: ...})

 {type: "complete",                  TaskStatusUpdate            {type: "complete",
  data: {history, step_outputs}}    -->  (COMPLETED,            -->  data: {history, ...}}
                                         metadata={__sdk_event__: ...})
```

### 编码规则

工作台把每个 SDK 事件编码为 TaskUpdate：

| SDK 事件 | A2A-T TaskUpdate 状态 | 人可读摘要 |
|----------|---------------------|-----------|
| step_start | WORKING | 步骤开始: {step} |
| agent_request | (artifact) | -> {agent} |
| agent_response | (artifact) | <- {agent} |
| task_status_changed | WORKING | 状态: {status} |
| route_decision | WORKING | 路由: {step} -> {next} |
| step_complete | (artifact) | 步骤完成: {step} |
| negotiation_request | WORKING | 协商请求: {agent} |
| negotiation_resolved | WORKING | 协商解决: {agent} |
| negotiation_failed | WORKING | 协商失败: {agent} |
| authorization_request | WORKING | 授权请求: {agent} |
| notification | WORKING | 通知: {agent} |
| complete | COMPLETED | 工作流执行完成 |
| error | FAILED | 错误: {error} |
| close | (final) | (流结束) |

原始 SDK 事件完整保留在 TaskUpdate 的 metadata["__sdk_event__"] 中，编排中心原样转发给前端。

### 编排中心解码

```python
# 编排中心 exec_engine.py -- 瘦身后
async def events(self, intent: str) -> AsyncIterator[dict]:
    engine_client = self._get_engine_client()

    async for response in engine_client.send_message_stream(
        "Transport Workbench Agent", intent
    ):
        meta = response.metadata or {}
        sdk_event_json = meta.get("__sdk_event__")
        if sdk_event_json:
            yield json.loads(sdk_event_json)
```

---

## 6. 前端执行过程可观测重构

### 当前问题

| 问题 | 现状 | 目标 |
|------|------|------|
| 无步骤分组 | 所有事件平铺在时间线 | 按 step 分组，步骤内嵌套智能体交互 |
| 协议不可见 | 只显示文本摘要 | 完整展示 Task-T prompt、Extensions header、metadata |
| 无阶段状态 | 只有事件级状态 | 步骤级状态（等待/执行中/完成/失败）+ 耗时 |
| 无关联关系 | request/response 分散 | request->response 配对，协商轮次内嵌 |
| 无自旋展示 | self-loop 和普通 task 一样 | 明确标识 self-loop 本地处理 |
| 无最终结果 | complete 事件只显示 history 文本 | 结构化展示 step_outputs + history |

### 重构后布局

```
+-------------------------------------------------------------------------+
|  执行过程面板                                                            |
|                                                                         |
|  +-- 工作流头部 ------------------------------------------------------+ |
|  |  SPN专线故障诊断与抢通  *执行中  00:23  步骤 3/5                  | |
|  +----------------------------------------------------------------+ |
|                                                                         |
|  *-- 步骤1: diagnosis_city1  [完成]  3s                                 |
|  |   +-- SPN Domain Agent City1 -------------------------------+    |
|  |   |  -> REQUEST                    10:46:58                     |    |
|  |   |  Headers: A2A-Extensions: Task-T/v1                      |    |
|  |   |  Body: ## 任务
SPN专线故障诊断 - 粤东OMC侧...            |    |
|  |   |  Metadata: Task-T/v1: ## 任务类型(Task Type)
...        |    |
|  |   |                                                            |    |
|  |   |  <- RESPONSE                   10:47:02  [COMPLETED]      |    |
|  |   |  Text: 诊断结果：粵东城市OMC诊断结果 - 端口Down...        |    |
|  |   |  Metadata: Task-T/v1: 诊断结果：粵东城市OMC...            |    |
|  |   |                                                            |    |
|  |   |  [协商] Round 1                                             |    |
|  |   |     -> 粵东OMC诊断需要确认...                               |    |
|  |   |     <- 请补充粵东侧端口详细信息...                           |    |
|  |   +------------------------------------------------------------+    |
|  |   输出: 诊断结果+恢复方案 (467 chars)                                |
|  |                                                                      |
|  *-- 步骤2: diagnosis_city2  [完成]  2s                                 |
|  |   +-- SPN Domain Agent City2 -------------------------------+    |
|  |   |  ...                                                     |    |
|  |   +------------------------------------------------------------+    |
|  |                                                                      |
|  *-- 步骤3: merge_analysis  [执行中]                                    |
|  |   +-- Self-Loop (工作台本地处理) -----------------------------+    |
|  |   |  -> 汇总两地市OMC诊断结论                                  |    |
|  |   |  正在生成汇总结论...                                       |    |
|  |   +------------------------------------------------------------+    |
|  |                                                                      |
|  o-- 步骤4: recovery  [等待]                                           |
|  o-- 步骤5: endNode  [等待]                                            |
|                                                                         |
|  +-- 最终结果 -----------------------------------------------------+   |
|  |  [完成] 工作流执行完成  总耗时 28s                               |   |
|  |  Step Outputs:                                                   |   |
|  |    diagnosis_city1: 诊断结果+恢复方案...                          |   |
|  |    diagnosis_city2: 诊断结果:正常...                              |   |
|  |    merge_analysis: 故障定位:粵东城市OMC...                         |   |
|  +----------------------------------------------------------------+   |
+-------------------------------------------------------------------------+
```

### 组件结构

```
ExecutionTimeline (主容器)
  |-- WorkflowHeader          (工作流名称、整体状态、进度条、总耗时)
  |-- StepPhase[]             (按步骤分组)
  |    |-- StepHeader         (步骤名、状态图标、耗时、step_type标签)
  |    |-- AgentInteraction[] (步骤内的智能体交互)
  |    |    |-- AgentBadge    (智能体名、头像)
  |    |    |-- ProtocolCard  (A2A-T 请求：headers + body + metadata)
  |    |    |-- ProtocolCard  (A2A-T 响应：text + state + metadata)
  |    |    +-- NegotiationRound[] (协商轮次，内嵌在交互内)
  |    |-- SelfLoopCard       (自旋节点：本地处理、LLM结果)
  |    |-- RouteDecisionCard  (路由决策：step -> next_step + reason)
  |    +-- StepOutput         (步骤输出摘要)
  +-- FinalResultCard         (最终结果：history + step_outputs)
```

### 事件分组逻辑

```javascript
function groupEventsByStep(events) {
    const steps = new Map();
    let currentStep = null;

    for (const event of events) {
        const stepName = event.data?.step;
        if (event.type === 'step_start') {
            currentStep = stepName;
            steps.set(stepName, {
                name: stepName, status: 'running', startTime: event.timestamp,
                interactions: [], route: null, output: null,
            });
        }
        if (!currentStep || !steps.has(currentStep)) continue;
        const step = steps.get(currentStep);

        switch (event.type) {
            case 'agent_request':
                step.interactions.push({ agent: event.data.agent, request: event.data, timestamp: event.timestamp });
                break;
            case 'agent_response':
                const last = step.interactions.findLast(i => i.agent === event.data.agent && !i.response);
                if (last) last.response = event.data;
                break;
            case 'negotiation_request':
            case 'negotiation_resolved':
            case 'negotiation_failed':
                const interaction = step.interactions.findLast(i => i.agent === event.data.agent);
                if (interaction) {
                    interaction.negotiations = interaction.negotiations || [];
                    interaction.negotiations.push(event);
                }
                break;
            case 'route_decision':
                step.route = event.data;
                break;
            case 'step_complete':
                step.status = 'completed'; step.endTime = event.timestamp;
                break;
            case 'error':
                step.status = 'failed'; step.endTime = event.timestamp; step.error = event.data;
                break;
        }
    }
    return Array.from(steps.values());
}
```

### 步骤状态可视化

```javascript
const StepStatus = {
    PENDING:   { icon: Clock,        color: 'text-zinc-400',    dot: 'bg-zinc-300' },
    RUNNING:   { icon: Loader,       color: 'text-blue-500',    dot: 'bg-blue-500 animate-pulse' },
    COMPLETED: { icon: CheckCircle2, color: 'text-emerald-500', dot: 'bg-emerald-500' },
    FAILED:    { icon: XCircle,      color: 'text-rose-500',    dot: 'bg-rose-500' },
};
```

### ProtocolCard 组件

完整展示 A2A-T 协议消息，对标 Java SDK 的 ProtocolLogger 输出格式：

- 方向标识 (-> REQUEST / <- RESPONSE)
- 时间戳
- Headers (A2A-Extensions、Authorization 等)
- Body (Markdown 渲染)
- Metadata (可折叠，每个 extension URI 的值用 Markdown 渲染)

---

## 7. 落地步骤

### Phase 1: 工作台智能体 (后端)

| 步骤 | 做什么 | 文件 |
|------|--------|------|
| 1.1 | 创建 WorkbenchAgentExecutor | samples/agents/workbench_agent.py |
| 1.2 | WorkbenchControlPoint (从 exec_engine 迁移路由/协商/自旋) | 同上 |
| 1.3 | 意图处理 + PSOP 检索 + agent cards 加载 | 同上 |
| 1.4 | SDK 事件 -> A2A-T TaskUpdate 转换 | 同上 |
| 1.5 | 注册工作台 Agent Card | samples/agentcard/transport_workbench_agent.json |
| 1.6 | start_agents_server.py 注册工作台 | samples/start_agents_server.py |

### Phase 2: 编排中心瘦身 (后端)

| 步骤 | 做什么 | 文件 |
|------|--------|------|
| 2.1 | exec_engine.py events() 瘦身为 A2A-T 下发 + 事件转发 | orchestrate/runtime/exec_engine.py |
| 2.2 | consume_stream 解码 __sdk_event__ | 同上 |
| 2.3 | 移除 DynamicWorkflowEngine / _EngineControlPoint (已迁移到工作台) | 同上 |

### Phase 3: 前端可观测重构 (前端)

| 步骤 | 做什么 | 文件 |
|------|--------|------|
| 3.1 | 新建 ExecutionTimeline 组件 | execution_center/timeline/index.jsx |
| 3.2 | groupEventsByStep 事件分组逻辑 | 同上 |
| 3.3 | ProtocolCard 协议展示 (headers + body + metadata) | 同上 |
| 3.4 | SelfLoopCard 自旋节点展示 | 同上 |
| 3.5 | FinalResultCard 最终结果展示 | 同上 |
| 3.6 | StepPhase 步骤容器 (状态、耗时、内嵌交互) | 同上 |
| 3.7 | 修改 execution_center/index.jsx 右侧面板用新组件 | execution_center/index.jsx |

### Phase 4: 端到端验证

| 步骤 | 做什么 |
|------|--------|
| 4.1 | 启动 agents server (含工作台 + SPN agents) |
| 4.2 | 启动编排中心 |
| 4.3 | 前端触发工作流 -> 编排中心 -> A2A-T -> 工作台 -> SPN agents |
| 4.4 | 验证事件回传 -> 前端实时展示 |
| 4.5 | 验证步骤分组、协议展示、状态更新、最终结果 |

### 并行策略

- Phase 1 和 Phase 3 可以并行（前端先用 mock 事件开发，后端通了再对接）
- Phase 2 依赖 Phase 1 完成
- Phase 4 依赖 Phase 1 + 2 + 3 完成

---

## 8. 扩展性体现

新增一个工作流（比如"5G基站节能优化"），只需要：

| 步骤 | 改什么 | 编排中心改吗 |
|------|--------|-------------|
| 定义 PSOP 工作流 | 编排中心前端创建 | 不改代码 |
| 部署节能智能体 | 注册 Agent Card + 启动 agent | 不改代码 |
| 工作台 ControlPoint | 如果路由策略不同，改工作台 | 不改代码 |
| 触发执行 | 前端选择 PSOP -> 下发 | 不改代码 |

编排中心是一套稳定框架，不同业务域只需要换工作台智能体（或调整其 ControlPoint 策略）+ 部署对应的 worker 智能体。

---

## 9. 部署模式

### 同进程模式 (开发/演示)

工作台智能体和编排中心跑在同一个进程里（通过 localhost A2A-T 调用）。工作台注册在 start_agents_server.py 里，与 SPN agents 一起启动。

### 独立进程模式 (生产)

工作台智能体单独部署（可以 Java 也可以 Python），编排中心通过网络调用。编排中心完全不用动。

---

## 10. 相关文件清单

### 新建

| 文件 | 说明 |
|------|------|
| samples/agents/workbench_agent.py | 工作台智能体 executor + ControlPoint |
| samples/agentcard/transport_workbench_agent.json | 工作台 Agent Card |
| execution_center/timeline/index.jsx | 执行过程可观测组件 |

### 修改

| 文件 | 说明 |
|------|------|
| samples/start_agents_server.py | 注册工作台智能体 |
| orchestrate/runtime/exec_engine.py | 瘦身为薄通道 |
| execution_center/index.jsx | 右侧面板替换为 ExecutionTimeline |

### 参考 (Java 对标)

| Java 文件 | 说明 |
|-----------|------|
| TransportWorkbenchAgentExecutor.java | A2A server 接收任务 |
| WorkbenchOrchestrator.java | 加载 cards/PSOP/预置/执行 |
| WorkbenchControlPoint.java | on_task/on_self_task/on_route/on_negotiation |
| ExtensionPrePositioner.java | 预置 Authorization-T + Notification-T |
| NegotiationStrategy.java | LLM 协商策略 |
