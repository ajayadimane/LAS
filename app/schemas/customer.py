"""Our contract for a signed-in customer's own details. Not the vendor's shape."""

from pydantic import BaseModel, Field

from app.schemas.person import PostalAddress


class CustomerProfile(BaseModel):
    """What the customer may see of their own record.

    Leaves out the settlement account, CIF, search keys and custom fields.
    """

    person_id: str
    first_name: str | None = None
    last_name: str | None = None
    mobile_number: str | None = None
    mobile_country_code: str | None = None
    email_address: str | None = None
    address: PostalAddress = Field(default_factory=PostalAddress)
    broker_id: str | None = Field(
        default=None, description="The id of the customer's BD."
    )
