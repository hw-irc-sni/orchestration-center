# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> This repo also has an `AGENTS.md` with detailed conventions and gotchas — read it too. The summary below highlights what matters most for working effectively here; AGENTS.md has more depth on config, auth, and TASK-T extension behavior.

## Project overview

A2A-T Multi-Agent Orchestration Center — a visual platform for designing and executing multi-agent workflows via the A2A protocol. Backend is Python/FastAPI; frontend is a React workflow designer (drag-and-drop, PDF import, natural-language intent).

| Layer | Stack |
|---|---|
| Backend | Python 3.12+, FastAPI, uvicorn, loguru |
| Frontend | Node 20.19+, React 18, Vite, Tailwind CSS, React Flow (`@xyflow/react`) |
| Agent protocol | a2a-t-sdk (fulfillment negotiation), a2a-sdk (http-server + grpc) |
| Storage | File-based JSON (`data/workflow_storage/`) or PostgreSQL |

## Commands

```bash
# Backend: create venv once, then install deps
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Start backend (port 5001) — always run as a module
python -m orchestrate.start

# Start sample A2A agents (required for workflow execution to actually succeed)
python -m samples.start_agents_server

# Frontend (from workflow-designer/)
npm install --force
npm run dev        # Vite dev server, port 3003
npm run build
npm run lint        # ESLint
npm run coverage    # Vitest + coverage
```

### Tests

No `pytest.ini`/`conftest.py`/`tox.ini`. Two separate test directories with no shared fixtures:

```bash
# Unit/module tests
pytest test/ -v
pytest test/test_exec_engine.py -v
pytest test/test_exec_engine.py::TestDynamicWorkflowEngine::test_linear_execution -v   # single test

# Integration tests — require a running backend (real HTTP calls), not run in CI
pytest tests/test_external_apis.py -v -s
```

CI (`.github/workflows/ci.yml`) runs `pytest test/ -v --tb=short` and `npm run lint` in `workflow-designer/`. No Python lint/typecheck/formatter is configured — don't invent one.

## Architecture

### Two API layers, one FastAPI app

- **Internal** — `/rest/v1/orchestrate/*` (`orchestrate/server/frontend_support_server.py`) — consumed by the React frontend. Workflow designer expects the backend at `http://127.0.0.1:5001` (hardcoded in `workflow-designer/src/service/api.js`).
- **External** — `/api/v1/*` (`orchestrate/server/external_api.py`) — public-facing API, plus legacy backward-compatible redirect routes.

### Execution engine

`DynamicWorkflowEngine` (`orchestrate/runtime/exec_engine.py`) drives workflow execution: async DAG traversal over PSOP steps, parallel A2A calls, conditional LLM-based routing (`JumpCondition` matching), and SSE streaming of 11 event types (`init`, `start`, `agent_request`, `agent_response`, `psop_update`, `negotiation_request`, `negotiation_resolved`, `negotiation_failed`, `complete`, `error`, `close`) back to the frontend.

Workflow creation has 3 modes, all converging on a PSOP: PDF import (parse → PreFlow → generate PSOP), manual drag-and-drop in the designer, or natural-language intent → LLM-generated PSOP.

### Config system (non-standard — don't reach for env vars or a config library)

`common/util/config_util.py:get_conf()` reads `etc/conf/server.conf` then `etc/conf/server.properties` (second file overrides first), parsing plain `key=value` lines. **All keys are lowercased.** Key keys: `ip`, `port`, `enable_https`, `persistence_mode`, `agent_registry_url`, `forwarded_allow_ips`.

### Persistence

`persistence_mode=file` (default): JSON files under `data/workflow_storage/`. `persistence_mode=postgresql`: auto-creates tables on startup (`database/utils/table_creation.py`), connection config from `etc/conf/db_config.json`. Access via the `WorkflowStorage` singleton, `get_workflow_storage()` (`@lru_cache(maxsize=1)`) — don't instantiate storage directly.

### A2A-T SDK / negotiation

`.env` for a2a-t-sdk is auto-generated from `common/config/llm_config.json` via `common/a2at_config.py` — edit the JSON, not the generated `.env`.

### Agent authentication

`common/auth/agent_credential_service.py` implements a2a-sdk's `CredentialService` (logs in, caches token with TTL) plus an `AgentAuthManager` singleton. `common/auth/extension_interceptor.py` injects the `A2A-Extensions` header from an AgentCard's `capabilities.extensions[].uri`. Per-agent credentials live in `etc/conf/agent_credentials.json`. `DynamicWorkflowEngine` only wires up these interceptors for agents whose AgentCard declares `securitySchemes`/`securityRequirements`/extensions — agents without those fields are unaffected, so don't add auth plumbing unconditionally.

### AgentCard normalization

External agent cards may use OpenAPI-style security notation. `AgentCardLoader._normalize_agent_dict()` converts these to the protobuf-compatible format before parsing; `get_raw_agent_dicts()` preserves the original, un-normalized format — use the right one depending on whether you need the normalized or raw shape.

### TASK-T extension

When an AgentCard declares the TASK-T extension, the engine puts the structured TASK-T prompt into `message.metadata[Task-T-URI]` (not `parts[].text`), sends the `A2A-Extensions` header, and falls back to extracting response text from task metadata. Sample agents read TASK-T prompts from `message.metadata` in `NegotiationBaseAgentExecutor.execute()`.

### HTTPS

The engine's httpx client uses `verify=False` to support self-signed-cert agents. This is intentional for dev/internal use, not an oversight — don't "fix" it without discussing production CA verification first.

## Repo layout

```text
orchestrate/
  core/model/            PSOP, PreFlow, ExecutionRecord (Pydantic)
  core/psop_generator.py, intent_psop_generator.py   LLM-driven PreFlow/intent → PSOP
  runtime/exec_engine.py                              DynamicWorkflowEngine
  server/frontend_support_server.py                   FastAPI app & internal API
  server/external_api.py                              External API routes
common/
  auth/                  Agent credential service + extension interceptor
  custom/                Pluggable handler pattern (HandlerRegistry)
  llm/                   LLM abstraction (generic HTTP client + auth strategies)
workflow-designer/       React frontend (separate Node project, JS/JSX not TypeScript)
samples/                 Sample A2A agents + start script
database/                PostgreSQL support (optional)
etc/conf/                server.conf, server.properties, db_config.json, agent_credentials.json
test/                    Unit/module tests (pytest)
tests/                   Integration tests (require a live server)
data/workflow_storage/   File-based persistence (PSOP, PreFlow, execution records)
```

## Conventions & gotchas

- All Python code runs as modules (`python -m ...`), never `python file.py` — imports are absolute paths rooted at repo root.
- Frontend is JS/JSX, not TypeScript.
- License headers (Apache 2.0, Huawei copyright) are required on all source files — see existing files for the exact header block.

## Merge workflow (GitCode)

- Commit to your personal fork first; never push directly to upstream (`OpenAN/orchestration-center`).
- Create the PR from fork → upstream.
- **Do not commit local-only files**: `workflow-designer/src/service/api.js` (local debug API endpoint) and `etc/conf/server.conf` (local server config) — revert local changes to these before committing.
