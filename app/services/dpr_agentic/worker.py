"""Restart-safe worker integration seam.

Host application must provide an authorized source loader and artifact writer, both
backed by Core LAS/Wallet APIs. Never use LAS_Email as the DPR job executor.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from app.core.logging import get_logger

from .jobs import claim, finish, release, renew_lease
from .parallel_graph import analyze_parallel
from .schemas import AnalysisInput, AnalysisResult

logger = get_logger(__name__)


class LeaseLostError(RuntimeError):
    """The run's lease could no longer be renewed: another worker may own it
    now, so this one stopped working on it."""


async def _heartbeat(
    engine,
    run_id: str,
    token: str,
    lease_seconds: int,
    interval: float,
    on_lost: Callable[[], None],
) -> None:
    """Keeps extending the lease while the main task is still working, so a
    slow analysis isn't reclaimed by another worker mid-run. A database error
    on one renewal is logged and retried on the next beat; a renewal the
    database refuses means the lease is gone, and `on_lost` stops the work
    rather than keep spending on a run this worker no longer owns."""
    while True:
        await asyncio.sleep(interval)
        try:
            renewed = renew_lease(engine, run_id, token, lease_seconds=lease_seconds)
        except Exception:  # noqa: BLE001 - transient DB error; try next beat
            logger.warning("dpr_lease_renew_failed run_id=%s", run_id, exc_info=True)
            continue
        if not renewed:
            logger.warning("dpr_lease_lost run_id=%s", run_id)
            on_lost()
            return


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
    grounding_enabled: bool = False,
    grounding_cache=None,
    on_claimed: Callable[[], None] | None = None,
) -> bool:
    """Claims the run and processes it; False if another worker holds it.

    `on_claimed` is called right after a successful claim, so the caller
    knows whether the attempt was its own even when this raises.

    Cancelled from outside (the worker stopping), the run is handed back to
    the queue with `release()` and CancelledError propagates."""
    token = claim(engine, run_id, lease_seconds=lease_seconds)
    if token is None:
        return False
    if on_claimed is not None:
        on_claimed()

    async def work() -> str:
        payload = await load_input(run_id)
        result = await analyze_parallel(
            payload,
            llm,
            checkpointer=checkpointer,
            thread_id=run_id,
            cam_engine_enabled=cam_engine_enabled,
            grounding_enabled=grounding_enabled,
            grounding_cache=grounding_cache,
        )
        ref = await store_result(run_id, result)
        if not ref:
            raise ValueError("Artifact writer did not return a reference")
        return ref

    work_task = asyncio.create_task(work())
    lease_lost = False

    def on_lost() -> None:
        nonlocal lease_lost
        lease_lost = True
        work_task.cancel()

    heartbeat_task = (
        asyncio.create_task(
            _heartbeat(
                engine, run_id, token, lease_seconds, heartbeat_interval, on_lost
            )
        )
        if heartbeat_interval
        else None
    )
    try:
        try:
            ref = await work_task
        except asyncio.CancelledError:
            if not lease_lost:
                # Stopped from outside: hand the run back, unfinished.
                release(engine, run_id, token)
                raise
            raise LeaseLostError(run_id) from None
        return finish(engine, run_id, token, success=True, artifact_ref=ref)
    except LeaseLostError:
        # Not ours any more: finish() would be refused anyway.
        raise
    except Exception as exc:
        # Safe failure code only; never write uploaded text or LLM tokens in logs.
        finish(engine, run_id, token, success=False, error_code=type(exc).__name__)
        raise
    finally:
        # asyncio.wait never re-raises a task's own outcome, so neither task
        # can replace the run's result; a cancellation of *this* task while
        # waiting still propagates.
        for task in (work_task, heartbeat_task):
            if task is None:
                continue
            if not task.done():
                task.cancel()
                await asyncio.wait([task])
            if not task.cancelled():
                task.exception()  # mark retrieved; already handled above
