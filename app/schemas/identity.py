"""Our request/response contract for identity. Not the vendor's shapes.

Passwords are SecretStr so that an accidental log, repr or traceback prints
`**********` instead of the value.
"""

from typing import Literal

from pydantic import BaseModel, EmailStr, Field, SecretStr

from app.schemas.common import VendorAck


class LoginRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=100, examples=["las"])
    password: SecretStr
    # Optional: falls back to settings.vendor_program_manager_id.
    program_manager_id: str | None = Field(default=None, examples=["LAS"])


class LoginResponse(BaseModel):
    """The vendor JWT, handed back to the dashboard to send on later calls.

    This service stores nothing. The dashboard holds the token and returns it
    in its own Authorization header, which we forward unchanged.

    The vendor documents no TTL, refresh or logout. The expiry here is read
    from the token's own `exp` claim (a real sandbox token lived 10 hours).
    There is no refresh, so the dashboard signs in again when it expires.
    """

    access_token: str
    token_type: str = "bearer"
    user_id: str = Field(
        description="The userId that logged in. The dashboard keeps it as the "
        "BD id (the brokerId of the customers it registers) or the customer id."
    )
    role: Literal["bd", "customer"] = Field(
        description="From the token's groupId: LAS.admin is a BD, LAS.enduser "
        "a customer. Any other account is refused at login (403)."
    )
    expires_in: int | None = Field(
        default=None,
        description="Seconds until the token expires, from its `exp` claim. "
        "Null if the token carries none.",
    )
    expires_at: int | None = Field(
        default=None, description="The token's `exp` claim, as Unix seconds."
    )
    transaction_id: str | None = None
    correlation_id: str | None = None


class CreateUserRequest(BaseModel):
    user_name: str = Field(min_length=1, examples=["pavan"])
    email: EmailStr
    first_name: str = Field(min_length=1)
    last_name: str = Field(min_length=1)
    group_name: str = Field(
        min_length=1,
        description="IAM group: '<programManagerId>.admin' or "
        "'<programManagerId>.enduser'. Not the same as a person's groupId.",
        examples=["ibkart.admin"],
    )
    mobile_country_code: str = Field(min_length=1, max_length=4, examples=["91"])
    mobile_number: str = Field(min_length=1, examples=["8374543039"])
    password: SecretStr
    two_factor_code_type: str = Field(default="OTP", examples=["OTP"])
    two_factor_delivery_channel: str = Field(default="SMS", examples=["SMS"])
    program_manager_id: str | None = None


class CreateUserResponse(VendorAck):
    pass


class ForgotPasswordRequest(BaseModel):
    user_id: str = Field(min_length=1)
    email: EmailStr
    program_manager_id: str | None = None


class ForgotPasswordResponse(VendorAck):
    pass
