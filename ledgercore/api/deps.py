from typing import AsyncGenerator
import asyncpg
from ledgercore.db import get_connection, transaction


async def get_db() -> AsyncGenerator[asyncpg.Connection, None]:
    async with get_connection() as conn:
        yield conn


async def get_tx() -> AsyncGenerator[asyncpg.Connection, None]:
    async with transaction() as conn:
        yield conn
