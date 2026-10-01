# ADR 005: Two-Tier Balance Architecture: Materialized Snapshots + Analytical Auditing

## Context
A pure append-only ledger requires calculating balances by summing every entry in an account's history:
$$\text{Balance}(t) = \sum_{i \in \text{entries}} \Delta \text{amount}_i$$
For accounts with millions of transactions (such as platform clearing accounts or active wallets), calculating $O(N)$ balances on every transfer or API balance inquiry introduces severe latency and massive database I/O pressure.

Conversely, relying solely on cached balances without continuous mathematical verification introduces the risk of cache drift or undetected corruption.

## Decision
LedgerCore implements a **Two-Tier Balance Architecture**:

### Tier 1: Real-Time Materialized Balance Cache (`account_balances`)
- Every account row in `accounts` is paired 1-to-1 with a row in `account_balances` storing `settled_balance_cents`, `pending_balance_cents`, and a monotonic `version` counter.
- Updates to `account_balances` occur inside the exact same ACID transaction that appends the `ledger_entries`.
- Row-level locks (`FOR UPDATE`) are taken on `account_balances` to guarantee serializable isolation for balance modifications while providing $O(1)$ read throughput for API consumers.

### Tier 2: Analytical Window Auditing & Continuous Reconciliation
- **Point-in-Time Auditing:** Reconstructs the exact cumulative running balance for any account across all historical journal entries using PostgreSQL analytical window functions:
  ```sql
  SELECT 
      t.posted_at,
      t.description,
      le.direction,
      le.amount_cents,
      SUM(CASE 
          WHEN (a.type IN ('ASSET', 'EXPENSE') AND le.direction = 'DEBIT') THEN le.amount_cents
          WHEN (a.type IN ('ASSET', 'EXPENSE') AND le.direction = 'CREDIT') THEN -le.amount_cents
          WHEN (a.type IN ('LIABILITY', 'EQUITY', 'REVENUE') AND le.direction = 'CREDIT') THEN le.amount_cents
          ELSE -le.amount_cents
      END) OVER (
          PARTITION BY le.account_id 
          ORDER BY t.posted_at ASC, le.id ASC
          ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
      ) AS running_balance_cents
  FROM ledger_entries le
  JOIN transactions t ON le.transaction_id = t.id
  JOIN accounts a ON le.account_id = a.id
  WHERE le.account_id = :target_account_id;
  ```
- **Automated System Reconciliation (`/v1/audit/reconcile`):** Executes an aggregated consistency query across every account in the database:
  $$\text{Discrepancy} = |\text{account\_balances.settled\_balance\_cents} - \sum \text{ledger\_entries}|$$
  If $\text{Discrepancy} > 0$ for any account, an immediate critical alert is reported.

## Consequences
- High-throughput $O(1)$ balance verification during transactional transfers.
- Absolute mathematical auditability and self-healing verification through PostgreSQL window functions.
