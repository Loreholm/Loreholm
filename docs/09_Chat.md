# V2 browser chat

**Status:** A minimal end-to-end chat and transcript-capture path is
implemented. Conversation persistence, history, abort, preferences, and mined
memory surfacing are not implemented in the V2 chat client or instance.

## Request path

```text
chat-v2 browser
  | OIDC bearer token
  v
front door POST /chat/stream
  | per-user sync token over Tailscale :8081
  v
endpoint shim POST /api/chat/stream
  |
  v
V2 instance -> Bifrost -> configured model
```

The browser never receives the instance sync token, Bifrost credentials, or a
direct Tailnet/database route.

## Authentication

`apps/chat-v2/config.js` may provide the OIDC issuer, client ID, audience, and
scope statically. When issuer is blank, the client fetches runtime configuration
from the front door's `/onboarding/auth/config` endpoint.

The client uses Authorization Code with PKCE through `oidc-client-ts`, stores
OIDC user state in browser local storage, and sends the access token to the
front door. Deployments must register the exact chat URL as an allowed redirect
and logout URI.

The current page loads `oidc-client-ts` from cdnjs at runtime. A deployment
that requires a fully self-contained frontend should vendor and pin that asset
instead of relying on the public CDN.

## Chat request

```http
POST /chat/stream
Authorization: Bearer <OIDC access token>
Content-Type: application/json

{
  "conversation_id": "browser-generated-uuid",
  "messages": [
    {"role": "user", "content": "What did we decide about networking?"}
  ]
}
```

The browser keeps the current conversation in memory and sends the accumulated
message list on each turn. A new conversation creates a new browser UUID and
clears that in-memory list. Reloading the page currently loses the conversation
view.

The V2 instance accepts 1–200 messages, each with role `system`, `user`, or
`assistant`, and content up to 100,000 characters. It requires at least one
user message.

## Streaming response

The response is `text/event-stream`. Each event is JSON in an SSE `data:` line:

```text
data: {"type":"content","content":"partial text"}

data: {"type":"done","model":"provider/model"}
```

Failures may produce:

```text
data: {"type":"error","message":"upstream detail"}
```

The front door passes SSE through without buffering. The instance suppresses a
leading model `<think>...</think>` block and streams only visible answer text.

## Passive transcript capture

Before inference, the instance records the latest user message as a raw
`transcript.message` capture. After a successful non-empty completion, it
records the assembled assistant response as a second capture. Both use the
browser conversation ID as `session_ref`.

Consequences:

- a user message remains captured when model inference later fails;
- an interrupted or empty assistant response does not create an assistant
  capture;
- the capture store, not the browser chat history, is the durable transcript
  foundation; and
- no memory tool call is required to preserve the exchange.

The current browser-chat captures use an instance-generated UUIDv7 and fixed
adapter/device metadata. A general spine will eventually own client identity,
offline queues, and policy enforcement for other surfaces.

## Model routing

The instance reads its selected provider/model configuration, then calls
Bifrost's OpenAI-compatible streaming endpoint. If no model configuration is
stored, it attempts `vllm-local/loreholm-local`.

See [policy and models](05_PolicyAndModels.md) for endpoint configuration and
the model-egress boundary.

## Current limitations

- No V2 conversation database or history list
- No resend, edit, branch, abort, or regeneration controls
- No mined-memory retrieval or graph grounding
- No token/cost usage display
- No explicit capture pause in the chat UI
- No offline queue
- No rendering beyond plain text
- No automatic OIDC token refresh recovery in the send path

The older cloud chat router still contains additional V1 proxy routes. Their
presence in the repository does not make conversation CRUD, preferences,
database prompts, models, usage, or abort part of the V2 instance contract.
