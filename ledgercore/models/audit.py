from typing import List, Optional
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, ConfigDict
from ledgercore.models.account import AccountType
from ledgercore.models.transaction import EntryDirection


class AccountRunningBalanceItem(BaseModel):
    posted_at: datetime
    transaction_id: UUID
    description: str
    direction: EntryDirection
    amount_cents: int
    amount_formatted: str
    running_balance_cents: int
    running_balance_formatted: str

    model_config = ConfigDict(from_attributes=True)


class AccountAuditResponse(BaseModel):
    account_id: UUID
    name: str
    type: AccountType
    currency: str
    settled_balance_cents: int
    settled_balance_formatted: str
    entries: List[AccountRunningBalanceItem]
    is_reconciled: bool
    reconciliation_notes: str


class AccountDiscrepancy(BaseModel):
    account_id: UUID
    account_name: str
    account_type: AccountType
    cached_settled_cents: int
    raw_computed_cents: int
    discrepancy_cents: int


class ReconciliationReport(BaseModel):
    total_accounts_audited: int
    balanced_accounts: int
    discrepant_accounts: int
    discrepancies: List[AccountDiscrepancy]
    audit_timestamp: datetime
    system_healthy: bool
