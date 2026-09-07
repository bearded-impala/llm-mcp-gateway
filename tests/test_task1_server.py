"""Tests for customer support MCP server tools and stdio transport."""

import json
import subprocess
import sys

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from src.task1_mcp_server.server import server


@pytest.mark.asyncio
async def test_tool_definitions_present():
    """Verify tools are registered with schemas exposing required fields."""
    tools = await server.list_tools()
    tool_map = {t.name: t for t in tools}

    assert "get_customer_record" in tool_map
    assert "trigger_refund" in tool_map

    # Inspect get_customer_record schema
    cust_schema = tool_map["get_customer_record"].input_schema
    assert "customer_id" in cust_schema["properties"]
    assert cust_schema["properties"]["customer_id"].get("pattern") is not None

    # Inspect trigger_refund schema
    refund_schema = tool_map["trigger_refund"].input_schema
    assert "customer_id" in refund_schema["properties"]
    assert "amount" in refund_schema["properties"]
    assert "reason" in refund_schema["properties"]


@pytest.mark.asyncio
async def test_get_customer_record_valid():
    """Verify fetching an existing customer returns full details."""
    res = await server.call_tool("get_customer_record", {"customer_id": "CUST-A1B2C"})
    assert not getattr(res, "is_error", False)
    content = getattr(res, "content", [])
    data = json.loads(getattr(content[0], "text", "{}"))
    assert data["customer_id"] == "CUST-A1B2C"
    assert data["name"] == "Jane Doe"
    assert data["tier"] == "enterprise"


@pytest.mark.asyncio
async def test_get_customer_record_invalid_patterns():
    """Verify malformed customer IDs are strictly rejected."""
    invalid_ids = ["invalid", "CUST-1", "CUST-123456", "cust-abcde", "12345", "CUST-!@#$%"]
    for bad_id in invalid_ids:
        with pytest.raises(ToolError):
            await server.call_tool("get_customer_record", {"customer_id": bad_id})


@pytest.mark.asyncio
async def test_trigger_refund_valid():
    """Verify valid refund parameters succeed."""
    res = await server.call_tool(
        "trigger_refund",
        {
            "customer_id": "CUST-98765",
            "amount": 129.50,
            "reason": "Customer requested refund due to duplicate charge.",
        },
    )
    assert not getattr(res, "is_error", False)
    content = getattr(res, "content", [])
    data = json.loads(getattr(content[0], "text", "{}"))
    assert data["customer_id"] == "CUST-98765"
    assert data["amount"] == 129.50
    assert data["status"] == "processed"
    assert data["refund_id"].startswith("ref_")


@pytest.mark.asyncio
async def test_trigger_refund_invalid_amount():
    """Verify negative or zero refund amounts fail validation."""
    for bad_amount in [-50.0, 0.0, -0.01]:
        with pytest.raises(ToolError):
            await server.call_tool(
                "trigger_refund",
                {
                    "customer_id": "CUST-98765",
                    "amount": bad_amount,
                    "reason": "Legitimate refund justification here.",
                }
            )


@pytest.mark.asyncio
async def test_trigger_refund_short_reason():
    """Verify reason strings shorter than 10 characters fail validation."""
    for bad_reason in ["too short", "123456789", ""]:
        with pytest.raises(ToolError):
            await server.call_tool(
                "trigger_refund",
                {
                    "customer_id": "CUST-98765",
                    "amount": 50.0,
                    "reason": bad_reason,
                }
            )


def test_stdio_channel_isolation():
    """Verify that when running via stdio, stdout contains only JSON-RPC and stderr receives logs."""
    # Spawn the server as a subprocess communicating over stdio
    proc = subprocess.Popen(
        [sys.executable, "-m", "src.task1_mcp_server.server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    # Handshake: initialize
    init_request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "1.0.0"}
        }
    }

    assert proc.stdin is not None, "proc.stdin must not be None"
    assert proc.stdout is not None, "proc.stdout must not be None"

    try:
        proc.stdin.write(json.dumps(init_request) + "\n")
        proc.stdin.flush()

        # Read line from stdout
        out_line = proc.stdout.readline()
        assert out_line, "Expected JSON-RPC response on stdout"

        # Verify stdout is strictly valid JSON
        parsed = json.loads(out_line)
        assert parsed.get("jsonrpc") == "2.0"
        assert parsed.get("id") == 1

        # Send notifications/initialized
        initialized_notification = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized"
        }
        proc.stdin.write(json.dumps(initialized_notification) + "\n")
        proc.stdin.flush()

        # Call tool: get_customer_record
        tool_call_request = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "get_customer_record",
                "arguments": {"customer_id": "CUST-A1B2C"}
            }
        }
        proc.stdin.write(json.dumps(tool_call_request) + "\n")
        proc.stdin.flush()

        tool_out_line = proc.stdout.readline()
        parsed_tool = json.loads(tool_out_line)
        assert parsed_tool.get("id") == 2
        assert "result" in parsed_tool

    finally:
        proc.stdin.close()
        proc.terminate()
        _, stderr_data = proc.communicate(timeout=2)

    # Assert stderr received log output
    assert "[mcp_server]" in stderr_data or "mcp" in stderr_data.lower(), (
        f"Expected logs in stderr, got: {stderr_data}"
    )
