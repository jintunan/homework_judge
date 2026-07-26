from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterable, Mapping, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import aiosqlite

from ..config import Settings

SqlParams = Sequence[Any] | Mapping[str, Any]


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=_json_default)


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "model_dump"):
        return value.model_dump(by_alias=True)
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")


def json_loads[T](value: Any, fallback: T) -> T:
    if not isinstance(value, str) or not value:
        return fallback
    try:
        return cast(T, json.loads(value))
    except json.JSONDecodeError:
        return fallback


class Database:
    def __init__(self, settings: Settings, path: Path | None = None) -> None:
        assert settings.database_path is not None
        self.settings = settings
        self.path = path or settings.database_path

    async def connect(self) -> aiosqlite.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = await aiosqlite.connect(self.path)
        connection.row_factory = aiosqlite.Row
        await connection.execute("PRAGMA foreign_keys = ON")
        await connection.execute("PRAGMA journal_mode = WAL")
        await connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    async def fetch_one(
        self,
        sql: str,
        params: SqlParams = (),
        *,
        connection: aiosqlite.Connection | None = None,
    ) -> dict[str, Any] | None:
        owned = connection is None
        conn = connection or await self.connect()
        try:
            cursor = await conn.execute(sql, params)
            row = await cursor.fetchone()
            await cursor.close()
            return dict(row) if row is not None else None
        finally:
            if owned:
                await conn.close()

    async def fetch_all(
        self,
        sql: str,
        params: SqlParams = (),
        *,
        connection: aiosqlite.Connection | None = None,
    ) -> list[dict[str, Any]]:
        owned = connection is None
        conn = connection or await self.connect()
        try:
            cursor = await conn.execute(sql, params)
            rows = await cursor.fetchall()
            await cursor.close()
            return [dict(row) for row in rows]
        finally:
            if owned:
                await conn.close()

    async def execute(
        self,
        sql: str,
        params: SqlParams = (),
        *,
        connection: aiosqlite.Connection | None = None,
    ) -> int:
        owned = connection is None
        conn = connection or await self.connect()
        try:
            cursor = await conn.execute(sql, params)
            rowcount = cursor.rowcount
            await cursor.close()
            if owned:
                await conn.commit()
            return rowcount
        finally:
            if owned:
                await conn.close()

    async def executemany(
        self,
        sql: str,
        params: Iterable[Sequence[Any]],
        *,
        connection: aiosqlite.Connection,
    ) -> None:
        cursor = await connection.executemany(sql, params)
        await cursor.close()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        connection = await self.connect()
        try:
            await connection.execute("BEGIN IMMEDIATE")
            yield connection
            await connection.commit()
        except BaseException:
            await connection.rollback()
            raise
        finally:
            await connection.close()
