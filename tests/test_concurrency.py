import asyncio
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_concurrent_double_spend_prevention(client: AsyncClient):
    """
    Bank Run / Race Condition Test:
    Account has $50.00 (5,000 cents).
    30 concurrent requests attempt to withdraw $10.00 (1,000 cents) at the exact same instant.
    Asserts:
    1. Exactly 5 requests succeed (status 201)
    2. Exactly 25 requests are cleanly rejected (status 400 Insufficient Funds)
    3. Final source balance is exactly $0.00
    4. Destination balance is exactly $50.00
    5. Zero deadlocks
    """
    # 1. Setup accounts
    src_res = await client.post("/v1/accounts", json={"name": "Race Source", "type": "ASSET", "allow_overdraft": False})
    src_id = src_res.json()["id"]

    dst_res = await client.post("/v1/accounts", json={"name": "Race Destination", "type": "ASSET", "allow_overdraft": False})
    dst_id = dst_res.json()["id"]

    vault_res = await client.post("/v1/accounts", json={"name": "Vault", "type": "EQUITY", "allow_overdraft": True})
    vault_id = vault_res.json()["id"]

    # Seed exactly $50.00 into source account
    fund_res = await client.post("/v1/transactions", json={
        "description": "Fund $50.00 to race source",
        "entries": [
            {"account_id": src_id, "direction": "DEBIT", "amount_cents": 5000},
            {"account_id": vault_id, "direction": "CREDIT", "amount_cents": 5000},
        ]
    })
    assert fund_res.status_code == 201

    # 2. Fire 30 concurrent transfer requests
    num_requests = 30
    transfer_amount = 1000  # $10.00

    async def fire_request(idx: int):
        return await client.post("/v1/transfers", json={
            "source_account_id": src_id,
            "destination_account_id": dst_id,
            "amount_cents": transfer_amount,
            "description": f"Concurrent transfer #{idx}",
            "idempotency_key": f"concurrency-race-test-{idx}"
        })

    tasks = [fire_request(i) for i in range(num_requests)]
    responses = await asyncio.gather(*tasks)

    successes = [r for r in responses if r.status_code == 201]
    rejections = [r for r in responses if r.status_code == 400]
    errors = [r for r in responses if r.status_code not in (201, 400)]

    assert len(errors) == 0, f"Unexpected errors during race: {[r.text for r in errors]}"
    assert len(successes) == 5, f"Expected exactly 5 successes, got {len(successes)}"
    assert len(rejections) == 25, f"Expected exactly 25 rejections, got {len(rejections)}"

    # 3. Check final balances
    bal_src = (await client.get(f"/v1/accounts/{src_id}/balance")).json()
    assert bal_src["settled_balance_cents"] == 0
    assert bal_src["settled_balance_formatted"] == "$0.00"

    bal_dst = (await client.get(f"/v1/accounts/{dst_id}/balance")).json()
    assert bal_dst["settled_balance_cents"] == 5000
    assert bal_dst["settled_balance_formatted"] == "$50.00"


@pytest.mark.asyncio
async def test_concurrent_bidirectional_transfers_no_deadlocks(client: AsyncClient):
    """
    Bidirectional Stress Test:
    Account A ($500.00) and Account B ($500.00).
    20 workers transfer $5.00 from A to B while 20 workers transfer $5.00 from B to A concurrently.
    In naive systems this triggers Postgres 40P01 deadlock detected.
    With LedgerCore's deterministic lock ordering (ORDER BY account_id ASC FOR UPDATE),
    circular wait is mathematically eliminated.
    Asserts:
    1. 100% of the 40 requests succeed with HTTP 201.
    2. Zero deadlocks.
    3. Final balances match initial balances ($500.00 each).
    """
    acc_a_res = await client.post("/v1/accounts", json={"name": "Bidirectional A", "type": "ASSET", "allow_overdraft": False})
    acc_a_id = acc_a_res.json()["id"]

    acc_b_res = await client.post("/v1/accounts", json={"name": "Bidirectional B", "type": "ASSET", "allow_overdraft": False})
    acc_b_id = acc_b_res.json()["id"]

    vault_res = await client.post("/v1/accounts", json={"name": "Bidirectional Vault", "type": "EQUITY", "allow_overdraft": True})
    vault_id = vault_res.json()["id"]

    # Fund both with $500.00 (50,000 cents)
    await client.post("/v1/transactions", json={
        "description": "Fund Bidirectional Accounts",
        "entries": [
            {"account_id": acc_a_id, "direction": "DEBIT", "amount_cents": 50000},
            {"account_id": acc_b_id, "direction": "DEBIT", "amount_cents": 50000},
            {"account_id": vault_id, "direction": "CREDIT", "amount_cents": 100000},
        ]
    })

    async def transfer_a_to_b(idx: int):
        return await client.post("/v1/transfers", json={
            "source_account_id": acc_a_id,
            "destination_account_id": acc_b_id,
            "amount_cents": 500,  # $5.00
            "description": f"A to B #{idx}",
            "idempotency_key": f"a-to-b-{idx}"
        })

    async def transfer_b_to_a(idx: int):
        return await client.post("/v1/transfers", json={
            "source_account_id": acc_b_id,
            "destination_account_id": acc_a_id,
            "amount_cents": 500,  # $5.00
            "description": f"B to A #{idx}",
            "idempotency_key": f"b-to-a-{idx}"
        })

    tasks = []
    for i in range(20):
        tasks.append(transfer_a_to_b(i))
        tasks.append(transfer_b_to_a(i))

    responses = await asyncio.gather(*tasks)

    statuses = [r.status_code for r in responses]
    assert all(s == 201 for s in statuses), f"Failed statuses: {[s for s in statuses if s != 201]}"

    # Verify final balances are unchanged ($500.00 each)
    bal_a = (await client.get(f"/v1/accounts/{acc_a_id}/balance")).json()
    assert bal_a["settled_balance_cents"] == 50000

    bal_b = (await client.get(f"/v1/accounts/{acc_b_id}/balance")).json()
    assert bal_b["settled_balance_cents"] == 50000
