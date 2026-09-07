"""MCP security gateway reverse proxy with method-level authorization."""

import logging
import os
from typing import Any

import httpx
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src.task2_security_gateway.auth import UserContext, get_current_user

logger = logging.getLogger("mcp_security_gateway")

DEFAULT_DOWNSTREAM_URL = os.getenv("DOWNSTREAM_MCP_URL", "http://127.0.0.1:8001/mcp")

gateway_app = FastAPI(
    title="MCP Security Gateway Proxy",
    description="Reverse proxy enforcing role-based tool access control",
    version="1.0.0",
)


class JsonRpcRequest(BaseModel):
    jsonrpc: str = Field(..., pattern=r"^2\.0$")
    id: Any | None = None
    method: str
    params: dict[str, Any] | None = None


def make_jsonrpc_error(rpc_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "error": {
            "code": code,
            "message": message,
        },
    }
    if data is not None:
        payload["error"]["data"] = data
    return payload


@gateway_app.post("/")
@gateway_app.post("/mcp")
async def proxy_mcp_request(
    request: Request,
    user: UserContext = Depends(get_current_user),
):
    try:
        raw_body = await request.json()
    except Exception as e:
        return JSONResponse(
            status_code=200,
            content=make_jsonrpc_error(None, -32700, f"Parse error: {e}"),
        )

    if not isinstance(raw_body, dict) or raw_body.get("jsonrpc") != "2.0" or "method" not in raw_body:
        rpc_id = raw_body.get("id") if isinstance(raw_body, dict) else None
        return JSONResponse(
            status_code=200,
            content=make_jsonrpc_error(rpc_id, -32600, "Invalid Request: expected JSON-RPC 2.0 object"),
        )

    rpc_id = raw_body.get("id")
    method = raw_body.get("method")
    params = raw_body.get("params") or {}

    # tools/list is forwarded transparently
    if method == "tools/list":
        return await forward_to_downstream(raw_body, request)

    # Enforce role restriction for admin tools
    if method == "tools/call":
        tool_name = params.get("name", "") if isinstance(params, dict) else ""
        if tool_name.startswith("admin_") and user.role != "admin":
            logger.warning("Denied access to %s for user %s (role: %s)", tool_name, user.token, user.role)
            return JSONResponse(
                status_code=200,
                content=make_jsonrpc_error(
                    rpc_id=rpc_id,
                    code=-32001,
                    message="Unauthorized Tool Call",
                    data={"tool": tool_name, "required_role": "admin", "user_role": user.role},
                ),
            )

    return await forward_to_downstream(raw_body, request)


async def forward_to_downstream(payload: dict[str, Any], original_request: Request) -> JSONResponse:
    downstream_url = getattr(gateway_app.state, "downstream_url", DEFAULT_DOWNSTREAM_URL)
    client: httpx.AsyncClient | None = getattr(gateway_app.state, "http_client", None)

    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=10.0)
        close_client = True

    try:
        response = await client.post(
            downstream_url,
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        return JSONResponse(
            status_code=response.status_code,
            content=response.json(),
        )
    except httpx.RequestError as exc:
        logger.error("Downstream request failed: %s", exc)
        return JSONResponse(
            status_code=200,
            content=make_jsonrpc_error(
                payload.get("id"),
                -32603,
                f"Internal error: downstream server unavailable ({exc.__class__.__name__})",
            ),
        )
    finally:
        if close_client:
            await client.aclose()
