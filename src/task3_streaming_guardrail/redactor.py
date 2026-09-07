"""Streaming text redactor with lookback buffering for chunk boundaries."""

import re
from collections.abc import AsyncIterator

EMAIL_REGEX = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
SSN_REGEX = re.compile(r"\b\d{3}-\d{2}-\d{4}\b|\b\d{9}\b")
CREDIT_CARD_REGEX = re.compile(r"\b(?:\d{4}[-\s]?){3}\d{4}\b")

SENSITIVE_PATTERNS = [
    EMAIL_REGEX,
    SSN_REGEX,
    CREDIT_CARD_REGEX,
]

REDACTION_TOKEN = "[REDACTED]"
DEFAULT_BUFFER_SIZE = 48


class StreamRedactor:
    """Buffers a trailing suffix across streaming chunks to prevent partial PII leaks."""

    def __init__(self, buffer_size: int = DEFAULT_BUFFER_SIZE):
        self.buffer_size = buffer_size
        self._buffer: str = ""

    def _redact(self, text: str) -> str:
        for pattern in SENSITIVE_PATTERNS:
            text = pattern.sub(REDACTION_TOKEN, text)
        return text

    def process_chunk(self, chunk: str) -> str:
        if not chunk:
            return ""

        combined = self._buffer + chunk
        redacted = self._redact(combined)

        if len(redacted) > self.buffer_size:
            emit_len = len(redacted) - self.buffer_size
            to_emit = redacted[:emit_len]
            self._buffer = redacted[emit_len:]
            return to_emit

        self._buffer = redacted
        return ""

    def finalize(self) -> str:
        if not self._buffer:
            return ""
        final_text = self._redact(self._buffer)
        self._buffer = ""
        return final_text

    async def redact_stream(self, stream: AsyncIterator[str]) -> AsyncIterator[str]:
        async for chunk in stream:
            emitted = self.process_chunk(chunk)
            if emitted:
                yield emitted

        tail = self.finalize()
        if tail:
            yield tail
