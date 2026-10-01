import os
import pytest
import pytest_asyncio
import asyncpg
from httpx import AsyncClient, ASGITransport
from typing import AsyncGenerator

from ledgercore.config import settings
from ledgercore.db import init_db_pool, close_db_pool, apply_schema
from ledgercore.api.app import app

TEST_DB_URL = os.getenv("TEST_DATABASE_URL", "postgresql://localhost:5432/ledgercore_test")


@pytest_asyncio.fixture(scope="function")
async def db_pool() -> AsyncGenerator[asyncpg.Pool, None]:
    pool = await init_db_pool(TEST_DB_URL)
    async with pool.acquire() as conn:
        await apply_schema(conn)
        await conn.execute("TRUNCATE accounts, transactions, idempotency_records CASCADE;")
    yield pool
    await close_db_pool()


@pytest_asyncio.fixture(scope="function")
async def db_conn(db_pool: asyncpg.Pool) -> AsyncGenerator[asyncpg.Connection, None]:
    async with db_pool.acquire() as conn:
        yield conn


@pytest_asyncio.fixture(scope="function")
async def client(db_pool: asyncpg.Pool) -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
