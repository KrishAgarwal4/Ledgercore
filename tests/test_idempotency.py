import pytest
from httpx import AsyncClient
import asyncpg


@pytest.mark.asyncio
async def test_idempotent_replay_returns_cached_response(client: AsyncClient):
    """
    Submitting the exact same request with the same Idempotency-Key
    must return the exact cached response (with X-Idempotent-Replay: true)
    without executing a duplicate transaction or creating duplicate ledger entries.
    """
    acc1_res = await client.post("/v1/accounts", json={"name": "Source Acc", "type": "ASSET", "allow_overdraft": True})
    acc1_id = acc1_res.json()["id"]

    acc2_res = await client.post("/v1/accounts", json={"name": "Dest Acc", "type": "ASSET", "allow_overdraft": False})
    acc2_id = acc2_res.json()["id"]

    idempotency_key = "test-key-uuid-12345"
    payload = {
        "source_account_id": acc1_id,
        "destination_account_id": acc2_id,
        "amount_cents": 2500,
        "description": "Unique idempotent charge"
    }

    # First request
    res1 = await client.post("/v1/transfers", json=payload, headers={"Idempotency-Key": idempotency_key})
    assert res1.status_code == 201
    assert "X-Idempotent-Replay" not in res1.headers
    tx1 = res1.json()

    # Second request: identical key and payload
    res2 = await client.post("/v1/transfers", json=payload, headers={"Idempotency-Key": idempotency_key})
    assert res2.status_code == 201
    assert res2.headers.get("X-Idempotent-Replay") == "true"
    tx2 = res2.json()

    # Must be exact same transaction ID
    assert tx1["id"] == tx2["id"]

    # Balance of dest account must be exactly $25.00 (NOT $50.00!)
    bal_res = await client.get(f"/v1/accounts/{acc2_id}/balance")
    assert bal_res.json()["settled_balance_cents"] == 2500


@pytest.mark.asyncio
async def test_idempotency_payload_mismatch_rejected(client: AsyncClient):
    """
    Reusing an Idempotency-Key with a modified payload must be rejected
    with HTTP 422 Unprocessable Entity and code idempotency_payload_mismatch.
    """
    acc1_res = await client.post("/v1/accounts", json={"name": "Source Mismatch", "type": "ASSET", "allow_overdraft": True})
    acc1_id = acc1_res.json()["id"]

    acc2_res = await client.post("/v1/accounts", json={"name": "Dest Mismatch", "type": "ASSET", "allow_overdraft": False})
    acc2_id = acc2_res.json()["id"]

    idempotency_key = "test-key-mismatch-999"

    # First request with amount $10.00
    res1 = await client.post("/v1/transfers", json={
        "source_account_id": acc1_id,
        "destination_account_id": acc2_id,
        "amount_cents": 1000,
        "description": "First payload"
    }, headers={"Idempotency-Key": idempotency_key})
    assert res1.status_code == 201

    # Second request with SAME KEY but amount $20.00
    res2 = await client.post("/v1/transfers", json={
        "source_account_id": acc1_id,
        "destination_account_id": acc2_id,
        "amount_cents": 2000,
        "description": "Second altered payload"
    }, headers={"Idempotency-Key": idempotency_key})
    assert res2.status_code == 422
    data = res2.json()
    assert data["error"]["code"] == "idempotency_payload_mismatch"
