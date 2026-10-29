"""Shared response pieces for our own API contract."""

from pydantic import BaseModel, Field


class VendorAck(BaseModel):
    """What the vendor returns when it creates something: ids and nothing else.

    The vendor never echoes the created resource, so there is no id to return
    beyond these. Both ids are kept because the vendor asks callers to quote
    them in support tickets.
    """

    transaction_id: str | None = Field(default=None)
    correlation_id: str | None = Field(default=None)
    timestamp: str | None = Field(default=None)

    @classmethod
    def from_vendor(cls, body: object) -> "VendorAck":
        if not isinstance(body, dict):
            return cls()
        return cls(
            transaction_id=body.get("transactionId"),
            correlation_id=body.get("correlationId"),
            timestamp=body.get("timeStamp"),
        )


class HealthResponse(BaseModel):
    status: str
    app: str
    environment: str
