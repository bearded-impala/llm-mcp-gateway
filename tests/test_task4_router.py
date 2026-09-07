"""Tests for rate limiting, model routing, and failover behavior."""

import asyncio
import os
import shutil
import tempfile

import httpx
import pytest

from src.task4_resilient_router.db import SqliteManager
from src.task4_resilient_router.rate_limiter import RateLimitExceeded, SlidingWindowRateLimiter
from src.task4_resilient_router.router import router_app


@pytest.fixture
def temp_db():
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "test_rate_limit.db")
    manager = SqliteManager(db_path=db_path)
    yield manager
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_sqlite_concurrent_reservations_no_locking(temp_db):
    """Verify that 50 concurrent async tasks execute without SQLite locking errors."""
    await temp_db.init_db()
    limiter = SlidingWindowRateLimiter(db_manager=temp_db, default_limit=100000)

    # Launch 50 concurrent tasks
    async def make_reservation(i: int):
        res_id = await limiter.reserve(tenant_id="load_test_tenant", estimated_tokens=100)
        await limiter.reconcile(res_id, actual_tokens=90)
        return res_id

    tasks = [make_reservation(i) for i in range(50)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for res in results:
        assert isinstance(res, str) and res.startswith("res_")

    usage = await limiter.get_usage("load_test_tenant")
    assert usage == 50 * 90


@pytest.mark.asyncio
async def test_rate_limiter_two_phase_lifecycle(temp_db):
    """Verify pre-request lease, reconciliation, and refund/release."""
    await temp_db.init_db()
    limiter = SlidingWindowRateLimiter(db_manager=temp_db, default_limit=1000)

    # Reserve 600 tokens
    res1 = await limiter.reserve("tenant_a", estimated_tokens=600)
    assert await limiter.get_usage("tenant_a") == 600

    # Attempting 500 more should fail (600 + 500 > 1000)
    with pytest.raises(RateLimitExceeded):
        await limiter.reserve("tenant_a", estimated_tokens=500)

    # Reconcile res1 to actual 300 tokens
    await limiter.reconcile(res1, actual_tokens=300)
    assert await limiter.get_usage("tenant_a") == 300

    # Now reserving 500 should succeed (300 + 500 <= 1000)
    res2 = await limiter.reserve("tenant_a", estimated_tokens=500)
    assert await limiter.get_usage("tenant_a") == 800

    # Release res2 (simulate failed request refund)
    await limiter.release(res2)
    assert await limiter.get_usage("tenant_a") == 300


@pytest.mark.asyncio
async def test_router_live_lm_studio_execution(temp_db):
    """Verify real completion routing and token reconciliation against LM Studio qwen3.5-0.8b."""
    await temp_db.init_db()
    router_app.state.db_manager = temp_db
    router_app.state.rate_limiter = SlidingWindowRateLimiter(db_manager=temp_db, default_limit=50000)
    router_app.state.primary_url = "http://127.0.0.1:1234/v1/chat/completions"
    router_app.state.secondary_url = "http://127.0.0.1:1234/v1/chat/completions"
    router_app.state.http_client = httpx.AsyncClient(timeout=30.0)

    transport = httpx.ASGITransport(app=router_app)
    client = httpx.AsyncClient(transport=transport, base_url="http://router")

    tenant_key = "live_test_tenant"
    res = await client.post(
        "/v1/chat/completions",
        json={
            "model": "qwen3.5-0.8b",
            "messages": [{"role": "user", "content": "Respond with the single word: OK"}],
        },
        headers={"X-API-Key": tenant_key},
    )
    assert res.status_code == 200
    data = res.json()
    assert "choices" in data
    assert res.headers.get("X-Gateway-Provider-Used") in ["primary", "secondary"]

    # Verify usage was reconciled in SQLite
    usage = await router_app.state.rate_limiter.get_usage(tenant_key)
    assert usage > 0, f"Expected usage to be recorded in SQLite, got {usage}"


@pytest.mark.asyncio
async def test_router_failover_on_primary_429(temp_db):
    """Verify automatic failover to secondary (LM Studio) when primary returns 429."""
    await temp_db.init_db()

    class FailoverTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            if "primary" in str(request.url):
                # Return 429 on primary
                return httpx.Response(status_code=429, json={"error": "Rate limit exceeded on primary"})
            # Forward secondary to live LM Studio
            import json
            body = json.loads(request.read().decode())
            async with httpx.AsyncClient(timeout=30.0) as live_client:
                return await live_client.post("http://127.0.0.1:1234/v1/chat/completions", json=body)

    router_app.state.db_manager = temp_db
    router_app.state.rate_limiter = SlidingWindowRateLimiter(db_manager=temp_db, default_limit=50000)
    router_app.state.primary_url = "http://simulated-primary/v1/chat/completions"
    router_app.state.secondary_url = "http://127.0.0.1:1234/v1/chat/completions"
    router_app.state.http_client = httpx.AsyncClient(transport=FailoverTransport())

    transport = httpx.ASGITransport(app=router_app)
    client = httpx.AsyncClient(transport=transport, base_url="http://router")

    res = await client.post(
        "/v1/chat/completions",
        json={
            "model": "qwen3.5-0.8b",
            "messages": [{"role": "user", "content": "Respond with the single word: OK"}],
        },
        headers={"X-API-Key": "fallback_tenant"},
    )
    assert res.status_code == 200
    assert res.headers["X-Gateway-Provider-Used"] == "secondary"
    data = res.json()
    assert "choices" in data


@pytest.mark.asyncio
async def test_router_failover_on_primary_timeout(temp_db):
    """Verify automatic failover to secondary (LM Studio) when primary times out (>3000ms)."""
    await temp_db.init_db()

    class TimeoutTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            if "primary" in str(request.url):
                # Sleep 3.5s to exceed the 3000ms primary timeout
                await asyncio.sleep(3.5)
                return httpx.Response(status_code=200, json={"choices": []})
            # Forward secondary to live LM Studio
            import json
            body = json.loads(request.content.decode())
            async with httpx.AsyncClient(timeout=30.0) as live_client:
                return await live_client.post("http://127.0.0.1:1234/v1/chat/completions", json=body)

    router_app.state.db_manager = temp_db
    router_app.state.rate_limiter = SlidingWindowRateLimiter(db_manager=temp_db, default_limit=50000)
    router_app.state.primary_url = "http://simulated-slow-primary/v1/chat/completions"
    router_app.state.secondary_url = "http://127.0.0.1:1234/v1/chat/completions"
    router_app.state.http_client = httpx.AsyncClient(transport=TimeoutTransport())

    transport = httpx.ASGITransport(app=router_app)
    client = httpx.AsyncClient(transport=transport, base_url="http://router")

    res = await client.post(
        "/v1/chat/completions",
        json={
            "model": "qwen3.5-0.8b",
            "messages": [{"role": "user", "content": "Respond with the single word: OK"}],
        },
        headers={"X-API-Key": "timeout_tenant"},
    )
    assert res.status_code == 200
    assert res.headers["X-Gateway-Provider-Used"] == "secondary"
    data = res.json()
    assert "choices" in data


@pytest.mark.asyncio
async def test_router_rate_limit_exceeded_error_sanitization(temp_db):
    """Verify rate limit returns standardized 429 error payload without leaking stack traces."""
    await temp_db.init_db()
    router_app.state.db_manager = temp_db
    router_app.state.rate_limiter = SlidingWindowRateLimiter(db_manager=temp_db, default_limit=50)
    router_app.state.http_client = httpx.AsyncClient()

    transport = httpx.ASGITransport(app=router_app)
    client = httpx.AsyncClient(transport=transport, base_url="http://router")

    # Send request exceeding 50 tokens
    res = await client.post(
        "/v1/chat/completions",
        json={
            "model": "qwen3.5-0.8b",
            "messages": [{"role": "user", "content": "This is a prompt that will exceed limit" * 10}],
            "max_tokens": 100,
        },
        headers={"X-API-Key": "restricted_tenant"},
    )
    assert res.status_code == 429
    data = res.json()

    # Validate sanitized gateway error schema
    assert "error" in data
    assert data["error"]["code"] == "rate_limit_exceeded"
    assert "request_id" in data["error"]
    assert "Retry-After" in res.headers
    assert "Traceback" not in res.text
