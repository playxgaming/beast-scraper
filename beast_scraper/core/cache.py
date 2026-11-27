"""Async SQLite response cache with TTL expiration."""

import hashlib
import json
import time
from pathlib import Path
from typing import Any

import aiosqlite


class AsyncSQLiteCache:
    """Lightweight persistent response cache backed by SQLite."""

    def __init__(self, db_path: Path, default_ttl: int = 3600) -> None:
        self.db_path = Path(db_path)
        self.default_ttl = default_ttl
        self._conn: aiosqlite.Connection | None = None

    async def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.db_path)
        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS response_cache (
                cache_key TEXT PRIMARY KEY,
                namespace TEXT NOT NULL,
                payload TEXT NOT NULL,
                expires_at REAL NOT NULL,
                created_at REAL NOT NULL
            )
            """
        )
        await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_cache_expires ON response_cache(expires_at)")
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @staticmethod
    def make_key(namespace: str, params: dict[str, Any]) -> str:
        raw = json.dumps({"ns": namespace, "p": params}, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    async def get(self, namespace: str, params: dict[str, Any]) -> Any | None:
        if self._conn is None:
            return None
        key = self.make_key(namespace, params)
        now = time.time()
        async with self._conn.execute(
            "SELECT payload, expires_at FROM response_cache WHERE cache_key = ?",
            (key,),
        ) as cursor:
            row = await cursor.fetchone()
        if not row:
            return None
        payload, expires_at = row
        if expires_at < now:
            await self._conn.execute("DELETE FROM response_cache WHERE cache_key = ?", (key,))
            await self._conn.commit()
            return None
        return json.loads(payload)

    async def set(
        self,
        namespace: str,
        params: dict[str, Any],
        value: Any,
        ttl: int | None = None,
    ) -> None:
        if self._conn is None:
            return
        ttl_sec = self.default_ttl if ttl is None else ttl
        if ttl_sec <= 0:
            return
        key = self.make_key(namespace, params)
        now = time.time()
        payload = json.dumps(value, default=str)
        if len(payload.encode("utf-8")) > 1024 * 1024:
            return
        await self._conn.execute("DELETE FROM response_cache WHERE expires_at < ?", (now,))
        await self._conn.execute(
            """
            INSERT INTO response_cache (cache_key, namespace, payload, expires_at, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET
                payload = excluded.payload,
                expires_at = excluded.expires_at,
                created_at = excluded.created_at
            """,
            (key, namespace, payload, now + ttl_sec, now),
        )
        await self._conn.execute(
            "DELETE FROM response_cache WHERE cache_key NOT IN "
            "(SELECT cache_key FROM response_cache ORDER BY created_at DESC LIMIT 256)"
        )
        await self._conn.commit()

    async def clear(self, namespace: str | None = None) -> int:
        if self._conn is None:
            return 0
        if namespace:
            cur = await self._conn.execute("DELETE FROM response_cache WHERE namespace = ?", (namespace,))
        else:
            cur = await self._conn.execute("DELETE FROM response_cache")
        await self._conn.commit()
        return cur.rowcount

    async def stats(self) -> dict[str, int]:
        if self._conn is None:
            return {"entries": 0, "active_entries": 0}
        now = time.time()
        async with self._conn.execute("SELECT COUNT(*) FROM response_cache") as c1:
            total = (await c1.fetchone())[0]
        async with self._conn.execute("SELECT COUNT(*) FROM response_cache WHERE expires_at >= ?", (now,)) as c2:
            active = (await c2.fetchone())[0]
        return {"entries": total, "active_entries": active}
