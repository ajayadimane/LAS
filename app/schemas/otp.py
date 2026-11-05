"""OTP verification of a customer's mobile number and email."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Channel = Literal["mobile", "email"]
# register: a BD adding a customer (person_id = the mobile being registered).
# update:   anyone changing an existing customer's detail.
Purpose = Literal["register", "update"]


class SendOtpRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    channel: Channel
    value: str = Field(
        min_length=3,
        max_length=100,
        description="The mobile number (10 digits) or email address to verify.",
        examples=["9000000013"],
    )
    purpose: Purpose
    person_id: str = Field(
        min_length=1,
        max_length=50,
        description="The customer: on `register`, the mobile number being "
        "registered; on `update`, the customer being edited.",
        examples=["9000000013"],
    )


class SendOtpResponse(BaseModel):
    otp_session: str = Field(
        description="Opaque; send it back with the code to /otp/verify."
    )
    sent_to: str = Field(description="Where the OTP went, partly masked.")
    expires_at: int = Field(description="Unix time the session ends.")
    resend_after_seconds: int
    max_attempts: int
    code_length: int


class VerifyOtpRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    otp_session: str = Field(min_length=1, max_length=2000)
    otp: str = Field(pattern=r"^\d{4,8}$", examples=["861283"])


class VerifyOtpResponse(BaseModel):
    verified: Literal[True] = True
    channel: Channel
    value: str
    verification_token: str = Field(
        description="Proof the value was verified. Send it with the save "
        "(`mobile_otp_token` / `email_otp_token`)."
    )
    expires_at: int


class OtpSettings(BaseModel):
    """What the dashboard needs to draw the OTP steps."""

    required: bool
    code_length: int
    max_attempts: int
    resend_after_seconds: int
