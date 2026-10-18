# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> This repo also has an `AGENTS.md` with detailed conventions and gotchas — read it too. It is the source of truth for config, auth, persistence and TASK-T behavior; this file only highlights what matters most.

## Project overview

A2A-T Multi-Agent Orchestration Center — a visual workflow designer plus a thin A2A-T dispatch channel for multi-agent collaboration. Backend is Python/FastAPI; frontend is a React workflow designer (drag-and-drop, PDF import, natural-language intent).

| Layer | Stack |
|---|---|
| Backend | Python 3.12+, FastAPI, uvicorn, loguru |
| Frontend | Node.js, React 18, Vite, Tailwind CSS, React Flow |
| Agent protocol | workflow-engine (execution + A2A-T transport), a2a-sdk |
| Storage | File-based JSON (`data/workflow_storage/`), PostgreSQL or MySQL |

## Commands

```bash
# Backend: create venv once, then install deps
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python -m orchestrate.start              # backend, port 5001 — always run as a module
python -m samples.start_agents_server    # sample A2A agents (needed for execution to succeed)

# Frontend (from workflow-designer/)
npm install
npm run dev        # Vite dev server, port 3003
npm run build
npm run lint       # ESLint
npm run test       # Vitest
npm run coverage   # Vitest + coverage
```

### Tests

All Python tests live in `tests/` with a shared `conftest.py` (there is no `test/` directory).

```bash
pytest tests/ -v
pytest tests/test_frontend_support_server.py -v
pytest tests/test_frontend_support_server.py::TestStartProcessStreamEndpoint::test_stream_no_agent_cards -v   # single test

# Integration tests — need a running backend (real HTTP calls)
pytest tests/test_external_apis.py -v -s
```

CI (`.github/workflows/ci.yml`) runs `pytest tests/`, the MySQL/PostgreSQL integration tests, container smoke tests, and `npm run lint` / `test` / `build` in `workflow-designer/`. No Python lint/typecheck/formatter is configured — don't invent one.

## Architecture

### The orchestration center does not execute workflows

```
Frontend (React) → OrchestrationEngine (thin A2A-T channel) → Host Agent (executes PSOP) → Worker Agents
```

`orchestrate/runtime/exec_engine.py:OrchestrationEngine` sends the intent and a PSOP snapshot to the Host Agent over A2A-T and streams SDK events back to the frontend over SSE. Generic execution hosting (task adaptation, workflow-engine invocation, cancellation) lives in `host_agent/`; the bundled SPN policy and demo composition root live in `samples/spn_host_agent/`.

Workflow creation has 3 modes, all converging on a PSOP: PDF import (parse → PreFlow → PSOP), manual drag-and-drop, or natural-language intent → LLM-generated PSOP.

### Two API layers, one FastAPI app

- **Internal** — `/rest/v1/orchestrate/*` (`orchestrate/server/frontend_support_server.py`) — consumed by the frontend. The designer defaults to `http://127.0.0.1:5001` (`workflow-designer/src/service/api.js`; runtime override in localStorage `server_config`).
- **External** — `/api/v1/*` (`orchestrate/server/external_api.py`) — public API plus legacy redirect routes.

### Config system (non-standard — don't reach for a config library)

- Server settings: `common/util/config_util.py:get_conf()` reads `etc/conf/server.conf` then `etc/conf/server.properties` (second overrides), plain `key=value`, **all keys lowercased**; explicit deployment env aliases are applied last (`apply_env_overrides`). Template: `etc/conf/server.conf.example`.
- Models: `etc/config/models.yaml` (git-ignored; copy `models.yaml.example`; path overridable via `LLM_CONFIG_FILE`). Keys under `models:` are capabilities (`chat`, `embed`, `rerank`, …). **Secrets never go in the file** — a field names the env var holding it (`api_key_env`, `auth.<field>_env`), and a literal secret is rejected. Resolution and wire-protocol profiles (`openai_compatible`, `aoc_signed`) are owned by `common/llm/config/model_sources.py`; add a profile for a new protocol rather than hardcoding it. See `etc/config/README_en.md`.
- Secrets come from the process environment or repo-root `.env`; a non-empty process value wins.
- `A2AT_*` variables (read from `.env`) configure the A2A-T SDK and are **independent** of `LLM_CHAT_*` — no auto-derivation. Keep `A2AT_LLM_PROVIDER=openai` unless every process registers the name used (see AGENTS.md).

### Persistence

`persistence_mode=file` (default), `postgresql`, or `mysql`; DB connection profiles are in `etc/conf/db/*.json` (git-ignored; copy the `.template`). Access storage through the `get_workflow_storage()` singleton (`@lru_cache(maxsize=1)`) — don't instantiate it directly.

### Agent authentication and AgentCards

Auth (Bearer login, custom headers, `A2A-Extensions` injection) is handled by the workflow-engine SDK; interceptors are only built for agents whose AgentCard declares `securitySchemes`/`securityRequirements`/extensions — don't add auth plumbing unconditionally. Sample credentials: `samples/agent_credentials.json`.

External agent cards may use OpenAPI-style security notation. `AgentCardLoader._normalize_agent_dict()` converts it to the protobuf-compatible format; `get_raw_agent_dicts()` preserves the original — use the right one for the shape you need.

### TASK-T extension

For agents declaring the TASK-T extension, the generated payload goes in `message.metadata[Task-T-URI]` (not `parts[].text`), the URI is activated in `message.extensions`, and the `A2A-Extensions` header is sent. Sample agents read it from `message.metadata` in `NegotiationBaseAgentExecutor.execute()`.

### HTTPS

Client certificate verification is controlled by `client_verify_server`. Keep it enabled with a trusted CA in production; disable only in controlled environments.

## Conventions & gotchas

- All Python runs as modules (`python -m ...`), never `python file.py` — imports are absolute from the repo root.
- Frontend is JS/JSX, not TypeScript.
- License headers (Apache 2.0, Huawei copyright) are required on all source files — copy the block from an existing file.
- Never write tokens, PATs or passwords into tracked files.

## Merge workflow (GitHub)

- Commit to your personal fork first (`origin`); never push directly to upstream (`project-openan/orchestration-center`).
- Open the PR from fork → upstream.
- **Do not commit local-only files**: `etc/conf/server.conf`, `etc/conf/db/postgresql.json`, `etc/conf/db/mysql.json`, `etc/config/models.yaml`, `.env`. `workflow-designer/src/service/api.js` is tracked — revert local debug-endpoint edits before committing.
