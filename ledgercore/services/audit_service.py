from typing import List
from uuid import UUID
from datetime import datetime, timezone
import asyncpg

from ledgercore.models.account import AccountType
from ledgercore.models.transaction import EntryDirection
from ledgercore.models.audit import (
    AccountRunningBalanceItem,
    AccountAuditResponse,
    ReconciliationReport,
    AccountDiscrepancy,
)
from ledgercore.exceptions import AccountNotFoundError
from ledgercore.services.account_service import _format_cents


class AuditService:
    @staticmethod
    async def get_account_audit_trail(conn: asyncpg.Connection, account_id: UUID) -> AccountAuditResponse:
        """
        Executes point-in-time auditing query using PostgreSQL Window Functions.
        Reconstructs the cumulative historical balance after every single transaction leg.
        """
        acc = await conn.fetchrow(
            """
            SELECT a.id, a.name, a.type, a.currency, b.settled_balance_cents
            FROM accounts a
            JOIN account_balances b ON a.id = b.account_id
            WHERE a.id = $1;
            """,
            account_id,
        )
        if not acc:
            raise AccountNotFoundError(str(account_id))

        # Window function: computes running balance partitioned by account and ordered by post time
        rows = await conn.fetch(
            """
            SELECT 
                t.posted_at,
                t.id AS transaction_id,
                t.description,
                le.direction,
                le.amount_cents,
                SUM(
                    CASE 
                        WHEN (a.type IN ('ASSET', 'EXPENSE') AND le.direction = 'DEBIT') THEN le.amount_cents
                        WHEN (a.type IN ('ASSET', 'EXPENSE') AND le.direction = 'CREDIT') THEN -le.amount_cents
                        WHEN (a.type IN ('LIABILITY', 'EQUITY', 'REVENUE') AND le.direction = 'CREDIT') THEN le.amount_cents
                        ELSE -le.amount_cents
                    END
                ) OVER (
                    PARTITION BY le.account_id 
                    ORDER BY t.posted_at ASC, le.id ASC
                    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                ) AS running_balance_cents
            FROM ledger_entries le
            JOIN transactions t ON le.transaction_id = t.id
            JOIN accounts a ON le.account_id = a.id
            WHERE le.account_id = $1
            ORDER BY t.posted_at ASC, le.id ASC;
            """,
            account_id,
        )

        entries: List[AccountRunningBalanceItem] = []
        for r in rows:
            rb_cents = int(r["running_balance_cents"])
            amt_cents = int(r["amount_cents"])
            entries.append(
                AccountRunningBalanceItem(
                    posted_at=r["posted_at"],
                    transaction_id=r["transaction_id"],
                    description=r["description"],
                    direction=EntryDirection(r["direction"]),
                    amount_cents=amt_cents,
                    amount_formatted=_format_cents(amt_cents),
                    running_balance_cents=rb_cents,
                    running_balance_formatted=_format_cents(rb_cents),
                )
            )

        final_running_cents = entries[-1].running_balance_cents if entries else 0
        cached_settled_cents = acc["settled_balance_cents"]
        is_reconciled = (final_running_cents == cached_settled_cents)

        notes = (
            f"Audit passed. Materialized cache matches raw ledger entry sum: {_format_cents(cached_settled_cents)}."
            if is_reconciled else
            f"DISCREPANCY: Cache ({_format_cents(cached_settled_cents)}) differs from raw entries ({_format_cents(final_running_cents)})."
        )

        return AccountAuditResponse(
            account_id=acc["id"],
            name=acc["name"],
            type=AccountType(acc["type"]),
            currency=acc["currency"],
            settled_balance_cents=cached_settled_cents,
            settled_balance_formatted=_format_cents(cached_settled_cents),
            entries=entries,
            is_reconciled=is_reconciled,
            reconciliation_notes=notes,
        )

    @staticmethod
    async def reconcile_all_accounts(conn: asyncpg.Connection) -> ReconciliationReport:
        """
        Continuous integrity verification:
        Compares account_balances.settled_balance_cents with SUM(ledger_entries)
        for every account in the system.
        """
        rows = await conn.fetch(
            """
            SELECT 
                a.id,
                a.name,
                a.type,
                b.settled_balance_cents AS cached_settled_cents,
                COALESCE(
                    SUM(
                        CASE 
                            WHEN (a.type IN ('ASSET', 'EXPENSE') AND le.direction = 'DEBIT') THEN le.amount_cents
                            WHEN (a.type IN ('ASSET', 'EXPENSE') AND le.direction = 'CREDIT') THEN -le.amount_cents
                            WHEN (a.type IN ('LIABILITY', 'EQUITY', 'REVENUE') AND le.direction = 'CREDIT') THEN le.amount_cents
                            ELSE -le.amount_cents
                        END
                    ), 0
                ) AS raw_computed_cents
            FROM accounts a
            JOIN account_balances b ON a.id = b.account_id
            LEFT JOIN ledger_entries le ON a.id = le.account_id
            GROUP BY a.id, a.name, a.type, b.settled_balance_cents
            ORDER BY a.name ASC;
            """
        )

        discrepancies: List[AccountDiscrepancy] = []
        balanced_count = 0

        for r in rows:
            cached = int(r["cached_settled_cents"])
            raw = int(r["raw_computed_cents"])
            if cached != raw:
                discrepancies.append(
                    AccountDiscrepancy(
                        account_id=r["id"],
                        account_name=r["name"],
                        account_type=AccountType(r["type"]),
                        cached_settled_cents=cached,
                        raw_computed_cents=raw,
                        discrepancy_cents=abs(cached - raw),
                    )
                )
            else:
                balanced_count += 1

        total = len(rows)
        return ReconciliationReport(
            total_accounts_audited=total,
            balanced_accounts=balanced_count,
            discrepant_accounts=len(discrepancies),
            discrepancies=discrepancies,
            audit_timestamp=datetime.now(timezone.utc),
            system_healthy=(len(discrepancies) == 0),
        )
