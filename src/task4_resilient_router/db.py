"""SQLite database management with WAL mode for rate limit tracking."""

import asyncio
import os
from pathlib import Path

import aiosqlite

DEFAULT_DB_PATH = os.getenv("SQLITE_DB_PATH", "data/rate_limit.db")


class SqliteManager:
    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        self._write_lock = asyncio.Lock()
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

    async def init_db(self) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode = WAL;")
            await db.execute("PRAGMA synchronous = NORMAL;")
            await db.execute("PRAGMA busy_timeout = 5000;")
            await db.execute("PRAGMA temp_store = MEMORY;")

            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS token_ledger (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    timestamp REAL NOT NULL,
                    estimated_tokens INTEGER NOT NULL,
                    actual_tokens INTEGER DEFAULT NULL,
                    status TEXT NOT NULL
                );
                """
            )

            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_tenant_timestamp_status
                ON token_ledger(tenant_id, timestamp, status);
                """
            )
            await db.commit()

    async def get_connection(self) -> aiosqlite.Connection:
        conn = await aiosqlite.connect(self.db_path)
        await conn.execute("PRAGMA busy_timeout = 5000;")
        return conn

    @property
    def write_lock(self) -> asyncio.Lock:
        return self._write_lock
