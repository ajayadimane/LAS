"""Our contract for persons.

Field constraints are the vendor's, from api_docs/ENDPOINTS.md > Field
validation reference. Validating them here turns a vendor round trip into an
immediate 422 with a useful message.
"""

from pydantic import BaseModel, EmailStr, Field

from app.schemas.common import VendorAck


class RegisterPersonRequest(BaseModel):
    # Optional: the vendor falls back to the mobile number when omitted.
    person_id: str | None = Field(
        default=None,
        pattern=r"^\S{1,50}$",
        description="1-50 characters, no spaces. Defaults to the mobile number.",
        examples=["88655489"],
    )
    first_name: str = Field(min_length=1, max_length=50)
    last_name: str | None = Field(default=None, min_length=1, max_length=50)
    email_address: EmailStr = Field(max_length=100)

    address_street_address: str = Field(max_length=100)
    address_city: str = Field(max_length=100)
    address_state_province: str = Field(min_length=2, max_length=100)
    address_postal_code: str = Field(min_length=5, max_length=12)
    address_country_code: str = Field(
        min_length=2, max_length=2, description="Exactly 2 characters.", examples=["IN"]
    )

    mobile_number: str = Field(
        pattern=r"^\d{10}$", description="Exactly 10 digits.", examples=["9824561458"]
    )
    # The vendor documents "exactly 2 digits", which excludes +1 and every
    # 3-digit calling code. Enforced as documented; see ENDPOINTS.md.
    mobile_country_code: str = Field(
        pattern=r"^\d{2}$", description="Exactly 2 digits.", examples=["91"]
    )

    settlement_account_no: str = Field(min_length=9, max_length=18)
    settlement_account_ifsc_code: str = Field(
        min_length=11, max_length=11, description="Exactly 11 characters."
    )

    group_id: str = Field(
        min_length=1,
        description="Business group, e.g. 'Merchant'. Not the IAM groupName.",
        examples=["Merchant"],
    )
    custom_fields: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Map of customFieldId to value. The vendor requires EVERY custom "
            "field defined for the program manager; call GET /custom-fields "
            "first to see which."
        ),
    )
    broker_id: str | None = Field(
        default=None,
        description="Required only when the group is configured to need a broker.",
    )
    program_manager_id: str | None = None


class RegisterPersonResponse(VendorAck):
    """The vendor returns ids only, never the created person.

    `person_id` is echoed back from the request so the dashboard does not have
    to hold onto it. It is null when the request omitted it, because the
    vendor then derives it from the mobile number and does not tell us what it
    chose.
    """

    person_id: str | None = None


def vendor_value(item: dict, path: str) -> str | None:
    """A value from a person-details body, as a string.

    The real body is flattened: nested values arrive under dotted keys such as
    `"contactInformation.postalAddress.city"`. A nested object is accepted
    too, in case another endpoint does not flatten.
    """
    value: object = item.get(path)
    if value is None and "." in path:
        value = item
        for part in path.split("."):
            value = value.get(part) if isinstance(value, dict) else None
    if value is None or isinstance(value, dict | list):
        return None
    return value if isinstance(value, str) else str(value)


class PostalAddress(BaseModel):
    street_address: str | None = None
    city: str | None = None
    state_province: str | None = None
    postal_code: str | None = None
    country_code: str | None = None


class PersonDetails(BaseModel):
    """A person as the dashboard sees it.

    Deliberately leaves out the settlement account, search keys and custom
    fields that the vendor also returns.
    """

    person_id: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    program_manager_id: str | None = None
    group_id: str | None = None
    broker_id: str | None = None
    mobile_number: str | None = None
    mobile_country_code: str | None = None
    email_address: str | None = None
    address: PostalAddress = Field(default_factory=PostalAddress)
    status: str | None = Field(default=None, examples=["ACTIVE"])
    created_at: str | None = None
    cif_number: str | None = None

    @classmethod
    def from_vendor(cls, item: object) -> "PersonDetails":
        if not isinstance(item, dict):
            return cls()

        def get(path: str) -> str | None:
            return vendor_value(item, path)

        addr = "contactInformation.postalAddress."
        return cls(
            person_id=get("personId"),
            first_name=get("firstName"),
            last_name=get("lastName"),
            program_manager_id=get("programManagerId"),
            group_id=get("groupId"),
            broker_id=get("brokerId.id") or get("brokerId"),
            mobile_number=get("mobileNumber"),
            mobile_country_code=get("contactInformation.mobileNumber.countryCode"),
            email_address=get("emailAddress"),
            address=PostalAddress(
                street_address=get(addr + "streetAddress"),
                city=get(addr + "city"),
                state_province=get(addr + "stateProvince"),
                postal_code=get(addr + "postalCode"),
                country_code=get(addr + "countryCode"),
            ),
            status=get("currentStatus.personStatus"),
            created_at=get("createdAt"),
            cif_number=get("cifNumber"),
        )


class PersonListResponse(BaseModel):
    """The vendor returns a bare array with no envelope and no total count.

    `count` is the length of this response only. The vendor documents no
    pagination and no behaviour on large result sets, so it must not be read
    as a total.
    """

    count: int
    persons: list[PersonDetails]
