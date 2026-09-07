"""Gateway error response schemas."""

from pydantic import BaseModel


class GatewayErrorDetail(BaseModel):
    code: str
    message: str
    request_id: str
    retry_after: int | None = None


class StandardGatewayError(BaseModel):
    error: GatewayErrorDetail
