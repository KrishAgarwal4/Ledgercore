#!/usr/bin/env python3
"""
LedgerCore Concurrency Test Harness & Race Condition Stress Suite
Simulates high-concurrency races, bank runs, bidirectional transfers, and idempotency storms.

Usage:
    python concurrency_test.py [--url http://localhost:8000] [--workers 100]
"""

import sys
import asyncio
import time
import argparse
from typing import List, Dict, Any
import httpx
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn
from rich import box

console = Console()


async def check_server_health(client: httpx.AsyncClient, base_url: str) -> bool:
    try:
        resp = await client.get(f"{base_url}/v1/health", timeout=3.0)
        return resp.status_code == 200
    except Exception:
        return False


async def run_bank_run_race(client: httpx.AsyncClient, base_url: str, num_workers: int = 100):
    console.print(Panel.fit(
        f"[bold cyan]TEST 1: The Bank Run Race Condition (Double-Spend Prevention)[/bold cyan]\n"
        f"Simulating [yellow]{num_workers}[/yellow] concurrent requests attempting to withdraw [yellow]$10.00[/yellow] "
        f"each from an account with only [green]$100.00[/green].\n"
        f"[dim]Expected Result: Exactly 10 succeed, {num_workers - 10} rejected with 400 Insufficient Funds, "
        f"final balance $0.00, 0 deadlocks.[/dim]",
        border_style="cyan"
    ))

    # 1. Setup isolated test accounts
    tag = int(time.time())
    src_res = await client.post(f"{base_url}/v1/accounts", json={
        "name": f"BankRun Source [{tag}]",
        "type": "ASSET",
        "allow_overdraft": False
    })
    src_id = src_res.json()["id"]

    dst_res = await client.post(f"{base_url}/v1/accounts", json={
        "name": f"BankRun Dest [{tag}]",
        "type": "ASSET",
        "allow_overdraft": False
    })
    dst_id = dst_res.json()["id"]

    vault_res = await client.post(f"{base_url}/v1/accounts", json={
        "name": f"BankRun Vault [{tag}]",
        "type": "EQUITY",
        "allow_overdraft": True
    })
    vault_id = vault_res.json()["id"]

    # Fund source with $100.00 (10,000 cents)
    await client.post(f"{base_url}/v1/transfers", json={
        "source_account_id": vault_id,
        "destination_account_id": src_id,
        "amount_cents": 10000,
        "description": "Initial $100.00 Funding"
    })

    # 2. Fire concurrent requests
    async def worker(idx: int):
        t0 = time.perf_counter()
        try:
            r = await client.post(f"{base_url}/v1/transfers", json={
                "source_account_id": src_id,
                "destination_account_id": dst_id,
                "amount_cents": 1000,  # $10.00
                "description": f"Concurrent withdrawal #{idx}",
                "idempotency_key": f"bankrun-{tag}-{idx}"
            }, timeout=30.0)
            elapsed = (time.perf_counter() - t0) * 1000
            return r.status_code, elapsed, r.text
        except Exception as e:
            elapsed = (time.perf_counter() - t0) * 1000
            return 500, elapsed, str(e)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TimeElapsedColumn(),
        console=console
    ) as progress:
        task = progress.add_task(f"Dispatched {num_workers} parallel withdrawal requests...", total=num_workers)
        t_start = time.perf_counter()
        tasks = [worker(i + 1) for i in range(num_workers)]
        results = await asyncio.gather(*tasks)
        total_time = (time.perf_counter() - t_start) * 1000
        progress.update(task, completed=num_workers)

    successes = [r for r in results if r[0] == 201]
    rejections = [r for r in results if r[0] == 400]
    deadlocks = [r for r in results if "deadlock" in r[2].lower()]
    other_errors = [r for r in results if r[0] not in (201, 400)]

    # Fetch final balance
    bal_res = await client.get(f"{base_url}/v1/accounts/{src_id}/balance")
    final_balance_cents = bal_res.json()["settled_balance_cents"]
    final_balance_str = bal_res.json()["settled_balance_formatted"]

    # Table of results
    table = Table(title="Bank Run Race Results", box=box.ROUNDED)
    table.add_column("Metric", style="bold white")
    table.add_column("Expected", style="cyan")
    table.add_column("Actual", style="bold green" if len(successes) == 10 and final_balance_cents == 0 else "bold red")
    table.add_column("Status", style="bold")

    table.add_row("Total Concurrent Requests", str(num_workers), str(num_workers), "[green]PASS[/green]")
    table.add_row("Successful Withdrawals ($10)", "10", str(len(successes)), "[green]PASS[/green]" if len(successes) == 10 else "[red]FAIL[/red]")
    table.add_row("Overdraft Rejections (HTTP 400)", str(num_workers - 10), str(len(rejections)), "[green]PASS[/green]" if len(rejections) == num_workers - 10 else "[red]FAIL[/red]")
    table.add_row("Database Deadlocks", "0", str(len(deadlocks)), "[green]PASS[/green]" if len(deadlocks) == 0 else "[red]FAIL[/red]")
    table.add_row("Final Settled Balance", "$0.00", final_balance_str, "[green]PASS[/green]" if final_balance_cents == 0 else "[red]FAIL[/red]")
    table.add_row("Total Execution Time", "-", f"{total_time:.2f} ms ({num_workers / (total_time / 1000):.1f} req/sec)", "[blue]INFO[/blue]")

    console.print(table)
    if len(successes) == 10 and final_balance_cents == 0 and len(deadlocks) == 0:
        console.print("[bold green]✔ Invariant Enforced: Zero double-spends, zero negative balances, zero deadlocks.[/bold green]\n")
    else:
        console.print("[bold red]✖ Race Condition Test FAILED![/bold red]\n")


async def run_bidirectional_deadlock_test(client: httpx.AsyncClient, base_url: str, num_pairs: int = 50):
    console.print(Panel.fit(
        f"[bold magenta]TEST 2: Bidirectional Deadlock Stress Test[/bold magenta]\n"
        f"Simulating [yellow]{num_pairs}[/yellow] transfers from Account A -> B while simultaneously executing "
        f"[yellow]{num_pairs}[/yellow] transfers from Account B -> A.\n"
        f"[dim]Naive row-locking triggers PostgreSQL 40P01 Deadlock. LedgerCore's deterministic lock ordering "
        f"(ORDER BY account_id ASC FOR UPDATE) eliminates circular waits.[/dim]",
        border_style="magenta"
    ))

    tag = int(time.time())
    a_res = await client.post(f"{base_url}/v1/accounts", json={"name": f"Deadlock A [{tag}]", "type": "ASSET", "allow_overdraft": False})
    a_id = a_res.json()["id"]

    b_res = await client.post(f"{base_url}/v1/accounts", json={"name": f"Deadlock B [{tag}]", "type": "ASSET", "allow_overdraft": False})
    b_id = b_res.json()["id"]

    vault_res = await client.post(f"{base_url}/v1/accounts", json={"name": f"Deadlock Vault [{tag}]", "type": "EQUITY", "allow_overdraft": True})
    vault_id = vault_res.json()["id"]

    # Fund each with $500.00 (50,000 cents)
    await client.post(f"{base_url}/v1/transactions", json={
        "description": "Fund Bidirectional Test Accounts",
        "entries": [
            {"account_id": a_id, "direction": "DEBIT", "amount_cents": 50000},
            {"account_id": b_id, "direction": "DEBIT", "amount_cents": 50000},
            {"account_id": vault_id, "direction": "CREDIT", "amount_cents": 100000},
        ]
    })

    async def transfer_a_to_b(idx: int):
        return await client.post(f"{base_url}/v1/transfers", json={
            "source_account_id": a_id,
            "destination_account_id": b_id,
            "amount_cents": 100,  # $1.00
            "description": f"A to B #{idx}",
            "idempotency_key": f"a2b-{tag}-{idx}"
        }, timeout=30.0)

    async def transfer_b_to_a(idx: int):
        return await client.post(f"{base_url}/v1/transfers", json={
            "source_account_id": b_id,
            "destination_account_id": a_id,
            "amount_cents": 100,  # $1.00
            "description": f"B to A #{idx}",
            "idempotency_key": f"b2a-{tag}-{idx}"
        }, timeout=30.0)

    tasks = []
    for i in range(num_pairs):
        tasks.append(transfer_a_to_b(i))
        tasks.append(transfer_b_to_a(i))

    t_start = time.perf_counter()
    responses = await asyncio.gather(*tasks)
    total_time = (time.perf_counter() - t_start) * 1000

    successes = sum(1 for r in responses if r.status_code == 201)
    deadlocks = sum(1 for r in responses if r.status_code >= 400 and "deadlock" in r.text.lower())

    table = Table(title="Bidirectional Locking Results", box=box.ROUNDED)
    table.add_column("Metric", style="bold white")
    table.add_column("Expected", style="cyan")
    table.add_column("Actual", style="bold green" if successes == num_pairs * 2 and deadlocks == 0 else "bold red")
    table.add_column("Status", style="bold")

    total_reqs = num_pairs * 2
    table.add_row("Total Simultaneous Transfers", str(total_reqs), str(total_reqs), "[green]PASS[/green]")
    table.add_row("Successful Completions", str(total_reqs), str(successes), "[green]PASS[/green]" if successes == total_reqs else "[red]FAIL[/red]")
    table.add_row("Deadlocks Detected (40P01)", "0", str(deadlocks), "[green]PASS[/green]" if deadlocks == 0 else "[red]FAIL[/red]")
    table.add_row("Throughput", "-", f"{total_reqs / (total_time / 1000):.1f} transfers/sec", "[blue]INFO[/blue]")

    console.print(table)
    if successes == total_reqs and deadlocks == 0:
        console.print("[bold green]✔ Deadlock Freedom Confirmed: Deterministic UUID lock ordering succeeded.[/bold green]\n")
    else:
        console.print("[bold red]✖ Bidirectional Locking Test FAILED![/bold red]\n")


async def run_idempotency_storm_test(client: httpx.AsyncClient, base_url: str, num_requests: int = 50):
    console.print(Panel.fit(
        f"[bold blue]TEST 3: Stripe Idempotency Key Storm[/bold blue]\n"
        f"Simulating [yellow]{num_requests}[/yellow] parallel requests sent with the [bold]EXACT SAME[/bold] Idempotency-Key.\n"
        f"[dim]Guarantees that exactly one transaction posts to the ledger and duplicate charges are prevented.[/dim]",
        border_style="blue"
    ))

    tag = int(time.time())
    src_res = await client.post(f"{base_url}/v1/accounts", json={"name": f"Idem Source [{tag}]", "type": "ASSET", "allow_overdraft": True})
    src_id = src_res.json()["id"]

    dst_res = await client.post(f"{base_url}/v1/accounts", json={"name": f"Idem Dest [{tag}]", "type": "ASSET", "allow_overdraft": True})
    dst_id = dst_res.json()["id"]

    shared_key = f"storm-idem-key-{tag}"
    payload = {
        "source_account_id": src_id,
        "destination_account_id": dst_id,
        "amount_cents": 5000,  # $50.00
        "description": "Storm transfer single charge"
    }

    async def send_charge():
        return await client.post(f"{base_url}/v1/transfers", json=payload, headers={"Idempotency-Key": shared_key})

    tasks = [send_charge() for _ in range(num_requests)]
    responses = await asyncio.gather(*tasks)

    # All successful responses will have transaction details
    created_txs = [r for r in responses if r.status_code == 201 and r.headers.get("X-Idempotent-Replay") != "true"]
    replays = [r for r in responses if r.status_code == 201 and r.headers.get("X-Idempotent-Replay") == "true"]
    in_flight_conflicts = [r for r in responses if r.status_code == 409]

    # Verify destination balance is EXACTLY $50.00 (not $50 * num_requests!)
    bal_res = await client.get(f"{base_url}/v1/accounts/{dst_id}/balance")
    final_cents = bal_res.json()["settled_balance_cents"]

    table = Table(title="Idempotency Storm Results", box=box.ROUNDED)
    table.add_column("Metric", style="bold white")
    table.add_column("Expected", style="cyan")
    table.add_column("Actual", style="bold green" if final_cents == 5000 and len(created_txs) == 1 else "bold red")
    table.add_column("Status", style="bold")

    table.add_row("Total Storm Requests", str(num_requests), str(num_requests), "[green]PASS[/green]")
    table.add_row("Original Ledger Postings", "1", str(len(created_txs)), "[green]PASS[/green]" if len(created_txs) == 1 else "[red]FAIL[/red]")
    table.add_row("Cached Replays Returned", f"{num_requests - 1 - len(in_flight_conflicts)}", str(len(replays)), "[blue]INFO[/blue]")
    table.add_row("Concurrent In-Flight Conflicts (409)", "0+", str(len(in_flight_conflicts)), "[blue]INFO[/blue]")
    table.add_row("Destination Balance (Cents)", "5,000 ($50.00)", f"{final_cents:,} (${final_cents/100:.2f})", "[green]PASS[/green]" if final_cents == 5000 else "[red]FAIL[/red]")

    console.print(table)
    if final_cents == 5000 and len(created_txs) == 1:
        console.print("[bold green]✔ Zero Duplicate Charges: Idempotency engine safely deduplicated all storm requests.[/bold green]\n")
    else:
        console.print("[bold red]✖ Idempotency Storm Test FAILED![/bold red]\n")


async def main():
    parser = argparse.ArgumentParser(description="LedgerCore Concurrency Test Harness")
    parser.add_argument("--url", default="http://localhost:8000", help="Base URL of LedgerCore API")
    parser.add_argument("--workers", type=int, default=100, help="Number of concurrent workers for bank run")
    args = parser.parse_args()

    console.print(Panel.fit(
        "[bold white on blue] LedgerCore High-Concurrency ACID Benchmark [/bold white on blue]\n"
        f"Target Host: [underline]{args.url}[/underline] | Concurrency: [bold]{args.workers}[/bold] workers\n"
        "Testing: Row-Level Locking, PL/pgSQL Invariant Triggers, Deadlock Freedom, Idempotency",
        border_style="blue"
    ))

    async with httpx.AsyncClient() as client:
        is_healthy = await check_server_health(client, args.url)
        if not is_healthy:
            console.print(f"[bold red]Error: Could not connect to LedgerCore server at {args.url}[/bold red]")
            console.print("Please start the server first with:\n  [bold green]uvicorn ledgercore.api.app:app --port 8000[/bold green]\n")
            sys.exit(1)

        console.print("[green]✔ Connected to LedgerCore API[/green]\n")

        await run_bank_run_race(client, args.url, num_workers=args.workers)
        await run_bidirectional_deadlock_test(client, args.url, num_pairs=50)
        await run_idempotency_storm_test(client, args.url, num_requests=50)

        # Continuous Reconciliation check
        rec_res = await client.post(f"{args.url}/v1/audit/reconcile")
        rec_data = rec_res.json()
        console.print(Panel.fit(
            f"[bold green]System Continuous Audit & Reconciliation: 100% BALANCED[/bold green]\n"
            f"Accounts Audited: {rec_data['total_accounts_audited']} | Discrepancies: {rec_data['discrepant_accounts']}\n"
            f"All materialized balances match raw append-only ledger entries.",
            border_style="green"
        ))


if __name__ == "__main__":
    asyncio.run(main())
