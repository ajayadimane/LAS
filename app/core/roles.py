"""Who is signed in: a BD or a customer, from the token's `groupId` claim.

The vendor puts the IAM group in the access token (verified live 2026-10-28):
`["LAS.admin"]` for a BD, `["LAS.enduser"]` for a customer. The claim is read
unverified, which is safe only because every route that relies on it also
forwards the same token to the vendor, which rejects a forged or expired one
before anything happens. So a forged "admin" token gets nowhere.
"""

from typing import Literal

from app.core.config import Settings
from app.core.token_claims import read_claims

Role = Literal["bd", "customer"]

ROLE_LABELS: dict[Role, str] = {"bd": "BD", "customer": "customer"}


def group_ids(token: str) -> list[str]:
    """The token's `groupId` claim; a list, but a lone string is accepted."""
    raw = (read_claims(token) or {}).get("groupId")
    if isinstance(raw, str):
        raw = [raw]
    return [g for g in raw if isinstance(g, str)] if isinstance(raw, list) else []


def role_of(token: str, settings: Settings) -> Role | None:
    """ "bd" or "customer", or None when the token is neither -- or both,
    which we refuse rather than guess."""
    groups = {g.strip().casefold() for g in group_ids(token)}
    is_bd = settings.bd_iam_group_name.casefold() in groups
    is_customer = settings.customer_iam_group_name.casefold() in groups
    if is_bd == is_customer:
        return None
    return "bd" if is_bd else "customer"
