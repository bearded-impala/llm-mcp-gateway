"""Streaming proxy endpoint with real-time PII redaction."""

import json
import logging
import os
from collections.abc import AsyncIterator

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

from src.task3_streaming_guardrail.redactor import StreamRedactor

logger = logging.getLogger("llm_streaming_guardrail")

DEFAULT_UPSTREAM_LLM_URL = os.getenv(
    "UPSTREAM_LLM_URL", "http://127.0.0.1:1234/v1/chat/completions"
)
DEFAULT_MODEL_NAME = os.getenv("DEFAULT_MODEL_NAME", "qwen3.5-0.8b")

guardrail_app = FastAPI(
    title="Streaming Guardrail Gateway",
    description="Real-time PII redaction for streaming LLM responses",
    version="1.0.0",
)


async def sse_redactor_transformer(
    upstream_response_lines: AsyncIterator[str],
    redactor: StreamRedactor,
) -> AsyncIterator[str]:
    async for line in upstream_response_lines:
        line_str = line.strip()
        if not line_str:
            continue

        if line_str == "data: [DONE]":
            final_delta = redactor.finalize()
            if final_delta:
                final_event = {
                    "id": "chatcmpl-guardrail-tail",
                    "object": "chat.completion.chunk",
                    "choices": [{"delta": {"content": final_delta}, "index": 0}],
                }
                yield f"data: {json.dumps(final_event)}\n\n"
            yield "data: [DONE]\n\n"
            break

        if line_str.startswith("data: "):
            try:
                data_obj = json.loads(line_str[6:])
                choices = data_obj.get("choices", [])
                if not choices:
                    continue

                content = choices[0].get("delta", {}).get("content", "")
                if content:
                    safe_emitted = redactor.process_chunk(content)
                    if safe_emitted:
                        data_obj["choices"][0]["delta"]["content"] = safe_emitted
                        yield f"data: {json.dumps(data_obj)}\n\n"
            except json.JSONDecodeError:
                continue

    residual = redactor.finalize()
    if residual:
        residual_event = {
            "id": "chatcmpl-guardrail-tail",
            "object": "chat.completion.chunk",
            "choices": [{"delta": {"content": residual}, "index": 0}],
        }
        yield f"data: {json.dumps(residual_event)}\n\n"


@guardrail_app.post("/v1/chat/completions")
async def proxy_chat_completions(request: Request):
    body = await request.json()
    if "model" not in body:
        body["model"] = DEFAULT_MODEL_NAME
    body["stream"] = True

    upstream_url = getattr(guardrail_app.state, "upstream_url", DEFAULT_UPSTREAM_LLM_URL)
    client: httpx.AsyncClient | None = getattr(guardrail_app.state, "http_client", None)

    async def stream_generator():
        close_client = False
        nonlocal client
        if client is None:
            client = httpx.AsyncClient(timeout=60.0)
            close_client = True

        redactor = StreamRedactor(buffer_size=48)

        try:
            req = client.build_request("POST", upstream_url, json=body)
            upstream_res = await client.send(req, stream=True)
            async for sse_chunk in sse_redactor_transformer(
                upstream_res.aiter_lines(), redactor
            ):
                yield sse_chunk
        finally:
            if close_client:
                await client.aclose()

    return StreamingResponse(
        stream_generator(),
        media_type="text/event-stream",
    )
