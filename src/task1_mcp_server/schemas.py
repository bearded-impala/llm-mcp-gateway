"""Request and response schemas for customer support tools."""

from pydantic import BaseModel, ConfigDict, Field

CUSTOMER_ID_REGEX = r"^CUST-[A-Za-z0-9]{5}$"


class CustomerRecordInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    customer_id: str = Field(
        ...,
        pattern=CUSTOMER_ID_REGEX,
        description="Customer ID in CUST-XXXXX format",
        examples=["CUST-A1B2C", "CUST-98765"],
    )


class RefundInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    customer_id: str = Field(
        ...,
        pattern=CUSTOMER_ID_REGEX,
        description="Customer ID in CUST-XXXXX format",
    )
    amount: float = Field(
        ...,
        gt=0.0,
        description="Refund amount",
        examples=[49.99, 150.0],
    )
    reason: str = Field(
        ...,
        min_length=10,
        description="Reason for refund (minimum 10 characters)",
        examples=["Customer requested cancellation within 30 days"],
    )


class CustomerRecordOutput(BaseModel):
    customer_id: str
    name: str
    status: str
    tier: str
    balance: float


class RefundOutput(BaseModel):
    refund_id: str
    customer_id: str
    amount: float
    reason: str
    status: str
