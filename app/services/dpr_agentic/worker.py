"""Restart-safe worker integration seam.

Host application must provide an authorized source loader and artifact writer, both
backed by Core LAS/Wallet APIs. Never use LAS_Email as the DPR job executor.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable

from .jobs import claim, finish, renew_lease
from .parallel_graph import analyze_parallel
from .schemas import AnalysisInput, AnalysisResult


async def _heartbeat(
    engine, run_id: str, token: str, lease_seconds: int, interval: float
) -> None:
    """Keeps extending the lease while the main task is still working, so a
    slow analysis isn't reclaimed by another worker mid-run."""
    while True:
        await asyncio.sleep(interval)
        renew_lease(engine, run_id, token, lease_seconds=lease_seconds)


async def run_claimed_analysis(
    engine,
    run_id: str,
    llm,
    load_input: Callable[[str], Awaitable[AnalysisInput]],
    store_result: Callable[[str, AnalysisResult], Awaitable[str]],
    *,
    checkpointer=None,
    lease_seconds: int = 900,
    heartbeat_interval: float | None = None,
    cam_engine_enabled: bool = False,
) -> bool:
    token = claim(engine, run_id, lease_seconds=lease_seconds)
    if token is None:
        return False
    heartbeat_task = (
        asyncio.create_task(
            _heartbeat(engine, run_id, token, lease_seconds, heartbeat_interval)
        )
        if heartbeat_interval
        else None
    )
    try:
        payload = await load_input(run_id)
        result = await analyze_parallel(
            payload,
            llm,
            checkpointer=checkpointer,
            thread_id=run_id,
            cam_engine_enabled=cam_engine_enabled,
        )
        ref = await store_result(run_id, result)
        if not ref:
            raise ValueError("Artifact writer did not return a reference")
        return finish(engine, run_id, token, success=True, artifact_ref=ref)
    except Exception as exc:
        # Safe failure code only; never write uploaded text or LLM tokens in logs.
        finish(engine, run_id, token, success=False, error_code=type(exc).__name__)
        raise
    finally:
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat_task
