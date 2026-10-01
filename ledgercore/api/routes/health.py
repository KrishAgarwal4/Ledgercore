from fastapi import APIRouter, Depends
from typing import Dict, Any
import asyncpg
from ledgercore.api.deps import get_db

router = APIRouter(tags=["Health"])


@router.get("/health")
async def health_check(conn: asyncpg.Connection = Depends(get_db)) -> Dict[str, Any]:
    """
    Returns system health status and database connectivity.
    """
    db_version = await conn.fetchval("SELECT version();")
    account_count = await conn.fetchval("SELECT count(*) FROM accounts;")
    tx_count = await conn.fetchval("SELECT count(*) FROM transactions;")
    entries_count = await conn.fetchval("SELECT count(*) FROM ledger_entries;")

    return {
        "status": "healthy",
        "service": "LedgerCore",
        "database": {
            "connected": True,
            "version": db_version,
            "total_accounts": account_count,
            "total_transactions": tx_count,
            "total_ledger_entries": entries_count,
        }
    }
