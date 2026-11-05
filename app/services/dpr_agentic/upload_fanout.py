"""Two-branch upload coordinator, independent of Wallet SDK and web framework.

The upload handler must first authenticate the caller, validate the file and persist
both jobs with a durable queue; this function is the *execution* boundary only.
It does not falsely claim that staging, queueing or storage is implemented.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class BranchOutcome:
    branch: str
    succeeded: bool
    reference: str | None = None
    error: str | None = None


async def execute_parallel_branches(
    wallet_storage: Callable[[], Awaitable[str]],
    dpr_analysis: Callable[[], Awaitable[str]],
) -> tuple[BranchOutcome, BranchOutcome]:
    async def run(name, call):
        try:
            reference = await call()
            if not reference:
                raise ValueError("Branch returned no authoritative reference")
            return BranchOutcome(name, True, str(reference))
        except Exception as exc:
            # Return bounded exception code without emitting secrets or
            # document content.
            return BranchOutcome(name, False, error=type(exc).__name__)

    wallet, analysis = await asyncio.gather(
        run("wallet_storage", wallet_storage), run("dpr_analysis", dpr_analysis)
    )
    return wallet, analysis


def can_finalize(
    wallet: BranchOutcome,
    analysis: BranchOutcome,
    *,
    document_identity_verified: bool,
    access_authorized: bool,
) -> bool:
    """Legacy strict completion helper retained for compatibility only.

    Do not use this function to decide access to an analysis report. Instead use
    report_access.evaluate_report_access with fresh, trusted payment and consent
    evidence. The Wallet upload outcome is a separate synchronization status.
    """
    return (
        wallet.succeeded
        and analysis.succeeded
        and document_identity_verified
        and access_authorized
    )
