# LLM Configuration File (llm_config.json) Description

The LLM module uses a **configuration-driven** architecture. To add new models, simply edit `common/config/llm_config.json` — no Python code needed.

## File Structure Overview

```json
{
  "chat":   { ... },    // Chat/LLM model (text generation)
  "embed":  { ... },    // Embedding model (text vectorization)
  "rerank": { ... }     // Reranker model (result reordering)
}
```

Each capability key (`chat`, `embed`, `rerank`) configures one model instance. Unconfigured capabilities will raise an error when called.

## Common Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `description` | string | No | Model description for logging. |
| `model` | string | **Yes** | Model name, injected via `$MODEL` placeholder. |
| `url` | string | **Yes** | API endpoint URL. |
| `api_key` | string | **Yes** | API key; auto-added as `Authorization: Bearer` when `auth` is null. |
| `enable_thinking` | boolean | No | Chain-of-thought mode, injected via `$ENABLE_THINKING`. |
| `verify_ssl` | boolean | No | TLS certificate verification (default `true`). Set `false` for self-signed endpoints. |
| `auth` | object/string/null | No | Authentication strategy (see below). |
| `headers` | object | No | Extra static HTTP headers. |
| `body` | object | **Yes** | Request body template with `$` placeholders. |
| `response` | object | **Yes** | Response extraction paths (dot notation). |

`model`, `url` and `api_key` ship as `<YOUR_...>` placeholders. Leaving one unset raises a
`ValueError` naming the field and the variable that sets it, rather than failing at the provider.

## Environment Overrides

Any **scalar** field above can be overridden without editing this file, using
`LLM_<CAPABILITY>_<FIELD>` — for example `LLM_CHAT_MODEL`, `LLM_CHAT_API_KEY`,
`LLM_EMBED_URL`. Precedence:

```
environment variables  >  <repo root>/.env  >  common/config/llm_config.json
```

- Empty values count as unset, so Docker Compose's `${LLM_CHAT_MODEL:-}` cannot blank out a default.
- Booleans accept `true/false`, `1/0`, `yes/no`, `on/off`.
- Structured fields (`auth`, `headers`, `body`, `response`) are request templates and cannot be
  set this way — they stay in the JSON.
- In containers only the `LLM_CHAT_*` variables are forwarded (see `docker-compose.yml`); the repo
  root `.env` is not mounted, so other capabilities remain JSON-configured there.

This config drives `GenericLLM` — the orchestration backend's own LLM calls (intent parsing, PSOP
retrieval, PDF/BPMN summarization). It is independent of the A2A-T negotiation SDK's `A2AT_*`
config in the repo-root `.env` (see the top-level README). The cache lives for the process
lifetime — restart after a change.

## Authentication (`auth`)

| Value | Description |
|-------|-------------|
| `null` | No special auth. If `api_key` is set, adds `Bearer` header automatically. |
| `{"type": "aoc_signed", ...}` | AOC platform signed headers (`x-sg-*` series). |

`aoc_signed` required params: `app_key`, `app_secret`, `authorization`, `api_code`.  
Optional with defaults: `scenario_code` ("B99999999999"), `scenario_version` ("V1"), `ability_code` ("A999999999"), `api_version` ("1.0"), `test_flag` ("1").

## Body Template Placeholders

| Placeholder | Expands to | Capability |
|-------------|-----------|------------|
| `$MODEL` | `model` field value | chat, embed, rerank |
| `$PROMPT` | `ask_llm()` / `embed()` prompt | chat, embed |
| `$QUERY` | `rerank()` query | rerank |
| `$DOCUMENTS` | `rerank()` documents (JSON array) | rerank |
| `$ENABLE_THINKING` | `enable_thinking` field value | chat, embed, rerank |

## Response Extraction Paths (`response`)

| Capability | Key | Description |
|-----------|-----|-------------|
| chat | `answer` | Answer text path, e.g. `"choices.0.message.content"` |
| chat | `reasoning` | Reasoning/thinking path (optional) |
| embed | `embedding` | Vector array path, e.g. `"data.0.embedding"` |
| rerank | `results` | Rerank results path, e.g. `"results"` |

## Configuration Examples

### OpenAI-compatible API

Works for OpenAI, DeepSeek, Qwen/DashScope, or any self-hosted gateway — only `model` and `url`
change.

```json
{
  "chat": {
    "model": "gpt-4o",
    "url": "https://api.openai.com/v1/chat/completions",
    "api_key": "sk-xxxxxxxx",
    "enable_thinking": true,
    "auth": null,
    "body": {
      "model": "$MODEL",
      "messages": [{"role": "user", "content": "$PROMPT"}]
    },
    "response": {
      "answer": "choices.0.message.content",
      "reasoning": "choices.0.message.reasoning_content"
    }
  }
}
```

### AOC Platform (Chat + Embed + Rerank)

```json
{
  "chat": {
    "model": "Qwen3_32B",
    "url": "http://HOST:PORT/aoc/openapi/ENDPOINT_ID",
    "auth": {
      "type": "aoc_signed",
      "app_key": "YOUR_APP_KEY",
      "app_secret": "YOUR_APP_SECRET",
      "authorization": "Bearer YOUR_TOKEN",
      "api_code": "YOUR_API_CODE"
    },
    "body": {
      "model": "$MODEL",
      "messages": [{"role": "user", "content": "$PROMPT"}],
      "chat_template_kwargs": {"enable_thinking": "$ENABLE_THINKING"}
    },
    "response": {
      "answer": "choices.0.message.content",
      "reasoning": "choices.0.message.reasoning_content"
    }
  },
  "embed": {
    "model": "bge-m3",
    "url": "http://HOST:PORT/aoc/openapi/ENDPOINT_ID",
    "auth": { "type": "aoc_signed", "app_key": "...", "app_secret": "...", "authorization": "Bearer ...", "api_code": "..." },
    "body": { "model": "$MODEL", "input": "$PROMPT" },
    "response": { "embedding": "data.0.embedding" }
  },
  "rerank": {
    "model": "bge-reranker-v2-m3",
    "url": "http://HOST:PORT/aoc/openapi/interface/bge-reranker-v2-m3",
    "auth": { "type": "aoc_signed", "app_key": "...", "app_secret": "...", "authorization": "Bearer ...", "api_code": "..." },
    "body": { "model": "$MODEL", "query": "$QUERY", "documents": "$DOCUMENTS" },
    "response": { "results": "results" }
  }
}
```

## Usage Example (Python)

```python
from common.llm import get_llm_instance, get_embed_instance, get_rerank_instance

# Chat
llm = get_llm_instance()  # defaults to "chat"
reasoning, answer = llm.ask_llm("who are you?")

# Embedding
emb = get_embed_instance()
vector = emb.embed("text to embed")

# Rerank
rerank = get_rerank_instance()
results = rerank.rerank("query", ["candidate1", "candidate2"])
```

> For detailed configuration guide, see [Orchestration Center Development Guide](../../docs/en/Orchestration%20Center%20Development%20Guide.md).
