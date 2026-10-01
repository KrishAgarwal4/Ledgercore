# ADR 001: Append-Only Immutable Ledger vs In-Place Balance Updates

## Context
Traditional relational database schemas frequently represent balances by modifying an account row directly (`UPDATE accounts SET balance = balance - 100 WHERE id = ...`). While computationally simple, in-place balance mutation introduces catastrophic failure modes in financial systems:
1. **Loss of Auditability:** Intermediate state transitions are overwritten and permanently erased. Forensic reconciliation of unauthorized adjustments or bugs becomes impossible.
2. **Reconciliation Ambiguity:** Discrepancies cannot be mathematically traced back to specific transactions.
3. **Race Conditions:** Naive updates rely on application-level checks that easily produce lost updates or negative balances under high concurrent load.

## Decision
LedgerCore implements a **strict append-only immutable double-entry ledger**:
- Every movement of money is represented as an immutable set of ledger entries bound to a transaction journal header.
- The `ledger_entries` table is protected by a PostgreSQL database trigger (`trg_immutable_ledger_entries`) that strictly raises an exception (SQLSTATE `P0002`) on any attempted `UPDATE` or `DELETE`.
- Corrections and reversals are modeled exclusively as new compensating transactions (reversals/refunds).

## Consequences
- **Positive:** Guaranteed non-repudiation and complete financial audit trails. Any historical point-in-time balance can be reconstructed with mathematical certainty.
- **Trade-off:** Purely calculating balances on-the-fly from the beginning of time is $O(N)$ with respect to total transaction history. LedgerCore mitigates this with a two-tier balance architecture (ADR 005) combining an $O(1)$ materialized cache with periodic automated reconciliation against raw entries.
