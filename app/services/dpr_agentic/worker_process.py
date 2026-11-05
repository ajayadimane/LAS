"""Runs scripts/dpr_worker.py as a child of the API process.

With DPR_WORKER_AUTOSTART=true, starting the API (uvicorn) also starts the
DPR worker, and stopping the API stops it. It stays a *separate process*, not
a task inside the API's event loop, for the reasons in INCREMENT6.md's Phase
A: a multi-minute analysis (and LiteParse's blocking OCR) must never stall
dashboard requests, and a worker crash must never take the API down.

If the worker exits unexpectedly it is restarted after a short delay. Running
the systemd unit (deploy/las-dpr-worker.service) as well is safe -- jobs.claim()
leases each run to one worker -- but redundant; pick one.

Stopping the worker mid-run (API shutdown, or every code change under
`uvicorn --reload`) leaves that run RUNNING until its lease expires
(dpr_worker.LEASE_SECONDS); then the worker picks it up again.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import sys
from pathlib import Path

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKER_SCRIPT = REPO_ROOT / "scripts" / "dpr_worker.py"
RESTART_DELAY_SECONDS = 5.0
STOP_TIMEOUT_SECONDS = 10.0


def missing_worker_settings(settings: Settings) -> list[str]:
    """The settings scripts/dpr_worker.py itself refuses to start without."""
    return [
        name
        for name, value in (
            ("DPR_AGENTIC_ENABLED", settings.dpr_agentic_enabled or None),
            ("DPR_DATABASE_URL", settings.dpr_database_url),
            ("DPR_STAGING_ENCRYPTION_KEY", settings.dpr_staging_encryption_key),
            ("DPR_AGENTIC_GEMINI_API_KEY", settings.dpr_agentic_gemini_api_key),
        )
        if not value
    ]


def _die_with_parent() -> None:  # pragma: no cover - runs in the child
    """Linux only: SIGTERM the worker if the API process dies without running
    its shutdown (e.g. kill -9), so no orphaned worker keeps running."""
    with contextlib.suppress(Exception):
        import ctypes

        PR_SET_PDEATHSIG = 1
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(PR_SET_PDEATHSIG, signal.SIGTERM)


class DprWorkerProcess:
    def __init__(
        self,
        command: list[str] | None = None,
        *,
        restart_delay: float = RESTART_DELAY_SECONDS,
    ) -> None:
        self._command = command or [sys.executable, str(WORKER_SCRIPT)]
        self._restart_delay = restart_delay
        self._proc: asyncio.subprocess.Process | None = None
        self._supervisor: asyncio.Task | None = None
        self._stopping = False

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    async def start(self) -> None:
        self._supervisor = asyncio.create_task(self._supervise())

    async def _spawn(self) -> asyncio.subprocess.Process:
        # stdout/stderr inherited: the worker's log lines show up in the same
        # terminal / journal as the API's.
        return await asyncio.create_subprocess_exec(
            *self._command,
            cwd=str(REPO_ROOT),
            preexec_fn=_die_with_parent if sys.platform == "linux" else None,
        )

    async def _supervise(self) -> None:
        while not self._stopping:
            try:
                self._proc = await self._spawn()
            except OSError:
                logger.exception("dpr_worker_autostart_failed")
                return
            logger.info("dpr_worker_autostarted pid=%s", self._proc.pid)
            code = await self._proc.wait()
            if self._stopping:
                return
            logger.warning(
                "dpr_worker_exited code=%s restarting_in_s=%s",
                code,
                self._restart_delay,
            )
            await asyncio.sleep(self._restart_delay)

    async def stop(self) -> None:
        self._stopping = True
        proc = self._proc
        if proc is not None and proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), STOP_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
            logger.info("dpr_worker_stopped pid=%s", proc.pid)
        if self._supervisor is not None:
            self._supervisor.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._supervisor


async def start_if_enabled(settings: Settings) -> DprWorkerProcess | None:
    if not settings.dpr_worker_autostart:
        return None
    missing = missing_worker_settings(settings)
    if missing:
        logger.warning(
            "dpr_worker_autostart_skipped missing_settings=%s", ",".join(missing)
        )
        return None
    worker = DprWorkerProcess()
    await worker.start()
    return worker
