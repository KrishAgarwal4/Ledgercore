import asyncpg
from typing import List, Optional
from uuid import UUID
from ledgercore.models.account import (
    AccountCreate,
    AccountResponse,
    AccountBalanceResponse,
    AccountType,
    AccountStatus,
)
from ledgercore.exceptions import AccountNotFoundError


def _format_cents(cents: int) -> str:
    cents = int(cents)
    sign = "-" if cents < 0 else ""
    abs_cents = abs(cents)
    dollars = abs_cents // 100
    rem_cents = abs_cents % 100
    return f"{sign}${dollars:,}.{rem_cents:02d}"


class AccountService:
    @staticmethod
    async def create_account(conn: asyncpg.Connection, data: AccountCreate) -> AccountResponse:
        row = await conn.fetchrow(
            """
            INSERT INTO accounts (name, type, currency, allow_overdraft, description)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id, name, type, currency, allow_overdraft, status, description, created_at;
            """,
            data.name,
            data.type.value,
            data.currency.upper(),
            data.allow_overdraft,
            data.description,
        )

        # Trigger auto-creates account_balances with 0 balance
        return AccountResponse(
            id=row["id"],
            name=row["name"],
            type=AccountType(row["type"]),
            currency=row["currency"],
            allow_overdraft=row["allow_overdraft"],
            status=AccountStatus(row["status"]),
            description=row["description"],
            created_at=row["created_at"],
            settled_balance_cents=0,
            pending_balance_cents=0,
            settled_balance_formatted="$0.00",
        )

    @staticmethod
    async def get_account(conn: asyncpg.Connection, account_id: UUID) -> AccountResponse:
        row = await conn.fetchrow(
            """
            SELECT 
                a.id, a.name, a.type, a.currency, a.allow_overdraft, a.status, a.description, a.created_at,
                COALESCE(b.settled_balance_cents, 0) as settled_balance_cents,
                COALESCE(b.pending_balance_cents, 0) as pending_balance_cents
            FROM accounts a
            LEFT JOIN account_balances b ON a.id = b.account_id
            WHERE a.id = $1;
            """,
            account_id,
        )
        if not row:
            raise AccountNotFoundError(str(account_id))

        settled = row["settled_balance_cents"]
        return AccountResponse(
            id=row["id"],
            name=row["name"],
            type=AccountType(row["type"]),
            currency=row["currency"],
            allow_overdraft=row["allow_overdraft"],
            status=AccountStatus(row["status"]),
            description=row["description"],
            created_at=row["created_at"],
            settled_balance_cents=settled,
            pending_balance_cents=row["pending_balance_cents"],
            settled_balance_formatted=_format_cents(settled),
        )

    @staticmethod
    async def list_accounts(conn: asyncpg.Connection) -> List[AccountResponse]:
        rows = await conn.fetch(
            """
            SELECT 
                a.id, a.name, a.type, a.currency, a.allow_overdraft, a.status, a.description, a.created_at,
                COALESCE(b.settled_balance_cents, 0) as settled_balance_cents,
                COALESCE(b.pending_balance_cents, 0) as pending_balance_cents
            FROM accounts a
            LEFT JOIN account_balances b ON a.id = b.account_id
            ORDER BY a.created_at ASC;
            """
        )
        results = []
        for r in rows:
            settled = r["settled_balance_cents"]
            results.append(
                AccountResponse(
                    id=r["id"],
                    name=r["name"],
                    type=AccountType(r["type"]),
                    currency=r["currency"],
                    allow_overdraft=r["allow_overdraft"],
                    status=AccountStatus(r["status"]),
                    description=r["description"],
                    created_at=r["created_at"],
                    settled_balance_cents=settled,
                    pending_balance_cents=r["pending_balance_cents"],
                    settled_balance_formatted=_format_cents(settled),
                )
            )
        return results

    @staticmethod
    async def get_balance(conn: asyncpg.Connection, account_id: UUID) -> AccountBalanceResponse:
        row = await conn.fetchrow(
            """
            SELECT 
                a.id, a.name, a.type, a.currency,
                b.settled_balance_cents, b.pending_balance_cents, b.version, b.updated_at
            FROM accounts a
            JOIN account_balances b ON a.id = b.account_id
            WHERE a.id = $1;
            """,
            account_id,
        )
        if not row:
            raise AccountNotFoundError(str(account_id))

        settled = row["settled_balance_cents"]
        return AccountBalanceResponse(
            account_id=row["id"],
            name=row["name"],
            type=AccountType(row["type"]),
            currency=row["currency"],
            settled_balance_cents=settled,
            pending_balance_cents=row["pending_balance_cents"],
            settled_balance_formatted=_format_cents(settled),
            version=row["version"],
            updated_at=row["updated_at"],
        )
