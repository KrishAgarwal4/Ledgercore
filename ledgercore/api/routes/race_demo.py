import asyncio
import time
from typing import List, Dict, Any
from uuid import UUID, uuid4
from pydantic import BaseModel, Field
from fastapi import APIRouter
import asyncpg

from ledgercore.db import transaction, get_db_pool
from ledgercore.models.account import AccountCreate, AccountType
from ledgercore.models.transaction import TransferRequest, TransactionCreate, LedgerLeg, EntryDirection
from ledgercore.services.account_service import AccountService, _format_cents
from ledgercore.services.ledger_service import LedgerService
from ledgercore.services.audit_service import AuditService
from ledgercore.exceptions import InsufficientFundsError

router = APIRouter(prefix="/demo", tags=["Demo"])


class RaceDemoRequest(BaseModel):
    num_requests: int = Field(default=50, ge=5, le=200, description="Number of concurrent transfer attempts")
    initial_balance_cents: int = Field(default=10000, ge=1000, description="Initial balance in cents ($100.00)")
    transfer_amount_cents: int = Field(default=1000, ge=100, description="Transfer amount per request ($10.00)")


class WorkerResult(BaseModel):
    worker_id: int
    success: bool
    status_code: int
    message: str
    duration_ms: float


class RaceDemoResponse(BaseModel):
    total_requests: int
    initial_balance_formatted: str
    transfer_amount_formatted: str
    expected_successes: int
    actual_successes: int
    actual_rejections: int
    deadlocks_detected: int
    total_elapsed_ms: float
    source_final_balance_formatted: str
    dest_final_balance_formatted: str
    invariant_passed: bool
    reconciliation_passed: bool
    workers: List[WorkerResult]


@router.post("/race", response_model=RaceDemoResponse)
async def run_race_demo(req: RaceDemoRequest) -> RaceDemoResponse:
    """
    Executes a high-concurrency race condition benchmark:
    1. Sets up an account with exact initial funds (e.g. $100.00).
    2. Dispatches N concurrent requests (e.g. 50 workers trying to withdraw $10.00 each) in parallel.
    3. Proves that exactly 10 succeed, 40 are rejected for insufficient funds, 0 deadlocks occur,
       and the final balance is exactly $0.00.
    """
    pool = get_db_pool()

    # Step 1: Create isolated demo accounts
    demo_id = str(uuid4())[:8]
    source_name = f"Race Source [{demo_id}]"
    dest_name = f"Race Dest [{demo_id}]"
    vault_name = f"Race Vault [{demo_id}]"

    async with transaction() as conn:
        src = await AccountService.create_account(
            conn,
            AccountCreate(name=source_name, type=AccountType.ASSET, allow_overdraft=False)
        )
        dst = await AccountService.create_account(
            conn,
            AccountCreate(name=dest_name, type=AccountType.ASSET, allow_overdraft=False)
        )
        vault = await AccountService.create_account(
            conn,
            AccountCreate(name=vault_name, type=AccountType.EQUITY, allow_overdraft=True)
        )

        # Seed initial funding to source account
        await LedgerService.post_transaction(
            conn,
            TransactionCreate(
                description=f"Fund Race Demo Source Account with {_format_cents(req.initial_balance_cents)}",
                entries=[
                    LedgerLeg(account_id=src.id, direction=EntryDirection.DEBIT, amount_cents=req.initial_balance_cents),
                    LedgerLeg(account_id=vault.id, direction=EntryDirection.CREDIT, amount_cents=req.initial_balance_cents),
                ],
                currency="USD"
            )
        )

    # Step 2: Concurrency worker function
    async def worker_task(worker_id: int) -> WorkerResult:
        start_t = time.perf_counter()
        transfer_req = TransferRequest(
            source_account_id=src.id,
            destination_account_id=dst.id,
            amount_cents=req.transfer_amount_cents,
            description=f"Race worker #{worker_id} transfer",
            idempotency_key=f"race-{demo_id}-worker-{worker_id}"
        )
        try:
            async with transaction() as conn:
                await LedgerService.execute_transfer(conn, transfer_req)
                duration = (time.perf_counter() - start_t) * 1000
                return WorkerResult(
                    worker_id=worker_id,
                    success=True,
                    status_code=201,
                    message="Transfer posted successfully",
                    duration_ms=round(duration, 2)
                )
        except InsufficientFundsError as e:
            duration = (time.perf_counter() - start_t) * 1000
            return WorkerResult(
                worker_id=worker_id,
                success=False,
                status_code=400,
                message="Rejected: Insufficient funds (overdraft prevented)",
                duration_ms=round(duration, 2)
            )
        except asyncpg.DeadlockDetectedError:
            duration = (time.perf_counter() - start_t) * 1000
            return WorkerResult(
                worker_id=worker_id,
                success=False,
                status_code=500,
                message="DEADLOCK DETECTED",
                duration_ms=round(duration, 2)
            )
        except Exception as e:
            duration = (time.perf_counter() - start_t) * 1000
            return WorkerResult(
                worker_id=worker_id,
                success=False,
                status_code=500,
                message=f"Error: {str(e)}",
                duration_ms=round(duration, 2)
            )

    # Step 3: Launch all concurrent workers simultaneously
    total_start = time.perf_counter()
    tasks = [worker_task(i + 1) for i in range(req.num_requests)]
    worker_results: List[WorkerResult] = await asyncio.gather(*tasks)
    total_elapsed = (time.perf_counter() - total_start) * 1000

    # Step 4: Verify balances and audit trail
    async with transaction() as conn:
        src_bal = await AccountService.get_balance(conn, src.id)
        dst_bal = await AccountService.get_balance(conn, dst.id)
        src_audit = await AuditService.get_account_audit_trail(conn, src.id)
        dst_audit = await AuditService.get_account_audit_trail(conn, dst.id)

    successes = sum(1 for w in worker_results if w.success)
    rejections = sum(1 for w in worker_results if not w.success and w.status_code == 400)
    deadlocks = sum(1 for w in worker_results if "DEADLOCK" in w.message)

    expected_successes = req.initial_balance_cents // req.transfer_amount_cents
    expected_src_final = req.initial_balance_cents - (successes * req.transfer_amount_cents)
    expected_dst_final = successes * req.transfer_amount_cents

    invariant_passed = (
        src_bal.settled_balance_cents == expected_src_final and
        dst_bal.settled_balance_cents == expected_dst_final and
        successes == expected_successes and
        deadlocks == 0
    )

    reconciliation_passed = src_audit.is_reconciled and dst_audit.is_reconciled

    return RaceDemoResponse(
        total_requests=req.num_requests,
        initial_balance_formatted=_format_cents(req.initial_balance_cents),
        transfer_amount_formatted=_format_cents(req.transfer_amount_cents),
        expected_successes=expected_successes,
        actual_successes=successes,
        actual_rejections=rejections,
        deadlocks_detected=deadlocks,
        total_elapsed_ms=round(total_elapsed, 2),
        source_final_balance_formatted=src_bal.settled_balance_formatted,
        dest_final_balance_formatted=dst_bal.settled_balance_formatted,
        invariant_passed=invariant_passed,
        reconciliation_passed=reconciliation_passed,
        workers=worker_results,
    )
