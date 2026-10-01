from enum import Enum
from typing import List, Optional, Dict, Any
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, Field, model_validator, ConfigDict


class EntryDirection(str, Enum):
    DEBIT = "DEBIT"
    CREDIT = "CREDIT"


class LedgerLeg(BaseModel):
    account_id: UUID = Field(..., description="Target account UUID")
    direction: EntryDirection = Field(..., description="DEBIT or CREDIT")
    amount_cents: int = Field(..., gt=0, description="Positive integer amount in cents")


class TransactionCreate(BaseModel):
    description: str = Field(..., min_length=1, max_length=1000, description="Journal entry description")
    entries: List[LedgerLeg] = Field(..., min_length=2, description="At least two balanced ledger legs")
    currency: str = Field(default="USD", min_length=3, max_length=3)
    idempotency_key: Optional[str] = Field(default=None, max_length=255)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_zero_sum(self) -> "TransactionCreate":
        total_debits = sum(e.amount_cents for e in self.entries if e.direction == EntryDirection.DEBIT)
        total_credits = sum(e.amount_cents for e in self.entries if e.direction == EntryDirection.CREDIT)
        if total_debits != total_credits:
            raise ValueError(
                f"Double-entry zero-sum invariant violated: Total debits ({total_debits} cents) "
                f"do not equal total credits ({total_credits} cents)."
            )
        return self


class TransferRequest(BaseModel):
    """
    Convenient high-level representation for a 2-legged transfer between two accounts.
    """
    source_account_id: UUID = Field(..., description="Debited/Credited account losing funds")
    destination_account_id: UUID = Field(..., description="Credited/Debited account receiving funds")
    amount_cents: int = Field(..., gt=0, description="Amount in cents (e.g. 5000 = $50.00)")
    description: str = Field(..., min_length=1, max_length=1000)
    idempotency_key: Optional[str] = Field(default=None, max_length=255)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_distinct_accounts(self) -> "TransferRequest":
        if self.source_account_id == self.destination_account_id:
            raise ValueError("Source and destination accounts must be distinct.")
        return self


class LedgerEntryResponse(BaseModel):
    id: UUID
    transaction_id: UUID
    account_id: UUID
    account_name: Optional[str] = None
    account_type: Optional[str] = None
    direction: EntryDirection
    amount_cents: int
    amount_formatted: str
    sequence: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TransactionResponse(BaseModel):
    id: UUID
    description: str
    status: str
    currency: str
    idempotency_key: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    posted_at: datetime
    created_at: datetime
    entries: List[LedgerEntryResponse] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)
