# Comparative Analysis: orchestration-center vs cat26-orchestration-center

**OPENAN-VERSION** (this repo): `/home/lorenzo/git/openan/orchestration-center`

**CATALYST-VERSION**: `/home/lorenzo/git/openan-catalyst/cat26-orchestration-center`

Both repos share the same origin (identical top-level layout: `orchestrate/`, `common/`, `samples/`, `database/`, `etc/`, `test/`) but have diverged substantially since forking. This is **not** a thin fork with a few tweaks — CATALYST-VERSION rewrote the core execution engine (1002 → 2535 lines), swapped the TASK-T/negotiation SDK for a different package entirely, dropped the frontend, and added platform-integration features (BPMN import, agent fabric, secure passport signing) that only make sense inside a larger deployment ("catalyst26") this repo doesn't participate in.

---

## 1. What each repo actually is

**OPENAN-VERSION** is a self-contained product: FastAPI backend + React workflow designer, deployable as a single container, with two API layers (`/rest/v1/orchestrate/*` internal, `/api/v1/*` external), file-based or Postgres persistence, and an explicit CLAUDE.md/AGENTS.md describing every subsystem accurately.

**CATALYST-VERSION** is a backend-only component of a larger platform called **"catalyst26"**: its `docker-compose.yaml` (`name: catalyst26`) wires it to external services by hostname (`relational-db`, `OLLAMA_SERVICE_HOST`, `AGENT_REGISTRY_URL`, `AGENT_FABRIC`), it ships a *second* compose file (`docker-compose-agents.yaml`) that deploys the sample-agent fleet independently and at a different version tag (`2.0.0` vs `1.0.0`), and several features (Agent Fabric external registry, MEFDEV Secure Passport signing, a runtime-toggleable A2A-T flag surfaced over its own REST endpoint) only make sense as integration points into that larger platform. Its own documentation acknowledges this only partially: `PROJECT_BRIEF.md` — the file `AGENTS.md` tells contributors to read for "the full architectural design" — is the **entirely unfilled template**, and `AGENTS.md` also points to a `CODING_TASKS.md` that does not exist anywhere in the repo.

---

## 2. Structural diff (top level)

```
OPENAN-VERSION-only:  deploy-all.ps1, docker-compose.yml, docker-entrypoint.sh, docs/, README_zh.md,
         scripts/, tests/ (integration), workflow-designer/, .github/workflows/ci.yml
CATALYST-VERSION-only:  config/, docker-compose-agents.yaml, docker-compose.yaml, Dockerfile_agents,
         PROJECT_BRIEF.md, shared/, .gitlab/issue_templates/
```

Inside `orchestrate/` and `common/`:

```
orchestrate/a2at/            CATALYST-VERSION-only — extension_parser.py, message_builder.py, settings.py,
                              template_renderer.py, agent_task_type.py
orchestrate/messages/        CATALYST-VERSION-only — message_events.py, message_persistence.py
orchestrate/core/model/execution_record.py   OPENAN-VERSION-only
orchestrate/server/external_api.py           OPENAN-VERSION-only (no /api/v1/* in B at all)
orchestrate/server/response_utils.py         OPENAN-VERSION-only
orchestrate/server/shared_handlers.py        OPENAN-VERSION-only
orchestrate/server/sse_executor.py           OPENAN-VERSION-only
orchestrate/agentcard_loader.py              OPENAN-VERSION-only (B has only samples/agentcard_loader.py, unrelated)
orchestrate/solution_package/parse_bpmn.py   CATALYST-VERSION-only
orchestrate/registry_client/client_external_factory.py   CATALYST-VERSION-only
common/auth/                  OPENAN-VERSION-only — agent_credential_service.py, extension_interceptor.py
common/a2at_config.py         OPENAN-VERSION-only
common/negotiation_utils.py   OPENAN-VERSION-only
common/custom/execution_record_processor.py   OPENAN-VERSION-only
common/custom/message_processor.py            CATALYST-VERSION-only
```

No CI exists in CATALYST-VERSION (only GitLab issue templates); OPENAN-VERSION runs `pytest test/ -v` + `npm run lint` in `.github/workflows/ci.yml`.

---

## 3. Feature inventory — implemented only on one side

### Only in OPENAN-VERSION

| Capability | Where | Consequence if absent (as it is, in CATALYST-VERSION) |
|---|---|---|
| React workflow designer (drag-and-drop, PDF import UI) | `workflow-designer/` | no visual UI at all — confirmed no `workflow-designer/` directory anywhere, despite CATALYST-VERSION's own README describing one at `localhost:3003` |
| External public API (`/api/v1/*`) | `orchestrate/server/external_api.py` | no equivalent surface; nothing external can drive workflows except the internal SSE route |
| Per-agent credential/login auth | `common/auth/agent_credential_service.py`, `extension_interceptor.py`, `etc/conf/agent_credentials.json` | **no per-agent authentication system at all** — confirmed via exhaustive grep (no `CredentialService`, no `Bearer`, no per-agent token flow). CATALYST-VERSION's engine assumes every agent is directly callable with no login step |
| AgentCard security-scheme normalization | `orchestrate/agentcard_loader.py` (`_normalize_agent_dict`) | it parses raw AgentCard dicts with no normalization step |
| Bounded negotiation protocol (hard 3-round cap) | `common/negotiation_utils.py`, `_NEGOTIATION_MAX_ROUNDS = 3` | CATALYST-VERSION's negotiation has no round cap (see §4.4) — risk of unbounded recursion |
| Per-run execution audit trail (`ExecutionRecord`) | `orchestrate/core/model/execution_record.py`, `common/custom/execution_record_processor.py`, `/execution-records` endpoint | no run-level execution record; only a flat per-message log, and even that log's persistence path is unwired (see §4.5) |
| Parallel/DAG workflow execution, `ANY_SUCCESS` racing | `exec_engine.py` `run()` join/fan-out logic | it executes steps strictly linearly (see §4.1) |
| `/templates` + template import endpoint | `frontend_support_server.py` | No equivalent |
| Async LLM routing offloaded to a thread pool | `exec_engine.py:930-997`, `ThreadPoolExecutor(max_workers=4)` | routing call blocks the asyncio event loop (see §4.2) |

### Only in CATALYST-VERSION

| Capability | Where | Notes |
|---|---|---|
| BPMN 2.0 XML workflow import | `orchestrate/solution_package/parse_bpmn.py`, `/parse-bpmn` route | Parses `<process>`/lanes/sequence flows via `xml.etree.ElementTree`, converts to Markdown via LLM, feeds the same PreFlow→PSOP pipeline as PDF import. OPENAN-VERSION cannot import BPMN at all (zero references) |
| Dedicated `orchestrate/a2at/` package (structured TASK-T pipeline) | `orchestrate/a2at/*.py` | YAML-template-driven, per-field required/optional resolution (`template_renderer.py`, 786 lines) — see §4.3 |
| Runtime-toggleable global TASK-T flag | `orchestrate/a2at/settings.py`, `shared/a2at_settings.conf`, `/settings` GET/PUT | Global on/off switch, unrelated to per-agent AgentCard capability declarations |
| Second "Agent Fabric" external registry | `orchestrate/registry_client/client_external_factory.py`, `AGENT_FABRIC` env var | A genuinely separate registry service (not a rename of OPENAN-VERSION's "external API") — used for calls resolved via a `calling_agent` param |
| Per-A2A-message event log | `orchestrate/messages/message_events.py`, `message_persistence.py`, `/messages`, `/messages/all`, `/rest/messages/events` | Flat message-level log, not a run-level record; persistence handler is registered but never actually invoked (bug — see §4.5) |
| MEFDEV Secure Passport JWS re-auth retry | `exec_engine.py:107-444, 1938-1976` | Triggered only for one hardcoded agent/skill pair on a specific failure message; calls an external signing endpoint |
| TIO/ICM JSON-LD intent payload (`parts[].data`) | `exec_engine.py:1230-1376`, `_build_tio_message` | Third message-construction channel, hardcoded to one `(agent, skill)` pair |
| Declarative "No"-labeled error routing | `exec_engine.py:2205-2213` | On subtask failure, looks for a PSOP step with a `"No"` condition and reroutes there instead of hard-stopping the whole run (OPENAN-VERSION always hard-stops) |
| Async/streaming WORKING-state pass-through dispatch | `exec_engine.py`, `ASYNC_DISPATCH_AGENT_SKILLS` | Hardcoded agent/skill set routed via `message:stream` with unbounded read timeout and a keep-consuming-until-terminal loop |
| SSE push throttling (min 1s interval) | `exec_engine.py:821-830`, `_push_event` | Explicitly to give the frontend time to render — notable since B has no frontend |

---

## 4. Same function, different implementation

### 4.1 Execution engine — parallel DAG vs. linear pointer

This is the single largest behavioral divergence found. OPENAN-VERSION's `run()` (`exec_engine.py:241-287`) is a true async DAG engine: a deque of ready steps, deferred re-enqueue until all predecessors have produced output (real join semantics), `_determine_next_steps` returns a list so a step can fan out to multiple parallel branches, and subtasks within a step run concurrently (`asyncio.gather`, or `asyncio.as_completed` + cancellation for `StepType.ANY_SUCCESS`).

CATALYST-VERSION's `run()` (`exec_engine.py:842-851`) is `while self.current_step_idx < len(self.workflow.steps): await self._execute_single_step()` — a single linear pointer, no predecessor/join tracking, subtasks run in a strictly sequential loop that breaks on first failure, and LLM-based routing can only select **one** next step (or `"end"`) — there is no way to fan out.

**Concrete consequence**: `StepType.ANY_SUCCESS` still exists in the shared PSOP schema (`orchestrate/core/model/psop.py`), but CATALYST-VERSION's engine never imports `StepType` at all — a PSOP authored with parallel branches or `AnySuccess` racing, built in OPENAN-VERSION's designer, would silently execute as a sequential all-must-succeed chain if run against CATALYST-VERSION's engine.

A separate consequence: OPENAN-VERSION short-circuits the LLM entirely when all `next` conditions are empty (deterministic edges cost no LLM call, `exec_engine.py:292-300`); CATALYST-VERSION always calls `_llm_route_decision`, adding an LLM round-trip to every single step transition regardless of whether the routing is actually conditional.

### 4.2 LLM-based routing — async thread-pool offload vs. blocking sync call

OPENAN-VERSION's `_llm_route_decision` (`exec_engine.py:930-997`) is `async def`, offloading the blocking LLM call to a shared, class-level `ThreadPoolExecutor(max_workers=4)` via `run_in_executor` — the asyncio event loop stays free.

CATALYST-VERSION's `_llm_route_decision` (`exec_engine.py:2431-2529`) is a **plain synchronous method** called directly from async code with no thread offload — it blocks the entire event loop (i.e. every other concurrent request/workflow in the same process) for the duration of the LLM HTTP round-trip. Worse, its retry-backoff logic (`exec_engine.py:2505-2510`) calls `asyncio.get_event_loop().run_until_complete(asyncio.sleep(1))` from inside an already-running loop, which raises `RuntimeError`; this is caught by a bare `except` and falls back to synchronous `time.sleep(1)` — which also blocks the event loop, for a full second, on every retry (up to 3 retries per routing decision). Combined with §4.1's finding that CATALYST-VERSION calls this on *every* step transition, this is a real production-stability concern for concurrent workflow execution in CATALYST-VERSION.

On the positive side, CATALYST-VERSION's fallback logic is slightly smarter: on total LLM failure, if exactly one unconditional next step exists, it takes that deterministically rather than always terminating (OPENAN-VERSION always defaults to `"end"`).

### 4.3 TASK-T / A2A-T handling — different SDKs, incompatible extension URIs

OPENAN-VERSION depends on `a2a-t-sdk` (package `a2a_t`, `requirements.txt: a2a-t-sdk>=0.1.8`). TASK-T logic lives inline in `exec_engine.py` (`_get_task_t_uris`/`_extract_task_t_uri`, lines 310-320: scans `AgentCard.capabilities.extensions` for a URI containing the case-sensitive substring `'Task-T'`) and is per-agent opt-in — an agent whose AgentCard doesn't declare the matching extension gets no TASK-T handling. Prompt text is either a static stub (`etc/conf/task_prompt_stubs.json`) or generated by the SDK's own LLM call (`a2at_client.generate_task_prompt`).

CATALYST-VERSION depends on an **entirely different, unrelated package**, `a2a_telecom` — confirmed never listed in `requirements.txt` at all; it's supplied only via a Docker multi-stage `COPY --from=a2a_telecom_src` editable install from a sibling source tree, meaning a plain `pip install -r requirements.txt` does not produce a runnable CATALYST-VERSION. TASK-T logic is extracted into a dedicated `orchestrate/a2at/` package: `agent_task_type.py` classifies task type from agent name/skill via string matching, `template_renderer.py` (786 lines) maps task type → one of 7 YAML templates (sourced from an external sibling `A2A-T/templates/` checkout) and does deterministic required/optional field resolution — an LLM is invoked only to backfill fields the template marks required and for which no real context value exists. Crucially, the trigger is **not per-agent AgentCard capability** — it's a single global flag, `a2at_settings.enabled`, read from `shared/a2at_settings.conf` and toggleable live via `/settings`, applied uniformly to every agent call regardless of what that agent's AgentCard declares (confirmed: CATALYST-VERSION's own sample AgentCards declare no `extensions` block at all, yet still receive Task-T metadata).

**This will break cross-repo interop.**
- OPENAN-VERSION's sample AgentCard declares extension URI `https://projects.tmforum.org/a2aproject/telecommunication/extensions/Task-T/v1`;
- CATALYST-VERSION's SDK constant `TASK_T_NL_URI` is `https://github.com/a2a-t/extensions-telecom/task-t/nl/v1` — different host, different casing (`task-t` lowercase), different path scheme.

An AgentCard authored for one repo's extension-detection logic will not be recognized by the other's.

The one point of alignment: both repos place the resulting payload in **message-level** `message.metadata` keyed by the extension URI, not in `parts[].text` — so an agent that reads "whatever extension URI is present in `message.metadata`" generically, without hardcoding a specific URI string, could interoperate with either.

A third message-construction channel exists only in CATALYST-VERSION: the hardcoded TIO/ICM intent path (`_build_tio_message`, exec_engine.py:1230-1376) places a JSON-LD payload in `parts[].data` instead of metadata, for exactly one `(agent, skill)` pair.

### 4.4 Negotiation protocol — bounded state machine vs. unbounded recursion

OPENAN-VERSION's `_send_with_negotiation` (`exec_engine.py:332-384`) tracks an explicit round counter bounded at `_NEGOTIATION_MAX_ROUNDS = 3` (`common/negotiation_utils.py`); on `INPUT_REQUIRED`, if the round cap is exceeded it raises and streams a `negotiation_failed` SSE event. Three distinct SSE event types track negotiation state: `negotiation_request`, `negotiation_resolved`, `negotiation_failed`.

CATALYST-VERSION has no `common/negotiation_utils.py` and no round counter anywhere. On `INPUT_REQUIRED` (`exec_engine.py:1983-2125`), it either enriches missing fields from execution history via an LLM call, or **recursively calls `self.send_message_to_agent(agent_name=from_agent_name, ...)`** to ask the upstream agent for clarification, then loops. The only guards are structural: refusing self-routing and refusing to forward when there's no upstream agent. A genuine circular negotiation across three or more agents (A asks B, B asks C, C asks A) is not caught by either guard and could recurse indefinitely. Negotiation-related payloads are surfaced to the frontend as a single unified `negotiation_t` SSE event (info/feasibility subtypes), for observability only — parsing the structured payload has no effect on control flow, which simply falls through to the generic clarification-forwarding loop either way.

### 4.5 Execution/message tracking — per-run record vs. per-message log (and an unwired persistence bug)

OPENAN-VERSION's `ExecutionRecord` (`orchestrate/core/model/execution_record.py`) is a whole-run model: `execution_id`, `status` (running/success/failed/stopped), `started_at`/`completed_at`, `execution_history` (step-level), `final_psop`, `events` (for replay) — one row per workflow run, persisted via `common/custom/execution_record_processor.py` to a Postgres `execution_records` table.

CATALYST-VERSION's `orchestrate/messages/` package tracks individual A2A messages instead: `MessageStorage.emit_event()` builds an envelope per message (`message_id`, `from_agent`, `to_agent`, `message_content`, `psop_id`, `psop_run_id`) with no run-level status, step history, or final-state snapshot. These are answering different questions (OPENAN-VERSION: "what happened during run X, in replayable order"; CATALYST-VERSION: "what individual messages were exchanged") and are not interchangeable — there is no lossless conversion between the two schemas in either direction.

**Bug found while tracing this**:
- CATALYST-VERSION's `MessageStorage.__init__` and `exec_engine.py:79` both assign `self.save_handle = HandlerRegistry.get_handler(InterfaceType.SAVE_MESSAGE)`, but `self.save_handle.handle(...)` is never actually called anywhere in the codebase. `MessageStorage.emit_event()` only broadcasts over SSE (`_push_event`) — despite a code comment in `frontend_support_server.py` claiming `message_storage.emit_event()` saves the message, it does not. The `messages` Postgres table and the fully-implemented `custom_save_message()` handler both exist and are registered, but nothing calls them — A2A messages in CATALYST-VERSION are visible live in the UI/SSE stream but are **not durably persisted**.

### 4.6 Registry client — incompatible REST contracts, plus a second registry

OPENAN-VERSION's `AgentRegistryClient` is async (`httpx.AsyncClient`), hitting `/rest/v1/registry-center/agent-cards*`. CATALYST-VERSION's `client.py` was rewritten to synchronous (`requests.Session`) hitting a completely different path scheme, `/rest/a2a-t/v1/agents/*` (e.g. `register` is `POST /rest/v1/registry-center/agent-cards` in OPENAN-VERSION vs `POST /rest/a2a-t/v1/agents/register` in CATALYST-VERSION). The two clients cannot talk to each other's registry backend, and CATALYST-VERSION additionally serializes AgentCard payloads with `preserving_proto_field_name=True` (snake_case) vs OPENAN-VERSION's default camelCase — a further wire-format divergence.

CATALYST-VERSION also adds a wholly separate second registry, "Agent Fabric" (`client_external_factory.py`, env var `AGENT_FABRIC`, default port 8090) — used when a workflow is invoked *by* another agent (`calling_agent` query param on `/rest/start_process_stream`) to resolve the agent roster externally instead of from the internal registry. This is unrelated to OPENAN-VERSION's "external API" (`/api/v1/*`, a public REST surface, not a second registry) — the two repos use the word "external" for different concepts. OPENAN-VERSION has no dual-registry concept at all.

### 4.7 LLM provider abstraction — same capabilities, different code organization

Both repos support chat, embeddings, and reranking, and both implement the same Huawei-internal "AOC" HMAC-signed-header auth scheme.
- OPENAN-VERSION does it with one generic, config-driven class (`GenericLLM` in `common/llm/provider/generic_llm.py`) whose behavior (endpoint, request template, response-extraction JSONPath, auth strategy) is entirely determined by which config block (`chat`/`embed`/`rerank`) is loaded from `common/config/llm_config.json`.
- CATALYST-VERSION decomposes the same capabilities into an explicit `LLMType`-keyed registry with one class per provider (`OpenAIStyleLLM`, `AOCChatLLM`, `AOCEmbeddingLLM`, `AOCRerankerLLM`), and its `psop_generator.py` additionally threads an `LLM_TYPE` env var through to pick a provider at call time — a capability OPENAN-VERSION's callers don't have (they always get the one default instance).

Neither repo actually uses embeddings/reranking for retrieval — both `retrieval.py` implementations do pure LLM-prompt-based PSOP-candidate selection over a list, not vector search.
- OPENAN-VERSION's embed/rerank capability is more accessible to application code (dedicated `get_embed_instance()`/`get_rerank_instance()` accessors);
- CATALYST-VERSION has no equivalent convenience accessor for its embedding/reranker classes.

**Flag**:
- CATALYST-VERSION's `config/llm_config.json` is git-tracked (confirmed not gitignored) and appears to contain real, non-placeholder secrets — a DeepSeek API key (`sk-f80c71a4058c4ea9a6df03e69804ecec`), and AOC `app_key`/`app_secret`/bearer tokens.
- OPENAN-VERSION's equivalent file uses only placeholders (`<YOUR_API_KEY>`, etc.). This is worth rotating/scrubbing independent of the architecture comparison.

### 4.8 Server/API layer — route prefix, external API, and rate limiting

- OPENAN-VERSION mounts an `APIRouter(prefix="/rest/v1/orchestrate")` for the internal API and separately mounts `external_api.py`'s `/api/v1/*` router.
- CATALYST-VERSION has **no route prefix at all** — every route lives at the root (`/settings`, `/parse-pdf`, `/parse-bpmn`, `/psops`, `/rest/start_process_stream`, etc.) — and has no external API surface whatsoever.

- OPENAN-VERSION centralizes response formatting (`response_utils.py`), shared retrieval/save handlers (`shared_handlers.py`), and SSE streaming (`sse_executor.py`, shared by both API layers, using `asyncio.Queue` in the same event loop as the request).
- CATALYST-VERSION inlines all of this directly into `frontend_support_server.py`: per-route Pydantic response models instead of a shared envelope helper, module-scope singleton calls instead of a shared handler registry wrapper, and — most consequentially — its SSE workflow-execution logic runs the workflow in a **separate OS thread with its own new event loop**, handed off via a plain `queue.Queue`, rather than an `asyncio.Task` in the request's own loop.

**Confirmed gap, not speculative**: CATALYST-VERSION's `/rest/start_process_stream` — its most expensive, longest-running endpoint — has **no rate limiter and no concurrency semaphore**, unlike every other route in the same file (which all have `Depends(RateLimiter(...))` or a semaphore) and unlike OPENAN-VERSION's equivalent `/execute` route, which does apply `RateLimiter`. The rate-limit key still exists in `server.properties`/`middleware.py`'s config map (under a typo'd helper name), it's simply never wired to the route. Separately, CATALYST-VERSION's `TimeoutMiddleware` removed OPENAN-VERSION's explicit SSE bypass (OPENAN-VERSION skips the global timeout wrapper for `/execute`-suffixed paths), so CATALYST-VERSION's long-running SSE endpoint is subject to a 300s global ceiling that OPENAN-VERSION's isn't.

### 4.9 HTTP client — connection reuse, timeouts, TLS

OPENAN-VERSION creates one `httpx.AsyncClient` per engine run, reused for the entire workflow, with a uniform 60s connect/read/write timeout and explicit `verify=False` (intentional, documented in CLAUDE.md, for self-signed-cert dev/internal agents).

CATALYST-VERSION creates a **new** `httpx.AsyncClient` per call, in three different code paths with three different timeout configurations, and none of them pass `verify=`, so they default to `verify=True` (standard CA verification) — the opposite default from OPENAN-VERSION. Read timeout in both of CATALYST-VERSION's configured paths is `None` (unbounded), meaning an agent call in CATALYST-VERSION can hang indefinitely, where OPENAN-VERSION bounds every call at 60s. Both repos agree on having no retry logic (only catching `httpx.TimeoutException`/`httpx.ConnectError`, re-raised as `RuntimeError`).

### 4.10 Solution package parsing — PDF path is compatible, BPMN is CATALYST-VERSION-only

`manager.py` is a JSON-cache layer in both repos (not a dispatcher, contrary to how it might look) — cosmetic diff only. `parse_flow.py` (PDF-chapter → Markdown) differs only in prompt wording and whitespace between the two repos; a PreFlow built by one repo's PDF parser has the same `steps_md` shape in the other. `parse_bpmn.py` (CATALYST-VERSION only, 469 lines) parses BPMN 2.0 XML via `xml.etree.ElementTree`, extracts lanes/nodes/sequence flows, and feeds the same PreFlow→PSOP generation pipeline PDF import uses. OPENAN-VERSION cannot ingest BPMN at all. Note this only covers PDF/BPMN *document* import — not the separate concern of designer-exported workflow JSON compatibility, which wasn't in scope here since CATALYST-VERSION has no designer.

### 4.11 Auth interceptor wiring — partially unapplied in B even accounting for its absent auth module

Beyond §3's "CATALYST-VERSION has no `common/auth/`" finding: even for the one thing CATALYST-VERSION does send (`A2A-Extensions` header, `x-agent-id`, `x-psop-id` etc., via a manually-assembled `_build_a2a_headers`), it's only invoked on the two Agent-Fabric proxy routing paths (`calling_agent_key` set and the calling/target agents differ) — not on the direct-SDK path taken by the orchestrator's own top-level calls, which is the common case. OPENAN-VERSION's `ExtensionInterceptor` applies uniformly to every call to any extension-declaring agent regardless of routing path. This looks like an unintentional regression in CATALYST-VERSION rather than a deliberate design choice.

---

## 5. Additional risk/quality flags found incidentally

These surfaced while tracing functional differences and are worth flagging on their own, independent of the architecture comparison:

1. **Real credentials appear to be git-tracked** in CATALYST-VERSION's `config/llm_config.json` (DeepSeek API key, AOC app_key/app_secret/bearer tokens) — not gitignored, not placeholders. (§4.7)
2. **Message persistence is silently a no-op** in CATALYST-VERSION despite a fully-built handler and a misleading code comment claiming it saves. (§4.5)
3. **LLM routing blocks the asyncio event loop** on every step transition in CATALYST-VERSION, with a broken retry-backoff that raises and falls back to a second blocking `time.sleep`. (§4.2)
4. **Negotiation has no bound** in CATALYST-VERSION — risk of unbounded recursion across circular multi-agent negotiation chains. (§4.4)
5. **`/rest/start_process_stream` has no rate limiting or concurrency cap** in CATALYST-VERSION, unlike every other route in the same file. (§4.8)
6. **A hardcoded default Basic-auth credential** is embedded in CATALYST-VERSION's source: `MEFDEV_IDENTITY_SIGN_AUTH` defaults to `"Basic YXBpX3VzZXI6V2lkZWNvdXAx"` (`exec_engine.py:255-258`) if the env var isn't set.
7. **`requirements.txt` is insufficient to run CATALYST-VERSION** — its negotiation SDK (`a2a_telecom`) is never listed there; it's only obtainable via a Docker multi-stage copy from a sibling source tree, and there's no written setup instruction for it anywhere (`PROJECT_BRIEF.md` is empty, `CLAUDE.md` just points to `AGENTS.md`, `AGENTS.md` doesn't mention it).
8. **`Dockerfile_agents` is orphaned and would be broken if built** — unreferenced by either compose file, and lacks the `a2a_telecom` editable install that every sample agent needs at import time.
9. **`sse_starlette` is an unused dependency** in CATALYST-VERSION's `requirements.txt` — never imported anywhere.

---

## 6. Summary table

| Dimension | OPENAN-VERSION | CATALYST-VERSION |
|---|---|---|
| Frontend | React workflow designer | None |
| Execution model | Async DAG, parallel fan-out, `ANY_SUCCESS` racing | Linear single-pointer, sequential subtasks |
| LLM routing | Async, thread-pool offloaded | Sync, blocks event loop |
| TASK-T/A2A-T SDK | `a2a-t-sdk` (`a2a_t`), per-agent opt-in via AgentCard extension | `a2a_telecom` (uninstallable via `requirements.txt` alone), global on/off flag |
| Negotiation | Bounded, 3-round cap, fails fast | Unbounded recursive retry |
| Per-agent auth | `common/auth/` credential/login service | None |
| Execution tracking | Per-run `ExecutionRecord`, replayable | Per-message log; persistence handler unwired (no-op) |
| Registry client | Async, `/rest/v1/registry-center/*` | Sync, `/rest/a2a-t/v1/agents/*`, plus a second "Agent Fabric" registry |
| BPMN import | No | Yes |
| External public API | `/api/v1/*` | None |
| HTTP client TLS default | `verify=False` (documented, intentional) | `verify=True` (undocumented default) |
| HTTP read timeout | 60s bounded | Unbounded (`None`) |
| Rate limiting on execute endpoint | Yes | No |
| CI | GitHub Actions (pytest + lint) | None |

---

## 7. Sources

**OPENAN-VERSION** (`/home/lorenzo/git/openan/orchestration-center`): `orchestrate/runtime/exec_engine.py`, `orchestrate/server/{frontend_support_server,external_api,middleware,response_utils,shared_handlers,sse_executor}.py`, `orchestrate/agentcard_loader.py`, `orchestrate/core/model/{psop,execution_record}.py`, `orchestrate/core/{psop_generator,intent_psop_generator,retrieval,prompts}.py`, `orchestrate/solution_package/{manager,parse_flow}.py`, `orchestrate/registry_client/{client,client_factory}.py`, `common/auth/{agent_credential_service,extension_interceptor}.py`, `common/a2at_config.py`, `common/negotiation_utils.py`, `common/custom/execution_record_processor.py`, `common/llm/provider/{generic_llm,auth_strategies}.py`, `common/config/llm_config.json`, `etc/conf/{server.conf,server.properties,agent_credentials.json}`, `requirements.txt`, `README.md`, `AGENTS.md`, `CLAUDE.md`, `.github/workflows/ci.yml`.

**CATALYST-VERSION** (`/home/lorenzo/git/openan-catalyst/cat26-orchestration-center`): `orchestrate/runtime/exec_engine.py`, `orchestrate/server/{frontend_support_server,middleware}.py`, `orchestrate/a2at/{__init__,agent_task_type,extension_parser,message_builder,settings,template_renderer}.py`, `orchestrate/messages/{message_events,message_persistence}.py`, `orchestrate/core/model/psop.py`, `orchestrate/core/{psop_generator,intent_psop_generator,retrieval}.py`, `orchestrate/solution_package/{manager,parse_flow,parse_bpmn}.py`, `orchestrate/registry_client/{client,client_factory,client_external_factory}.py`, `common/custom/message_processor.py`, `common/llm/provider/{base_llm,aoc_base_llm,aoc_chat_llm,aoc_embedding_llm,aoc_reranker_llm,llm_openai,llm_provider_registry}.py`, `config/llm_config.json`, `shared/a2at_settings.conf`, `etc/conf/{server.conf,server.properties}`, `Dockerfile`, `Dockerfile_agents`, `docker-compose.yaml`, `docker-compose-agents.yaml`, `requirements.txt`, `README.md`, `AGENTS.md`, `CLAUDE.md`, `PROJECT_BRIEF.md`.
