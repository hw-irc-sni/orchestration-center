# LLM 配置文件（llm_config.json）说明

LLM 模块采用配置驱动架构，接入新模型通过编辑 `common/config/llm_config.json` 完成，无需编写 Python 代码。

## 文件结构

```json
{
  "chat":   { ... },    // Chat/LLM 模型（文本生成）
  "embed":  { ... },    // Embedding 模型（文本向量化）
  "rerank": { ... }     // Reranker 模型（结果重排序）
}
```

每个能力 key（`chat`、`embed`、`rerank`）配置一个模型实例，按需配置。

## 通用字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `description` | string | 否 | 模型描述，用于日志 |
| `model` | string | **是** | 模型名称，通过 `$MODEL` 占位符注入 |
| `url` | string | **是** | API 端点地址 |
| `api_key` | string | **是** | API 密钥，`auth` 为 null 时自动作为 `Authorization: Bearer` 头 |
| `enable_thinking` | boolean | 否 | 思考模式开关，通过 `$ENABLE_THINKING` 注入 |
| `verify_ssl` | boolean | 否 | 是否校验 TLS 证书（默认 `true`）。自签名端点可设为 `false` |
| `auth` | object/string/null | 否 | 认证策略（见下文） |
| `headers` | object | 否 | 额外静态 HTTP 头 |
| `body` | object | **是** | 请求体模板，支持 `$` 占位符 |
| `response` | object | **是** | 响应提取路径（点分路径） |

`model`、`url`、`api_key` 默认为 `<YOUR_...>` 占位值。若未填写，会抛出 `ValueError` 并指明
具体字段及对应的环境变量，而不是等到调用厂商接口才失败。

## 环境变量覆盖

上述**标量**字段均可通过 `LLM_<能力>_<字段>` 覆盖，无需修改本文件 —— 例如
`LLM_CHAT_MODEL`、`LLM_CHAT_API_KEY`、`LLM_EMBED_URL`。优先级：

```
环境变量  >  <仓库根目录>/.env  >  common/config/llm_config.json
```

- 空值视为未设置，因此 Docker Compose 的 `${LLM_CHAT_MODEL:-}` 不会把默认值清空。
- 布尔值支持 `true/false`、`1/0`、`yes/no`、`on/off`。
- 结构化字段（`auth`、`headers`、`body`、`response`）属于请求模板，无法通过环境变量设置，仍保留在 JSON 中。
- 容器内仅透传 `LLM_CHAT_*`（见 `docker-compose.yml`），仓库根目录的 `.env` 未挂载进容器，其他能力仍通过 JSON 配置。

该配置驱动 `GenericLLM` —— 编排后端自身的 LLM 调用（意图解析、PSOP 检索、PDF/BPMN 摘要）。
它与 A2A-T 协商 SDK 在仓库根目录 `.env` 中的 `A2AT_*` 配置相互独立（详见顶层 README）。
配置缓存与进程同生命周期 —— 修改后需重启。

## 认证策略（`auth`）

| 值 | 说明 |
|-----|------|
| `null` | 无特殊认证，`api_key` 非空时自动加 Bearer 头，适合 OpenAI 兼容 API |
| `{"type": "aoc_signed", ...}` | AOC 平台签名 Header（`x-sg-*` 系列） |

`aoc_signed` 必填参数：`app_key`、`app_secret`、`authorization`、`api_code`。  
带默认值的可选参数：`scenario_code`（"B99999999999"）、`scenario_version`（"V1"）、`ability_code`（"A999999999"）、`api_version`（"1.0"）、`test_flag`（"1"）。

## 请求体占位符

| 占位符 | 展开为 | 适用能力 |
|--------|--------|----------|
| `$MODEL` | `model` 字段值 | chat, embed, rerank |
| `$PROMPT` | `ask_llm()` / `embed()` 的 prompt 参数 | chat, embed |
| `$QUERY` | `rerank()` 的 query 参数 | rerank |
| `$DOCUMENTS` | `rerank()` 的 documents 参数（JSON 数组） | rerank |
| `$ENABLE_THINKING` | `enable_thinking` 字段值 | chat, embed, rerank |

## 响应提取路径（`response`）

| 能力 | response 键 | 说明 |
|------|-------------|------|
| chat | `answer` | 回答文本路径，如 `"choices.0.message.content"` |
| chat | `reasoning` | 推理/思考过程路径（可选） |
| embed | `embedding` | 向量数组路径，如 `"data.0.embedding"` |
| rerank | `results` | 重排结果路径，如 `"results"` |

## 配置示例

### OpenAI 兼容 API

适用于 OpenAI、DeepSeek、Qwen/DashScope 及自建网关 —— 只需调整 `model`、`url`。

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

### AOC 平台（Chat + Embed + Rerank）

```json
{
  "chat": {
    "model": "Qwen3_32B",
    "url": "http://宿主机:端口/aoc/openapi/端点ID",
    "auth": {
      "type": "aoc_signed",
      "app_key": "你的_APP_KEY",
      "app_secret": "你的_APP_SECRET",
      "authorization": "Bearer 你的_TOKEN",
      "api_code": "你的_API_CODE"
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
    "url": "http://宿主机:端口/aoc/openapi/端点ID",
    "auth": { "type": "aoc_signed", "app_key": "...", "app_secret": "...", "authorization": "Bearer ...", "api_code": "..." },
    "body": { "model": "$MODEL", "input": "$PROMPT" },
    "response": { "embedding": "data.0.embedding" }
  },
  "rerank": {
    "model": "bge-reranker-v2-m3",
    "url": "http://宿主机:端口/aoc/openapi/interface/bge-reranker-v2-m3",
    "auth": { "type": "aoc_signed", "app_key": "...", "app_secret": "...", "authorization": "Bearer ...", "api_code": "..." },
    "body": { "model": "$MODEL", "query": "$QUERY", "documents": "$DOCUMENTS" },
    "response": { "results": "results" }
  }
}
```

## 代码调用示例

```python
from common.llm import get_llm_instance, get_embed_instance, get_rerank_instance

# Chat
llm = get_llm_instance()  # 默认使用 "chat"
reasoning, answer = llm.ask_llm("你好")

# Embedding
emb = get_embed_instance()
vector = emb.embed("需要向量化的文本")

# Rerank
rerank = get_rerank_instance()
results = rerank.rerank("查询", ["候选1", "候选2"])
```

> 详细配置指南见 [编排中心开发指南](../../docs/zh/开发指南.md)。
