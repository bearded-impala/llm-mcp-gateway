"""Upstream provider client and fallback routing logic."""

import asyncio
import logging
import uuid
from typing import Any

import httpx

from src.common.errors import GatewayErrorDetail, StandardGatewayError

logger = logging.getLogger("model_fallback_router")

PRIMARY_TIMEOUT_SECONDS = 3.0


class UpstreamProviderError(Exception):
    def __init__(self, status_code: int, message: str, is_timeout: bool = False):
        self.status_code = status_code
        self.message = message
        self.is_timeout = is_timeout
        super().__init__(message)


async def call_provider(
    client: httpx.AsyncClient,
    url: str,
    payload: dict[str, Any],
    timeout_seconds: float,
) -> dict[str, Any]:
    try:
        response = await asyncio.wait_for(
            client.post(url, json=payload, timeout=timeout_seconds),
            timeout=timeout_seconds,
        )
        if response.status_code == 429:
            raise UpstreamProviderError(status_code=429, message="Upstream provider rate limited")
        if response.status_code >= 400:
            raise UpstreamProviderError(
                status_code=response.status_code,
                message=f"Upstream provider returned HTTP {response.status_code}",
            )
        return response.json()
    except (TimeoutError, httpx.TimeoutException) as e:
        raise UpstreamProviderError(status_code=504, message="Upstream provider timed out", is_timeout=True) from e
    except httpx.RequestError as e:
        raise UpstreamProviderError(status_code=502, message=f"Upstream connection failed: {e.__class__.__name__}") from e


async def route_with_fallback(
    client: httpx.AsyncClient,
    primary_url: str,
    secondary_url: str,
    payload: dict[str, Any],
    request_id: str | None = None,
) -> tuple[dict[str, Any], str]:
    req_id = request_id or f"req_{uuid.uuid4().hex[:12]}"
    primary_failed = False

    try:
        data = await call_provider(
            client=client,
            url=primary_url,
            payload=payload,
            timeout_seconds=PRIMARY_TIMEOUT_SECONDS,
        )
        return data, "primary"
    except UpstreamProviderError as err:
        if err.status_code == 429 or err.is_timeout or err.status_code >= 500:
            primary_failed = True
            logger.warning("[%s] Primary model unavailable (%s). Failing over to backup...", req_id, err)
        else:
            raise

    if primary_failed:
        try:
            data = await call_provider(
                client=client,
                url=secondary_url,
                payload=payload,
                timeout_seconds=10.0,
            )
            return data, "secondary"
        except UpstreamProviderError as sec_err:
            logger.error("[%s] Secondary backup model failed: %s", req_id, sec_err)
            raise UpstreamProviderError(
                status_code=503,
                message="All upstream model providers failed or timed out",
                is_timeout=sec_err.is_timeout,
            ) from sec_err

    raise UpstreamProviderError(status_code=500, message="Routing failure")


def build_sanitized_gateway_error(
    code: str, message: str, request_id: str, retry_after: int | None = None
) -> dict[str, Any]:
    payload = StandardGatewayError(
        error=GatewayErrorDetail(
            code=code,
            message=message,
            request_id=request_id,
            retry_after=retry_after,
        )
    )
    return payload.model_dump()
