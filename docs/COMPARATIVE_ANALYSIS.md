# Comparative Analysis: orchestration-center vs. catalyst-fabric vs. agentgateway

**Date:** 2026-07-02
**Scope:** A2A (agent-to-agent) execution/routing architecture
**Purpose:** Inform an architecture decision — should `orchestration-center` extract a dedicated data-plane/routing layer for outbound agent traffic, or continue routing in-engine?

Repos analyzed:
- `orchestration-center` (this repo) — `orchestrate/runtime/exec_engine.py`, `orchestrate/registry_client/`, `common/auth/`
- `catalyst-fabric/backend` (`/home/lorenzo/git/catalyst-fabric/backend/src/agent_fabric/`) — Python/FastAPI control plane, "Agent Fabric"
- `agentgateway` (github.com/agentgateway/agentgateway) — Rust A2A/MCP/AI proxy, Linux Foundation project, shallow-cloned and read for this analysis

---

## 1. The one question this doc answers

`orchestration-center`'s `DynamicWorkflowEngine` calls agents directly over HTTP — it resolves a URL from the agent's AgentCard and issues the request itself via a shared `httpx.AsyncClient`, with **no proxy, gateway, or sidecar in between** (`orchestrate/runtime/exec_engine.py:200-214,426-444`). That client has no retries, no load balancing, no circuit breaking, no mTLS, and disables TLS verification (`verify=False`, `exec_engine.py:211`, intentional per `CLAUDE.md` for dev/self-signed certs).

`agentgateway` exists specifically to own that job: it's a Rust data-plane proxy with production-grade load balancing (P2C + EWMA health), circuit breaking, retries, rate limiting, mTLS/SPIFFE, and OpenTelemetry tracing — but it has **zero concept of a multi-step workflow**. It routes one HTTP hop, not a DAG.

`catalyst-fabric` is the interesting middle case: its own design brief says it delegates *all* A2A data-plane traffic to `agentgateway` and reimplements none of it. Its code says otherwise — it ships a **fully-built, hand-rolled A2A reverse proxy** (`api/a2a_proxy.py`) as a first-class alternative to the gateway, selected by whether `gateway_url` is configured. In other words, catalyst-fabric's own team faced the exact fork `orchestration-center` faces now, and **hedged by building both paths** rather than committing to one. That makes it a worked precedent, not just a third data point — see §5.

The rest of this document breaks the comparison into the two things people mean by "routing" (§2), a rubric-based diff across all three systems (§3), the catalyst-fabric dual-mode precedent in detail (§5), the concrete gaps this exposes in `orchestration-center` (§6), and the options for closing them with their tradeoffs (§7).

---

## 2. Two different things called "routing" — don't conflate them

| | Network/data-plane routing | Workflow/orchestration routing |
|---|---|---|
| **Question it answers** | "Which host/port do I send this HTTP request to, and how reliably?" | "Given this step's result, which step runs next?" |
| **Unit of work** | One HTTP request/response (or SSE stream) | A DAG of many agent calls, possibly with LLM-evaluated branching |
| **orchestration-center** | None — direct httpx call, static URL off the AgentCard (`exec_engine.py:426-437`) | `DynamicWorkflowEngine._determine_next_steps()` + `_llm_route_decision()`, `JumpCondition`-driven (`exec_engine.py:289-305,930-997`) |
| **catalyst-fabric** | Two selectable implementations: delegate to `agentgateway`, or its own `a2a_proxy.py` direct-proxy path (see §5) | Explicitly out of scope (non-goal in `PROJECT_BRIEF.md`); notifies an *external* orchestrator instead (`a2a_proxy.py:_notify_orchestration_center`) |
| **agentgateway** | Its entire purpose — Bind→Listener→Route→Backend matching, P2C load balancing, retries, circuit breaking | Not applicable — confirmed no workflow/DAG/multi-step concept anywhere in the codebase |

Every axis below is evidence for one of these two rows. Keep them separate — a system can be strong on one and absent on the other, and two of the three systems in this comparison are exactly that.

---

## 3. Rubric comparison

| Axis | orchestration-center | catalyst-fabric | agentgateway |
|---|---|---|---|
| **1. Agent discovery/registry** | Client of an *external* Agent Registry microservice (`orchestrate/registry_client/client.py`), re-queried live per request, no local cache; local file-based AgentCards also supported for dev (`agentcard_loader.py`) | Full DB-backed registry it *owns* (`Agent` ORM table, JSONB AgentCard + `fabric_profile`), plus continuous sync from an upstream "OpenAN" registry treated as authoritative | None — resolves *backends*, not "agents"; no agent-card catalog beyond what's declared in pushed config |
| **2. Endpoint resolution** | Static: `agent_card.supported_interfaces[0].url`, always index 0, no pool (`exec_engine.py:426-437`) | Two paths: gateway-config route write, or direct resolution from the same `supported_interfaces[*].url` in `a2a_proxy.py` | Config-driven `Backend` (Service/xDS-resolved or static host:port), DNS via `hickory_resolver`; K8s Service discovery when run against xDS |
| **3. Transport & TLS** | Single shared client, `verify=False` (intentional, dev), no mTLS support (unimplemented, not just unused) | Real PKI: own Root+Intermediate CA, leaf cert issuance/rotation/CRL (RFC 5280), JWS AgentCard signing (RFC 7515); app-level mTLS termination; direct-mode uses `verify=True` | Full TLS termination + upstream mTLS (`BackendTLS`), plus optional SPIFFE/HBONE mesh mode with automatic cert rotation via a CA client |
| **4. Auth to agents** | Per-agent, conditional on AgentCard declaring `security_schemes`+`security_requirements`; token caching w/ TTL (`agent_credential_service.py`) | Inbound: peer-cert CN extraction, **not used for authorization** (logged only); outbound: lateral mTLS to gateway/agents. No agent-facing token vault | `BackendAuth`: passthrough JWT, static key injection, or cloud-native (GCP/AWS SigV4/Azure/Copilot) credential providers — generic, not A2A-specific |
| **5. Request routing (network layer)** | **Confirmed absent** — engine is the client | **Both**: gateway-mediated *or* full hand-rolled proxy (`a2a_proxy.py`) selected by `gateway_url` config — see §5 | **Core function** — path/header/method match → weighted backend selection |
| **6. Orchestration/next-step routing (workflow layer)** | **Core function** — DAG traversal + `JumpCondition` LLM routing, bounded negotiation rounds (max 3) | **Confirmed absent**, explicit non-goal; forwards lifecycle events to an external orchestrator | **Confirmed absent** — zero hits for workflow/DAG concepts in the codebase |
| **7. Policy/traffic management** | Inbound only (rate limiting, connection cap, timeouts on the *frontend* API); **nothing on outbound agent calls** | Schema exists (`policies` in the gateway config writer) but routes ship with an **empty policy** (`policies: {"a2a": {}}`); `FabricProfile`/reputation stored but not read by any enforcement path | Full: local + remote (Envoy-RLS-style) rate limiting, P2C load balancing, CEL-configurable circuit breaking with eviction/backoff, traffic mirroring/redirect/rewrite |
| **8. Retries/failover** | **Not present** — timeouts/connect errors are caught and re-raised as `RuntimeError`, no retry | **Not present** in `a2a_proxy.py` direct mode — single request, `httpx.HTTPError` → immediate 502 | Full retry policy (attempts/backoff/retryable codes/CEL conditions) + passive health-based failover with eviction/recovery |
| **9. Observability/tracing** | 11 SSE event types, durably persisted to `ExecutionRecord`; loguru logging; **no distributed tracing** | Structured `structlog` audit logging + durable `messages` table with cross-system correlation IDs; OTel collector *provisioned* in compose but **not integrated** in backend code | Prometheus metrics, OTel OTLP tracing w/ CEL-filterable sampling, structured access logs, live admin/debug endpoints (`/config_dump`, pprof, task dump) |
| **10. A2A-T extension handling** | Native: TASK-T prompt in `message.metadata[Task-T-URI]`, `A2A-Extensions` header via `ExtensionInterceptor`, negotiation protocol built on top | Fabric-T capability profile exposed via AgentCard extensions + a prompt-template CMS (content, not routing/trust constraints) | **Not applicable** — protocol-generic; treats A2A-T traffic as opaque A2A JSON-RPC, passes headers/metadata through untouched |

---

## 4. What each system actually is

- **agentgateway** — a pure **data plane**. Single-hop HTTP/A2A/MCP/AI proxy with the traffic-management maturity of an Envoy/istio-ztunnel-class product (same architectural lineage, now Linux Foundation governed). Zero orchestration semantics. Configured via static YAML (hot-reloaded) or dynamic xDS from a control plane, plus a separate CA-client channel for workload identity.
- **orchestration-center** — a pure **workflow engine with an embedded, minimal HTTP client**. All the sophistication is in DAG traversal, conditional/LLM-based branching, negotiation, and A2A-T handling. The outbound leg to each agent is the least mature part of the system by a wide margin: no retries, no LB, no mTLS, no distributed tracing.
- **catalyst-fabric** — a **control plane that also contains a full data-plane implementation it doesn't fully trust itself**. It owns agent registry, real PKI/mTLS issuance, and A2A-T capability metadata — genuinely production-grade in those areas — but explicitly disclaims workflow ownership, and its own gateway-vs-direct-proxy split shows it hasn't (yet) fully committed its data-plane traffic to `agentgateway` either.

None of the three is a strict superset of another. They occupy different — and only partially overlapping — layers.

---

## 5. catalyst-fabric's dual-mode hedge: a worked precedent

`catalyst-fabric/backend/src/agent_fabric/api/a2a_proxy.py` (docstring, lines 1-22) is explicit about the fork:

> - **Gateway-mediated**: when `settings.gateway_url` is set, Fabric forwards `/a2a/{agent_key}{op}` through the `agentgateway` container unchanged.
> - **Direct (Fabric-routed)**: when no gateway is configured, Fabric resolves the agent's backend URL from `supported_interfaces[*].url` in the agent card and forwards `{agent_card_url}{op}` directly... **Direct mode uses default TLS (`verify=True`)... per-agent mTLS without the gateway is a follow-up** [i.e., not yet built].

This is not a stub or a fallback shim — `a2a_proxy.py` fully implements `message:send`, `message:stream` (SSE passthrough), `tasks` CRUD, push-notification config, JSON-RPC role normalization for protobuf compatibility, and persists every message to a `messages` table for cross-system correlation. It is real, maintained code, sitting directly alongside the gateway-mediated path.

Why this matters for `orchestration-center`'s decision:

1. **It confirms the two paths are genuinely competitive, not obviously dominated.** A team building the same kind of system, with an explicit design brief favoring "delegate everything to agentgateway," still found it worth keeping a direct-proxy code path alive and using it whenever the gateway isn't configured (e.g. dev/local, or before gateway policy/mTLS is fully wired up).
2. **The direct-proxy path is exactly as thin as `orchestration-center`'s engine-embedded client.** No retries, no load balancing, no circuit breaking (§3, row 8) — catalyst-fabric didn't reimplement agentgateway's traffic management in the direct path, it just made the request. This suggests that *if* `orchestration-center` built its own gateway-mediated mode, the "direct" fallback it would presumably still need for dev/local use should stay this thin by design, not be improved in place.
3. **The switch is a single config flag** (`gateway_url` set vs. unset) with the routing/auth/observability layers built to be indifferent to which mode is active. That's a concrete integration pattern `orchestration-center` could mirror if it goes the "adopt agentgateway" route (§7, option A): keep `DynamicWorkflowEngine`'s direct httpx path as the no-gateway-configured fallback, and add a gateway-mediated path behind the same interface.

---

## 6. Concrete gaps in orchestration-center's outbound path

All of the following are **verified absent** in `orchestrate/runtime/exec_engine.py` and its dependencies, based on direct source reading (not inference):

- **No retries or backoff.** `httpx.TimeoutException`/`httpx.ConnectError` are caught once and re-raised as `RuntimeError` (`exec_engine.py:539-542`); the caller marks the task failed and moves on. One transient network blip fails the whole step (and, for `ALL_SUCCESS` steps, the whole workflow run).
- **No load balancing or multi-endpoint failover.** `supported_interfaces[0]` is hardcoded — an agent with multiple registered interfaces/replicas can never be spread across them or failed over between them.
- **No circuit breaking or backpressure protection** for an agent under load — nothing bounds how many concurrent calls a single workflow's parallel subtasks can fire at one agent.
- **No mTLS**, and `verify=False` disables server cert verification outbound (intentional per project convention, but it means there is currently no path to hardened transport security without code changes).
- **No distributed tracing.** `execution_context_id` is an A2A protocol correlation ID for one agent conversation, not a cross-service trace; there's no OpenTelemetry span propagation across the workflow → agent boundary.
- **Inbound policy (rate limiting, connection caps, timeouts) exists and is solid** (`orchestrate/server/middleware.py`) — this is a real asymmetry: the frontend-facing API is defended, the outbound agent-facing leg is not.

These are the same gaps catalyst-fabric's direct-proxy path has (§5, point 2) — this is not an orchestration-center-specific weakness, it's what every hand-rolled A2A client looks like without a dedicated data plane. That reframes the question from "what's wrong with orchestration-center" to "which of the two available fixes is worth the cost."

---

## 7. Options and their costs

### Option A — Adopt `agentgateway` as an outbound data-plane layer
Route `DynamicWorkflowEngine`'s agent calls through a locally-run `agentgateway` instance instead of calling agents directly.

- **Gets you:** retries, P2C load balancing + passive circuit breaking, rate limiting, mTLS/SPIFFE, OTel tracing, Prometheus metrics — all for free, maintained upstream, Linux Foundation governed.
- **Costs:** a new operational component (Rust binary/container) in every deployment; a config-generation layer analogous to catalyst-fabric's `agentgateway_writer.py` (route computation from the agent registry, schema-validated YAML writes, hot-reload semantics); A2A-T metadata (`message.metadata[Task-T-URI]`, custom `A2A-Extensions` header) must be verified to pass through agentgateway unmodified — agentgateway treats A2A-T as opaque JSON-RPC, which should be safe but needs testing, since agentgateway does rewrite AgentCard URLs in responses (§3 row 5's "URL rewriting" behavior) and that rewrite must not break TASK-T URI matching in `_get_task_t_uris()`.
- **Best if:** the team wants production-grade traffic management without building/maintaining it, and is willing to add and operate a new component.

### Option B — Build the missing traffic management in-engine
Add retries/backoff, a simple round-robin or health-aware endpoint pool, and basic circuit breaking directly into `exec_engine.py`'s httpx usage; wire OTel manually.

- **Gets you:** no new component, no deployment topology change, full control over exactly what's needed for the A2A-T/negotiation flows this engine already owns.
- **Costs:** reimplementing (even a subset of) what agentgateway already solved — retry/backoff policy, health-based eviction, LB algorithm — in Python, and maintaining it. Historically this is where hand-rolled clients stay thin forever (see catalyst-fabric's direct-proxy path, which has had this exact opportunity and hasn't taken it).
- **Best if:** the team judges the current gap (no retries, no LB) as low-severity for its actual deployment shape (e.g., single-instance agents, controlled environments) and prefers zero new infra.

### Option C — Mirror catalyst-fabric's dual-mode pattern
Keep the current direct httpx path as the default/dev mode; add an optional gateway-mediated mode behind a config flag (e.g. `agent_gateway_url` alongside the existing `agent_registry_url` in `server.conf`), selected the same way catalyst-fabric selects `gateway_url`.

- **Gets you:** incremental adoption — ship Option A's benefits only where a gateway is actually deployed, without forcing it as a hard dependency; a direct path stays available for local/dev/demo runs (`samples/start_agents_server.py`-style flows), which is a documented, in-use workflow in this repo.
- **Costs:** two code paths to keep in sync (same class of complexity catalyst-fabric is currently carrying, including that project's related follow-up work — "per-agent mTLS without the gateway" — not yet done there either).
- **Best if:** the team wants A/B or staged rollout, or expects some deployments to have a gateway and others not (matches this repo's existing config-driven persistence-mode pattern — file vs. PostgreSQL — which is architecturally the same kind of pluggable-backend decision).

### Recommendation
Given orchestration-center's existing config-driven pluggability pattern (`persistence_mode=file|postgresql`) and that catalyst-fabric — facing the identical fork — chose to hedge rather than commit, **Option C is the lowest-risk path**: it doesn't block on standing up new infra, it's additive to the current engine rather than a rewrite, and it leaves the door open to Option A's benefits wherever a gateway is later deployed. Option A alone is the better end-state if/when agent fleets grow large enough that manual LB/retry logic becomes a real operational cost; Option B is worth doing regardless of A/C as a cheap near-term mitigation (retries + a bounded connection semaphore per agent are a few hours of work and meaningfully reduce the current single-transient-failure blast radius) while a longer-term data-plane decision is made.

---

## 8. Sources

- `orchestration-center`: `orchestrate/runtime/exec_engine.py`, `orchestrate/registry_client/{client.py,client_factory.py}`, `common/auth/{agent_credential_service.py,extension_interceptor.py}`, `orchestrate/core/model/`, `orchestrate/server/{frontend_support_server.py,external_api.py,middleware.py,sse_executor.py,response_utils.py}`, `orchestrate/agentcard_loader.py`, `samples/agentcard/spn_agent_card.json`, `samples/agents/negotiation_base_agent.py`, `etc/conf/server.conf`.
- `catalyst-fabric`: `PROJECT_BRIEF.md`, `backend/README.md`, `backend/config.example.yaml`, `docker-compose.yaml`, `backend/src/agent_fabric/{api/,ca/,orm/,registry.py,app.py,auth.py,agentgateway_writer.py,external_registry.py,resync.py,prompt_sync.py,models.py}`, `backend/openapi.json`, `backend/alembic/versions/`.
- `agentgateway` (github.com/agentgateway/agentgateway, shallow-cloned for this analysis): `crates/agentgateway/src/{a2a/mod.rs,types/{agent.rs,discovery.rs,loadbalancer.rs,frontend.rs,backend.rs},http/{auth/mod.rs,backendtls.rs,health.rs,retry/mod.rs,remoteratelimit.rs},telemetry/,management/admin.rs,control/caclient.rs}`, `crates/xds/`, `crates/agentgateway-app/src/commands/run.rs`, `controller/pkg/syncer/`, `examples/{a2a,ratelimiting,tls}/`, `CHARTER.md`.

All file:line citations above were read directly from source during this analysis (not inferred from documentation), except where explicitly noted as doc-only claims (e.g. catalyst-fabric's `PROJECT_BRIEF.md` framing, contradicted by its own code per §5).
