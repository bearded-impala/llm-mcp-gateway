"""Sliding window token rate limiter with SQLite persistence."""

import time
import uuid

from src.task4_resilient_router.db import SqliteManager


class RateLimitExceeded(Exception):
    def __init__(self, tenant_id: str, current: int, attempted: int, limit: int, retry_after: int = 5):
        self.tenant_id = tenant_id
        self.current = current
        self.attempted = attempted
        self.limit = limit
        self.retry_after = retry_after
        super().__init__(
            f"Rate limit exceeded for tenant '{tenant_id}': "
            f"{current + attempted}/{limit} tokens in window. Retry after {retry_after}s."
        )


class SlidingWindowRateLimiter:
    def __init__(self, db_manager: SqliteManager, default_limit: int = 50000, window_seconds: int = 60):
        self.db = db_manager
        self.default_limit = default_limit
        self.window_seconds = window_seconds

    async def get_usage(self, tenant_id: str) -> int:
        cutoff = time.time() - self.window_seconds
        conn = await self.db.get_connection()
        try:
            async with conn.execute(
                """
                SELECT COALESCE(SUM(COALESCE(actual_tokens, estimated_tokens)), 0)
                FROM token_ledger
                WHERE tenant_id = ?
                  AND timestamp > ?
                  AND status IN ('reserved', 'completed');
                """,
                (tenant_id, cutoff),
            ) as cursor:
                row = await cursor.fetchone()
                return int(row[0]) if row else 0
        finally:
            await conn.close()

    async def reserve(self, tenant_id: str, estimated_tokens: int, custom_limit: int | None = None) -> str:
        limit = custom_limit if custom_limit is not None else self.default_limit
        reservation_id = f"res_{uuid.uuid4().hex[:12]}"
        now = time.time()
        cutoff = now - self.window_seconds

        async with self.db.write_lock:
            conn = await self.db.get_connection()
            try:
                async with conn.execute(
                    """
                    SELECT COALESCE(SUM(COALESCE(actual_tokens, estimated_tokens)), 0)
                    FROM token_ledger
                    WHERE tenant_id = ?
                      AND timestamp > ?
                      AND status IN ('reserved', 'completed');
                    """,
                    (tenant_id, cutoff),
                ) as cursor:
                    row = await cursor.fetchone()
                    current_usage = int(row[0]) if row else 0

                if current_usage + estimated_tokens > limit:
                    raise RateLimitExceeded(
                        tenant_id=tenant_id,
                        current=current_usage,
                        attempted=estimated_tokens,
                        limit=limit,
                        retry_after=max(1, int(self.window_seconds - (now - cutoff))),
                    )

                await conn.execute(
                    """
                    INSERT INTO token_ledger (id, tenant_id, timestamp, estimated_tokens, actual_tokens, status)
                    VALUES (?, ?, ?, ?, NULL, 'reserved');
                    """,
                    (reservation_id, tenant_id, now, estimated_tokens),
                )
                await conn.commit()
                return reservation_id
            finally:
                await conn.close()

    async def reconcile(self, reservation_id: str, actual_tokens: int) -> None:
        async with self.db.write_lock:
            conn = await self.db.get_connection()
            try:
                await conn.execute(
                    """
                    UPDATE token_ledger
                    SET actual_tokens = ?, status = 'completed'
                    WHERE id = ?;
                    """,
                    (actual_tokens, reservation_id),
                )
                await conn.commit()
            finally:
                await conn.close()

    async def release(self, reservation_id: str) -> None:
        async with self.db.write_lock:
            conn = await self.db.get_connection()
            try:
                await conn.execute(
                    """
                    UPDATE token_ledger
                    SET actual_tokens = 0, status = 'released'
                    WHERE id = ?;
                    """,
                    (reservation_id,),
                )
                await conn.commit()
            finally:
                await conn.close()

    async def evict_expired(self, retention_seconds: int = 3600) -> int:
        cutoff = time.time() - retention_seconds
        async with self.db.write_lock:
            conn = await self.db.get_connection()
            try:
                cursor = await conn.execute(
                    "DELETE FROM token_ledger WHERE timestamp < ?;",
                    (cutoff,),
                )
                await conn.commit()
                return cursor.rowcount
            finally:
                await conn.close()
