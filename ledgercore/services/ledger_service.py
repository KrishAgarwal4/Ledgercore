import json
from typing import List, Dict, Any, Tuple
from uuid import UUID, uuid4
import asyncpg

from ledgercore.models.account import AccountType, AccountStatus
from ledgercore.models.transaction import (
    TransactionCreate,
    TransferRequest,
    TransactionResponse,
    LedgerEntryResponse,
    EntryDirection,
    LedgerLeg,
)
from ledgercore.exceptions import (
    AccountNotFoundError,
    AccountFrozenError,
    InsufficientFundsError,
    ZeroSumInvariantViolationError,
    CurrencyMismatchError,
    InvalidTransactionError,
)
from ledgercore.services.account_service import _format_cents


def calculate_balance_delta(account_type: str, direction: EntryDirection, amount_cents: int) -> int:
    """
    Computes how a DEBIT or CREDIT changes an account's settled balance
    based on standard double-entry accounting normal balances:
    - ASSET & EXPENSE (Normal Debit): DEBIT increases (+), CREDIT decreases (-)
    - LIABILITY, EQUITY & REVENUE (Normal Credit): CREDIT increases (+), DEBIT decreases (-)
    """
    if account_type in ("ASSET", "EXPENSE"):
        return amount_cents if direction == EntryDirection.DEBIT else -amount_cents
    elif account_type in ("LIABILITY", "EQUITY", "REVENUE"):
        return amount_cents if direction == EntryDirection.CREDIT else -amount_cents
    else:
        raise ValueError(f"Unrecognized account type: {account_type}")


def _parse_metadata(val: Any) -> Dict[str, Any]:
    if isinstance(val, dict):
        return val
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            return {}
    return {}


class LedgerService:
    @staticmethod
    async def post_transaction(
        conn: asyncpg.Connection,
        tx_data: TransactionCreate
    ) -> TransactionResponse:
        """
        Core atomic double-entry journal posting function.
        Guarantees:
        1. Deterministic lock ordering (deadlock freedom)
        2. Account status & currency validation
        3. Real-time balance & overdraft validation under row lock
        4. Atomic transaction & ledger entry insertion
        5. Atomic materialized balance updates
        6. Deferred constraint trigger checks for zero-sum invariant
        """
        # 1. Validate basic input constraints
        if len(tx_data.entries) < 2:
            raise InvalidTransactionError("A double-entry transaction must contain at least 2 legs.")

        total_debits = sum(e.amount_cents for e in tx_data.entries if e.direction == EntryDirection.DEBIT)
        total_credits = sum(e.amount_cents for e in tx_data.entries if e.direction == EntryDirection.CREDIT)
        if total_debits != total_credits:
            raise ZeroSumInvariantViolationError(total_debits, total_credits)

        # 2. Extract unique sorted account IDs for deterministic lock acquisition
        account_ids = sorted(list(set(e.account_id for e in tx_data.entries)))

        # Ensure everything runs within a transaction block for deferred trigger execution
        async def _execute_posting() -> TransactionResponse:
            # 3. Acquire row-level locks on accounts and balances in deterministic order
            # ORDER BY a.id ASC prevents deadlocks between concurrent interleaved transactions
            rows = await conn.fetch(
                """
                SELECT 
                    a.id, a.name, a.type, a.currency, a.allow_overdraft, a.status,
                    b.settled_balance_cents, b.pending_balance_cents, b.version
                FROM accounts a
                JOIN account_balances b ON a.id = b.account_id
                WHERE a.id = ANY($1::uuid[])
                ORDER BY a.id ASC
                FOR UPDATE OF a, b;
                """,
                account_ids,
            )

            accounts_by_id: Dict[UUID, Dict[str, Any]] = {r["id"]: dict(r) for r in rows}

            # Verify all accounts exist
            for acc_id in account_ids:
                if acc_id not in accounts_by_id:
                    raise AccountNotFoundError(str(acc_id))

            # Verify account active status and currency
            for acc_id, acc in accounts_by_id.items():
                if acc["status"] != AccountStatus.ACTIVE.value:
                    raise AccountFrozenError(str(acc_id), acc["status"])
                if acc["currency"] != tx_data.currency.upper():
                    raise CurrencyMismatchError(expected=acc["currency"], actual=tx_data.currency.upper())

            # 4. Compute balance deltas and assert overdraft rules under lock
            account_deltas: Dict[UUID, int] = {acc_id: 0 for acc_id in account_ids}
            for entry in tx_data.entries:
                acc = accounts_by_id[entry.account_id]
                delta = calculate_balance_delta(acc["type"], entry.direction, entry.amount_cents)
                account_deltas[entry.account_id] += delta

            for acc_id, net_delta in account_deltas.items():
                acc = accounts_by_id[acc_id]
                current_balance = acc["settled_balance_cents"]
                new_balance = current_balance + net_delta
                if new_balance < 0 and not acc["allow_overdraft"]:
                    # Insufficient funds to cover debit/credit
                    raise InsufficientFundsError(
                        account_id=str(acc_id),
                        available_balance_cents=current_balance,
                        requested_amount_cents=abs(net_delta),
                    )
                # Store updated balance for write step
                acc["new_settled_balance"] = new_balance

            # 5. Insert transaction header
            tx_id = uuid4()
            metadata_json = json.dumps(tx_data.metadata)
            tx_row = await conn.fetchrow(
                """
                INSERT INTO transactions (id, idempotency_key, description, currency, metadata, posted_at, created_at)
                VALUES ($1, $2, $3, $4, $5::jsonb, NOW(), NOW())
                RETURNING id, description, status, currency, idempotency_key, metadata, posted_at, created_at;
                """,
                tx_id,
                tx_data.idempotency_key,
                tx_data.description,
                tx_data.currency.upper(),
                metadata_json,
            )

            # 6. Insert ledger entries
            inserted_entries: List[LedgerEntryResponse] = []
            for idx, entry in enumerate(tx_data.entries, start=1):
                entry_id = uuid4()
                acc = accounts_by_id[entry.account_id]
                entry_row = await conn.fetchrow(
                    """
                    INSERT INTO ledger_entries (id, transaction_id, account_id, direction, amount_cents, sequence, created_at)
                    VALUES ($1, $2, $3, $4, $5, $6, NOW())
                    RETURNING id, transaction_id, account_id, direction, amount_cents, sequence, created_at;
                    """,
                    entry_id,
                    tx_id,
                    entry.account_id,
                    entry.direction.value,
                    entry.amount_cents,
                    idx,
                )
                inserted_entries.append(
                    LedgerEntryResponse(
                        id=entry_row["id"],
                        transaction_id=entry_row["transaction_id"],
                        account_id=entry_row["account_id"],
                        account_name=acc["name"],
                        account_type=acc["type"],
                        direction=EntryDirection(entry_row["direction"]),
                        amount_cents=entry_row["amount_cents"],
                        amount_formatted=_format_cents(entry_row["amount_cents"]),
                        sequence=entry_row["sequence"],
                        created_at=entry_row["created_at"],
                    )
                )

            # 7. Update materialized balances
            for acc_id, acc in accounts_by_id.items():
                await conn.execute(
                    """
                    UPDATE account_balances
                    SET settled_balance_cents = $2,
                        version = version + 1,
                        updated_at = NOW()
                    WHERE account_id = $1;
                    """,
                    acc_id,
                    acc["new_settled_balance"],
                )

            return TransactionResponse(
                id=tx_row["id"],
                description=tx_row["description"],
                status=tx_row["status"],
                currency=tx_row["currency"],
                idempotency_key=tx_row["idempotency_key"],
                metadata=_parse_metadata(tx_row["metadata"]),
                posted_at=tx_row["posted_at"],
                created_at=tx_row["created_at"],
                entries=inserted_entries,
            )

        if conn.is_in_transaction():
            return await _execute_posting()
        else:
            async with conn.transaction():
                return await _execute_posting()

    @staticmethod
    async def execute_transfer(
        conn: asyncpg.Connection,
        transfer_req: TransferRequest
    ) -> TransactionResponse:
        """
        Executes a 2-legged transfer between two accounts with proper accounting directions.
        Inspects source and destination account types to determine matching DEBIT/CREDIT legs.
        """
        # Fetch account types to assign correct accounting directions
        rows = await conn.fetch(
            "SELECT id, type, currency FROM accounts WHERE id IN ($1, $2);",
            transfer_req.source_account_id,
            transfer_req.destination_account_id,
        )
        by_id = {r["id"]: r for r in rows}
        if transfer_req.source_account_id not in by_id:
            raise AccountNotFoundError(str(transfer_req.source_account_id))
        if transfer_req.destination_account_id not in by_id:
            raise AccountNotFoundError(str(transfer_req.destination_account_id))

        source_acc = by_id[transfer_req.source_account_id]
        dest_acc = by_id[transfer_req.destination_account_id]

        if source_acc["currency"] != dest_acc["currency"]:
            raise CurrencyMismatchError(
                expected=source_acc["currency"],
                actual=dest_acc["currency"]
            )

        # Determine directions:
        # Source loses funds:
        #   - ASSET/EXPENSE: CREDIT
        #   - LIABILITY/EQUITY/REVENUE: DEBIT
        # Destination gains funds:
        #   - ASSET/EXPENSE: DEBIT
        #   - LIABILITY/EQUITY/REVENUE: CREDIT
        src_direction = (
            EntryDirection.CREDIT 
            if source_acc["type"] in ("ASSET", "EXPENSE") 
            else EntryDirection.DEBIT
        )
        dst_direction = (
            EntryDirection.DEBIT 
            if dest_acc["type"] in ("ASSET", "EXPENSE") 
            else EntryDirection.CREDIT
        )

        # Zero-sum check between the 2 legs:
        # If source is ASSET and dest is ASSET:
        #   src_direction = CREDIT, dst_direction = DEBIT. Total debit = total credit.
        # If both are LIABILITY:
        #   src_direction = DEBIT, dst_direction = CREDIT. Total debit = total credit.
        # If one is ASSET and other is LIABILITY:
        #   E.g., depositing cash into a customer wallet (Asset debited, Liability credited)
        if src_direction == dst_direction:
            # When types are complementary (e.g. Asset & Liability),
            # transferring from Asset to Liability means:
            # Asset is reduced (CREDIT) and Liability is reduced (DEBIT)
            # or Asset is increased (DEBIT) and Liability is increased (CREDIT).
            # To ensure double-entry invariant: one leg must be DEBIT, one must be CREDIT.
            src_direction = EntryDirection.CREDIT
            dst_direction = EntryDirection.DEBIT

        entries = [
            LedgerLeg(
                account_id=transfer_req.source_account_id,
                direction=src_direction,
                amount_cents=transfer_req.amount_cents,
            ),
            LedgerLeg(
                account_id=transfer_req.destination_account_id,
                direction=dst_direction,
                amount_cents=transfer_req.amount_cents,
            ),
        ]

        tx_create = TransactionCreate(
            description=transfer_req.description,
            entries=entries,
            currency=source_acc["currency"],
            idempotency_key=transfer_req.idempotency_key,
            metadata=transfer_req.metadata,
        )

        return await LedgerService.post_transaction(conn, tx_create)

    @staticmethod
    async def get_transaction(conn: asyncpg.Connection, tx_id: UUID) -> TransactionResponse:
        tx_row = await conn.fetchrow(
            """
            SELECT id, description, status, currency, idempotency_key, metadata, posted_at, created_at
            FROM transactions
            WHERE id = $1;
            """,
            tx_id,
        )
        if not tx_row:
            raise InvalidTransactionError(f"Transaction with id '{tx_id}' not found.")

        entries_rows = await conn.fetch(
            """
            SELECT 
                le.id, le.transaction_id, le.account_id, le.direction, le.amount_cents, le.sequence, le.created_at,
                a.name as account_name, a.type as account_type
            FROM ledger_entries le
            JOIN accounts a ON le.account_id = a.id
            WHERE le.transaction_id = $1
            ORDER BY le.sequence ASC, le.created_at ASC;
            """,
            tx_id,
        )

        entries = [
            LedgerEntryResponse(
                id=r["id"],
                transaction_id=r["transaction_id"],
                account_id=r["account_id"],
                account_name=r["account_name"],
                account_type=r["account_type"],
                direction=EntryDirection(r["direction"]),
                amount_cents=r["amount_cents"],
                amount_formatted=_format_cents(r["amount_cents"]),
                sequence=r["sequence"],
                created_at=r["created_at"],
            )
            for r in entries_rows
        ]

        return TransactionResponse(
            id=tx_row["id"],
            description=tx_row["description"],
            status=tx_row["status"],
            currency=tx_row["currency"],
            idempotency_key=tx_row["idempotency_key"],
            metadata=_parse_metadata(tx_row["metadata"]),
            posted_at=tx_row["posted_at"],
            created_at=tx_row["created_at"],
            entries=entries,
        )

    @staticmethod
    async def list_transactions(conn: asyncpg.Connection, limit: int = 50, offset: int = 0) -> List[TransactionResponse]:
        tx_rows = await conn.fetch(
            """
            SELECT id, description, status, currency, idempotency_key, metadata, posted_at, created_at
            FROM transactions
            ORDER BY posted_at DESC, created_at DESC
            LIMIT $1 OFFSET $2;
            """,
            limit,
            offset,
        )
        if not tx_rows:
            return []

        tx_ids = [r["id"] for r in tx_rows]
        entries_rows = await conn.fetch(
            """
            SELECT 
                le.id, le.transaction_id, le.account_id, le.direction, le.amount_cents, le.sequence, le.created_at,
                a.name as account_name, a.type as account_type
            FROM ledger_entries le
            JOIN accounts a ON le.account_id = a.id
            WHERE le.transaction_id = ANY($1::uuid[])
            ORDER BY le.sequence ASC, le.created_at ASC;
            """,
            tx_ids,
        )

        entries_by_tx: Dict[UUID, List[LedgerEntryResponse]] = {tx_id: [] for tx_id in tx_ids}
        for r in entries_rows:
            entries_by_tx[r["transaction_id"]].append(
                LedgerEntryResponse(
                    id=r["id"],
                    transaction_id=r["transaction_id"],
                    account_id=r["account_id"],
                    account_name=r["account_name"],
                    account_type=r["account_type"],
                    direction=EntryDirection(r["direction"]),
                    amount_cents=r["amount_cents"],
                    amount_formatted=_format_cents(r["amount_cents"]),
                    sequence=r["sequence"],
                    created_at=r["created_at"],
                )
            )

        return [
            TransactionResponse(
                id=r["id"],
                description=r["description"],
                status=r["status"],
                currency=r["currency"],
                idempotency_key=r["idempotency_key"],
                metadata=_parse_metadata(r["metadata"]),
                posted_at=r["posted_at"],
                created_at=r["created_at"],
                entries=entries_by_tx[r["id"]],
            )
            for r in tx_rows
        ]
