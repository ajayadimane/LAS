"""Celery tasks: thin wrappers that run the async jobs on a per-process event loop.

Process a mail, re-drive stuck mail, deliver events to Core LAS, and housekeeping."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

from celery.signals import worker_process_init

from las.common.deps import Deps, build_deps
from las.common.logging import configure_logging, get_logger
from las.common.settings import get_settings
from las.worker.celery_app import app

log = get_logger(__name__)
_T = TypeVar("_T")
# One event loop per worker process, reused by every task (asyncio.Runner is 3.11+).
_loop: asyncio.AbstractEventLoop | None = None
_deps: Deps | None = None


@worker_process_init.connect
def _init(**_: Any) -> None:
    s = get_settings()
    configure_logging(s.log_level, json=s.log_json)


def run(coro_fn: Callable[..., Coroutine[Any, Any, _T]], *args: Any) -> _T:
    """Run an async job on this worker process's persistent event loop and deps."""
    global _loop, _deps
    if _loop is None or _loop.is_closed():
        _loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_loop)
    if _deps is None:
        _deps = build_deps()
    return _loop.run_until_complete(coro_fn(_deps, *args))


def enqueue_process_inbound(inbound_id: int) -> None:
    process_inbound_task.delay(inbound_id)


@app.task(name="las.inbound.process", bind=True, max_retries=6, acks_late=True)
def process_inbound_task(self: Any, inbound_id: int) -> str:
    from las.workflow.pipeline import mark_failed, process_inbound

    try:
        return str(run(process_inbound, inbound_id).status)
    except Exception as e:
        log.exception("inbound_processing_failed", inbound_id=inbound_id)
        run(mark_failed, inbound_id, f"{type(e).__name__}: {e}"[:500])
        raise self.retry(exc=e, countdown=min(30 * 2**self.request.retries, 3600)) from e


@app.task(name="las.sweep.inbound")
def sweep_inbound() -> int:
    from las.worker.jobs import stale_inbound_ids

    ids: list[int] = run(stale_inbound_ids)
    for i in ids:
        process_inbound_task.delay(i)
    return len(ids)


@app.task(name="las.jobs.usage_rollup")
def usage_rollup() -> None:
    from las.worker.jobs import refresh_usage_rollup

    run(refresh_usage_rollup)


@app.task(name="las.jobs.audit_verify")
def audit_verify() -> bool:
    from las.worker.jobs import verify_audit_chain

    return run(verify_audit_chain)


@app.task(name="las.jobs.core_intake_handoff", acks_late=True)
def core_intake_handoff() -> dict[str, int]:
    from las.core_handoff import dispatch_core_intake

    async def go(d: Deps) -> dict[str, int]:
        return await dispatch_core_intake(d, d.settings.core_intake_batch_size)

    return run(go)
