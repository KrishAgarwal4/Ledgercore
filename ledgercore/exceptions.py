class LedgerCoreError(Exception):
    """Base exception for all LedgerCore domain errors."""
    def __init__(self, message: str, code: str = "ledger_error", status_code: int = 500):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code


class AccountNotFoundError(LedgerCoreError):
    def __init__(self, account_id: str):
        super().__init__(
            message=f"Account with id '{account_id}' was not found.",
            code="account_not_found",
            status_code=404
        )


class AccountFrozenError(LedgerCoreError):
    def __init__(self, account_id: str, status: str):
        super().__init__(
            message=f"Account '{account_id}' cannot transact because it is in '{status}' status.",
            code="account_frozen",
            status_code=403
        )


class InsufficientFundsError(LedgerCoreError):
    def __init__(self, account_id: str, available_balance_cents: int, requested_amount_cents: int):
        available_dollars = available_balance_cents / 100.0
        requested_dollars = requested_amount_cents / 100.0
        super().__init__(
            message=(
                f"Insufficient funds in account '{account_id}'. "
                f"Current settled balance is ${available_dollars:,.2f} ({available_balance_cents} cents), "
                f"requested transfer is ${requested_dollars:,.2f} ({requested_amount_cents} cents)."
            ),
            code="insufficient_funds",
            status_code=400
        )


class ZeroSumInvariantViolationError(LedgerCoreError):
    def __init__(self, debit_sum: int, credit_sum: int):
        super().__init__(
            message=(
                f"Double-entry zero-sum invariant violated: Total debits ({debit_sum} cents) "
                f"must exactly equal total credits ({credit_sum} cents). Difference: {debit_sum - credit_sum} cents."
            ),
            code="zero_sum_invariant_violation",
            status_code=422
        )


class CurrencyMismatchError(LedgerCoreError):
    def __init__(self, expected: str, actual: str):
        super().__init__(
            message=f"Currency mismatch in transaction: expected '{expected}', got '{actual}'.",
            code="currency_mismatch",
            status_code=400
        )


class InvalidTransactionError(LedgerCoreError):
    def __init__(self, message: str):
        super().__init__(
            message=message,
            code="invalid_transaction",
            status_code=400
        )


class LedgerImmutabilityError(LedgerCoreError):
    def __init__(self, message: str = "Ledger entries are strictly append-only and immutable."):
        super().__init__(
            message=message,
            code="ledger_immutable",
            status_code=403
        )


class IdempotencyConflictError(LedgerCoreError):
    def __init__(self, idempotency_key: str):
        super().__init__(
            message=f"A request with idempotency key '{idempotency_key}' is currently processing.",
            code="idempotency_conflict",
            status_code=409
        )


class IdempotencyPayloadMismatchError(LedgerCoreError):
    def __init__(self, idempotency_key: str):
        super().__init__(
            message=f"Idempotency key '{idempotency_key}' was previously used with a different request payload.",
            code="idempotency_payload_mismatch",
            status_code=422
        )
