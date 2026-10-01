import pytest
import asyncpg
from uuid import uuid4
from ledgercore.models.account import AccountCreate, AccountType
from ledgercore.models.transaction import TransactionCreate, LedgerLeg, EntryDirection
from ledgercore.services.account_service import AccountService
from ledgercore.services.ledger_service import LedgerService
from ledgercore.exceptions import ZeroSumInvariantViolationError


@pytest.mark.asyncio
async def test_zero_sum_deferred_trigger_rejection(db_conn: asyncpg.Connection):
    """
    Directly attempts to commit an unbalanced transaction via raw SQL to bypass
    application-level checks and verify that PostgreSQL's deferred PL/pgSQL
    constraint trigger strictly blocks the commit with SQLSTATE P0001.
    """
    # Create test account
    acc = await AccountService.create_account(
        db_conn,
        AccountCreate(name="Trigger Test Account", type=AccountType.ASSET, allow_overdraft=True)
    )

    tx_id = uuid4()
    entry_id = uuid4()

    # Raw SQL inserting only 1 leg (unbalanced DEBIT of 5000 cents without matching CREDIT)
    await db_conn.execute(
        "INSERT INTO transactions (id, description) VALUES ($1, 'Unbalanced raw test');",
        tx_id
    )

    # In a transaction block, inserting entry succeeds initially, but COMMIT must fail!
    with pytest.raises(asyncpg.PostgresError) as exc_info:
        async with db_conn.transaction():
            await db_conn.execute(
                """
                INSERT INTO ledger_entries (id, transaction_id, account_id, direction, amount_cents, sequence)
                VALUES ($1, $2, $3, 'DEBIT', 5000, 1);
                """,
                entry_id,
                tx_id,
                acc.id
            )

    assert exc_info.value.sqlstate == "P0001"
    assert "Zero-sum transaction balance invariant violated" in str(exc_info.value)


@pytest.mark.asyncio
async def test_ledger_immutability_trigger(db_conn: asyncpg.Connection):
    """
    Verifies that ledger_entries cannot be modified (UPDATE) or deleted (DELETE).
    The append-only audit trail must be strictly immutable at the database level.
    """
    acc1 = await AccountService.create_account(
        db_conn,
        AccountCreate(name="Acc 1", type=AccountType.ASSET, allow_overdraft=True)
    )
    acc2 = await AccountService.create_account(
        db_conn,
        AccountCreate(name="Acc 2", type=AccountType.EQUITY, allow_overdraft=True)
    )

    tx = await LedgerService.post_transaction(
        db_conn,
        TransactionCreate(
            description="Balanced initial funding",
            entries=[
                LedgerLeg(account_id=acc1.id, direction=EntryDirection.DEBIT, amount_cents=10000),
                LedgerLeg(account_id=acc2.id, direction=EntryDirection.CREDIT, amount_cents=10000),
            ]
        )
    )

    entry_id = tx.entries[0].id

    # Attempt to UPDATE entry amount
    with pytest.raises(asyncpg.PostgresError) as exc_update:
        await db_conn.execute(
            "UPDATE ledger_entries SET amount_cents = 9999 WHERE id = $1;",
            entry_id
        )
    assert exc_update.value.sqlstate == "P0002"
    assert "strictly append-only and immutable" in str(exc_update.value)

    # Attempt to DELETE entry
    with pytest.raises(asyncpg.PostgresError) as exc_delete:
        await db_conn.execute(
            "DELETE FROM ledger_entries WHERE id = $1;",
            entry_id
        )
    assert exc_delete.value.sqlstate == "P0002"
    assert "strictly append-only and immutable" in str(exc_delete.value)


@pytest.mark.asyncio
async def test_positive_amount_check_constraint(db_conn: asyncpg.Connection):
    """
    Verifies that amount_cents must be strictly positive (> 0).
    """
    acc = await AccountService.create_account(
        db_conn,
        AccountCreate(name="Check Account", type=AccountType.ASSET, allow_overdraft=True)
    )
    tx_id = uuid4()
    await db_conn.execute("INSERT INTO transactions (id, description) VALUES ($1, 'Test');", tx_id)

    with pytest.raises(asyncpg.CheckViolationError):
        await db_conn.execute(
            """
            INSERT INTO ledger_entries (id, transaction_id, account_id, direction, amount_cents, sequence)
            VALUES ($1, $2, $3, 'DEBIT', 0, 1);
            """,
            uuid4(),
            tx_id,
            acc.id
        )
