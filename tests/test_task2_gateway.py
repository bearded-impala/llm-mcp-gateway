"""Tests for MCP security gateway authentication and tool authorization."""

import httpx
import pytest

from src.task2_security_gateway.downstream_server import downstream_app, downstream_execution_log
from src.task2_security_gateway.gateway import gateway_app


@pytest.fixture(autouse=True)
def reset_downstream_state():
    downstream_execution_log.clear()


@pytest.fixture
def test_clients():
    downstream_transport = httpx.ASGITransport(app=downstream_app)
    downstream_client = httpx.AsyncClient(transport=downstream_transport, base_url="http://downstream")

    gateway_app.state.downstream_url = "http://downstream/mcp"
    gateway_app.state.http_client = downstream_client

    gateway_transport = httpx.ASGITransport(app=gateway_app)
    client = httpx.AsyncClient(transport=gateway_transport, base_url="http://gateway")
    return client


@pytest.mark.asyncio
async def test_auth_missing_bearer(test_clients):
    client = test_clients
    res = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_auth_invalid_token(test_clients):
    client = test_clients
    res = await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"Authorization": "Bearer completely-unknown-token"},
    )
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_tools_list_forwarded_transparently_for_viewer(test_clients):
    client = test_clients
    res = await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 10, "method": "tools/list"},
        headers={"Authorization": "Bearer viewer-readonly-token-abc"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["id"] == 10
    tools = data["result"]["tools"]

    tool_names = [t["name"] for t in tools]
    assert "get_customer_record" in tool_names
    assert "admin_reset_key" in tool_names
    assert "admin_wipe_cache" in tool_names

    assert len(downstream_execution_log) == 1
    assert downstream_execution_log[0]["method"] == "tools/list"


@pytest.mark.asyncio
async def test_standard_tool_call_allowed_for_viewer(test_clients):
    client = test_clients
    req_body = {
        "jsonrpc": "2.0",
        "id": 20,
        "method": "tools/call",
        "params": {
            "name": "get_customer_record",
            "arguments": {"customer_id": "CUST-A1B2C"},
        },
    }
    res = await client.post(
        "/mcp",
        json=req_body,
        headers={"Authorization": "Bearer viewer-readonly-token-abc"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["id"] == 20
    assert "result" in data
    assert len(downstream_execution_log) == 1
    assert downstream_execution_log[0]["params"]["name"] == "get_customer_record"


@pytest.mark.asyncio
async def test_admin_tool_intercepted_for_viewer(test_clients):
    client = test_clients
    req_body = {
        "jsonrpc": "2.0",
        "id": 30,
        "method": "tools/call",
        "params": {
            "name": "admin_reset_key",
            "arguments": {"key_id": "api-key-123"},
        },
    }
    res = await client.post(
        "/mcp",
        json=req_body,
        headers={"Authorization": "Bearer viewer-readonly-token-abc"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["id"] == 30
    assert "error" in data
    assert data["error"]["code"] == -32001
    assert data["error"]["message"] == "Unauthorized Tool Call"

    # Verify downstream was not called
    assert len(downstream_execution_log) == 0


@pytest.mark.asyncio
async def test_admin_tool_allowed_for_admin(test_clients):
    """Verify admin_* tool call is forwarded and succeeds for admin role."""
    client = test_clients
    req_body = {
        "jsonrpc": "2.0",
        "id": 40,
        "method": "tools/call",
        "params": {
            "name": "admin_reset_key",
            "arguments": {"key_id": "api-key-123"},
        },
    }
    res = await client.post(
        "/mcp",
        json=req_body,
        headers={"Authorization": "Bearer admin-secret-token-xyz"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["id"] == 40
    assert "result" in data
    assert "Successfully reset key api-key-123" in data["result"]["content"][0]["text"]

    # Verify downstream received the call
    assert len(downstream_execution_log) == 1
    assert downstream_execution_log[0]["params"]["name"] == "admin_reset_key"


@pytest.mark.asyncio
async def test_malformed_jsonrpc_handling(test_clients):
    """Verify malformed JSON-RPC payloads return standard JSON-RPC errors."""
    client = test_clients
    res = await client.post(
        "/mcp",
        json={"invalid": "payload"},
        headers={"Authorization": "Bearer admin-secret-token-xyz"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["error"]["code"] == -32600
