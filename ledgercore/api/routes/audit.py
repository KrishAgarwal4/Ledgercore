from uuid import UUID
from fastapi import APIRouter, Depends
import asyncpg

from ledgercore.api.deps import get_db
from ledgercore.models.audit import AccountAuditResponse, ReconciliationReport
from ledgercore.services.audit_service import AuditService

router = APIRouter(prefix="/audit", tags=["Audit & Integrity"])


@router.get("/accounts/{account_id}", response_model=AccountAuditResponse)
async def get_account_audit_trail(
    account_id: UUID,
    conn: asyncpg.Connection = Depends(get_db)
) -> AccountAuditResponse:
    """
    Computes point-in-time running balances using SQL Window Functions.
    Reconstructs the cumulative historical balance for every journal entry.
    """
    return await AuditService.get_account_audit_trail(conn, account_id)


@router.post("/reconcile", response_model=ReconciliationReport)
async def run_system_reconciliation(
    conn: asyncpg.Connection = Depends(get_db)
) -> ReconciliationReport:
    """
    Performs system-wide continuous integrity audit.
    Compares the O(1) materialized balance against the raw O(N) append-only ledger entries
    for every account in the system and reports any discrepancies.
    """
    return await AuditService.reconcile_all_accounts(conn)
