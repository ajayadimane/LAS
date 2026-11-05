"""A stub for the vendor's money-moving transfer call.

PUT /transfer/systemAccountPayment (app/services/enrolment.py's `_transfer`)
moves real money between the pool and main system accounts on the real
vendor. There is no safe way to exercise the full pay-later flow end to end
(including a real UI click-through, e.g. with Playwright) without either
real vendor credentials or a way to fake just that one call -- this is the
latter.

Gated by Settings.vendor_wallet_stub_enabled, which Settings itself refuses
to be True outside environment in {local, dev, test} (see
app/core/config.py's `_wallet_stub_only_outside_production` validator) -- so
this can never silently activate in a real deployment's config. It holds no
vendor credentials and never makes an HTTP call.
"""

import uuid
from datetime import datetime, timezone

from app.schemas.common import VendorAck


class StubWalletPaymentClient:
    """Fakes a successful pool-to-main transfer. Never contacts the vendor."""

    async def transfer(self, amount: int) -> VendorAck:
        return VendorAck(
            transaction_id=f"stub-{uuid.uuid4().hex}",
            correlation_id=f"stub-{uuid.uuid4().hex}",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
