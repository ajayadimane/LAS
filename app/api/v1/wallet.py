"""Wallet routes for the signed-in user, BD or customer."""

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import (
    get_authenticated_context,
    get_token_user_id,
    get_wallet_service,
)
from app.clients.vendor_client import VendorCallContext
from app.schemas.wallet import WalletBalance
from app.services.wallet import WalletService

router = APIRouter(prefix="/wallet", tags=["wallet"])


def get_own_wallet_id(
    wallet_id: str | None = Query(
        default=None,
        description="The userId saved at login (BD id or customer id). Defaults "
        "to the token's user; if sent, it must be that user.",
    ),
    token_user: str = Depends(get_token_user_id),
) -> str:
    """The signed-in user's wallet id, which is their user id.

    The vendor does not check that a walletId belongs to the token, so we do,
    case-insensitively as in `get_bd_id` (Keycloak lowercases usernames).
    """
    if wallet_id is not None and wallet_id.casefold() != token_user.casefold():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only see your own wallet.",
        )
    return wallet_id or token_user


@router.get("/balance", response_model=WalletBalance)
async def get_balance(
    wallet_id: str = Depends(get_own_wallet_id),
    service: WalletService = Depends(get_wallet_service),
    ctx: VendorCallContext = Depends(get_authenticated_context),
) -> WalletBalance:
    """The signed-in user's wallet balance."""
    return await service.balance(wallet_id, ctx)
