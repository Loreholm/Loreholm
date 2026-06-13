import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.main import app  # noqa: E402
from app.mcp import mcp_server  # noqa: E402
from conftest import make_async_client  # noqa: E402


@pytest.fixture()
async def client():
    async with await make_async_client(app) as test_client:
        yield test_client


async def _fake_authenticate_request(_request):
    return {"sub": "test-user"}


@pytest.mark.anyio
async def test_notifications_initialized_is_accepted(client, monkeypatch) -> None:
    monkeypatch.setattr(mcp_server, "authenticate_request", _fake_authenticate_request)

    response = await client.post(
        "/mcp/v1/",
        json={
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        },
    )

    assert response.status_code == 202
    assert response.text == ""


@pytest.mark.anyio
async def test_tools_list_does_not_require_database_connection(client, monkeypatch) -> None:
    monkeypatch.setattr(mcp_server, "authenticate_request", _fake_authenticate_request)

    async def _fail_if_called(*_args, **_kwargs):
        raise AssertionError("get_user_store should not be called for tools/list")

    monkeypatch.setattr(mcp_server, "get_user_store", _fail_if_called)

    response = await client.post(
        "/mcp/v1/",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == 1
    assert "result" in body
    assert body["result"]["tools"]
    tools_by_name = {tool["name"]: tool for tool in body["result"]["tools"]}
    tool_names = set(tools_by_name)
    assert "loreholm_delete_entities" in tool_names
    assert "loreholm_delete_memories" in tool_names
    assert "Step 1" in tools_by_name["loreholm_search"]["description"]
    assert "Step 2" in tools_by_name["loreholm_context"]["description"]


@pytest.mark.anyio
async def test_unknown_method_returns_jsonrpc_error_payload(client, monkeypatch) -> None:
    monkeypatch.setattr(mcp_server, "authenticate_request", _fake_authenticate_request)

    response = await client.post(
        "/mcp/v1/",
        json={
            "jsonrpc": "2.0",
            "id": 99,
            "method": "does/not/exist",
            "params": {},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == 99
    assert body["error"]["code"] == -32601


@pytest.mark.anyio
async def test_resources_list_returns_empty_set(client, monkeypatch) -> None:
    monkeypatch.setattr(mcp_server, "authenticate_request", _fake_authenticate_request)

    response = await client.post(
        "/mcp/v1/",
        json={
            "jsonrpc": "2.0",
            "id": 10,
            "method": "resources/list",
            "params": {},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == 10
    assert body["result"] == {"resources": []}


@pytest.mark.anyio
async def test_resource_templates_list_returns_empty_set(client, monkeypatch) -> None:
    monkeypatch.setattr(mcp_server, "authenticate_request", _fake_authenticate_request)

    response = await client.post(
        "/mcp/v1/",
        json={
            "jsonrpc": "2.0",
            "id": 11,
            "method": "resources/templates/list",
            "params": {},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == 11
    assert body["result"] == {"resourceTemplates": []}


@pytest.mark.anyio
async def test_tools_call_surfaces_missing_user_store_as_error(client, monkeypatch) -> None:
    monkeypatch.setattr(mcp_server, "authenticate_request", _fake_authenticate_request)

    async def _missing_user_store(*_args, **_kwargs):
        return None

    monkeypatch.setattr(mcp_server, "get_user_store", _missing_user_store)

    response = await client.post(
        "/mcp/v1/",
        json={
            "jsonrpc": "2.0",
            "id": 12,
            "method": "tools/call",
            "params": {
                "name": "loreholm_stats",
                "arguments": {},
            },
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == 12
    assert body["result"]["isError"] is True
    assert "not connected" in body["result"]["content"][0]["text"]


@pytest.mark.anyio
async def test_tools_call_in_byodb_mode_uses_database_ref_from_api_key(client, monkeypatch) -> None:
    async def _auth_with_database_ref(_request):
        return {
            "sub": "test-user",
            "database_ref": "dt_123",
        }

    captured = {}

    async def _capture_user_store(user_id, database_target_id):
        captured["user_id"] = user_id
        captured["database_target_id"] = database_target_id

        class _StubStore:
            def stats(self):
                return {"entity_count": 1, "memory_count": 1, "top_entities": []}

        return _StubStore()

    monkeypatch.setattr(mcp_server, "authenticate_request", _auth_with_database_ref)
    monkeypatch.setattr(mcp_server, "get_user_store", _capture_user_store)

    response = await client.post(
        "/mcp/v1/",
        json={
            "jsonrpc": "2.0",
            "id": 121,
            "method": "tools/call",
            "params": {
                "name": "loreholm_stats",
                "arguments": {},
            },
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == 121
    assert body["result"]["isError"] is False
    assert captured["user_id"] == "test-user"
    assert captured["database_target_id"] == "dt_123"


@pytest.mark.anyio
async def test_tools_call_delete_entities(client, monkeypatch) -> None:
    monkeypatch.setattr(mcp_server, "authenticate_request", _fake_authenticate_request)

    class _StubStore:
        def delete_entities(self, entity_ids):
            assert entity_ids == ["e1", "e2"]
            return {
                "deleted_entity_ids": ["e1"],
                "not_found_entity_ids": ["e2"],
                "deleted_count": 1,
            }

    async def _return_stub(*_args, **_kwargs):
        return _StubStore()

    monkeypatch.setattr(mcp_server, "get_user_store", _return_stub)

    response = await client.post(
        "/mcp/v1/",
        json={
            "jsonrpc": "2.0",
            "id": 14,
            "method": "tools/call",
            "params": {
                "name": "loreholm_delete_entities",
                "arguments": {"entity_ids": ["e1", "e2"]},
            },
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == 14
    assert body["result"]["isError"] is False
    assert "deleted_count" in body["result"]["content"][0]["text"]


@pytest.mark.anyio
async def test_tools_call_delete_memories(client, monkeypatch) -> None:
    monkeypatch.setattr(mcp_server, "authenticate_request", _fake_authenticate_request)

    class _StubStore:
        def delete_memories(self, memory_ids):
            assert memory_ids == ["m1", "m2"]
            return {
                "deleted_memory_ids": ["m1"],
                "not_found_memory_ids": ["m2"],
                "deleted_count": 1,
            }

    async def _return_stub(*_args, **_kwargs):
        return _StubStore()

    monkeypatch.setattr(mcp_server, "get_user_store", _return_stub)

    response = await client.post(
        "/mcp/v1/",
        json={
            "jsonrpc": "2.0",
            "id": 140,
            "method": "tools/call",
            "params": {
                "name": "loreholm_delete_memories",
                "arguments": {"memory_ids": ["m1", "m2"]},
            },
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == 140
    assert body["result"]["isError"] is False
    assert "deleted_count" in body["result"]["content"][0]["text"]


@pytest.mark.anyio
async def test_tools_call_upsert_entities_surfaces_store_validation_errors(
    client, monkeypatch
) -> None:
    """Phase 4: entity-type validation moved from the pydantic layer to
    `store.upsert_entities`, which raises ValueError when the input type
    isn't in the per-database authored vocabulary. The MCP handler should
    catch it and return an `isError` content block to the LLM.
    """
    monkeypatch.setattr(mcp_server, "authenticate_request", _fake_authenticate_request)

    class _StubStore:
        def upsert_entities(self, _entities, allowed_entity_types=None):
            raise ValueError(
                "Invalid entity type 'Human'. Allowed types: Person"
            )

    async def _return_stub(*_args, **_kwargs):
        return _StubStore()

    monkeypatch.setattr(mcp_server, "get_user_store", _return_stub)

    response = await client.post(
        "/mcp/v1/",
        json={
            "jsonrpc": "2.0",
            "id": 15,
            "method": "tools/call",
            "params": {
                "name": "loreholm_upsert_entities",
                "arguments": {
                    "entities": [
                        {"name": "Kevin", "type": "Human", "aliases": ["Kev"]}
                    ]
                },
            },
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == 15
    assert body["result"]["isError"] is True
    assert "Invalid entity type" in body["result"]["content"][0]["text"]


@pytest.mark.anyio
async def test_tools_call_upsert_entities_passes_allowed_types_to_store(
    client, monkeypatch
) -> None:
    """Phase 5: the MCP handler loads the per-database authored schema from
    the cached `database_targets` row and passes the resolver down to
    `store.upsert_entities(allowed_entity_types=...)` so the store can
    canonicalize user-supplied type strings.
    """

    async def _auth_with_target(_request):
        return {"sub": "test-user", "database_ref": "dt_test"}

    monkeypatch.setattr(mcp_server, "authenticate_request", _auth_with_target)

    # Handler snapshots the routing record via `get_database_target_for_routing`
    # and pulls `schema_json` off it. Return a schema with a single
    # authoritative "Person" type so the resolver is non-empty on the call site.
    async def _fake_routing(_user_id, _target_id):
        return {
            "database_id": "db-test",
            "profile_hash": "hash-1",
            "schema_json": {
                "entity_types": [{"name": "Person", "description": "A human."}],
                "relationship_types": [],
                "entity_type_aliases": {},
                "relationship_type_aliases": {},
            },
        }

    captured = {}

    class _StubStore:
        last_profile_hash = "hash-1"

        def upsert_entities(self, entities, allowed_entity_types=None):
            captured["entities"] = entities
            captured["allowed_entity_types"] = dict(allowed_entity_types or {})
            return [
                {
                    "entity_id": "e1",
                    "name": entities[0]["name"],
                    "type": "Person",
                    "aliases": entities[0].get("aliases", []),
                    "created": True,
                }
            ]

    async def _return_stub(*_args, **_kwargs):
        return _StubStore()

    monkeypatch.setattr(mcp_server, "get_user_store", _return_stub)
    monkeypatch.setattr(mcp_server, "get_database_target_for_routing", _fake_routing)

    response = await client.post(
        "/mcp/v1/",
        json={
            "jsonrpc": "2.0",
            "id": 16,
            "method": "tools/call",
            "params": {
                "name": "loreholm_upsert_entities",
                "arguments": {
                    "entities": [
                        {"name": "Kevin", "type": "person", "aliases": ["Kev"]}
                    ]
                },
            },
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == 16
    assert body["result"]["isError"] is False
    # The handler did not normalize the type itself — it delegated that
    # work to the store by passing `allowed_entity_types`.
    assert captured["entities"][0]["type"] == "person"
    assert captured["allowed_entity_types"] == {"person": "Person"}
    # The stub returns the canonical type, which the handler surfaces.
    assert "\"type\": \"Person\"" in body["result"]["content"][0]["text"]


@pytest.mark.anyio
async def test_tools_call_write_memory_accepts_source_message_metadata(client, monkeypatch) -> None:
    monkeypatch.setattr(mcp_server, "authenticate_request", _fake_authenticate_request)

    class _StubStore:
        def write_memory(self, payload):
            source_ref = payload["source_ref"]
            assert source_ref["platform"] == "chatgpt"
            assert source_ref["messages"][0]["id"] == "msg_1"
            assert source_ref["messages"][0]["role"] == "user"
            return {
                "memory_id": "m1",
                "timestamp": "2026-02-05T22:37:03.880781+00:00",
                "linked_entities": payload.get("about_entity_ids", []),
            }

    async def _return_stub(*_args, **_kwargs):
        return _StubStore()

    monkeypatch.setattr(mcp_server, "get_user_store", _return_stub)

    response = await client.post(
        "/mcp/v1/",
        json={
            "jsonrpc": "2.0",
            "id": 17,
            "method": "tools/call",
            "params": {
                "name": "loreholm_write_memory",
                "arguments": {
                    "text": "Kevin's birthday month is November.",
                    "confidence": 0.96,
                    "about_entity_ids": ["e1"],
                    "tags": ["birthday"],
                    "source_ref": {
                        "conversation_id": "chat_1",
                        "message_ids": ["msg_1"],
                        "platform": "chatgpt",
                        "messages": [
                            {
                                "id": "msg_1",
                                "role": "user",
                                "text": "My birthday is in November",
                                "timestamp": "2026-02-05T22:35:00Z",
                            }
                        ],
                    },
                },
            },
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == 17
    assert body["result"]["isError"] is False
    assert "\"memory_id\": \"m1\"" in body["result"]["content"][0]["text"]


@pytest.mark.anyio
async def test_tools_call_context_returns_relationship_metadata(client, monkeypatch) -> None:
    monkeypatch.setattr(mcp_server, "authenticate_request", _fake_authenticate_request)

    class _StubStore:
        def context(self, entity_ids, depth, limit):
            assert entity_ids == ["project_1"]
            assert depth == 1
            assert limit == 5
            return {
                "memories": [],
                "entities": [
                    {
                        "entity_id": "tool_1",
                        "name": "FastAPI",
                        "type": "Tool",
                        "from_entity_id": "project_1",
                        "relationship": "implemented_with",
                        "confidence": 0.7,
                    }
                ],
            }

    async def _return_stub(*_args, **_kwargs):
        return _StubStore()

    monkeypatch.setattr(mcp_server, "get_user_store", _return_stub)

    response = await client.post(
        "/mcp/v1/",
        json={
            "jsonrpc": "2.0",
            "id": 18,
            "method": "tools/call",
            "params": {
                "name": "loreholm_context",
                "arguments": {
                    "entity_ids": ["project_1"],
                    "depth": 1,
                    "limit": 5,
                },
            },
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == 18
    assert body["result"]["isError"] is False
    assert "\"from_entity_id\": \"project_1\"" in body["result"]["content"][0]["text"]
    assert "\"relationship\": \"implemented_with\"" in body["result"]["content"][0]["text"]
    assert "\"confidence\": 0.7" in body["result"]["content"][0]["text"]
