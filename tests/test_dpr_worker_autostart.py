"""DPR_WORKER_AUTOSTART: the API starts, restarts and stops the DPR worker
as a child process. A dummy command stands in for scripts/dpr_worker.py, so
no Gemini, database or LiteParse is touched."""

import asyncio
import os
import sys

import pytest

from app.core.config import Settings
from app.services.dpr_agentic import worker_process
from app.services.dpr_agentic.worker_process import (
    DprWorkerProcess,
    missing_worker_settings,
    start_if_enabled,
)

SLEEPER = [sys.executable, "-c", "import time; time.sleep(60)"]


def _settings(**overrides) -> Settings:
    base = dict(
        _env_file=None,
        dpr_worker_autostart=True,
        dpr_agentic_enabled=True,
        dpr_database_url="postgresql+psycopg://u:p@localhost/x",
        dpr_staging_encryption_key="x" * 43 + "=",
        dpr_agentic_gemini_api_key="k",
    )
    base.update(overrides)
    return Settings.model_construct(**base)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


async def _wait_for_pid(worker: DprWorkerProcess, not_equal=None) -> int:
    for _ in range(100):
        if worker.pid is not None and worker.pid != not_equal:
            return worker.pid
        await asyncio.sleep(0.05)
    raise AssertionError("worker never started")


async def test_start_and_stop_child_process():
    worker = DprWorkerProcess(SLEEPER)
    await worker.start()
    pid = await _wait_for_pid(worker)
    assert _alive(pid)
    await worker.stop()
    assert not _alive(pid)


async def test_restarts_after_unexpected_exit():
    worker = DprWorkerProcess(SLEEPER, restart_delay=0.05)
    await worker.start()
    first = await _wait_for_pid(worker)
    os.kill(first, 9)
    second = await _wait_for_pid(worker, not_equal=first)
    assert second != first
    await worker.stop()
    assert not _alive(second)


async def test_disabled_by_default_starts_nothing():
    assert await start_if_enabled(_settings(dpr_worker_autostart=False)) is None


async def test_missing_settings_skips_start():
    settings = _settings(dpr_agentic_gemini_api_key=None)
    assert missing_worker_settings(settings) == ["DPR_AGENTIC_GEMINI_API_KEY"]
    assert await start_if_enabled(settings) is None


async def test_enabled_starts_worker(monkeypatch):
    monkeypatch.setattr(
        worker_process,
        "DprWorkerProcess",
        lambda: DprWorkerProcess(SLEEPER),
    )
    worker = await start_if_enabled(_settings())
    assert worker is not None
    pid = await _wait_for_pid(worker)
    await worker.stop()
    assert not _alive(pid)


def test_default_setting_is_off():
    assert Settings.model_fields["dpr_worker_autostart"].default is False


@pytest.fixture(autouse=True)
def _no_real_worker_script(monkeypatch):
    # Belt and braces: nothing in this file may ever spawn the real worker.
    monkeypatch.setattr(worker_process, "WORKER_SCRIPT", "/nonexistent")
