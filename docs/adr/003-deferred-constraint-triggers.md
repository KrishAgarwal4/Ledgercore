# ADR 003: Deferred Constraint Triggers for Atomic Zero-Sum Enforcement

## Context
The cardinal invariant of double-entry bookkeeping is that the sum of all debits must equal the sum of all credits for every transaction:
$$\sum \text{Debits} - \sum \text{Credits} = 0$$

In multi-legged transactions (e.g., Stripe payment processing involving customer wallet deduction, merchant liability credit, and platform processing fee revenue), entries must be inserted sequentially into the `ledger_entries` table. If a trigger validates balance after every single row insertion, the very first row inserted would immediately fail because the offsetting leg has not yet been written.

Conversely, enforcing the zero-sum invariant solely in application code (e.g. Python FastAPI) is fragile: a direct SQL script, manual database intervention, or application bug could write unbalanced entries and corrupt the entire financial system.

## Decision
LedgerCore implements a **PL/pgSQL Constraint Trigger** declared as `DEFERRABLE INITIALLY DEFERRED`:
```sql
CREATE CONSTRAINT TRIGGER trg_check_transaction_balance
AFTER INSERT OR UPDATE OR DELETE ON ledger_entries
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION check_transaction_balance();
```

### Mechanism
1. Within a database transaction, individual ledger legs can be inserted sequentially without immediate validation failures.
2. At the moment of `COMMIT`, PostgreSQL executes `check_transaction_balance()`.
3. The function computes the net balance of all entries for the given `transaction_id`:
   ```sql
   SELECT SUM(CASE WHEN direction = 'DEBIT' THEN amount_cents ELSE -amount_cents END)
   INTO v_net_balance
   FROM ledger_entries WHERE transaction_id = v_tx_id;
   ```
4. If `v_net_balance != 0`, the function raises SQLSTATE `P0001` (`Zero-sum transaction balance invariant violated`), causing PostgreSQL to automatically roll back the entire transaction.

## Consequences
- The zero-sum rule is enforced at the database kernel level with zero possibility of application bypass.
- Complex multi-party transactions (2, 3, or $N$ legs) are cleanly supported within standard ACID transaction boundaries.
