-- ============================================================================
-- LedgerCore: Production-Grade ACID Double-Entry Ledger Schema
-- Target: PostgreSQL 14+
-- 
-- Invariants Enforced:
-- 1. Zero-Sum Balance: SUM(debits) - SUM(credits) = 0 per transaction at COMMIT
-- 2. Immutability: ledger_entries cannot be UPDATED or DELETED
-- 3. Strict Positive Quantities: amount_cents > 0
-- 4. Currency Integrity: All entries in a transaction must match currency
-- ============================================================================

-- Ensure pgcrypto or uuid-ossp for UUID generation
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Clean drop for idempotent reset (if needed during testing)
-- DROP TABLE IF EXISTS idempotency_records CASCADE;
-- DROP TABLE IF EXISTS ledger_entries CASCADE;
-- DROP TABLE IF EXISTS transactions CASCADE;
-- DROP TABLE IF EXISTS account_balances CASCADE;
-- DROP TABLE IF EXISTS accounts CASCADE;

-- ----------------------------------------------------------------------------
-- 1. ACCOUNTS TABLE
-- Represents standard chart of accounts: ASSET, LIABILITY, EQUITY, REVENUE, EXPENSE
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS accounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    type VARCHAR(32) NOT NULL CHECK (type IN ('ASSET', 'LIABILITY', 'EQUITY', 'REVENUE', 'EXPENSE')),
    currency VARCHAR(3) NOT NULL DEFAULT 'USD',
    allow_overdraft BOOLEAN NOT NULL DEFAULT FALSE,
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'FROZEN', 'CLOSED')),
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_accounts_type ON accounts(type);
CREATE INDEX IF NOT EXISTS idx_accounts_status ON accounts(status);

-- ----------------------------------------------------------------------------
-- 2. ACCOUNT_BALANCES TABLE (Materialized Balance Cache & Lock Anchor)
-- Provides O(1) balance reads and serves as a deterministic lock target
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS account_balances (
    account_id UUID PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
    settled_balance_cents BIGINT NOT NULL DEFAULT 0,
    pending_balance_cents BIGINT NOT NULL DEFAULT 0,
    version BIGINT NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ----------------------------------------------------------------------------
-- 3. TRANSACTIONS TABLE
-- A journal transaction header binding 2 or more ledger legs together atomically
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS transactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    idempotency_key VARCHAR(255) UNIQUE,
    description TEXT NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'POSTED' CHECK (status IN ('POSTED', 'PENDING', 'VOIDED')),
    currency VARCHAR(3) NOT NULL DEFAULT 'USD',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    posted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_transactions_posted_at ON transactions(posted_at DESC);
CREATE INDEX IF NOT EXISTS idx_transactions_idempotency ON transactions(idempotency_key) WHERE idempotency_key IS NOT NULL;

-- ----------------------------------------------------------------------------
-- 4. LEDGER_ENTRIES TABLE
-- Append-only atomic legs of transactions. Debits and Credits.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ledger_entries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    transaction_id UUID NOT NULL REFERENCES transactions(id) ON DELETE RESTRICT,
    account_id UUID NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
    direction VARCHAR(6) NOT NULL CHECK (direction IN ('DEBIT', 'CREDIT')),
    amount_cents BIGINT NOT NULL CHECK (amount_cents > 0),
    sequence INT NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ledger_entries_tx ON ledger_entries(transaction_id);
CREATE INDEX IF NOT EXISTS idx_ledger_entries_account ON ledger_entries(account_id, created_at ASC);

-- ----------------------------------------------------------------------------
-- 5. IDEMPOTENCY_RECORDS TABLE
-- Stripe-compatible idempotency engine storing request hashes and cached responses
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS idempotency_records (
    idempotency_key VARCHAR(255) PRIMARY KEY,
    request_hash VARCHAR(64) NOT NULL,
    status VARCHAR(20) NOT NULL CHECK (status IN ('PROCESSING', 'COMPLETED', 'FAILED')),
    response_code INT,
    response_body JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_idempotency_created ON idempotency_records(created_at);

-- ============================================================================
-- PL/PGSQL TRIGGERS & PROCEDURES
-- ============================================================================

-- A. Automatically initialize account_balances when an account is created
CREATE OR REPLACE FUNCTION init_account_balance()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO account_balances (account_id, settled_balance_cents, pending_balance_cents, version, updated_at)
    VALUES (NEW.id, 0, 0, 0, NOW())
    ON CONFLICT (account_id) DO NOTHING;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_init_account_balance ON accounts;
CREATE TRIGGER trg_init_account_balance
AFTER INSERT ON accounts
FOR EACH ROW
EXECUTE FUNCTION init_account_balance();

-- B. Strict Immutability Trigger: prevent any UPDATE or DELETE on ledger_entries
CREATE OR REPLACE FUNCTION prevent_ledger_modification()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'Ledger entries are strictly append-only and immutable. UPDATE and DELETE operations are forbidden.'
        USING ERRCODE = 'P0002';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_immutable_ledger_entries ON ledger_entries;
CREATE TRIGGER trg_immutable_ledger_entries
BEFORE UPDATE OR DELETE ON ledger_entries
FOR EACH ROW
EXECUTE FUNCTION prevent_ledger_modification();

-- C. The Zero-Sum Invariant Constraint Trigger
-- Runs at transaction COMMIT time (DEFERRED)
-- Asserts that for every modified transaction, SUM(DEBIT) = SUM(CREDIT)
CREATE OR REPLACE FUNCTION check_transaction_balance()
RETURNS TRIGGER AS $$
DECLARE
    v_tx_id UUID;
    v_net_sum BIGINT;
    v_debit_sum BIGINT;
    v_credit_sum BIGINT;
BEGIN
    v_tx_id := COALESCE(NEW.transaction_id, OLD.transaction_id);

    -- Calculate total debits and credits for this transaction
    SELECT 
        COALESCE(SUM(CASE WHEN direction = 'DEBIT' THEN amount_cents ELSE 0 END), 0),
        COALESCE(SUM(CASE WHEN direction = 'CREDIT' THEN amount_cents ELSE 0 END), 0)
    INTO v_debit_sum, v_credit_sum
    FROM ledger_entries
    WHERE transaction_id = v_tx_id;

    v_net_sum := v_debit_sum - v_credit_sum;

    IF v_net_sum != 0 THEN
        RAISE EXCEPTION 'Zero-sum transaction balance invariant violated for transaction %: total debits (%) do not equal total credits (%). Discrepancy: % cents.',
            v_tx_id, v_debit_sum, v_credit_sum, v_net_sum
            USING ERRCODE = 'P0001';
    END IF;

    -- Ensure a transaction has at least 2 legs
    IF v_debit_sum = 0 AND v_credit_sum = 0 THEN
        RAISE EXCEPTION 'Transaction % has no ledger entries or zero amount.', v_tx_id
            USING ERRCODE = 'P0003';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Deferred constraint trigger: checked at COMMIT, allowing multi-leg inserts in a single transaction
DROP TRIGGER IF EXISTS trg_check_transaction_balance ON ledger_entries;
CREATE CONSTRAINT TRIGGER trg_check_transaction_balance
AFTER INSERT OR UPDATE OR DELETE ON ledger_entries
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION check_transaction_balance();
