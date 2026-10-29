"""Wallet reads.

A wallet's id is its owner's user id: the BD id for a BD, the customer id
(mobile number) for a customer (user spec, 2026-10-27). One wallet per tag,
and every wallet we open uses the configured tag.
"""

from app.clients.vendor_client import VendorCallContext, VendorClient
from app.core.config import Settings
from app.schemas.wallet import WalletBalance

# A read under /walletInfoRetrieval: no trailing slash, and retried.
BALANCE_PATH = "/walletInfoRetrieval/walletTransferService/wallet/walletBalance"


class WalletService:
    def __init__(self, client: VendorClient, settings: Settings) -> None:
        self._client = client
        self._settings = settings

    async def balance(self, wallet_id: str, ctx: VendorCallContext) -> WalletBalance:
        tag_id = self._settings.vendor_wallet_tag_id
        payload = {
            "walletId": wallet_id,
            "tagId": tag_id,
            "programManagerId": self._settings.vendor_program_manager_id,
        }
        body = await self._client.post(BALANCE_PATH, payload, ctx)
        return WalletBalance.from_vendor(body, wallet_id, tag_id)
