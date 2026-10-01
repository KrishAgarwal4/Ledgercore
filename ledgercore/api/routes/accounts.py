from typing import List
from uuid import UUID
from fastapi import APIRouter, Depends, status
import asyncpg

from ledgercore.api.deps import get_db, get_tx
from ledgercore.models.account import (
    AccountCreate,
    AccountResponse,
    AccountBalanceResponse,
)
from ledgercore.services.account_service import AccountService

router = APIRouter(prefix="/accounts", tags=["Accounts"])


@router.post("", response_model=AccountResponse, status_code=status.HTTP_201_CREATED)
async def create_account(
    account_in: AccountCreate,
    conn: asyncpg.Connection = Depends(get_tx)
) -> AccountResponse:
    """
    Creates a new ledger account within the chart of accounts.
    Automatically initializes the corresponding row in account_balances.
    """
    return await AccountService.create_account(conn, account_in)


@router.get("", response_model=List[AccountResponse])
async def list_accounts(
    conn: asyncpg.Connection = Depends(get_db)
) -> List[AccountResponse]:
    """
    Retrieves all accounts with their materialized settled and pending balances.
    """
    return await AccountService.list_accounts(conn)


@router.get("/{account_id}", response_model=AccountResponse)
async def get_account(
    account_id: UUID,
    conn: asyncpg.Connection = Depends(get_db)
) -> AccountResponse:
    """
    Retrieves account metadata and current balance for a specific account.
    """
    return await AccountService.get_account(conn, account_id)


@router.get("/{account_id}/balance", response_model=AccountBalanceResponse)
async def get_account_balance(
    account_id: UUID,
    conn: asyncpg.Connection = Depends(get_db)
) -> AccountBalanceResponse:
    """
    Returns the current materialized balance snapshot and optimistic locking version.
    """
    return await AccountService.get_balance(conn, account_id)
