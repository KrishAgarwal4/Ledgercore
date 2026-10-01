import pytest
from httpx import AsyncClient
import asyncpg


@pytest.mark.asyncio
async def test_window_function_running_balance(client: AsyncClient):
    """
    Posts multiple transactions to an account and verifies that the PostgreSQL
    window function calculates the exact cumulative running balance row-by-row.
    """
    user_res = await client.post("/v1/accounts", json={"name": "Timeline User", "type": "ASSET", "allow_overdraft": True})
    user_id = user_res.json()["id"]

    vault_res = await client.post("/v1/accounts", json={"name": "Vault Source", "type": "EQUITY", "allow_overdraft": True})
    vault_id = vault_res.json()["id"]

    # 1. Deposit +$100.00
    await client.post("/v1/transfers", json={
        "source_account_id": vault_id,
        "destination_account_id": user_id,
        "amount_cents": 10000,
        "description": "Initial deposit"
    })

    # 2. Spend -$30.00
    await client.post("/v1/transfers", json={
        "source_account_id": user_id,
        "destination_account_id": vault_id,
        "amount_cents": 3000,
        "description": "Book purchase"
    })

    # 3. Spend -$20.00
    await client.post("/v1/transfers", json={
        "source_account_id": user_id,
        "destination_account_id": vault_id,
        "amount_cents": 2000,
        "description": "Coffee purchase"
    })

    # 4. Deposit +$50.00
    await client.post("/v1/transfers", json={
        "source_account_id": vault_id,
        "destination_account_id": user_id,
        "amount_cents": 5000,
        "description": "Salary deposit"
    })

    # Fetch audit timeline
    audit_res = await client.get(f"/v1/audit/accounts/{user_id}")
    assert audit_res.status_code == 200
    audit_data = audit_res.json()

    assert audit_data["is_reconciled"] is True
    assert audit_data["settled_balance_cents"] == 10000  # $100.00 - 30 - 20 + 50 = $100.00 (10,000 cents)

    entries = audit_data["entries"]
    assert len(entries) == 4

    # Verify cumulative running balance after each entry:
    assert entries[0]["running_balance_cents"] == 10000  # $100.00
    assert entries[1]["running_balance_cents"] == 7000   # $70.00
    assert entries[2]["running_balance_cents"] == 5000   # $50.00
    assert entries[3]["running_balance_cents"] == 10000  # $100.00


@pytest.mark.asyncio
async def test_system_wide_reconciliation(client: AsyncClient):
    """
    Runs system-wide continuous integrity audit and asserts all accounts
    materialized caches match the raw ledger entry calculation.
    """
    rec_res = await client.post("/v1/audit/reconcile")
    assert rec_res.status_code == 200
    rec_data = rec_res.json()
    assert rec_data["system_healthy"] is True
    assert rec_data["discrepant_accounts"] == 0
    assert len(rec_data["discrepancies"]) == 0
