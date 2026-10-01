from enum import Enum
from typing import Optional
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, Field, ConfigDict


class AccountType(str, Enum):
    ASSET = "ASSET"
    LIABILITY = "LIABILITY"
    EQUITY = "EQUITY"
    REVENUE = "REVENUE"
    EXPENSE = "EXPENSE"


class AccountStatus(str, Enum):
    ACTIVE = "ACTIVE"
    FROZEN = "FROZEN"
    CLOSED = "CLOSED"


class AccountCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=255, description="Human-readable account label")
    type: AccountType = Field(..., description="Chart of accounts classification")
    currency: str = Field(default="USD", min_length=3, max_length=3, description="ISO-4217 3-letter currency code")
    allow_overdraft: bool = Field(default=False, description="Whether balance is permitted to drop below 0")
    description: Optional[str] = Field(default=None, max_length=1000)

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "Customer Alice Checking",
                "type": "ASSET",
                "currency": "USD",
                "allow_overdraft": False,
                "description": "User deposit account for Alice"
            }
        }
    )


class AccountResponse(BaseModel):
    id: UUID
    name: str
    type: AccountType
    currency: str
    allow_overdraft: bool
    status: AccountStatus
    description: Optional[str] = None
    created_at: datetime
    settled_balance_cents: int = 0
    pending_balance_cents: int = 0
    settled_balance_formatted: str = "$0.00"

    model_config = ConfigDict(from_attributes=True)


class AccountBalanceResponse(BaseModel):
    account_id: UUID
    name: str
    type: AccountType
    currency: str
    settled_balance_cents: int
    pending_balance_cents: int
    settled_balance_formatted: str
    version: int
    updated_at: datetime
