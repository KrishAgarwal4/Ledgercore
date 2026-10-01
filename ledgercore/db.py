import asyncpg
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional
from pathlib import Path
from ledgercore.config import settings

logger = logging.getLogger("ledgercore.db")

_pool: Optional[asyncpg.Pool] = None


async def init_db_pool(database_url: Optional[str] = None) -> asyncpg.Pool:
    """
    Initialize the global AsyncPG connection pool.
    """
    global _pool
    target_url = database_url or settings.DATABASE_URL

    # Normalize url if sqlalchemy-style postgresql+asyncpg://
    if target_url.startswith("postgresql+asyncpg://"):
        target_url = target_url.replace("postgresql+asyncpg://", "postgresql://")

    logger.info(f"Initializing AsyncPG pool with min={settings.DB_POOL_MIN_SIZE}, max={settings.DB_POOL_MAX_SIZE}")
    
    _pool = await asyncpg.create_pool(
        dsn=target_url,
        min_size=settings.DB_POOL_MIN_SIZE,
        max_size=settings.DB_POOL_MAX_SIZE,
        command_timeout=settings.DB_COMMAND_TIMEOUT_SEC,
    )
    return _pool


async def close_db_pool() -> None:
    """
    Gracefully close all connections in the pool.
    """
    global _pool
    if _pool:
        logger.info("Closing AsyncPG pool...")
        await _pool.close()
        _pool = None


def get_db_pool() -> asyncpg.Pool:
    """
    Retrieve the current connection pool instance.
    """
    if _pool is None:
        raise RuntimeError("Database connection pool is not initialized. Call init_db_pool() first.")
    return _pool


@asynccontextmanager
async def get_connection() -> AsyncGenerator[asyncpg.Connection, None]:
    """
    Acquire a connection from the pool and return it when done.
    """
    pool = get_db_pool()
    async with pool.acquire() as connection:
        yield connection


@asynccontextmanager
async def transaction(isolation: str = "read_committed") -> AsyncGenerator[asyncpg.Connection, None]:
    """
    Acquire a connection and begin an explicit transaction block.
    Rolls back automatically on unhandled exceptions and commits on clean exit.
    """
    pool = get_db_pool()
    async with pool.acquire() as conn:
        tx = conn.transaction(isolation=isolation)
        await tx.start()
        try:
            yield conn
            await tx.commit()
        except Exception:
            await tx.rollback()
            raise


async def apply_schema(connection: asyncpg.Connection) -> None:
    """
    Apply db/schema.sql to the database. Useful for initial setup or testing.
    """
    schema_path = Path(__file__).resolve().parent.parent / "db" / "schema.sql"
    if not schema_path.exists():
        raise FileNotFoundError(f"Schema file not found at {schema_path}")
    
    sql = schema_path.read_text(encoding="utf-8")
    await connection.execute(sql)
