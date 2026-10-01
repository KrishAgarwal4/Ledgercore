from typing import Optional
from fastapi import APIRouter, Header, Response, status
import asyncpg

from ledgercore.db import transaction
from ledgercore.models.transaction import TransferRequest, TransactionResponse
from ledgercore.services.ledger_service import LedgerService
from ledgercore.services.idempotency_service import IdempotencyService, compute_request_hash

router = APIRouter(prefix="/transfers", tags=["Transfers"])


@router.post("", response_model=TransactionResponse, status_code=status.HTTP_201_CREATED)
async def create_transfer(
    transfer_in: TransferRequest,
    response: Response,
    idempotency_key_header: Optional[str] = Header(None, alias="Idempotency-Key")
) -> TransactionResponse:
    """
    Executes a high-level 2-legged transfer between two accounts with:
    - Row-level locking on accounts sorted by UUID (deadlock prevention)
    - Real-time balance and overdraft validation
    - Stripe-grade Idempotency-Key deduplication
    - Deferred PL/pgSQL constraint trigger verifying debit/credit equilibrium at commit
    """
    effective_idempotency_key = idempotency_key_header or transfer_in.idempotency_key
    if effective_idempotency_key:
        transfer_in.idempotency_key = effective_idempotency_key

    req_hash = None
    if effective_idempotency_key:
        req_hash = compute_request_hash("POST", "/v1/transfers", transfer_in.model_dump(mode="json"))

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
            tx_res = await LedgerService.execute_transfer(conn, transfer_in)
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
