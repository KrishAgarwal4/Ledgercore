from uuid import UUID, uuid4
import asyncpg
from ledgercore.models.account import AccountCreate, AccountType
from ledgercore.models.transaction import TransactionCreate, LedgerLeg, EntryDirection
from ledgercore.services.account_service import AccountService
from ledgercore.services.ledger_service import LedgerService


async def seed_chart_of_accounts(conn: asyncpg.Connection) -> None:
    """
    Seeds a realistic Stripe-style chart of accounts and initial balanced transactions
    if the accounts table is empty.
    """
    count = await conn.fetchval("SELECT count(*) FROM accounts;")
    if count > 0:
        return

    # 1. Chart of Accounts
    accounts_to_create = [
        # Assets
        AccountCreate(
            name="Stripe Reserve Cash",
            type=AccountType.ASSET,
            currency="USD",
            allow_overdraft=True,
            description="Central clearing cash account holding fiat reserves"
        ),
        AccountCreate(
            name="Alice Customer Wallet",
            type=AccountType.ASSET,
            currency="USD",
            allow_overdraft=False,
            description="Alice's user deposit wallet"
        ),
        AccountCreate(
            name="Bob Customer Wallet",
            type=AccountType.ASSET,
            currency="USD",
            allow_overdraft=False,
            description="Bob's user deposit wallet"
        ),
        AccountCreate(
            name="Charlie Customer Wallet",
            type=AccountType.ASSET,
            currency="USD",
            allow_overdraft=False,
            description="Charlie's user deposit wallet"
        ),
        # Liabilities
        AccountCreate(
            name="Merchant Acme Corp Payout Liability",
            type=AccountType.LIABILITY,
            currency="USD",
            allow_overdraft=False,
            description="Funds owed to merchant Acme Corp awaiting payout"
        ),
        # Revenues
        AccountCreate(
            name="Stripe Processing Fee Revenue",
            type=AccountType.REVENUE,
            currency="USD",
            allow_overdraft=True,
            description="Platform revenue earned from 2.9% + 30c processing fees"
        ),
        # Expenses
        AccountCreate(
            name="Visa/Mastercard Interchange Fee Expense",
            type=AccountType.EXPENSE,
            currency="USD",
            allow_overdraft=True,
            description="Interchange assessment fees paid to card networks"
        ),
        # Equity
        AccountCreate(
            name="System Initial Capital",
            type=AccountType.EQUITY,
            currency="USD",
            allow_overdraft=True,
            description="Founding capital equity source for initial funding"
        ),
    ]

    created = {}
    for acc in accounts_to_create:
        res = await AccountService.create_account(conn, acc)
        created[acc.name] = res.id

    # 2. Post Initial Funding Transaction
    # Debit: Alice ($1,000.00 = 100,000 cents), Bob ($500.00 = 50,000 cents), Stripe Cash ($10,000.00 = 1,000,000 cents)
    # Credit: System Initial Capital ($11,500.00 = 1,150,000 cents)
    funding_tx = TransactionCreate(
        description="Initial System Capital & User Seed Funding",
        entries=[
            LedgerLeg(
                account_id=created["Alice Customer Wallet"],
                direction=EntryDirection.DEBIT,
                amount_cents=100000,  # $1,000.00
            ),
            LedgerLeg(
                account_id=created["Bob Customer Wallet"],
                direction=EntryDirection.DEBIT,
                amount_cents=50000,   # $500.00
            ),
            LedgerLeg(
                account_id=created["Stripe Reserve Cash"],
                direction=EntryDirection.DEBIT,
                amount_cents=1000000, # $10,000.00
            ),
            LedgerLeg(
                account_id=created["System Initial Capital"],
                direction=EntryDirection.CREDIT,
                amount_cents=1150000, # $11,500.00
            ),
        ],
        currency="USD",
        metadata={"source": "system_bootstrap", "environment": "seed"}
    )
    await LedgerService.post_transaction(conn, funding_tx)

    # 3. Simulate a sample Stripe Payment Charge
    # Customer card charge at Acme Corp for $100.00, Stripe fee is $3.20.
    # Debit: Stripe Reserve Cash ($100.00 = 10,000 cents)
    # Credit: Merchant Acme Corp ($96.80 = 9,680 cents)
    # Credit: Stripe Processing Fee Revenue ($3.20 = 320 cents)
    # Total Debits = 10,000 cents, Total Credits = 9,680 + 320 = 10,000 cents!
    charge_tx = TransactionCreate(
        description="Charge ch_1N8xQz: Customer card payment at Acme Corp ($100.00) with fee ($3.20)",
        entries=[
            LedgerLeg(
                account_id=created["Stripe Reserve Cash"],
                direction=EntryDirection.DEBIT,  # Asset increases: Cash deposited in Stripe bank
                amount_cents=10000,
            ),
            LedgerLeg(
                account_id=created["Merchant Acme Corp Payout Liability"],
                direction=EntryDirection.CREDIT,  # Liability increases: Owed to merchant Acme
                amount_cents=9680,
            ),
            LedgerLeg(
                account_id=created["Stripe Processing Fee Revenue"],
                direction=EntryDirection.CREDIT,  # Revenue increases: Stripe platform fee
                amount_cents=320,
            ),
        ],
        currency="USD",
        metadata={"charge_id": "ch_1N8xQz", "merchant": "Acme Corp", "fee_cents": 320}
    )
    await LedgerService.post_transaction(conn, charge_tx)

    # 4. Simulate a user wallet transfer: Alice pays Bob $25.00
    # Alice (Asset) is CREDITED $25.00 (decreases Alice wallet)
    # Bob (Asset) is DEBITED $25.00 (increases Bob wallet)
    wallet_transfer_tx = TransactionCreate(
        description="P2P Transfer: Alice sends $25.00 to Bob",
        entries=[
            LedgerLeg(
                account_id=created["Alice Customer Wallet"],
                direction=EntryDirection.CREDIT,
                amount_cents=2500,
            ),
            LedgerLeg(
                account_id=created["Bob Customer Wallet"],
                direction=EntryDirection.DEBIT,
                amount_cents=2500,
            ),
        ],
        currency="USD",
        metadata={"type": "p2p_transfer", "sender": "Alice", "receiver": "Bob"}
    )
    await LedgerService.post_transaction(conn, wallet_transfer_tx)
