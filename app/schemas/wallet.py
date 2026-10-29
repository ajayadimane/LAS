"""Our contract for wallet reads. Not the vendor's shapes."""

from pydantic import BaseModel, Field


def _amount(body: dict, key: str) -> float | None:
    value = body.get(key)
    # bool is an int subclass; a true/false balance is not a balance.
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


class WalletBalance(BaseModel):
    """A wallet's balance in rupees, as the vendor reports it.

    The vendor sends JSON numbers, so these are floats: fine for display,
    not for arithmetic. An amount the vendor left out is null, never 0, so a
    malformed response cannot pass for an empty wallet.
    """

    wallet_id: str = Field(description="The BD id or customer id.")
    tag_id: str
    total_amount: float | None = Field(default=None, examples=[0.0])
    available_amount: float | None = Field(default=None, examples=[0.0])
    locked_amount: float | None = Field(default=None, examples=[0.0])

    @classmethod
    def from_vendor(cls, body: object, wallet_id: str, tag_id: str) -> "WalletBalance":
        if not isinstance(body, dict):
            body = {}
        return cls(
            wallet_id=wallet_id,
            tag_id=tag_id,
            total_amount=_amount(body, "totalAmount"),
            available_amount=_amount(body, "availableAmount"),
            locked_amount=_amount(body, "lockedAmount"),
        )
