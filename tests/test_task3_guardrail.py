"""Tests for streaming PII redactor and SSE proxy."""

import json

import httpx
import pytest

from src.task3_streaming_guardrail.guardrail import guardrail_app
from src.task3_streaming_guardrail.redactor import REDACTION_TOKEN, StreamRedactor


@pytest.mark.asyncio
async def test_redactor_clean_stream():
    """Verify clean text passes through properly."""
    redactor = StreamRedactor(buffer_size=30)
    chunks = ["Hello ", "world, ", "this is ", "a completely ", "safe response."]
    output = []
    for chunk in chunks:
        out = redactor.process_chunk(chunk)
        if out:
            output.append(out)
    tail = redactor.finalize()
    if tail:
        output.append(tail)

    full_text = "".join(output)
    assert full_text == "".join(chunks)


@pytest.mark.asyncio
async def test_redactor_ssn_split_across_chunks():
    """Verify SSN split across 3 chunks is cleanly redacted without leaking."""
    redactor = StreamRedactor(buffer_size=40)
    # Split: '123-' | '45-' | '6789'
    chunks = ["My SSN is ", "123-", "45-", "6789", ", please keep it secret."]
    output = []
    for chunk in chunks:
        out = redactor.process_chunk(chunk)
        if out:
            output.append(out)
    tail = redactor.finalize()
    if tail:
        output.append(tail)

    reconstructed = "".join(output)
    assert "123-45-6789" not in reconstructed
    assert f"My SSN is {REDACTION_TOKEN}, please keep it secret." in reconstructed


@pytest.mark.asyncio
async def test_redactor_email_split_across_chunks():
    """Verify email address split across chunks is cleanly redacted."""
    redactor = StreamRedactor(buffer_size=40)
    chunks = ["Contact me at ", "john.doe", "@enterprise-cloud", ".com for help."]
    output = []
    for chunk in chunks:
        out = redactor.process_chunk(chunk)
        if out:
            output.append(out)
    tail = redactor.finalize()
    if tail:
        output.append(tail)

    reconstructed = "".join(output)
    assert "john.doe@enterprise-cloud.com" not in reconstructed
    assert f"Contact me at {REDACTION_TOKEN} for help." in reconstructed


@pytest.mark.asyncio
async def test_redactor_credit_card_split_across_chunks():
    """Verify credit card split across 4 chunks is redacted."""
    redactor = StreamRedactor(buffer_size=48)
    chunks = ["Card number: ", "4111-", "2222-", "3333-", "4444", " expired."]
    output = []
    for chunk in chunks:
        out = redactor.process_chunk(chunk)
        if out:
            output.append(out)
    tail = redactor.finalize()
    if tail:
        output.append(tail)

    reconstructed = "".join(output)
    assert "4111-2222-3333-4444" not in reconstructed
    assert f"Card number: {REDACTION_TOKEN} expired." in reconstructed


@pytest.mark.asyncio
async def test_redactor_memory_bounded_invariant():
    """Verify buffer size never grows beyond O(K) regardless of stream volume."""
    k = 48
    redactor = StreamRedactor(buffer_size=k)
    # Stream 5,000 chunks
    for i in range(5000):
        redactor.process_chunk(f"word{i} ")
        assert len(redactor._buffer) <= k, f"Buffer invariant violated: {len(redactor._buffer)} > {k}"
    redactor.finalize()
    assert len(redactor._buffer) == 0


@pytest.mark.asyncio
async def test_streaming_proxy_end_to_end_with_lm_studio():
    """End-to-end test verifying full SSE pipeline redacts live stream from LM Studio."""
    guardrail_app.state.upstream_url = "http://127.0.0.1:1234/v1/chat/completions"
    guardrail_app.state.http_client = httpx.AsyncClient(timeout=30.0)

    guardrail_transport = httpx.ASGITransport(app=guardrail_app)
    client = httpx.AsyncClient(transport=guardrail_transport, base_url="http://guardrail")

    prompt = "Please output exactly: Contact: admin.support@corp.internal, SSN: 321-65-4321, Card: 4111-2222-3333-4444. Done."

    received_tokens = []
    first_chunk_received = False

    async with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "qwen3.5-0.8b",
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
        },
    ) as response:
        assert response.status_code == 200
        async for line in response.aiter_lines():
            line_str = line.strip()
            if not line_str or line_str == "data: [DONE]":
                continue
            if line_str.startswith("data: "):
                data = json.loads(line_str[6:])
                choices = data.get("choices", [])
                if choices:
                    content = choices[0]["delta"].get("content", "")
                    if content:
                        first_chunk_received = True
                        received_tokens.append(content)

    full_output = "".join(received_tokens)

    # Verify real-time reception
    assert first_chunk_received
    # Verify no raw PII leaked
    assert "admin.support@corp.internal" not in full_output
    assert "321-65-4321" not in full_output
    assert "4111-2222-3333-4444" not in full_output

    # Verify redactions are present
    assert REDACTION_TOKEN in full_output
