# AI Model Integration

This document explains how to integrate AI models with loreholm.

## Overview

loreholm has two integration surfaces:

1. **Cloud MCP API** — for MCP clients (Claude Desktop, Cursor, custom integrations) connecting to the cloud-hosted API
2. **Local Dashboard + Bifrost** — for the local AI-powered wizard agent and provider management

## Local LLM Integration (Bifrost)

The local dashboard uses **Bifrost** as an OpenAI-compatible LLM gateway. Bifrost routes requests to configured AI providers.

### Supported Providers

| Provider | Config Key | Auth | Model Discovery |
|----------|-----------|------|-----------------|
| OpenAI | `openai` | API key (`sk-...`) | `api.openai.com/v1/models` |
| Anthropic | `anthropic` | API key (`sk-ant-...`) | Hardcoded model list |
| Google (Gemini) | `google` | API key (`AIza...`) | `generativelanguage.googleapis.com` |
| Groq | `groq` | API key (`gsk_...`) | `api.groq.com/openai/v1/models` |
| Ollama (Local) | `local` | Base URL only | Local `/v1/models` endpoint |

### Configuration

Providers are configured through the local dashboard UI (AI Models tab) or by editing `~/.loreholm/chat-bifrost-config.json`:

```json
{
  "providers": {
    "openai": {
      "provider": "openai",
      "api_key": "sk-...",
      "model": "openai/gpt-4o-mini",
      "base_url": null
    },
    "local": {
      "provider": "local",
      "api_key": null,
      "model": "ollama/llama3.2",
      "base_url": "http://host.docker.internal:11434"
    }
  }
}
```

### Model Selection

The dashboard organizes discovered models by type: Chat, Reasoning, Vision, Image, Video, Audio, Embedding. The wizard agent only uses Chat and Reasoning models. Users can set a favorite model per provider.

### Wizard Agent Tools

The local dashboard wizard agent has access to these database management tools:

| Tool | Purpose | Requires Approval |
|------|---------|-------------------|
| `list_databases` | List all registered databases | No |
| `get_database_status` | Health counters (nodes, edges, memory) | No |
| `get_database_schema` | Labels, relationships, properties | No |
| `run_readonly_query` | Execute read-only Cypher | No |
| `run_query` | Execute any Cypher (CREATE, MERGE, etc.) | No |
| `start_database` | Start a stopped container | No |
| `redeploy_database` | Recreate container (fix config/SSL) | No |
| `deploy_database` | Create a new ArcadeDB container | Yes |

---

## Cloud MCP Integration

The cloud API supports **two approaches** for AI model integration:

### 1. **Official MCP Protocol** (Recommended for MCP Clients)
- **Standard**: Follows the [official MCP specification](https://modelcontextprotocol.io/specification/)
- **Transport**: JSON-RPC 2.0 over HTTP
- **Endpoint**: `/mcp/v1/`
- **Best for**: Claude.ai Custom Connectors, official MCP clients
- **Format**: `tools/list` and `tools/call` JSON-RPC methods

### 2. **REST API** (Simple HTTP Integration)
- **Standard**: Custom REST endpoints
- **Transport**: Standard HTTP POST/GET requests
- **Endpoints**: `/mcp/tools` (GET) and `/mcp/execute` (POST)
- **Best for**: Simple integrations, custom AI wrappers, quick prototyping
- **Format**: JSON request/response

---

## Approach 1: Official MCP Protocol (JSON-RPC)

This follows the [official MCP specification](https://modelcontextprotocol.io/specification/2025-06-18/) and works with Claude.ai Custom Connectors and other MCP-compliant clients.

### Authentication

All requests require authentication via `X-API-Key` header:
```
X-API-Key: your-api-key
```

### Routing Behavior

- Tool calls route to the authenticated user's ArcadeDB node via the local dashboard's query proxy.
- API keys can optionally include a database target reference (`db_ref`) so routing uses a saved database target before default node lookup.
- Write/read tools return an error if the user's node is not yet connected.

### Step 1: Initialize Connection

Send an `initialize` request to establish the session:

```bash
curl -X POST "https://api.loreholm.com/mcp/v1/" \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
      "protocolVersion": "2025-06-18",
      "clientInfo": {
        "name": "my-client",
        "version": "1.0.0"
      },
      "capabilities": {}
    }
  }'
```

**Response:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "protocolVersion": "2025-06-18",
    "serverInfo": {
      "name": "loreholm",
      "version": "1.0.0"
    },
    "capabilities": {
      "tools": {
        "listChanged": false
      }
    }
  }
}
```

Most MCP clients then send a lifecycle notification:

```bash
curl -X POST "https://api.loreholm.com/mcp/v1/" \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "method": "notifications/initialized",
    "params": {}
  }'
```

Expected behavior: HTTP `202 Accepted` (notification acknowledged, no response body).

### Step 2: List Available Tools

Request: `tools/list`

```bash
curl -X POST "https://api.loreholm.com/mcp/v1/" \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 2,
    "method": "tools/list",
    "params": {}
  }'
```

**Response:**
```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "tools": [
      {
        "name": "loreholm_search",
        "description": "Search memories using vector similarity search with optional filters",
        "inputSchema": {
          "type": "object",
          "properties": {
            "query": {
              "type": "string",
              "description": "Search query text"
            },
            "top_k": {
              "type": "integer",
              "default": 10,
              "description": "Number of results"
            }
          },
          "required": ["query"]
        }
      }
    ]
  }
}
```

`tools/list` returns static tool definitions and does **not** require a live ArcadeDB connection.

### Optional MCP Resource Probes

Some MCP clients probe resource methods during startup. loreholm supports:
- `resources/list` -> returns `{"resources": []}`
- `resources/templates/list` -> returns `{"resourceTemplates": []}`

These methods are implemented for compatibility even when no resources are exposed.

### Tool Guidance Channel

Because hosts control final system prompts, loreholm encodes recommended workflow directly in tool descriptions:
- `loreholm_search` -> Step 1 (retrieve first)
- `loreholm_context` -> Step 2 (traverse related graph context)
- `loreholm_upsert_entities` / `loreholm_link_entities` / `loreholm_write_memory` -> Step 3 (persist new durable context)

Default memory policy for assistant clients:
- Persist durable context by default after non-trivial turns.
- Skip writes only when the conversation is explicitly marked `no-memory`, `off-record`, or `ephemeral`.
- Force writes when users explicitly request persistence (for example: `remember:` or `save this`).

### Step 3: Call a Tool

Request: `tools/call`

```bash
curl -X POST "https://api.loreholm.com/mcp/v1/" \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 3,
    "method": "tools/call",
    "params": {
      "name": "loreholm_search",
      "arguments": {
        "query": "machine learning projects",
        "top_k": 5
      }
    }
  }'
```

**Response:**
```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "{\"items\": [{\"memory_id\": \"...\", \"text\": \"...\"}]}"
      }
    ],
    "isError": false
  }
}
```

### Using with Claude.ai Custom Connectors

1. Go to Claude.ai Settings → Connectors
2. Click "Add custom connector"
3. Enter your MCP server URL: `https://api.loreholm.com/mcp/v1/`
4. Complete authentication with your API key
5. Claude will automatically discover and use your tools!

---

## Approach 2: REST API (Simple Integration)

For simpler integrations, use the REST endpoints that don't require JSON-RPC.

### Authentication

Same as Approach 1: Use `X-API-Key` header.

## Endpoint 1: List Available Tools

**GET** `/mcp/tools`

Returns all available MCP tools with their schemas in a format suitable for AI models.

### Example Request

```bash
curl -X GET "https://your-api.com/mcp/tools" \
  -H "X-API-Key: your-api-key"
```

### Example Response

```json
{
  "tools": [
    {
      "name": "loreholm_upsert_entities",
      "description": "Create or update entities (people, projects, tools, concepts, places, etc.) in the knowledge graph",
      "parameters": [
        {
          "name": "entities",
          "type": "array",
          "description": "List of entities to create/update. Each entity has: name (string), type (Person|Project|Tool|Concept|Place|Other), aliases (array of strings)",
          "required": true
        }
      ]
    },
    {
      "name": "loreholm_search",
      "description": "Search memories using vector similarity search with optional filters",
      "parameters": [
        {
          "name": "query",
          "type": "string",
          "description": "Search query text",
          "required": true
        },
        {
          "name": "top_k",
          "type": "integer",
          "description": "Number of results to return (default: 10)",
          "required": false,
          "default": "10"
        }
      ]
    }
  ]
}
```

## Endpoint 2: Execute Tool Calls

**POST** `/mcp/execute`

Executes a specific tool with the provided parameters.

### Request Schema

```json
{
  "tool_name": "string",
  "parameters": {}
}
```

### Example: Creating Entities

```bash
curl -X POST "https://your-api.com/mcp/execute" \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "tool_name": "loreholm_upsert_entities",
    "parameters": {
      "entities": [
        {
          "name": "Alice",
          "type": "Person",
          "aliases": ["Alice Smith"]
        }
      ]
    }
  }'
```

### Example: Searching Memories

```bash
curl -X POST "https://your-api.com/mcp/execute" \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "tool_name": "loreholm_search",
    "parameters": {
      "query": "machine learning projects",
      "top_k": 5
    }
  }'
```

### Response Schema

```json
{
  "tool_name": "string",
  "success": true,
  "result": {},
  "error": null
}
```

## Integration with AI Models

### Step 1: Fetch Available Tools

First, fetch the list of available tools:

```python
import requests

api_key = "your-api-key"
base_url = "https://your-api.com"

response = requests.get(
    f"{base_url}/mcp/tools",
    headers={"X-API-Key": api_key}
)
tools = response.json()["tools"]
```

### Step 2: Convert to AI Model Format

Convert the tool definitions to your AI model's function calling format. For example, with OpenAI:

```python
def convert_to_openai_format(tools):
    return [
        {
            "name": tool["name"],
            "description": tool["description"],
            "parameters": {
                "type": "object",
                "properties": {
                    param["name"]: {
                        "type": param["type"],
                        "description": param["description"]
                    }
                    for param in tool["parameters"]
                },
                "required": [
                    param["name"] 
                    for param in tool["parameters"] 
                    if param["required"]
                ]
            }
        }
        for tool in tools
    ]
```

### Step 3: Let AI Model Call Tools

When the AI model wants to call a tool:

```python
def execute_tool_call(tool_name, parameters):
    response = requests.post(
        f"{base_url}/mcp/execute",
        headers={
            "X-API-Key": api_key,
            "Content-Type": "application/json"
        },
        json={
            "tool_name": tool_name,
            "parameters": parameters
        }
    )
    return response.json()
```

### Step 4: Return Results to AI Model

Pass the execution result back to the AI model:

```python
result = execute_tool_call("loreholm_search", {
    "query": "recent Python projects",
    "top_k": 3
})

if result["success"]:
    # Pass result["result"] back to AI model
    ai_continue_with_result(result["result"])
else:
    # Handle error
    print(f"Error: {result['error']}")
```

## Complete Example with Claude

```python
import anthropic
import requests

# Initialize
api_key = "your-mcp-api-key"
base_url = "https://your-api.com"
client = anthropic.Anthropic(api_key="your-claude-api-key")

# Fetch tools
tools_response = requests.get(
    f"{base_url}/mcp/tools",
    headers={"X-API-Key": api_key}
)
tools = tools_response.json()["tools"]

# Convert to Claude format
claude_tools = convert_to_openai_format(tools)

# Create message with tool use
message = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=1024,
    tools=claude_tools,
    messages=[
        {"role": "user", "content": "Search for information about machine learning"}
    ]
)

# Handle tool calls
if message.stop_reason == "tool_use":
    for block in message.content:
        if block.type == "tool_use":
            # Execute the tool
            result = execute_tool_call(block.name, block.input)
            
            # Continue conversation with result
            message = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=1024,
                tools=claude_tools,
                messages=[
                    {"role": "user", "content": "Search for information about machine learning"},
                    {"role": "assistant", "content": message.content},
                    {
                        "role": "user", 
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": str(result["result"])
                            }
                        ]
                    }
                ]
            )
```

## Available Tools

The following tools are available:

1. **loreholm_upsert_entities** - Create/update entities
2. **loreholm_write_memory** - Store memories about entities
3. **loreholm_link_entities** - Create relationships between entities
4. **loreholm_delete_entities** - Delete entities by ID
5. **loreholm_search** - Vector search for memories
6. **loreholm_context** - Get context around specific entities
7. **loreholm_recent** - Get recent memories
8. **loreholm_stats** - Get knowledge graph statistics

## Error Handling

The `/mcp/execute` endpoint returns structured error responses:

```json
{
  "tool_name": "loreholm_search",
  "success": false,
  "result": {},
  "error": "Invalid parameter: query is required"
}
```

Always check the `success` field before processing results.

For JSON-RPC (`/mcp/v1/`):
- Application-level JSON-RPC errors are returned in the response body (`error` object), typically with HTTP `200`.
- Transport/auth failures still use HTTP error status codes (for example `401` for missing/invalid API key, `400` for malformed JSON).

## Prompt + Tool-Call Contract (Recommended)

To keep entity creation and provenance consistent across runs, encode this contract in your system prompt:

1. Always call `loreholm_search` before writing new memory.
2. Only call `loreholm_upsert_entities` for durable nouns (person/project/tool/concept/place).
3. Normalize entity types to canonical set: `Person|Project|Tool|Concept|Place|Other`.
4. Reuse host-native IDs:
   - `source_ref.conversation_id`: stable chat/thread ID
   - `source_ref.message_ids`: stable message ID(s) that support the memory
5. When available, include `source_ref.messages` metadata (`id`, `role`, `text`, `timestamp`) so Message nodes are inspectable.
6. Write at most 5 memories per interaction and prefer lower confidence over overstatement.

Example memory write payload:

```json
{
  "text": "Kevin's birthday month is November.",
  "about_entity_ids": ["25ba20b721f548589ba3e5dac942af6e"],
  "confidence": 0.96,
  "tags": ["personal_profile", "birthday"],
  "source_ref": {
    "conversation_id": "chat_9f1d7d",
    "message_ids": ["msg_01JBR7X9H2W0QY7E8R2K"],
    "platform": "chatgpt",
    "messages": [
      {
        "id": "msg_01JBR7X9H2W0QY7E8R2K",
        "role": "user",
        "text": "My birthday is in November",
        "timestamp": "2026-02-05T22:35:00Z"
      }
    ]
  }
}
```

## Security Considerations

- Use API keys for programmatic access (more suitable for AI agents)
- API keys inherit the same permissions as the user who created them
- In BYODB mode, each API key accesses only its owner's databases and can optionally be pinned to a specific saved target
- Rate limiting applies per API key

## Next Steps

- See [11_ApiKeyAuth.md](11_ApiKeyAuth.md) for API key management
- See [03_McpTools.md](03_McpTools.md) for detailed tool documentation
- See [06_ToolSchemas.md](06_ToolSchemas.md) for complete schema reference
