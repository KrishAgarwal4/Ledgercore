from typing import List, Optional
from uuid import UUID
from fastapi import APIRouter, Header, Response, Query, Depends, status
import asyncpg

from ledgercore.db import transaction, get_connection
from ledgercore.api.deps import get_db
from ledgercore.models.transaction import TransactionCreate, TransactionResponse
from ledgercore.services.ledger_service import LedgerService
from ledgercore.services.idempotency_service import IdempotencyService, compute_request_hash

router = APIRouter(prefix="/transactions", tags=["Transactions"])


@router.post("", response_model=TransactionResponse, status_code=status.HTTP_201_CREATED)
async def create_transaction(
    tx_in: TransactionCreate,
    response: Response,
    idempotency_key_header: Optional[str] = Header(None, alias="Idempotency-Key")
) -> TransactionResponse:
    """
    Posts an arbitrary multi-legged double-entry transaction.
    Must contain at least 2 legs whose total DEBIT sum equals total CREDIT sum.
    """
    effective_idempotency_key = idempotency_key_header or tx_in.idempotency_key
    if effective_idempotency_key:
        tx_in.idempotency_key = effective_idempotency_key

    req_hash = None
    if effective_idempotency_key:
        req_hash = compute_request_hash("POST", "/v1/transactions", tx_in.model_dump(mode="json"))

    async with transaction() as conn:
        if effective_idempotency_key:
            cached = await IdempotencyService.process_or_reserve_key(
                conn, effective_idempotency_key, req_hash
            )
            if cached:
                cached_status, cached_body = cached
                response.status_code = cached_status
                response.headers["X-Idempotent-Replay"] = "true"
                return TransactionResponse(**cached_body)

        try:
            tx_res = await LedgerService.post_transaction(conn, tx_in)
            if effective_idempotency_key:
                await IdempotencyService.record_success(
                    conn,
                    effective_idempotency_key,
                    status.HTTP_201_CREATED,
                    tx_res.model_dump(mode="json")
                )
            return tx_res
        except Exception:
            if effective_idempotency_key:
                await IdempotencyService.record_failure(conn, effective_idempotency_key)
            raise


@router.get("", response_model=List[TransactionResponse])
async def list_transactions(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    conn: asyncpg.Connection = Depends(get_db)
) -> List[TransactionResponse]:
    """
    Returns posted transactions and their corresponding ledger entries in reverse chronological order.
    """
    return await LedgerService.list_transactions(conn, limit=limit, offset=offset)


@router.get("/{tx_id}", response_model=TransactionResponse)
async def get_transaction(
    tx_id: UUID,
    conn: asyncpg.Connection = Depends(get_db)
) -> TransactionResponse:
    """
    Returns the transaction detail and all ledger entries by ID.
    """
    return await LedgerService.get_transaction(conn, tx_id)
