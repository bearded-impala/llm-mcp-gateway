"""Chat completions routing endpoint with rate limiting and provider fallback."""

import logging
import os
import uuid
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Header, Request, status
from fastapi.responses import JSONResponse

from src.task4_resilient_router.db import SqliteManager
from src.task4_resilient_router.fallback import (
    UpstreamProviderError,
    build_sanitized_gateway_error,
    route_with_fallback,
)
from src.task4_resilient_router.rate_limiter import RateLimitExceeded, SlidingWindowRateLimiter
from src.task4_resilient_router.tokenizer import estimate_request_tokens

logger = logging.getLogger("llm_gateway_router")

DEFAULT_PRIMARY_URL = os.getenv("PRIMARY_MODEL_URL", "http://127.0.0.1:1234/v1/chat/completions")
DEFAULT_SECONDARY_URL = os.getenv("SECONDARY_MODEL_URL", "http://127.0.0.1:1234/v1/chat/completions")
DEFAULT_MODEL_NAME = os.getenv("DEFAULT_MODEL_NAME", "qwen3.5-0.8b")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not hasattr(app.state, "db_manager"):
        db_path = os.getenv("SQLITE_DB_PATH", "data/rate_limit.db")
        app.state.db_manager = SqliteManager(db_path=db_path)
    await app.state.db_manager.init_db()

    if not hasattr(app.state, "rate_limiter"):
        app.state.rate_limiter = SlidingWindowRateLimiter(
            db_manager=app.state.db_manager,
            default_limit=int(os.getenv("RATE_LIMIT_TOKENS_PER_MIN", "50000")),
            window_seconds=60,
        )
    yield


router_app = FastAPI(
    title="LLM Gateway Router",
    description="Completion router with rate limiting and failover",
    version="1.0.0",
    lifespan=lifespan,
)


@router_app.post("/v1/chat/completions")
async def route_completion(
    request: Request,
    authorization: str | None = Header(None),
    x_api_key: str | None = Header(None),
):
    req_id = f"req_{uuid.uuid4().hex[:12]}"

    tenant_id = "default_tenant"
    if x_api_key:
        tenant_id = x_api_key
    elif authorization and authorization.startswith("Bearer "):
        tenant_id = authorization.split()[1]

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content=build_sanitized_gateway_error(
                code="invalid_request",
                message="Malformed JSON body in request",
                request_id=req_id,
            ),
        )

    if "model" not in body:
        body["model"] = DEFAULT_MODEL_NAME

    messages = body.get("messages", [])
    max_tokens = body.get("max_tokens")

    estimated_tokens = estimate_request_tokens(messages, max_tokens=max_tokens)
    limiter: SlidingWindowRateLimiter = router_app.state.rate_limiter

    try:
        reservation_id = await limiter.reserve(tenant_id, estimated_tokens)
    except RateLimitExceeded as rle:
        logger.warning("[%s] Rate limit exceeded for tenant %s: %s", req_id, tenant_id, rle)
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content=build_sanitized_gateway_error(
                code="rate_limit_exceeded",
                message="Tenant rate limit exceeded. Please retry later.",
                request_id=req_id,
                retry_after=rle.retry_after,
            ),
            headers={"Retry-After": str(rle.retry_after)},
        )

    primary_url = getattr(router_app.state, "primary_url", DEFAULT_PRIMARY_URL)
    secondary_url = getattr(router_app.state, "secondary_url", DEFAULT_SECONDARY_URL)
    client: httpx.AsyncClient | None = getattr(router_app.state, "http_client", None)

    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=30.0)
        close_client = True

    try:
        response_data, provider_used = await route_with_fallback(
            client=client,
            primary_url=primary_url,
            secondary_url=secondary_url,
            payload=body,
            request_id=req_id,
        )

        usage = response_data.get("usage", {})
        actual_tokens = usage.get("total_tokens", estimated_tokens)
        await limiter.reconcile(reservation_id, actual_tokens)

        headers = {
            "X-Gateway-Request-ID": req_id,
            "X-Gateway-Provider-Used": provider_used,
        }
        return JSONResponse(status_code=200, content=response_data, headers=headers)

    except UpstreamProviderError as err:
        await limiter.release(reservation_id)
        error_code = "upstream_timeout" if err.is_timeout else "provider_unavailable"
        return JSONResponse(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT if err.is_timeout else status.HTTP_502_BAD_GATEWAY,
            content=build_sanitized_gateway_error(
                code=error_code,
                message="Upstream model provider is currently unavailable.",
                request_id=req_id,
            ),
        )
    except Exception as exc:
        await limiter.release(reservation_id)
        logger.error("[%s] Gateway error: %s", req_id, exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=build_sanitized_gateway_error(
                code="internal_gateway_error",
                message="Internal gateway router error.",
                request_id=req_id,
            ),
        )
    finally:
        if close_client:
            await client.aclose()
