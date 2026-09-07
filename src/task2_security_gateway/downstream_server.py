"""Downstream MCP HTTP server for Task 2 Security Gateway.

Provides standard JSON-RPC endpoints for tools/list and tools/call.
"""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

downstream_app = FastAPI(title="Downstream MCP Server")

# Execution audit log
downstream_execution_log: list[dict[str, Any]] = []

TOOLS_CATALOG = [
    {
        "name": "get_customer_record",
        "description": "Standard tool accessible to all authenticated roles.",
        "inputSchema": {
            "type": "object",
            "properties": {"customer_id": {"type": "string"}},
            "required": ["customer_id"],
        },
    },
    {
        "name": "admin_reset_key",
        "description": "Privileged tool requiring admin role.",
        "inputSchema": {
            "type": "object",
            "properties": {"key_id": {"type": "string"}},
            "required": ["key_id"],
        },
    },
    {
        "name": "admin_wipe_cache",
        "description": "Privileged tool requiring admin role.",
        "inputSchema": {
            "type": "object",
            "properties": {"namespace": {"type": "string"}},
            "required": ["namespace"],
        },
    },
]


@downstream_app.post("/")
@downstream_app.post("/mcp")
async def handle_mcp_rpc(request: Request):
    payload = await request.json()
    downstream_execution_log.append(payload)

    rpc_id = payload.get("id")
    method = payload.get("method")
    params = payload.get("params", {})

    if method == "tools/list":
        return JSONResponse(
            content={
                "jsonrpc": "2.0",
                "id": rpc_id,
                "result": {"tools": TOOLS_CATALOG},
            }
        )

    if method == "tools/call":
        tool_name = params.get("name")
        args = params.get("arguments", {})

        if tool_name == "get_customer_record":
            return JSONResponse(
                content={
                    "jsonrpc": "2.0",
                    "id": rpc_id,
                    "result": {
                        "content": [
                            {"type": "text", "text": f"Customer record for {args.get('customer_id')}"}
                        ]
                    },
                }
            )

        if tool_name == "admin_reset_key":
            return JSONResponse(
                content={
                    "jsonrpc": "2.0",
                    "id": rpc_id,
                    "result": {
                        "content": [
                            {"type": "text", "text": f"Successfully reset key {args.get('key_id')}"}
                        ]
                    },
                }
            )

        return JSONResponse(
            content={
                "jsonrpc": "2.0",
                "id": rpc_id,
                "error": {"code": -32601, "message": f"Tool '{tool_name}' not found"},
            }
        )

    return JSONResponse(
        content={
            "jsonrpc": "2.0",
            "id": rpc_id,
            "error": {"code": -32601, "message": f"Method '{method}' not implemented"},
        }
    )
