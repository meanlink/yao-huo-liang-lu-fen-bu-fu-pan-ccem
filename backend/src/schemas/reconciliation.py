from pydantic import BaseModel, Field


class ReconciliationInput(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    target: float = Field(default=93.0, gt=0, le=100)
    items: list[dict] = Field(min_length=1, max_length=500)


class LedgerInput(BaseModel):
    ledger_total: int = Field(ge=0)
    expected_revision: int = Field(ge=1)


class SupplyInput(BaseModel):
    batch: str = Field(min_length=1, max_length=40)
    supplied_passed: int = Field(ge=0)
    expected_revision: int = Field(ge=1)


class ApproveInput(BaseModel):
    expected_revision: int = Field(ge=1)
