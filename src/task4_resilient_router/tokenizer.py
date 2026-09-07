"""Token estimation helper for rate limit checks."""

from collections.abc import Sequence
from typing import Any

DEFAULT_ESTIMATED_COMPLETION_TOKENS = 500


def estimate_prompt_tokens(messages: Sequence[dict[str, Any]]) -> int:
    total_chars = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total_chars += len(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and "text" in part:
                    total_chars += len(part["text"])

    return max(1, (total_chars // 4) + (len(messages) * 4) + 2)


def estimate_request_tokens(
    messages: Sequence[dict[str, Any]], max_tokens: int | None = None
) -> int:
    prompt_tokens = estimate_prompt_tokens(messages)
    completion_budget = max_tokens if (max_tokens and max_tokens > 0) else DEFAULT_ESTIMATED_COMPLETION_TOKENS
    return prompt_tokens + completion_budget
