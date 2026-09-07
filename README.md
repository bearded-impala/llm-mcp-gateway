# LLM & MCP Gateways

1. **MCP Server**: Stdio transport with Pydantic validation and stderr-only logging.
2. **MCP Security Gateway**: HTTP reverse proxy with Bearer auth, transparent `tools/list`, and `-32001` checks on `admin_*` calls.
3. **Streaming Guardrail**: Real-time SSE proxy redacting PII across chunk boundaries with a sliding lookback buffer.
4. **Resilient Model Router**: Token-aware sliding window limiter backed by SQLite (WAL mode) with 3s timeout / 429 failover.

---

## Prerequisites

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/)

### LM Studio Setup (Tasks 3 & 4)

Tasks 3 and 4 route completions through a local LLM instance.

1. Install [LM Studio](https://lmstudio.ai/).
2. Download `qwen3.5-0.8b`.
3. Open the **Local Server** tab, select `qwen3.5-0.8b`, and start the server on port `1234`.
4. Verify the server is responding:
   ```bash
   curl http://127.0.0.1:1234/v1/models
   ```

---

## Setup

```bash
uv sync
```

---

## Running & Verification

### Task 1: MCP Server (Stdio Transport)

Implementation: `src/task1_mcp_server/`

- Exposes `get_customer_record` (`CUST-XXXXX`) and `trigger_refund` (positive amount, reason >= 10 chars).
- stdout is reserved strictly for JSON-RPC messages; all logging goes to stderr.

Run directly:
```bash
uv run python -m src.task1_mcp_server.server
```

Run tests:
```bash
uv run pytest tests/test_task1_server.py -v
```

---

### Task 2: MCP Security Gateway Proxy

Implementation: `src/task2_security_gateway/`

- **Port 8001 (Internal)**: Downstream MCP server exposing standard and administrative tools.
- **Port 8000 (Public Gateway)**: Validates Bearer tokens. Forwards `tools/list` transparently (preserving client prompt caching). Intercepts `tools/call` for `admin_*` tools and returns `-32001 Unauthorized Tool Call` if caller is not an admin.

Start downstream server (Terminal 1):
```bash
uv run uvicorn src.task2_security_gateway.downstream_server:downstream_app --port 8001
```

Start gateway proxy (Terminal 2):
```bash
uv run uvicorn src.task2_security_gateway.gateway:gateway_app --port 8000
```

Test with curl:
```bash
# 1. tools/list works for viewer (transparent pass-through)
curl -X POST http://127.0.0.1:8000/mcp -H "Authorization: Bearer viewer-readonly-token-abc" -H "Content-Type: application/json" -d "{\"jsonrpc\": \"2.0\", \"id\": 1, \"method\": \"tools/list\"}"

# 2. admin_* tool call is blocked for viewer (intercepted at :8000 with -32001)
curl -X POST http://127.0.0.1:8000/mcp -H "Authorization: Bearer viewer-readonly-token-abc" -H "Content-Type: application/json" -d "{\"jsonrpc\": \"2.0\", \"id\": 2, \"method\": \"tools/call\", \"params\": {\"name\": \"admin_reset_key\", \"arguments\": {\"key_id\": \"test\"}}}"

# 3. admin_* tool call succeeds for admin (forwarded to :8001)
curl -X POST http://127.0.0.1:8000/mcp -H "Authorization: Bearer admin-secret-token-xyz" -H "Content-Type: application/json" -d "{\"jsonrpc\": \"2.0\", \"id\": 3, \"method\": \"tools/call\", \"params\": {\"name\": \"admin_reset_key\", \"arguments\": {\"key_id\": \"test\"}}}"
```

Run tests:
```bash
uv run pytest tests/test_task2_gateway.py -v
```

---

### Task 3: Streaming Guardrail (PII Redaction)

Implementation: `src/task3_streaming_guardrail/`

- Intercepts streaming SSE deltas in real time.
- Uses a 48-character sliding suffix buffer so PII split across chunk boundaries (emails, SSNs, credit cards) is redacted without buffering the entire response in memory.

Start guardrail proxy (Terminal 3):
```bash
uv run uvicorn src.task3_streaming_guardrail.guardrail:guardrail_app --port 8002
```

Test streaming with curl:
```bash
curl -N -X POST http://127.0.0.1:8002/v1/chat/completions -H "Content-Type: application/json" -d "{\"model\": \"qwen3.5-0.8b\", \"messages\": [{\"role\": \"user\", \"content\": \"Echo this back: My SSN is 123-45-6789 and card is 4111-2222-3333-4444.\"}], \"stream\": true}"
```

Run tests:
```bash
uv run pytest tests/test_task3_guardrail.py -v
```

---

### Task 4: Resilient Router & Rate Limiter

Implementation: `src/task4_resilient_router/`

- Tracks token usage per tenant using a 60-second sliding window stored in SQLite.
- Uses WAL mode (`PRAGMA journal_mode = WAL`) and `PRAGMA busy_timeout = 5000` to prevent write lock contention under async load.
- Two-phase token lease: speculative reservation before dispatch $\to$ reconciliation with actual token usage after generation $\to$ rollback on failure.
- Automatically fails over to the secondary provider if the primary returns 429 or exceeds 3000ms.
- Standardized error format (`rate_limit_exceeded`, `upstream_timeout`) without leaking internal topology.

Start router (Terminal 4):
```bash
uv run uvicorn src.task4_resilient_router.router:router_app --port 8003
```

Test with curl:
```bash
curl -i -X POST http://127.0.0.1:8003/v1/chat/completions -H "X-API-Key: tenant_alpha" -H "Content-Type: application/json" -d "{\"model\": \"qwen3.5-0.8b\", \"messages\": [{\"role\": \"user\", \"content\": \"Say hello!\"}]}"
```

Inspect database ledger:
```bash
uv run python -c "import sqlite3; con = sqlite3.connect('data/rate_limit.db'); print(con.execute('SELECT * FROM token_ledger').fetchall())"
```

Run tests:
```bash
uv run pytest tests/test_task4_router.py -v
```

---

## Running All Tests & Linters

```bash
# Run tests
uv run pytest tests/ -v

# Type check
uv run pyright

# Lint
uv run ruff check .
```
