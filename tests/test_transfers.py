import pytest
from httpx import AsyncClient
import asyncpg

from ledgercore.models.account import AccountCreate, AccountType
from ledgercore.services.account_service import AccountService
from ledgercore.services.ledger_service import LedgerService
from ledgercore.models.transaction import TransactionCreate, LedgerLeg, EntryDirection


@pytest.mark.asyncio
async def test_standard_transfer_and_balances(client: AsyncClient, db_conn: asyncpg.Connection):
    """
    Tests creating two accounts, funding the source, executing a transfer,
    and verifying updated balances on both accounts.
    """
    # 1. Create Source (Alice) and Destination (Bob)
    alice_res = await client.post("/v1/accounts", json={
        "name": "Alice Wallet",
        "type": "ASSET",
        "currency": "USD",
        "allow_overdraft": False
    })
    assert alice_res.status_code == 201
    alice_id = alice_res.json()["id"]

    bob_res = await client.post("/v1/accounts", json={
        "name": "Bob Wallet",
        "type": "ASSET",
        "currency": "USD",
        "allow_overdraft": False
    })
    assert bob_res.status_code == 201
    bob_id = bob_res.json()["id"]

    vault_res = await client.post("/v1/accounts", json={
        "name": "Capital Vault",
        "type": "EQUITY",
        "currency": "USD",
        "allow_overdraft": True
    })
    vault_id = vault_res.json()["id"]

    # 2. Fund Alice with $100.00 (10,000 cents)
    fund_res = await client.post("/v1/transactions", json={
        "description": "Fund Alice account",
        "currency": "USD",
        "entries": [
            {"account_id": alice_id, "direction": "DEBIT", "amount_cents": 10000},
            {"account_id": vault_id, "direction": "CREDIT", "amount_cents": 10000},
        ]
    })
    assert fund_res.status_code == 201

    # 3. Transfer $40.00 from Alice to Bob
    transfer_res = await client.post("/v1/transfers", json={
        "source_account_id": alice_id,
        "destination_account_id": bob_id,
        "amount_cents": 4000,
        "description": "Lunch payment Alice to Bob"
    })
    assert transfer_res.status_code == 201
    tx_data = transfer_res.json()
    assert len(tx_data["entries"]) == 2

    # 4. Verify balances
    bal_alice = (await client.get(f"/v1/accounts/{alice_id}/balance")).json()
    assert bal_alice["settled_balance_cents"] == 6000  # $60.00
    assert bal_alice["settled_balance_formatted"] == "$60.00"

    bal_bob = (await client.get(f"/v1/accounts/{bob_id}/balance")).json()
    assert bal_bob["settled_balance_cents"] == 4000  # $40.00
    assert bal_bob["settled_balance_formatted"] == "$40.00"


@pytest.mark.asyncio
async def test_overdraft_prevention_rejected(client: AsyncClient):
    """
    Verifies that when allow_overdraft=False, transfers exceeding the account's
    available balance are strictly rejected with HTTP 400 Insufficient Funds.
    """
    alice_res = await client.post("/v1/accounts", json={
        "name": "Alice Strict",
        "type": "ASSET",
        "allow_overdraft": False
    })
    alice_id = alice_res.json()["id"]

    bob_res = await client.post("/v1/accounts", json={
        "name": "Bob Dest",
        "type": "ASSET",
        "allow_overdraft": False
    })
    bob_id = bob_res.json()["id"]

    # Alice has $0.00. Attempt to transfer $10.00
    transfer_res = await client.post("/v1/transfers", json={
        "source_account_id": alice_id,
        "destination_account_id": bob_id,
        "amount_cents": 1000,
        "description": "Attempted overdraft"
    })
    assert transfer_res.status_code == 400
    error_data = transfer_res.json()
    assert error_data["error"]["code"] == "insufficient_funds"
    assert "Insufficient funds in account" in error_data["error"]["message"]


@pytest.mark.asyncio
async def test_overdraft_allowed_when_configured(client: AsyncClient, db_conn: asyncpg.Connection):
    """
    Verifies that when allow_overdraft=True (e.g. for clearing/settlement or line-of-credit accounts),
    transfers can push balance negative.
    """
    clearing_res = await client.post("/v1/accounts", json={
        "name": "Clearing Line of Credit",
        "type": "ASSET",
        "allow_overdraft": True
    })
    clearing_id = clearing_res.json()["id"]

    bob_res = await client.post("/v1/accounts", json={
        "name": "Bob Target",
        "type": "ASSET",
        "allow_overdraft": False
    })
    bob_id = bob_res.json()["id"]

    # Transfer $50.00 from $0 clearing account
    transfer_res = await client.post("/v1/transfers", json={
        "source_account_id": clearing_id,
        "destination_account_id": bob_id,
        "amount_cents": 5000,
        "description": "Overdraft allowed transfer"
    })
    assert transfer_res.status_code == 201

    bal = (await client.get(f"/v1/accounts/{clearing_id}/balance")).json()
    assert bal["settled_balance_cents"] == -5000
    assert bal["settled_balance_formatted"] == "-$50.00"
