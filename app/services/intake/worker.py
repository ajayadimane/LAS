"""Runs the intake processor in the API process, every intake_poll_seconds.

The API runs one uvicorn worker (Dockerfile), so there is exactly one loop;
event claims still use leases and SKIP LOCKED, so a second process would be
safe, just redundant. A failing pass is logged and the loop carries on.
"""

import asyncio

from app.core.logging import get_logger
from app.services.intake.processor import IntakeProcessor

logger = get_logger(__name__)


class IntakeWorker:
    def __init__(self, processor: IntakeProcessor, poll_seconds: float) -> None:
        self._processor = processor
        self._poll_seconds = poll_seconds
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="intake-worker")
        logger.info("intake_worker_started poll_seconds=%s", self._poll_seconds)

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                counts = await self._processor.tick()
                if counts:
                    logger.info("intake_worker_pass %s", counts)
            except Exception:
                logger.exception("intake_worker_pass_failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._poll_seconds)
            except asyncio.TimeoutError:
                pass

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
        logger.info("intake_worker_stopped")
