"""Vendor error codes translated into something a person can read.

The vendor's `errorMessage` is not prose -- it is the machine code repeated
verbatim (`INVALID_CREDENTIALS_TO_AUTHENTICATE_USER`). Showing that to a
dashboard user is not acceptable, so every outgoing error gets a message from
this module instead. The raw code still travels to the dashboard in a separate
field, and the raw vendor text is still logged.

Codes here were observed against the live sandbox. Do not invent entries: an
unobserved code that never arrives is dead weight, and the generic fallback
already covers it safely. Add a code once you have actually seen it.
"""

GENERIC_MESSAGE = (
    "Something went wrong. Please try again. If it keeps happening, quote the "
    "reference below when you contact support."
)

# Every credential failure must be indistinguishable to the caller: a wrong
# password and an unknown user get the same message, so the dashboard cannot be
# used to enumerate valid user ids. The vendor currently returns one code for
# both; any further credential-failure code must be added to this set rather
# than to VENDOR_ERROR_MESSAGES, so it inherits the same message automatically.
CREDENTIAL_FAILURE_MESSAGE = "The user ID or password is incorrect."

CREDENTIAL_FAILURE_CODES = frozenset(
    {
        "INVALID_CREDENTIALS_TO_AUTHENTICATE_USER",
    }
)

# Observed codes -> message. Verified live on 2026-10-14.
_SPECIFIC_MESSAGES = {
    "PM_NOT_FOUND": (
        "That program manager could not be found. Check the value and try again."
    ),
    "PROGRAM_MANAGER_ID_CAN_NOT_BE_EMPTY": "A program manager ID is required.",
    # Seen 2026-10-28: a person only has the custom fields that existed when
    # they were registered, and the vendor will not add one on update. So
    # customers registered before a service's fields were created cannot use
    # that service until the vendor backfills them. Nothing was written.
    "CUSTOM_FIELD_ID_NOT_EXISTS": (
        "This customer was registered before this service was set up, so it "
        "cannot be added for them yet. Nothing was charged. Please contact "
        "support."
    ),
}

# Our own codes, raised by app/services/admin_session.py when the middleware
# cannot act as the vendor super admin. A configuration problem on our side.
_ADMIN_MESSAGE = (
    "Customer logins cannot be created right now. Please try again later, or "
    "contact support if it keeps happening."
)
_OWN_CODES = {
    code: _ADMIN_MESSAGE
    for code in ("ADMIN_NOT_CONFIGURED", "ADMIN_LOGIN_FAILED", "ADMIN_TOKEN_REJECTED")
}

_OWN_CODES["TRANSFER_NOT_PERMITTED"] = (
    "The payment could not be made: this account is not allowed to transfer "
    "from the pool. Nothing was charged. Please contact support."
)

_OWN_CODES["EMAIL_ALREADY_IN_USE"] = (
    "This email address is already used by another customer's login, and each "
    "login needs its own email. Change the customer's email (Edit details) "
    "and create the login again."
)

# The IAM service sometimes puts a raw upstream (Keycloak) error where the
# machine code belongs. Recognised ones become our own code; anything else is
# dropped by app.core.exceptions.error_details, since it can carry internals.
# Seen live 2026-10-29 on POST /IdentityAccessMgmt/user/ (HTTP 400):
#   Error: 409 CONFLICT {"errorMessage":"User exists with same email"}
UPSTREAM_ERROR_CODES = {
    "User exists with same email": "EMAIL_ALREADY_IN_USE",
}


def upstream_code(raw: str | None) -> str | None:
    """Our code for a recognised raw upstream error, else None."""
    if not raw:
        return None
    return next((c for text, c in UPSTREAM_ERROR_CODES.items() if text in raw), None)


VENDOR_ERROR_MESSAGES = {
    **_SPECIFIC_MESSAGES,
    **_OWN_CODES,
    **{code: CREDENTIAL_FAILURE_MESSAGE for code in CREDENTIAL_FAILURE_CODES},
}

# Used when the vendor sends no code at all. Several real failures have an
# empty body (429, and 500 on a missing geolocation header), so this is not a
# theoretical path. Keyed on the status we return, not the vendor's.
STATUS_MESSAGES = {
    400: "The request was not valid. Please check the details and try again.",
    401: "Your session has expired. Please sign in again.",
    403: "You do not have permission to do that.",
    404: "We could not find what you were looking for.",
    409: "That already exists.",
    422: "The request was not valid. Please check the details and try again.",
    429: "Too many requests. Please wait a moment and try again.",
    502: "The service is temporarily unavailable. Please try again shortly.",
    504: "The service took too long to respond. Please try again.",
}


def friendly_message(error_code: str | None, status: int) -> str:
    """The message the dashboard shows. Never returns vendor internals.

    Resolution order: known code, then the status, then a generic message.
    """
    if error_code:
        known = VENDOR_ERROR_MESSAGES.get(error_code)
        if known:
            return known
    return STATUS_MESSAGES.get(status, GENERIC_MESSAGE)


def is_known_code(error_code: str | None) -> bool:
    return bool(error_code) and error_code in VENDOR_ERROR_MESSAGES
